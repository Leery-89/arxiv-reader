"""prompt 版本对比：全量评，不抽样（D19）。

    cd backend
    python eval_versions.py --tags v2 v3 --dry-run                 # 只数判断单元，不调模型，估成本
    python eval_versions.py --tags v2 v3 --judges gpt-6.1-sol      # 一个评审员
    python eval_versions.py --tags v2 v3 --judges deepseek-chat gpt-6.1-sol claude-sonnet-5.5   # 三家投票

做法：
  1. 每个版本的每句论断（harness 拆句），按"一句论断 + 一个段落 ID 下的全部 quote"拆成判断单元
     ——和 D15 校准评审员时的设置完全一样，所以评审员的 kappa 0.614 在这里成立。
  2. 评审员判每个单元 Y / P / N。多个评审员时取多数票；三方各不相同时用第一个评审员的判断。
  3. 论断级结果由 ID 级汇总：全部 Y → Y；全部 N → N；其他 → P。
  4. [推断] 不进评审，单独统计。引了 ID 但没给任何 quote 的单元直接记 N（无证据）。
  5. abstract_only 不评（D14：只有一段可引，支撑率没有意义）。

注意（报告时要写明）：评审员对 N 偏宽松（D15：人工 9 条 N 只抓到 5 条），绝对的 N 比例偏低；
但这个偏差对两个版本相同，前后对比仍然公平。
"""

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import judge
from harness import HarnessConfig, run_harness
from harness.claims import split_claims
from snapshot import load_snapshot

EVAL_DIR = Path(__file__).parent / "eval"
SKIP = {"abstract_only"}
UNIT_COLS = ["paper_id", "category", "field", "idx", "claim_key", "evidence_id",
             "quote", "claim", "label", "notes", "no_quote"]


def results_dir(tag: str) -> Path:
    d = EVAL_DIR / f"results_{tag}"
    if not d.exists() and tag == "v2":
        d = EVAL_DIR / "results"          # v2 是最早的那批，存在原始目录
    return d


