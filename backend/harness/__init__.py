"""Harness：在模型外面拦幻觉。设计见 DECISIONS.md D17。

    from harness import run_harness, HarnessConfig
    report = run_harness(paper, result, HarnessConfig())
"""

from models import Paper

from .checks.numbers import check_numbers
from .claims import NOT_MENTIONED, split_claims
from .models import Claim, ClaimResult, HarnessConfig, HarnessReport, Issue


def decide(claim: Claim, issues: list[Issue], config: HarnessConfig) -> tuple[str, dict]:
    """检查结果 → 状态。返回 (status, auto_quotes)。

        没问题                     → verified
        只有 warn 且开了补引        → verified（把段落里那句补成 quote）
        有 block                   → flagged（等评审员；judge=None 时停在这）
    """
    blocks = [i for i in issues if i.severity == "block"]
    warns = [i for i in issues if i.severity == "warn"]
    auto: dict[str, list[str]] = {}
    if warns and config.auto_requote:
        for w in warns:
            pid = w.found_in[0]
            if w.suggest and w.suggest not in auto.get(pid, []):
                auto.setdefault(pid, []).append(w.suggest)
    if blocks:
        return "flagged", auto
    if warns and not config.auto_requote:
        return "flagged", auto
    return "verified", auto


def run_harness(paper: Paper, result: dict, config: HarnessConfig | None = None) -> HarnessReport:
    config = config or HarnessConfig()
    out = []
    for claim in split_claims(paper, result):
        if claim.is_inference:
            out.append(ClaimResult(claim, "inference"))
            continue
        if not claim.ids:
            # "原文未明确提及"这类句子本来就不该有引用，不算违规
            status = "not_mentioned" if NOT_MENTIONED in claim.text else "uncited"
            out.append(ClaimResult(claim, status))
            continue

        issues: list[Issue] = []
        if config.numbers:
            issues += check_numbers(claim)
        # entities / hedges：待实现

        status, auto = decide(claim, issues, config)
        out.append(ClaimResult(claim, status, issues, auto_quotes=auto))
    return HarnessReport(paper.arxiv_id, config, out)
