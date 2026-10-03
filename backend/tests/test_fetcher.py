"""fetcher 的小测试。用 HTML 片段，不联网。

    python tests/test_fetcher.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

from fetcher import para_text


def pt(html):
    return para_text(BeautifulSoup(f"<div>{html}</div>", "html.parser").div)


def test_math_becomes_latex():
    # 公式后面会多一个空格（get_text 的行为）。已有评测的 quote 都基于这个格式，不改。
    assert pt('accuracy of <math alttext="12.47">x</math>%') == "accuracy of $12.47$ %"


def test_latexml_thousands_bug():
    # 2405.15793 原文：alttext="2true294"，应为 2,294
    assert pt('of the <math alttext="2true294">x</math> tasks') == "of the $2,294$ tasks"
    assert pt('<math alttext="1true234true567">x</math>') == "$1,234,567$"


def test_true_in_real_formula_untouched():
    assert pt('<math alttext="x=\\\\text{true}">x</math>') == "$x=\\\\text{true}$"
    assert pt('<math alttext="2true29">x</math>') == "$2true29$"     # 不是三位一组，不像千分位，不动


def test_citations_removed():
    assert pt('as shown <cite>[20]</cite>.') == "as shown."


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
