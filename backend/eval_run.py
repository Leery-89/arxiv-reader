"""D14 评测集：批量跑。

    cd backend
    python eval_run.py                # 跑 eval/papers.txt 里所有论文，已跑过的跳过
    python eval_run.py --force        # 全部重跑（改了 prompt 之后用）
    python eval_run.py --only physics # 只跑某个类别

直接 import 后端模块，不走 HTTP：
  - 绕开限流（限流是给公网的，不是给自己的）
  - 不用先起服务
  - 每篇的完整输出落盘，之后改 verify.py 可以离线重算，不用再花钱

产物：
    eval/results/<id>.json    每篇：论文元信息 + 模型完整输出 + 校验结果
    eval/summary.csv          每篇一行的机器指标，eval_report.py 拿它汇总
    eval/annotate.csv         人工标注表：每篇抽 5 条 evidence，你填 label 列
"""

import argparse
import csv
import json
import random
import re
import sys
import time
import traceback
from pathlib import Path

from fetcher import fetch_paper
from llm import analyze
from serialize import paper_to_text
from snapshot import save_snapshot
from verify import FIELDS, check

EVAL_DIR = Path(__file__).parent / "eval"
RESULTS_DIR = EVAL_DIR / "results"
PAPERS_FILE = EVAL_DIR / "papers.txt"
SUMMARY_CSV = EVAL_DIR / "summary.csv"
ANNOTATE_CSV = EVAL_DIR / "annotate.csv"

NOT_MENTIONED = "原文未明确提及"
from prompts import PROMPTS as _PROMPTS
PROMPTS_KNOWN = set(_PROMPTS)       # 含 v3 消融变体：--tag 和 PROMPT 必须一致
SAMPLES_PER_PAPER = 5
random.seed(42)          # 抽样固定，重跑抽到同样的条目


# ───────────────────────── 读清单 ─────────────────────────

