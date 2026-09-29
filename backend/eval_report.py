"""D14 评测集：汇总报告。

    cd backend
    python eval_report.py            # 读 eval/summary.csv + eval/annotate.csv，打印表格
    python eval_report.py --md       # 输出 Markdown 表格，直接贴 README

两层指标：
  机器指标（summary.csv）  quote 命中率、内嵌 ID 合规率、耗时、token —— 回答「引用存在吗」
  人工指标（annotate.csv） label 列：Y 支撑 / N 不支撑 / P 部分 —— 回答「引用支撑论断吗」

人工指标只在你标了 label 之后才有；没标就只打机器那一半。
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean

EVAL_DIR = Path(__file__).parent / "eval"
SUMMARY_CSV = EVAL_DIR / "summary.csv"
ANNOTATE_CSV = EVAL_DIR / "annotate.csv"


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def _pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def load_summary():
    with SUMMARY_CSV.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f)]


def load_annot():
    if not ANNOTATE_CSV.exists():
        return []
    with ANNOTATE_CSV.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f)]


def machine_metrics(rows):
    """按类别聚合。命中率用「总命中 / 总条数」而不是各篇平均——
    否则一篇只有 2 条 evidence 的论文权重和 30 条的一样。"""
    groups = defaultdict(list)
    for r in rows:
        groups[r["category"]].append(r)
    groups["ALL"] = rows

    table = []
    for cat, rs in groups.items():
        ok = [r for r in rs if r["status"] == "ok"]
        qh = sum(int(r["quote_hits"]) for r in ok)
        qt = sum(int(r["quote_total"]) for r in ok)
        ih = sum(int(r["inline_hits"]) for r in ok)
        it = sum(int(r["inline_total"]) for r in ok)
        table.append({
            "category": cat,
            "n": len(rs),
            "ok": len(ok),
            "abstract_only": sum(1 for r in ok if r["source"] == "abstract_only"),
            "truncated": sum(1 for r in ok if r["truncated"] == "True"),
            "quote_rate": qh / qt if qt else None,
            "quote_n": qt,
            "quote_min": min((_f(r["quote_rate"]) for r in ok if _f(r["quote_rate"]) is not None), default=None),
            "inline_rate": ih / it if it else None,
            "inline_n": it,
            "not_mentioned": _avg([_f(r["not_mentioned_fields"]) for r in ok]),
            "elapsed": _avg([_f(r["elapsed_s"]) for r in ok]),
            "tokens_in": _avg([_f(r["prompt_tokens"]) for r in ok]),
            "tokens_out": _avg([_f(r["completion_tokens"]) for r in ok]),
        })
    return table


def human_metrics(annot):
    groups = defaultdict(list)
    for r in annot:
        groups[r["category"]].append(r)
    groups["ALL"] = annot

    table = []
    for cat, rs in groups.items():
        labeled = [r for r in rs if r["label"].strip().upper() in ("Y", "N", "P")]
        y = sum(1 for r in labeled if r["label"].strip().upper() == "Y")
        p = sum(1 for r in labeled if r["label"].strip().upper() == "P")
        table.append({
            "category": cat,
            "sampled": len(rs),
            "labeled": len(labeled),
            "support_rate": y / len(labeled) if labeled else None,          # 严格：只算 Y
            "support_rate_loose": (y + p) / len(labeled) if labeled else None,  # 宽松：Y+P
        })
    return table


def print_table(rows, cols, md=False):
    """cols: [(key, header, fmt)]"""
    head = [h for _, h, _ in cols]
    body = [[fmt(r.get(k)) for k, _, fmt in cols] for r in rows]
    widths = [max(len(str(x)) for x in col) for col in zip(head, *body)]
    if md:
        print("| " + " | ".join(head) + " |")
        print("|" + "|".join("---" for _ in head) + "|")
        for b in body:
            print("| " + " | ".join(str(x) for x in b) + " |")
    else:
        print("  ".join(h.ljust(w) for h, w in zip(head, widths)))
        for b in body:
            print("  ".join(str(x).ljust(w) for x, w in zip(b, widths)))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="输出 Markdown 表格")
    args = ap.parse_args()

    rows = load_summary()
    annot = load_annot()
    s = str
    num = lambda d: (lambda x: "-" if x is None else f"{x:.{d}f}")

    print(f"评测集：{len(rows)} 篇，成功 {sum(1 for r in rows if r['status'] == 'ok')} 篇\n")

    print("── 机器指标（引用存在吗）──")
    print_table(machine_metrics(rows), [
        ("category", "类别", s), ("n", "篇", s), ("abstract_only", "无HTML", s), ("truncated", "截断", s),
        ("quote_rate", "quote命中", _pct), ("quote_n", "条", s), ("quote_min", "最差篇", _pct),
        ("inline_rate", "内嵌ID合规", _pct), ("inline_n", "条", s),
        ("not_mentioned", "未提及/5", num(2)),
        ("elapsed", "耗时s", num(1)), ("tokens_in", "in", num(0)), ("tokens_out", "out", num(0)),
    ], md=args.md)

    if annot:
        print("── 人工指标（引用支撑论断吗）──")
        print_table(human_metrics(annot), [
            ("category", "类别", s), ("sampled", "抽样", s), ("labeled", "已标", s),
            ("support_rate", "支撑率(Y)", _pct), ("support_rate_loose", "宽松(Y+P)", _pct),
        ], md=args.md)
        print("  注：abstract_only 不做人工标注——只有一段可引，quote 必然是摘要的截句，"
              "支撑率反映的是压缩比例而不是引用能力。这一类看机器指标里的「未提及」列：输入不够时模型会不会编。\n")

    errs = [r for r in rows if r["status"] == "error"]
    if errs:
        print("── 失败 ──")
        for r in errs:
            print(f"  {r['arxiv_id']}  {r['category']}  {r['error']}")


if __name__ == "__main__":
    main()
