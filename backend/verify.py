"""校验模型输出的证据：两个可以自动算的指标。

1. quote 命中率：evidence 里的每条 quote，是不是真的在它声称的段落里
2. 内嵌 ID 合规率：text 里出现的每个 [S3.p1]，是不是在论文里存在、
   并且出现在该字段的 evidence 里（prompt v2 的要求）

这两个指标只回答「引用存在吗」，不回答「引用支撑论断吗」——
后者要人来判断，是 D14 评测集的事。见 DECISIONS.md「可溯源率的边界」。
"""

import re

from models import Paper

FIELDS = ["research_question", "method", "experiments", "findings", "limitations"]
INLINE_ID = re.compile(r"\[([A-Za-z0-9.]+)\]")


def _norm(s: str) -> str:
    """比对前压平空白、转小写，避免空格换行差异误判。"""
    return re.sub(r"\s+", " ", s or "").strip().lower()


def check(paper: Paper, result: dict) -> dict:
    para_by_id = {p.id: p.text for s in paper.sections for p in s.paragraphs}

    quote_hits = quote_total = 0
    inline_hits = inline_total = 0
    issues: list[dict] = []

    for field in FIELDS:
        block = result.get(field) or {}
        evidence = block.get("evidence") or []
        evidence_ids = {ev.get("id") for ev in evidence}

        # ── 1. quote 命中 ──
        for ev in evidence:
            quote_total += 1
            pid, quote = ev.get("id", ""), ev.get("quote", "")
            para = para_by_id.get(pid)
            if para is None:
                issues.append({"field": field, "id": pid, "reason": "段落 ID 不存在"})
            elif _norm(quote) in _norm(para):
                quote_hits += 1
            else:
                issues.append({"field": field, "id": pid, "reason": "quote 不在该段落里",
                               "quote": quote[:80]})

        # ── 2. 内嵌 ID 合规 ──
        for pid in INLINE_ID.findall(block.get("text", "")):
            inline_total += 1
            if pid not in para_by_id:
                issues.append({"field": field, "id": pid, "reason": "内嵌 ID 不存在"})
            elif pid not in evidence_ids:
                issues.append({"field": field, "id": pid, "reason": "内嵌 ID 未出现在 evidence 里"})
            else:
                inline_hits += 1

    return {
        "quote_hits": quote_hits,
        "quote_total": quote_total,
        "quote_rate": round(quote_hits / quote_total, 3) if quote_total else None,
        "inline_hits": inline_hits,
        "inline_total": inline_total,
        "inline_rate": round(inline_hits / inline_total, 3) if inline_total else None,
        "issues": issues,
    }