def load_papers(path: Path) -> list[tuple[str, str]]:
    """返回 [(category, arxiv_id), ...]。跳过空行、注释、没填 ID 的行。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()          # 去掉行尾注释
        if not line or ":" not in line:
            continue
        cat, pid = (x.strip() for x in line.split(":", 1))
        if cat and pid:
            out.append((cat, pid))
    return out


# ───────────────────────── 跑一篇 ─────────────────────────

def run_one(arxiv_id: str) -> dict:
    paper = fetch_paper(arxiv_id)
    save_snapshot(paper)            # harness 离线检查要用段落全文（D17）
    text = paper_to_text(paper)
    result = analyze(text)
    verify = check(paper, result)

    not_mentioned = sum(
        1 for f in FIELDS if NOT_MENTIONED in (result.get(f) or {}).get("text", "")
    )
    return {
        "paper": {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "source": paper.source,
            "truncated_sections": paper.truncated_sections,
            "char_count": paper.char_count(),
            "n_sections": len(paper.sections),
            "n_paragraphs": sum(len(s.paragraphs) for s in paper.sections),
        },
        "result": result,
        "verify": verify,
        "not_mentioned_fields": not_mentioned,
    }


# ───────────────────────── 标注表 ─────────────────────────

def _claim_for(text: str, pid: str) -> str:
    """从 text 里找出引用了 [pid] 的那一句，作为标注时的「论断」。找不到就给开头。"""
    # 中文句号直接切；英文句号后面必须跟空格才切，否则 S3.p1 里的点会被切开
    for sent in re.split(r"(?<=[。．!?！？])|(?<=\.)\s+", text):
        if f"[{pid}]" in sent:
            return sent.strip()
    return text[:120]


def sample_evidence(arxiv_id: str, category: str, rec: dict) -> list[dict]:
    """每篇抽 SAMPLES_PER_PAPER 条 evidence，只抽 quote 命中的——
    没命中的已经是机器判定的错，不用人再看。"""
    pool = []
    for field in FIELDS:
        block = rec["result"].get(field) or {}
        for ev in block.get("evidence") or []:
            pool.append({
                "paper_id": arxiv_id,
                "category": category,
                "field": field,
                "evidence_id": ev.get("id", ""),
                "quote": ev.get("quote", ""),
                "claim": _claim_for(block.get("text", ""), ev.get("id", "")),
                "label": "",
                "notes": "",
            })
    bad = {(i["field"], i["id"]) for i in rec["verify"]["issues"]}
    pool = [r for r in pool if (r["field"], r["evidence_id"]) not in bad]
    return random.sample(pool, min(SAMPLES_PER_PAPER, len(pool)))


# ───────────────────────── 主流程 ─────────────────────────

SUMMARY_COLS = [
    "arxiv_id", "category", "title", "status", "source", "truncated", "chars",
    "prompt_tokens", "completion_tokens", "elapsed_s",
    "quote_hits", "quote_total", "quote_rate",
    "inline_hits", "inline_total", "inline_rate",
    "not_mentioned_fields", "error",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="已有结果也重跑")
    ap.add_argument("--only", help="只跑这个类别")
    ap.add_argument("--tag", help="版本标签，结果存到 eval/results_<tag>/（D19）。不给 = v2 的原始目录")
    args = ap.parse_args()

    global RESULTS_DIR, SUMMARY_CSV, ANNOTATE_CSV
    from prompts import ACTIVE_PROMPT
    if args.tag:
        # 防呆：--tag v3 却忘了 PROMPT=v3，会把 v2 的输出存进 v3 的目录
        if args.tag in PROMPTS_KNOWN and args.tag != ACTIVE_PROMPT:
            sys.exit(f"--tag {args.tag} 但当前 prompt 是 {ACTIVE_PROMPT}。请这样跑：PROMPT={args.tag} python eval_run.py --tag {args.tag}")
        RESULTS_DIR = EVAL_DIR / f"results_{args.tag}"
        SUMMARY_CSV = EVAL_DIR / f"summary_{args.tag}.csv"
        ANNOTATE_CSV = EVAL_DIR / f"annotate_{args.tag}.csv"
    print(f"prompt {ACTIVE_PROMPT} → {RESULTS_DIR.name}/")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    papers = load_papers(PAPERS_FILE)
    if args.only:
        papers = [(c, p) for c, p in papers if c == args.only]
    if not papers:
        sys.exit("清单为空，先填 eval/papers.txt")

    rows, annot = [], []
    for i, (cat, pid) in enumerate(papers, 1):
        out = RESULTS_DIR / f"{pid}.json"
        if out.exists() and not args.force:
            rec = json.loads(out.read_text(encoding="utf-8"))
            print(f"[{i}/{len(papers)}] {pid:14} {cat:14} 已有结果，跳过")
        else:
            print(f"[{i}/{len(papers)}] {pid:14} {cat:14} 跑…", end="", flush=True)
            t0 = time.time()
            try:
                rec = run_one(pid)
                rec["category"] = cat
                from prompts import ACTIVE_PROMPT
                from cache import prompt_version
                rec["prompt"] = {"name": ACTIVE_PROMPT, "hash": prompt_version()}
                out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                v = rec["verify"]
                print(f" {time.time() - t0:5.1f}s  {rec['paper']['source']:13} "
                      f"quote {v['quote_hits']}/{v['quote_total']}  inline {v['inline_hits']}/{v['inline_total']}")
            except Exception as e:
                print(f" 失败：{e}")
                traceback.print_exc(limit=1)
                rows.append({"arxiv_id": pid, "category": cat, "status": "error", "error": str(e)[:200]})
                continue

        p, v, m = rec["paper"], rec["verify"], rec["result"]["_meta"]
        rows.append({
            "arxiv_id": pid, "category": cat, "title": p["title"][:60], "status": "ok",
            "source": p["source"], "truncated": bool(p["truncated_sections"]), "chars": p["char_count"],
            "prompt_tokens": m["prompt_tokens"], "completion_tokens": m["completion_tokens"],
            "elapsed_s": m["elapsed_s"],
            "quote_hits": v["quote_hits"], "quote_total": v["quote_total"], "quote_rate": v["quote_rate"],
            "inline_hits": v["inline_hits"], "inline_total": v["inline_total"], "inline_rate": v["inline_rate"],
            "not_mentioned_fields": rec["not_mentioned_fields"], "error": "",
        })
        annot.extend(sample_evidence(pid, cat, rec))

    with SUMMARY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLS)
        w.writeheader()
        w.writerows(rows)

    # 标注表：如果已经有一份并且填过 label，不要覆盖——合并进去
    existing = {}
    if ANNOTATE_CSV.exists():
        with ANNOTATE_CSV.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[(r["paper_id"], r["field"], r["evidence_id"], r["quote"])] = r
    merged = []
    for r in annot:
        k = (r["paper_id"], r["field"], r["evidence_id"], r["quote"])
        merged.append(existing.get(k, r))
    with ANNOTATE_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(annot[0].keys()) if annot else
                           ["paper_id", "category", "field", "evidence_id", "quote", "claim", "label", "notes"])
        w.writeheader()
        w.writerows(merged)

    ok = [r for r in rows if r["status"] == "ok"]
    print(f"\n完成 {len(ok)}/{len(rows)} 篇。summary → {SUMMARY_CSV.name}，标注表 → {ANNOTATE_CSV.name}"
          f"（{len(merged)} 条，已标 {sum(1 for r in merged if r['label'])} 条）")
    print("下一步：python eval_report.py")


if __name__ == "__main__":
    main()
