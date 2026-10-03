"""在评测集上离线跑 harness，看它拦得准不准。

    cd backend
    python snapshot.py          # 先拍快照（只需一次）
    python harness_eval.py      # 再跑这个（不调模型，不花钱，几秒钟）

输出三样：
  1. 每篇 / 总体的论断状态分布
  2. harness 的判断 vs 人工标签（annotate.csv）：
       flagged 的论断里，人判 P/N 的比例  → 拦得准不准（精确率）
       人判 P/N 的论断里，被 flagged 的比例 → 拦得全不全（召回率）
  3. eval/harness_review.csv：所有 warn / block 逐条列出，用来找误报、改规则
"""

import csv
import json
import re
from collections import Counter
from pathlib import Path

from harness import HarnessConfig, run_harness
from snapshot import load_snapshot

EVAL_DIR = Path(__file__).parent / "eval"
RESULTS_DIR = EVAL_DIR / "results"
REVIEW_CSV = EVAL_DIR / "harness_review.csv"
ANNOTATE = EVAL_DIR / "annotate.csv"
CALIB = {"standard", "long", "odd", "science"}

NORM = re.compile(r"\s+")


def norm(s: str) -> str:
    return NORM.sub("", s or "")


def main():
    config = HarnessConfig()
    reports, missing = [], []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        pid = rec["paper"]["arxiv_id"]
        paper = load_snapshot(pid)
        if paper is None:
            missing.append(pid)
            continue
        reports.append((rec.get("category", ""), run_harness(paper, rec["result"], config)))

    if missing:
        print(f"没有快照、跳过 {len(missing)} 篇：{missing[:5]}{'…' if len(missing) > 5 else ''}")
        print("先跑 python snapshot.py\n")
    if not reports:
        return

    # ── 1. 状态分布 ──
    total = Counter()
    print(f"{'论文':12} {'类别':13} " + " ".join(f"{s:>9}" for s in
          ["verified", "flagged", "inference", "uncited", "not_ment"]) + "   补引")
    for cat, rep in reports:
        c = rep.counts(); total.update(c)
        n_auto = sum(1 for r in rep.results if r.auto_quotes)
        print(f"{rep.arxiv_id:12} {cat:13} " + " ".join(f"{c.get(s, 0):>9}" for s in
              ["verified", "flagged", "inference", "uncited", "not_mentioned"]) + f"   {n_auto:>4}")
    n = sum(total.values())
    cited = total["verified"] + total["flagged"]
    print(f"\n总计 {n} 句；带引用 {cited} 句，其中 verified {total['verified']}（{total['verified']/cited*100:.1f}%），"
          f"flagged {total['flagged']}（{total['flagged']/cited*100:.1f}%）")
    all_res = [r for _, rep in reports for r in rep.results]
    print(f"确定性补引 {sum(1 for r in all_res if r.auto_quotes)} 句；"
          f"block 中「引错了段」{sum(1 for r in all_res for i in r.issues if i.severity == 'block' and i.found_in)} 个、"
          f"「全文都没有」{sum(1 for r in all_res for i in r.issues if i.severity == 'block' and not i.found_in)} 个")

    # ── 2. 对照人工标签 ──
    if ANNOTATE.exists():
        rows = [r for r in csv.DictReader(ANNOTATE.open(encoding="utf-8-sig"))
                if r["category"] in CALIB and r["label"].strip().upper() in ("Y", "P", "N")]
        by_paper = {}
        for cat, rep in reports:
            by_paper[rep.arxiv_id] = rep
        pairs = []                                  # (人工标签, harness 状态)
        for r in rows:
            rep = by_paper.get(r["paper_id"])
            if not rep:
                continue
            target = norm(r["claim"])
            hit = [cr for cr in rep.results if cr.claim.field == r["field"] and r["evidence_id"] in cr.claim.ids
                   and (target in norm(cr.claim.raw) or norm(cr.claim.raw) in target)]
            if hit:
                pairs.append((r["label"].strip().upper(), hit[0].status))
        if pairs:
            print(f"\n── 对照人工标签（校准集里匹配上 {len(pairs)} 行）──")
            print("            verified  flagged")
            for lab in ["Y", "P", "N"]:
                print(f"    人工 {lab}   {sum(1 for h, s in pairs if h == lab and s == 'verified'):>7}  "
                      f"{sum(1 for h, s in pairs if h == lab and s == 'flagged'):>7}")
            flagged = [h for h, s in pairs if s == "flagged"]
            bad = [s for h, s in pairs if h in ("P", "N")]
            if flagged:
                print(f"  精确率：flagged 里人判 P/N 的占 {sum(h in 'PN' for h in flagged)/len(flagged)*100:.0f}%")
            if bad:
                print(f"  召回率：人判 P/N 的里被 flagged 的占 {sum(s == 'flagged' for s in bad)/len(bad)*100:.0f}%")
            print("  注：数字检查只管带数字的论断，召回率低是预期内的——别的错法要靠后面几层")

    # ── 3. 逐条复查表 ──
    with REVIEW_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["paper_id", "field", "idx", "severity", "token", "detail", "claim", "suggest", "误报?"])
        for _, rep in reports:
            for r in rep.results:
                for i in r.issues:
                    w.writerow([rep.arxiv_id, r.claim.field, r.claim.idx, i.severity, i.token,
                                i.detail, r.claim.raw, i.suggest, ""])
    print(f"\n逐条复查 → {REVIEW_CSV.name}（「误报?」列填 y，攒够了一起改规则）")


if __name__ == "__main__":
    main()
