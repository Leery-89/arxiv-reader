"""覆盖度：两版导读成对比较（D20）。

支撑率只量"写出来的有没有依据"，量不到"该写的有没有写"——少写就能高分。
RAG 只看到部分原文，最可能丢的正是覆盖面，所以单独评。

做法：评审员拿到论文全文 + 两版导读（去掉段落 ID，只看内容），判断哪版更完整地
覆盖了论文的关键内容，并列出各自漏掉的要点。每篇论文 A/B 顺序交换各问一次，
两次结论一致才算赢，不一致记平——抵消评审员偏向某个位置的倾向。

    cd backend
    python pairwise.py --a v3 --b rag_bm25_25                 # 默认评审员 deepseek-chat
    python pairwise.py --a v3 --b rag_bm25_25 --judge gpt-6.1-sol
    python pairwise.py --a v3 --b v3_rerun                    # 对照：同一个版本跑两次，应该接近全平
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import judge
from eval_versions import results_dir
from serialize import paper_to_text
from snapshot import load_snapshot

EVAL_DIR = Path(__file__).parent / "eval"
CACHE = EVAL_DIR / "pairwise_cache.jsonl"
FIELDS = [("summary", "导读"), ("research_question", "研究问题"), ("method", "方法"),
          ("experiments", "实验"), ("findings", "主要发现"), ("limitations", "局限")]

PROMPT = """你是严谨的论文审稿人。下面给你一篇论文的全文，以及两份由 AI 写的中文导读（A 和 B）。
导读的目的：让没读过论文的人快速掌握它的研究问题、方法、实验设置、主要结果和作者承认的局限。

请只比较**覆盖度**：哪份导读更完整地覆盖了论文中读者最需要知道的关键内容。
- 关键内容指：核心问题与动机、方法的核心思想、主要实验设置、最重要的定量结果与比较、作者明确承认的局限。
- 不要因为篇幅长就判赢：重复、空话、次要细节不算覆盖。
- 写错的内容（与原文不符）不算覆盖，并且要扣分。
- 两份覆盖程度相当时判 tie。

