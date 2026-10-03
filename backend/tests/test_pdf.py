"""pdf.py 的版面规则：用 PyMuPDF 现场生成一个小 PDF，不依赖网络。"""

import pymupdf

from pdf import parse_pdf

LOREM = ("We study a simple problem and report results on two benchmarks. "
         "The method improves accuracy by 3.2 points over the baseline. ") * 3


def make_pdf() -> bytes:
    doc = pymupdf.open()
    for pno in range(1, 4):
        page = doc.new_page()
        y = 72

        def put(text, size=10, bold=False):
            nonlocal y
            rect = pymupdf.Rect(72, y, 540, y + size * 6)
            page.insert_textbox(rect, text, fontsize=size, fontname="hebo" if bold else "helv")
            y += size * 6 + 6

        if pno == 1:
            put("A Very Large Paper Title", size=18, bold=True)
            put("Alice Author, Bob Author", size=11)
            put("Abstract", size=11, bold=True)
            put("This abstract is in the PDF but should be skipped.", size=9)
            put("1 Introduction", size=12, bold=True)
            put(LOREM)
            put("Figure 1: An overview of the pipeline.", size=9)
        elif pno == 2:
            put("2 Method", size=12, bold=True)
            put(LOREM)
            put("2.1 Encoder", size=11, bold=True)
            put(LOREM)
        else:
            put("References", size=12, bold=True)
            put("[1] A. Author. Some paper. 2020.", size=9)
            put("Appendix A Extra Results", size=12, bold=True)
            put(LOREM)
        page.insert_text((300, 770), str(pno), fontsize=9)                  # 页码
        page.insert_text((72, 30), "Preprint. Under review.", fontsize=8)   # 页眉
    return doc.tobytes()


def test_sections_and_ids():
    p = parse_pdf(make_pdf(), "0000.00000", "T", "abs")
    titles = [(s.title, s.kind, s.level) for s in p.sections]
    assert titles == [("1 Introduction", "body", 1), ("2 Method", "body", 1),
                      ("2.1 Encoder", "body", 2), ("Appendix A Extra Results", "appendix", 1)]
    intro = p.sections[0]
    assert intro.paragraphs[0].id == "pg1.b1"
    assert intro.paragraphs[1].kind == "caption"
    assert p.source == "pdf"


def test_plain_smallcaps_headings():
    """ICLR / AASTeX / IEEE：标题不加粗、字号不大于正文，只靠"编号 + 单独一行"认（ViT 实测）。"""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for text, size in [("ABSTRACT", 9.6), ("Abstract text that is skipped.", 10),
                       ("1 INTRODUCTION", 9.6), (LOREM, 10), ("2.2. pre-processing", 10), (LOREM, 10)]:
        page.insert_textbox(pymupdf.Rect(72, y, 540, y + 70), text, fontsize=size, fontname="helv")
        y += 76
    p = parse_pdf(doc.tobytes(), "0000.00000", "T", "abs")
    assert [s.title for s in p.sections] == ["1 INTRODUCTION", "2.2. pre-processing"]


def test_no_headings_falls_back_to_one_section():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(72, 72, 540, 300), LOREM, fontsize=10, fontname="helv")
    p = parse_pdf(doc.tobytes(), "0000.00000", "T", "abs")
    assert [s.title for s in p.sections] == ["正文"] and p.sections[0].paragraphs


def test_front_matter_header_and_refs_dropped():
    p = parse_pdf(make_pdf(), "0000.00000", "T", "abs")
    body = " ".join(g.text for s in p.sections for g in s.paragraphs)
    assert "should be skipped" not in body          # PDF 里的摘要块
    assert "Alice Author" not in body
    assert "Preprint" not in body                   # 页眉
    assert "Some paper" not in body and any("Some paper" in r for r in p.references)


def test_headingless_body_after_abstract():
    """Science 预印本：Abstract 之后直接是正文、没有章节标题，一路到 References（AlphaZero 实测，D25）。
    参考文献里 "34. G. Tesauro, …" 这种条目不能被当成编号标题。"""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for text, size, bold in [("A Big Title", 18, True), ("Abstract", 11, True),
                             ("Short abstract that is skipped here.", 9, False),
                             (LOREM, 10, False), (LOREM, 10, False),
                             ("References and Notes", 12, True),
                             ("34. G. Tesauro, Artificial Intelligence 134, 181 (2002).", 10, True)]:
        page.insert_textbox(pymupdf.Rect(72, y, 540, y + 80), text, fontsize=size,
                            fontname="hebo" if bold else "helv")
        y += 86
    p = parse_pdf(doc.tobytes(), "0000.00000", "T", "Short abstract that is skipped here.")
    assert [s.title for s in p.sections] == ["正文"]
    assert len(p.sections[0].paragraphs) == 2
    assert any("Tesauro" in r for r in p.references)
