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


# LaTeXML 的表格结构（照 arxiv.org/html/1706.03762 的 Table 2 简化）
TABLE_HTML = """
<article><h1 class="ltx_title_document">T</h1>
<section class="ltx_section" id="S6"><h2>6 Results</h2>
<div class="ltx_para" id="S6.p1"><p>See the table.</p></div>
<figure class="ltx_table" id="S6.T2">
  <figcaption>Table 2: BLEU scores.</figcaption>
  <table class="ltx_tabular"><tbody>
    <tr class="ltx_tr"><th class="ltx_th">Model</th><th class="ltx_th">EN-DE</th></tr>
    <tr class="ltx_tr"><td class="ltx_td">Transformer (big)</td><td class="ltx_td"><math alttext="28.4">x</math></td></tr>
    <tr class="ltx_tr"><td class="ltx_td">nested <table><tr><td>inner</td></tr></table></td><td class="ltx_td">1.0</td></tr>
  </tbody></table>
</figure>
</section></article>"""


def test_table_rows_kept_with_caption():
    from fetcher import parse_html
    paras = {p.id: p for s in parse_html(TABLE_HTML, "x").sections for p in s.paragraphs}
    t = paras["S6.T2"]
    assert t.kind == "caption"
    lines = t.text.split("\n")
    assert lines[0] == "Table 2: BLEU scores."
    assert lines[1] == "| Model | EN-DE |"
    assert lines[2] == "| Transformer (big) | $28.4$ |"
    assert lines[3] == "| nested inner | 1.0 |"          # 嵌套表格随外层单元格取文字，不另起行
    assert len(lines) == 4


def test_big_table_truncated():
    import fetcher
    rows = "".join(f"<tr><td>row{i}</td><td>{i}</td></tr>" for i in range(100))
    html = f'<figure class="ltx_table"><table>{rows}</table></figure>'
    text = fetcher.table_text(BeautifulSoup(html, "html.parser").figure)
    assert text.count("\n") == fetcher.TABLE_MAX_ROWS            # 40 行 + 一行截断说明
    assert text.endswith("（表格共 100 行，只保留前 40 行）")


def test_table_spans_aligned():
    """colspan/rowspan 展开后每行列数对齐；整列空的分隔列删掉；双重转义的 &amp;amp; 解开（D22 修正）"""
    import fetcher
    html = """<figure class="ltx_table"><table>
      <tr><th rowspan="2">Model</th><th colspan="2">BLEU</th><td></td><th colspan="2">Cost</th></tr>
      <tr><th>EN-DE</th><th>EN-FR</th><td></td><th>EN-DE</th><th>EN-FR</th></tr>
      <tr><td>A &amp;amp; B</td><td>28.4</td><td>41.8</td><td></td><td>1</td><td>2</td></tr>
    </table></figure>"""
    lines = fetcher.table_text(BeautifulSoup(html, "html.parser").figure).split("\n")
    assert lines == [
        "| Model | BLEU |  | Cost |  |",
        "|  | EN-DE | EN-FR | EN-DE | EN-FR |",
        "| A & B | 28.4 | 41.8 | 1 | 2 |",
    ]


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
