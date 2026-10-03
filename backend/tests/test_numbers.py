"""数字检查的测试。用例全部来自 D14 标注和 D17 粗筛时遇到的真实情况。

    cd backend
    python tests/test_numbers.py       # 不依赖 pytest
    pytest tests/                      # 装了 pytest 也能跑
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.checks.numbers import check_numbers, claim_numbers
from harness.models import Claim


def tokens(text):
    return [n["token"] for n in claim_numbers(text)]


def mk(text, paras, quotes=None, all_paras=None):
    ids = list(paras)
    all_paras = {**(all_paras or {}), **paras}
    return Claim(field="findings", idx=0, raw=text, text=text, ids=ids,
                 paras=paras, quotes=quotes or {k: [] for k in ids}, all_paras=all_paras)


# ── 不该被当成"要检查的数字"的 ──

def test_names_are_not_numbers():
    assert tokens("评估了 ResNet-50 和 ResNet-18") == []
    assert tokens("MACE-MP-0 机器学习势") == []
    assert tokens("与 GPT-4o 和 Llama-3 对比") == []


def test_identifier_chains():
    # D17 首轮误报：MD17-10k 是数据集名
    assert tokens("在 MD17-10k 上微调") == []
    assert tokens("Llama-3.1-8B 的结果") == []


def test_structural_refs_skipped():
    assert tokens("表 4 消融了块设计") == []
    assert tokens("如 Figure 2 所示") == []
    assert tokens("见第 3 节") == []


def test_letter_suffix():
    assert tokens("在 3D 分子生成上") == []
    assert tokens("一个 7B 模型") == ["7"]          # 7B 是数量，保留（值按 7e9 和 7 都算）


# ── 该匹配上的 ──

def test_exact_in_quote_passes():
    c = mk("熔点为 1121 K", {"S3.p2": "The experimental melting point is 1121 K."},
           quotes={"S3.p2": ["The experimental melting point is 1121 K."]})
    assert check_numbers(c) == []


def test_in_para_not_in_quote_is_warn():
    para = "We heat the crystal. The experimental melting point is 1121 K. Classical potentials underestimate it."
    c = mk("实验熔点为 1121 K", {"S3.p2": para}, quotes={"S3.p2": ["We heat the crystal."]})
    [i] = check_numbers(c)
    assert i.severity == "warn"
    assert i.suggest == "The experimental melting point is 1121 K."


def test_wan_vs_thousands():
    assert check_numbers(mk("合成了 10 万个体的传记", {"S2.p2": "a population of 100,000 individuals"},
                            quotes={"S2.p2": ["a population of 100,000 individuals"]})) == []
    assert check_numbers(mk("合成了 10 万个体的传记", {"S2.p2": "N=100K individuals"},
                            quotes={"S2.p2": ["N=100K individuals"]})) == []


def test_percent_vs_fraction():
    assert check_numbers(mk("准确率 60%", {"p": "reaching an accuracy of 0.60"},
                            quotes={"p": ["reaching an accuracy of 0.60"]})) == []


def test_word_numbers():
    q = "The encoder is composed of a stack of six identical layers."
    assert check_numbers(mk("编码器由 6 个相同层堆叠", {"p": q}, quotes={"p": [q]})) == []


def test_word_with_magnitude():
    q = "pre-trained on roughly one million hours of audio"
    assert check_numbers(mk("在约 100 万小时音频上预训练", {"p": q}, quotes={"p": [q]})) == []


def test_order_of_magnitude_notation():
    # Movie Gen 原文：pre-train the model on $\\mathcal{O}$ (1)M hours of audio
    q = "We pre-train the model on $\\mathcal{O}$ (1)M hours of audio"
    assert check_numbers(mk("在约 100 万小时音频上预训练", {"p": q}, quotes={"p": [q]})) == []


def test_rounding_to_claim_precision():
    q = "a mean absolute error of 2.47 GPa"
    assert check_numbers(mk("体模量 MAE 约 2.5 GPa", {"p": q}, quotes={"p": [q]})) == []


def test_approx_tolerance():
    q = "classical potentials underestimate the melting point by 297 K"
    assert check_numbers(mk("经典势低估熔点约 300 K", {"p": q}, quotes={"p": [q]})) == []
    # 没有"约"就不放宽
    assert check_numbers(mk("经典势低估熔点 300 K", {"p": q}, quotes={"p": [q]}))[0].severity == "block"


def test_latex_scientific():
    q = "learning rate of $10^{-4}$"
    assert check_numbers(mk("学习率为 1e-4", {"p": q}, quotes={"p": [q]})) == []


def test_ranges():
    q = "all remaining CDR regions (75-84% vs 63-76%)"
    assert check_numbers(mk("其余 CDR 区域 75-84%", {"p": q}, quotes={"p": [q]})) == []


def test_percent_precision_regression():
    # 曾经的 bug：48.6% → 0.486 按 1 位小数四舍五入成 0.5，撞上 "50%" 的 0.5
    q = "Using 50% dropout reduces this to a record 42.4%"
    [i] = check_numbers(mk("从 48.6% 降至 42.4%", {"p": q}, quotes={"p": [q]}))
    assert i.token == "48.6%"


# ── 该拦下的 ──

def test_number_cited_from_wrong_paragraph():
    c = mk("ImageNet 上从 48.6% 降至 42.4%",
           {"p11": "Using 50% dropout reduces this to a record 42.4%"},
           quotes={"p11": ["Using 50% dropout reduces this to a record 42.4%"]},
           all_paras={"p10": "The best published result is 48.6% error"})
    [i] = check_numbers(c)
    assert i.severity == "block" and i.token == "48.6%" and i.found_in == ["p10"]


def test_number_nowhere():
    [i] = check_numbers(mk("提升了 37%", {"p": "a large improvement"}, quotes={"p": ["a large improvement"]}))
    assert i.severity == "block" and "全文都没有" in i.detail


def test_no_ids_no_check():
    c = mk("提升了 37%", {})
    assert check_numbers(c) == []


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {name}: {type(e).__name__} {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
