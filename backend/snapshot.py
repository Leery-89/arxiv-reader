"""论文快照：把模型当时看到的论文原样存下来。

    cd backend
    python snapshot.py            # 给 eval/papers.txt 里所有论文拍快照（只抓 arXiv，不调模型，不花钱）
    python snapshot.py --force    # 已有快照也重抓

为什么需要：eval/results/ 里只存了模型输出和 quote，没有段落全文。
harness 的检查要对照"所引段落全文"（D17），离线跑就必须有快照。

存的是 fetch_paper() 的返回值——已经套过预算、截掉过附录，
和当时喂给模型的完全一致。之后 eval_run.py 每跑一篇会顺带存一份。

漂移检查：arXiv 上的 HTML 可能在评测之后更新过（新版本），段落 ID 会变。
所以抓完会用快照重算一遍 quote 命中率，和评测时记录的比：对不上就说明漂了。
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from config import PARSER_VERSION
from models import Paper, Paragraph, Section

EVAL_DIR = Path(__file__).parent / "eval"
SNAP_DIR = EVAL_DIR / "papers"
RESULTS_DIR = EVAL_DIR / "results"


def save_snapshot(paper: Paper, snap_dir: Path = SNAP_DIR) -> Path:
    snap_dir.mkdir(parents=True, exist_ok=True)
    path = snap_dir / f"{paper.arxiv_id}.json"
    # 记下是哪版解析器产出的（D18）；字段不属于 Paper，读回时剥掉
    d = {**asdict(paper), "_parser_version": PARSER_VERSION}
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_snapshot(arxiv_id: str, snap_dir: Path = SNAP_DIR) -> Paper | None:
    path = snap_dir / f"{arxiv_id}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    d.pop("_parser_version", None)       # 旧快照没有这个字段，当作版本 1
    d["sections"] = [
        Section(**{**s, "paragraphs": [Paragraph(**p) for p in s["paragraphs"]]})
        for s in d["sections"]
    ]
    return Paper(**d)


def snapshot_parser_version(arxiv_id: str, snap_dir: Path = SNAP_DIR) -> int | None:
    """快照是哪版解析器产出的；没有快照返回 None，D18 之前的旧快照返回 1。"""
    path = snap_dir / f"{arxiv_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("_parser_version", 1)


def main():
    from eval_run import PAPERS_FILE, load_papers
    from fetcher import fetch_paper
    from verify import check

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    papers = load_papers(PAPERS_FILE)
    drift = []
    for i, (cat, pid) in enumerate(papers, 1):
        if (SNAP_DIR / f"{pid}.json").exists() and not args.force:
            print(f"[{i}/{len(papers)}] {pid:12} 已有快照")
            continue
        try:
            paper = fetch_paper(pid)
        except Exception as e:
            print(f"[{i}/{len(papers)}] {pid:12} 抓取失败：{e}")
            continue
        save_snapshot(paper)

        # 漂移检查：用快照重算 quote 命中，和评测时记的比
        res_path = RESULTS_DIR / f"{pid}.json"
        note = ""
        if res_path.exists():
            rec = json.loads(res_path.read_text(encoding="utf-8"))
            old = rec["verify"]["quote_hits"]
            new = check(paper, rec["result"])["quote_hits"]
            note = f"quote 命中 评测时 {old} / 快照 {new}"
            if new != old:
                note += "  ← 漂移"
                drift.append(pid)
        n_para = sum(len(s.paragraphs) for s in paper.sections)
        print(f"[{i}/{len(papers)}] {pid:12} {paper.source:13} {n_para:4} 段  {note}")

    print(f"\n快照 → {SNAP_DIR}")
    if drift:
        print(f"有漂移的论文：{drift}——这几篇的 harness 结果要单独看待")
    else:
        print("没有漂移")


if __name__ == "__main__":
    main()