def build_units(tag: str, merge_ids: bool = False):
    """返回 (units, claim_stats, machine)。

    merge_ids（D20）：引了多个 ID 的论断不再按 ID 拆开判，而是把所有 quote 合成一个单元，
    整体判整句。按 ID 拆开时，每条 quote 只撑住综合论断的一部分，几乎必然判 P——
    v3 里多 ID 论断只有 7% 判 Y，单 ID 是 87%。拆开判等于惩罚"综合多段"。"""
    rdir = results_dir(tag)
    units, stats, machine = [], Counter(), Counter()
    infer_by = Counter()
    for path in sorted(rdir.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        cat = rec.get("category", "")
        pid = rec["paper"]["arxiv_id"]
        paper = load_snapshot(pid)
        if paper is None:
            stats["无快照跳过"] += 1
            continue
        meta = rec["result"].get("_meta", {})
        machine["papers"] += 1
        machine["prompt_tokens"] += meta.get("prompt_tokens", 0)
        machine["elapsed_ms"] += int(meta.get("elapsed_s", 0) * 1000)
        machine["quote_hits"] += rec["verify"]["quote_hits"]
        machine["quote_total"] += rec["verify"]["quote_total"]
        rep = run_harness(paper, rec["result"], HarnessConfig())
        for r in rep.results:
            if r.status in ("verified", "flagged"):
                machine["cited"] += 1
                machine["flagged"] += r.status == "flagged"
        if cat in SKIP:
            continue
        for c in split_claims(paper, rec["result"]):
            if c.is_inference:
                stats["推断"] += 1
                infer_by[(cat, c.field)] += 1
                continue
            if not c.ids:
                stats["未提及" if "原文未明确提及" in c.text else "无引用"] += 1
                continue
            stats["带引用论断"] += 1
            key = f"{pid}|{c.field}|{c.idx}"
            if merge_ids and len(c.ids) > 1:
                qs = [f"[{eid}] {q}" for eid in c.ids for q in c.quotes.get(eid, []) if q.strip()]
                units.append({"paper_id": pid, "category": cat, "field": c.field, "idx": c.idx,
                              "claim_key": key, "evidence_id": "+".join(c.ids), "quote": "\n".join(qs),
                              "claim": c.raw, "label": "", "notes": "", "no_quote": "" if qs else "1"})
                continue
            for eid in c.ids:
                qs = [q for q in c.quotes.get(eid, []) if q.strip()]
                units.append({"paper_id": pid, "category": cat, "field": c.field, "idx": c.idx,
                              "claim_key": key, "evidence_id": eid, "quote": qs[0] if qs else "",
                              "claim": c.raw, "label": "", "notes": "", "no_quote": "" if qs else "1"})
    return units, stats, machine, infer_by


def judge_units(tag: str, units: list[dict], models: list[str], force: bool, workers: int,
                suffix: str = "") -> list[str]:
    """返回每个单元的最终标签（与 units 同序）。"""
    judge.RESULTS_DIR = results_dir(tag)          # 兄弟 quote 从这个版本的结果里取
    judge._results_cache.clear()
    path = EVAL_DIR / f"units_{tag}{suffix}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=UNIT_COLS)
        w.writeheader()
        w.writerows([u for u in units if not u["no_quote"]])

    votes = defaultdict(list)                     # (claim_key, evidence_id) → [label...]
    for m in models:
        print(f"\n[{tag}] 评审员 {m}")
        for r in judge.run_judge(m, path, force, workers):
            if r["judge_label"] in ("Y", "P", "N"):
                votes[(r["claim_key"], r["evidence_id"])].append(r["judge_label"])

    final = []
    for u in units:
        if u["no_quote"]:
            final.append("N")
            continue
        v = votes.get((u["claim_key"], u["evidence_id"]), [])
        if not v:
            final.append("")
            continue
        top, n = Counter(v).most_common(1)[0]
        final.append(top if n > 1 or len(v) == 1 else v[0])
    return final


def claim_level(units, labels):
    """返回 [(类别, 论断标签, 是否多 ID)]。"""
    by = defaultdict(list)
    multi = defaultdict(bool)
    for u, l in zip(units, labels):
        multi[u["claim_key"]] |= "+" in u["evidence_id"]
        if l:
            by[u["claim_key"]].append((u["category"], l))
    out = []
    for key, ls in by.items():
        labs = {l for _, l in ls}
        out.append((ls[0][0], "Y" if labs == {"Y"} else "N" if labs == {"N"} else "P",
                    multi[key] or len(ls) > 1))
    return out


def pct(c: Counter, k: str) -> str:
    n = sum(c.values())
    return f"{c[k] / n * 100:5.1f}%" if n else "   - "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", nargs="+", default=["v2", "v3"])
    ap.add_argument("--judges", nargs="+", default=["deepseek-chat"])
    ap.add_argument("--dry-run", action="store_true", help="只数判断单元，不调模型")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--merge-ids", action="store_true",
                    help="多 ID 论断合成一个单元整体判（D20）；结果存 units_<tag>_m*.csv，不覆盖按 ID 判的")
    args = ap.parse_args()

    report = {}
    for tag in args.tags:
        if not results_dir(tag).exists():
            print(f"[{tag}] 没有结果目录 {results_dir(tag).name}/，先跑 PROMPT={tag} python eval_run.py --tag {tag}")
            continue
        sfx = "_m" if args.merge_ids else ""
        units, stats, machine, infer_by = build_units(tag, args.merge_ids)
        nq = sum(1 for u in units if u["no_quote"])
        print(f"[{tag}] 带引用论断 {stats['带引用论断']} 句 → 判断单元 {len(units)} 个"
              f"（其中引了 ID 却无 quote {nq} 个，直接记 N）；推断 {stats['推断']} 句")
        if args.dry_run:
            print(f"      每个评审员约 {len(units) - nq} 次调用（缓存命中的不再计费）")
            continue
        labels = judge_units(tag, units, args.judges, args.force, args.workers, sfx)
        cl = claim_level(units, labels)

        # 每个单元的最终结果落盘，方便抽查
        with (EVAL_DIR / f"units_{tag}{sfx}_judged.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=UNIT_COLS + ["final"])
            w.writeheader()
            for u, l in zip(units, labels):
                w.writerow({**u, "final": l})

        report[tag] = {"stats": stats, "machine": machine, "infer_by": infer_by,
                       "id": Counter(l for l in labels if l),
                       "claim": Counter(l for _, l, _ in cl),
                       "single": Counter(l for _, l, m in cl if not m),
                       "multi": Counter(l for _, l, m in cl if m),
                       "claim_by_cat": {cat: Counter(l for c, l, _ in cl if c == cat)
                                        for cat in sorted({c for c, _, _ in cl})}}

    if not report:
        return
    tags = list(report)
    print("\n══════ 版本对比 ══════（评审员：" + " / ".join(args.judges) + "）")
    print(f"{'':26}" + "".join(f"{t:>12}" for t in tags))

    def row(name, fn):
        print(f"{name:26}" + "".join(f"{fn(report[t]):>12}" for t in tags))

    row("带引用论断（句）", lambda r: str(r["stats"]["带引用论断"]))
    row("论断级 Y 完全支撑", lambda r: pct(r["claim"], "Y"))
    row("论断级 P 部分支撑", lambda r: pct(r["claim"], "P"))
    row("论断级 N 不支撑", lambda r: pct(r["claim"], "N"))
    row("多 ID 论断占比", lambda r: f"{sum(r['multi'].values()) / max(1, sum(r['claim'].values())) * 100:5.1f}%")
    row("  单 ID 论断 Y", lambda r: pct(r["single"], "Y"))
    row("  多 ID 论断 Y", lambda r: pct(r["multi"], "Y"))
    row("ID 级 Y", lambda r: pct(r["id"], "Y"))
    row("ID 级 N", lambda r: pct(r["id"], "N"))
    row("推断句数", lambda r: str(r["stats"]["推断"]))
    row("\"原文未明确提及\"句数", lambda r: str(r["stats"]["未提及"]))
    row("平均输入 token / 篇", lambda r: f"{r['machine']['prompt_tokens'] / max(1, r['machine']['papers']):.0f}")
    row("平均耗时 / 篇（秒）", lambda r: f"{r['machine']['elapsed_ms'] / 1000 / max(1, r['machine']['papers']):.1f}")
    row("quote 命中率", lambda r: f"{r['machine']['quote_hits'] / max(1, r['machine']['quote_total']) * 100:5.1f}%")
    row("数字检查拦截率", lambda r: f"{r['machine']['flagged'] / max(1, r['machine']['cited']) * 100:5.1f}%")

    print("\n论断级 Y / N（分类别）")
    cats = sorted({c for t in tags for c in report[t]["claim_by_cat"]})
    for cat in cats:
        print(f"  {cat:12}" + "".join(
            f"   {t}: Y {pct(report[t]['claim_by_cat'].get(cat, Counter()), 'Y')} N {pct(report[t]['claim_by_cat'].get(cat, Counter()), 'N')}"
            for t in tags))

    for t in tags:
        if report[t]["infer_by"]:
            print(f"\n[{t}] 推断分布：" + "，".join(f"{c}/{f} {n}" for (c, f), n in report[t]["infer_by"].most_common()))
    print("\n注：评审员对 N 偏宽松（D15），绝对 N 比例偏低；该偏差对各版本相同，前后对比公平。")
    print("   每个单元的结果见 eval/units_<tag>_judged.csv，可抽查。")


if __name__ == "__main__":
    main()
