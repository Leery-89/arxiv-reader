"""harness 端到端冒烟测试：拆句 → 检查 → 定状态。

    python tests/test_harness.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import HarnessConfig, run_harness
from models import Paper, Paragraph, Section

PAPER = Paper(
    arxiv_id="0000.00000", title="t", abstract="We propose X.",
    sections=[Section(1, "Results", paragraphs=[
        Paragraph("S3.p1", "Our model reaches 91.2% accuracy. The baseline gets 85.0%."),
        Paragraph("S3.p2", "Training takes 3 days on 8 GPUs."),
    ])],
)
RESULT = {
    "findings": {
        "text": ("模型准确率达到 91.2% [S3.p1]。"            # quote 里有 → verified
                 "基线为 85.0% [S3.p1]。"                   # 段落里有、quote 没有 → warn → 补引 → verified
                 "训练用了 8 块 GPU、耗时 3 天 [S3.p1]。"     # 数字在 S3.p2，引成了 S3.p1 → flagged
                 "作者认为该方法可推广到其他领域 [推断]。"      # inference
                 "原文未明确提及计算成本。"),                  # not_mentioned
        "evidence": [{"id": "S3.p1", "quote": "Our model reaches 91.2% accuracy."}],
    },
}


def test_statuses():
    rep = run_harness(PAPER, RESULT, HarnessConfig())
    got = [r.status for r in rep.results]
    assert got == ["verified", "verified", "flagged", "inference", "not_mentioned"], got


def test_auto_requote():
    rep = run_harness(PAPER, RESULT, HarnessConfig())
    assert rep.results[1].auto_quotes == {"S3.p1": ["The baseline gets 85.0%."]}


def test_misattribution_points_to_right_para():
    rep = run_harness(PAPER, RESULT, HarnessConfig())
    issues = rep.results[2].issues
    assert {i.token for i in issues} == {"8", "3"}
    assert all(i.found_in == ["S3.p2"] for i in issues)


def test_semicolon_clause_merges_forward():
    from harness.claims import _sentences
    t = "在 CoQA 上零样本 81.5 F1；少样本 85.0 F1 [S3.p1]。另一句 [S3.p2]。"
    assert _sentences(t) == ["在 CoQA 上零样本 81.5 F1；少样本 85.0 F1 [S3.p1]。", "另一句 [S3.p2]。"]
    # 前半句自己带 ID 时照常切开
    t2 = "A 成立 [S3.p1]；B 成立 [S3.p2]。"
    assert _sentences(t2) == ["A 成立 [S3.p1]；", "B 成立 [S3.p2]。"]


def test_config_off():
    rep = run_harness(PAPER, RESULT, HarnessConfig(numbers=False))
    assert [r.status for r in rep.results][:3] == ["verified"] * 3


def test_no_requote_means_flagged():
    rep = run_harness(PAPER, RESULT, HarnessConfig(auto_requote=False))
    assert rep.results[1].status == "flagged"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except Exception as e:
            failed += 1; print(f"  FAIL {name}: {type(e).__name__} {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
