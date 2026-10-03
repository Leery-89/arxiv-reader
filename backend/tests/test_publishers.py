"""publishers.py 的小测试：照 ACS 全文页的结构手写的 HTML，不联网。

    python tests/test_publishers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publishers import parse_page

LONG = "We grow ReS2/PdSe2 heterojunctions and measure a switchable photoresponse of 0.87 A/W. " * 12

ACS_HTML = f"""<html><head>
<meta name="citation_title" content="High Performance Switchable Dual-Mode Photoresponse">
<meta name="dc.identifier" content="doi:10.1021/acsnano.6c05081">
</head><body>
<header>Welcome, Jane Doe (Example University) <nav>My account</nav></header>
<div class="article_content">
  <div class="article_abstract"><h2>Abstract</h2><p>Two-dimensional heterojunctions are promising.</p></div>
  <div class="NLM_sec" id="sec1"><h2>Introduction</h2>
    <div class="NLM_p" id="sec1-p1">{LONG}<p>nested paragraph inside NLM_p</p></div>
    <div class="NLM_p">{LONG} with <math alttext="E_{{g}}">x</math> gap.</div>
  </div>
  <div class="NLM_sec" id="sec2"><h2>Results and Discussion</h2>
    <h3>Device Fabrication</h3>
    <div class="NLM_p" id="sec2-p1">{LONG}</div>
    <figure><img src="f1.png"><figcaption>Figure 1. Device schematic.</figcaption></figure>
  </div>
  <h2>Acknowledgments</h2><p>We thank the funding agency.</p>
  <div class="NLM_back"><h2>References</h2><p>(1) A. Author, Nature 2020.</p></div>
</div>
<footer>© ACS</footer>
</body></html>"""


def test_acs_full_text():
    p = parse_page("https://pubs.acs.org/doi/10.1021/acsnano.6c05081", ACS_HTML)
    assert p.source == "page"
    assert p.arxiv_id == "page:10.1021/acsnano.6c05081"
    assert p.title.startswith("High Performance")
    assert p.abstract == "Two-dimensional heterojunctions are promising."
    assert [(s.title, s.level) for s in p.sections] == [
        ("Introduction", 1), ("Device Fabrication", 2)]
    intro = p.sections[0].paragraphs
    assert intro[0].id == "sec1-p1" and "nested paragraph" in intro[0].text   # 套在 NLM_p 里的 p 不单独成段
    assert len(intro) == 2 and "$E_{g}$" in intro[1].text
    fab = p.sections[1].paragraphs
    assert fab[-1].kind == "caption" and fab[-1].text == "Figure 1. Device schematic."
    text = " ".join(g.text for s in p.sections for g in s.paragraphs)
    assert "Jane Doe" not in text and "funding agency" not in text and "A. Author" not in text


def test_preview_only_is_abstract_only():
    html = """<html><head><meta name="citation_title" content="T">
    <meta name="citation_abstract" content="Only the abstract is visible."></head>
    <body><article><h2>Abstract</h2><p>Only the abstract is visible.</p>
    <p>Get access to the full text.</p></article></body></html>"""
    p = parse_page("https://pubs.acs.org/doi/10.1021/x", html)
    assert p.source == "abstract_only" and p.abstract == "Only the abstract is visible."
    assert p.arxiv_id == "page:10.1021/x"


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