先在心里对照全文列出关键要点，再判断。只输出 JSON：
{"winner": "A" | "B" | "tie", "missing_in_A": ["A 漏掉的关键要点", ...], "missing_in_B": ["B 漏掉的关键要点", ...], "reason": "一句话理由"}"""
PROMPT_VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()[:8]
ID_RE = re.compile(r"\[[A-Za-z0-9.]+\]")


def guide_text(result: dict) -> str:
    """只留内容：去掉段落 ID 和 evidence，评审员只比较写了什么。"""
    parts = []
    for key, name in FIELDS:
        v = result.get(key)
        text = v if isinstance(v, str) else (v or {}).get("text", "")
        text = re.sub(r"\s+([。，；、）])", r"\1", ID_RE.sub("", text))   # 去 ID 后留下的空格
        parts.append(f"【{name}】{text.strip()}")
    return "\n".join(parts)


def load_cache() -> dict:
    if not CACHE.exists():
        return {}
    return {d["key"]: d for d in map(json.loads, CACHE.read_text(encoding="utf-8").splitlines()) if d}


def ask(client, model: str, paper_text: str, ga: str, gb: str) -> dict:
    msgs = [{"role": "system", "content": PROMPT},
            {"role": "user", "content": f"# 论文全文\n\n{paper_text}\n\n# 导读 A\n\n{ga}\n\n# 导读 B\n\n{gb}"}]
    resp = judge._create(client, model, msgs)
    d = judge._extract_json(resp.choices[0].message.content)
    if d.get("winner") not in ("A", "B", "tie"):
        raise ValueError(f"非法 winner：{d}")
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="版本标签，如 v3")
    ap.add_argument("--b", required=True, help="版本标签，如 rag_bm25_25")
    ap.add_argument("--judge", default="deepseek-chat")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    # 判断用的 prompt 有 max_tokens=400 的参数组；这里要列漏掉的要点，放宽一些
    judge._PARAM_SETS[0] = {**judge._PARAM_SETS[0], "max_tokens": 1500}
    judge._PARAM_SETS[1] = {**judge._PARAM_SETS[1], "max_tokens": 1500}

    jobs = []
    for f in sorted(results_dir(args.a).glob("*.json")):
        ra = json.loads(f.read_text(encoding="utf-8"))
        fb = results_dir(args.b) / f.name
        if ra.get("category") == "abstract_only" or not fb.exists():
            continue
        rb = json.loads(fb.read_text(encoding="utf-8"))
        pid = ra["paper"]["arxiv_id"]
        paper = load_snapshot(pid)
        if paper is None:
            continue
        ga, gb = guide_text(ra["result"]), guide_text(rb["result"])
        text = paper_to_text(paper)
        for order in ("AB", "BA"):
            x, y = (ga, gb) if order == "AB" else (gb, ga)
            key = hashlib.sha256(f"{args.judge}|{PROMPT_VERSION}|{pid}|{x}|{y}".encode()).hexdigest()[:20]
            jobs.append({"key": key, "pid": pid, "cat": ra["category"], "order": order,
                         "text": text, "x": x, "y": y})

    cache = load_cache()
    todo = [j for j in jobs if j["key"] not in cache]
    print(f"{args.a} vs {args.b}，评审员 {args.judge}：{len(jobs) // 2} 篇 × 2 个顺序，需要调用 {len(todo)}")
    if todo:
        client = judge.make_client(args.judge)
        def run(j):
            for i in range(3):
                try:
                    return j, ask(client, args.judge, j["text"], j["x"], j["y"])
                except Exception as e:
                    if i == 2:
                        print(f"  失败 {j['pid']} {j['order']}: {str(e)[:120]}")
                        return j, None
        with CACHE.open("a", encoding="utf-8") as cf, ThreadPoolExecutor(args.workers) as pool:
            for n, (j, d) in enumerate(pool.map(run, todo), 1):
                if d:
                    rec = {"key": j["key"], **d}
                    cache[j["key"]] = rec
                    cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    cf.flush()
                if n % 10 == 0:
                    print(f"  {n}/{len(todo)}")

    # 换算回"版本 a / 版本 b"：AB 顺序里 A=a，BA 顺序里 A=b
    by_pid: dict[str, dict] = {}
    for j in jobs:
        d = cache.get(j["key"])
        if not d:
            continue
        w = d["winner"]
        who = "tie" if w == "tie" else ({"A": "a", "B": "b"}[w] if j["order"] == "AB" else {"A": "b", "B": "a"}[w])
        miss_a = d.get("missing_in_A") if j["order"] == "AB" else d.get("missing_in_B")
        miss_b = d.get("missing_in_B") if j["order"] == "AB" else d.get("missing_in_A")
        e = by_pid.setdefault(j["pid"], {"cat": j["cat"], "votes": [], "miss_a": [], "miss_b": []})
        e["votes"].append(who)
        e["miss_a"].append(len(miss_a or []))
        e["miss_b"].append(len(miss_b or []))

    final, by_cat, flips = Counter(), {}, 0
    ma = mb = 0
    for pid, e in by_pid.items():
        if len(e["votes"]) < 2:
            continue
        v = e["votes"][0] if e["votes"][0] == e["votes"][1] else "tie"
        flips += e["votes"][0] != e["votes"][1]
        final[v] += 1
        by_cat.setdefault(e["cat"], Counter())[v] += 1
        ma += sum(e["miss_a"]) / 2
        mb += sum(e["miss_b"]) / 2
    n = sum(final.values())
    if not n:
        return
    print(f"\n══ 覆盖度成对比较：{args.a} vs {args.b}（评审员 {args.judge}，{n} 篇）══")
    print(f"  {args.a} 更完整 {final['a']:3} 篇   {args.b} 更完整 {final['b']:3} 篇   平 {final['tie']:3} 篇")
    print(f"  两个顺序结论不一致（记平）{flips} 篇——位置偏差的大小")
    print(f"  平均每篇漏掉的关键要点：{args.a} {ma / n:.1f} 条   {args.b} {mb / n:.1f} 条")
    for cat, c in sorted(by_cat.items()):
        print(f"    {cat:10} {args.a} {c['a']}  {args.b} {c['b']}  平 {c['tie']}")
    print(f"\n逐条理由和漏掉的要点在 {CACHE.name}")


if __name__ == "__main__":
    main()
