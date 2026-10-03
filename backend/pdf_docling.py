"""PDF → Paper，用 Docling 版面模型（D23 对比用，不上线）。

和 pdf.py 的 PyMuPDF 规则版输出同一个 Paper 结构，pdf_bench.py --engine docling 直接比：
  - 章节标题、正文、图注、页眉页脚由版面模型分类，不靠字号规则
  - 独立公式（display）开 do_formula_enrichment 后由 CodeFormula 模型转成 LaTeX，写成 $...$；
    行内公式 Docling 不转，仍是 PDF 字形
  - 表格开 do_table_structure（TableFormer），按 "| a | b |" 输出，格式同 HTML 的 table_text（D22）
  - 段落 ID 同 pdf.py：pg3.b4

Docling 要下载模型（首次约 1–2 GB）、要 torch，Railway 的小机器跑不动；
这里只回答一个问题：版面模型比规则好多少、慢多少，值不值得为公式/表格单独起一个服务。

    pip install docling
    python pdf_docling.py eval/pdf_cache/1706.03762.pdf      # 解析一篇，打印章节、公式、表格
"""

import re
import sys
from collections import Counter
from io import BytesIO

from models import Paper, Paragraph, Section
from pdf import APPENDIX_RE, CAPTION_RE, REFS_RE, _level

_CONVERTERS: dict = {}


def _converter(formulas: bool, tables: bool):
    key = (formulas, tables)
    if key not in _CONVERTERS:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.do_ocr = False                       # arXiv PDF 都有文字层
        opts.do_table_structure = tables
        opts.do_formula_enrichment = formulas
        _CONVERTERS[key] = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _CONVERTERS[key]


def _table_rows(item, doc) -> list[str]:
    """TableFormer 的网格里合并单元格会在每个被占的位置重复出现，只留左上角那格（同 fetcher.table_grid）。"""
    grid = item.data.grid if item.data else []
    rows = []
    for i, row in enumerate(grid):
        cells = []
        for j, c in enumerate(row):
            first = c.start_row_offset_idx == i and c.start_col_offset_idx == j
            cells.append(re.sub(r"\s+", " ", c.text).strip() if first else "")
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return rows


def parse_pdf_docling(data: bytes, arxiv_id: str, title: str = "", abstract: str = "",
                      formulas: bool = True, tables: bool = True) -> Paper:
    from docling.datamodel.base_models import DocumentStream
    from docling_core.types.doc import DocItemLabel

    res = _converter(formulas, tables).convert(DocumentStream(name=f"{arxiv_id}.pdf", stream=BytesIO(data)))
    doc = res.document

    skip = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.FOOTNOTE,
            DocItemLabel.PICTURE, DocItemLabel.TITLE}
    sections: list[Section] = []
    references: list[str] = []
    cur: Section | None = None
    state = "front"
    counter: Counter = Counter()
    for item, _ in doc.iterate_items():
        label = getattr(item, "label", None)
        if label in skip:
            continue
        page = item.prov[0].page_no if getattr(item, "prov", None) else 0
        text = re.sub(r"\s+", " ", getattr(item, "text", "") or "").strip()

        if label == DocItemLabel.SECTION_HEADER:
            if REFS_RE.match(text):
                state = "refs"
                continue
            if state == "refs" and not APPENDIX_RE.match(text):
                references.append(text)
                continue
            if re.match(r"^abstract$", text, re.I):
                state = "front"
                continue
            supp = re.match(r"^(?:(?i:appendix|supplementa(?:ry|l))|S\d)", text)
            kind = "appendix" if state in ("refs", "appendix") or supp else "body"
            state = "appendix" if kind == "appendix" else "body"
            cur = Section(level=_level(text), title=text, kind=kind)
            sections.append(cur)
            continue
        if state == "front" or cur is None:
            continue
        if state == "refs":
            if text:
                references.append(text)
            continue

        if label == DocItemLabel.TABLE:
            text = "\n".join(_table_rows(item, doc))     # 图注是表格的子节点，iterate_items 会单独给出
            kind = "caption"
        elif label == DocItemLabel.FORMULA:
            if not text:
                continue
            text = f"${text}$" if formulas else text
            kind = "body"
        elif label == DocItemLabel.CAPTION:
            kind = "caption"
        else:
            kind = "caption" if CAPTION_RE.match(text) else "body"
        if not text:
            continue
        counter[page] += 1
        cur.paragraphs.append(Paragraph(id=f"pg{page}.b{counter[page]}", text=text, kind=kind))

    sections = [s for s in sections if s.paragraphs]
    title = title or arxiv_id
    return Paper(arxiv_id=arxiv_id, title=title, abstract=abstract, sections=sections,
                 references=references, source="pdf")


if __name__ == "__main__":
    import time
    for path in sys.argv[1:]:
        t0 = time.time()
        p = parse_pdf_docling(open(path, "rb").read(), path.rsplit("/", 1)[-1].removesuffix(".pdf"))
        print(f"{path}  {time.time() - t0:.1f} 秒  章节 {len(p.sections)}  字数 {p.char_count():,}  参考文献 {len(p.references)}")
        for s in p.sections:
            print(f"  {'  ' * (s.level - 1)}[{s.kind[0]}] {s.title}  ({len(s.paragraphs)} 段)")
        maths = [g for s in p.sections for g in s.paragraphs if g.text.startswith("$")]
        print(f"\n  独立公式 {len(maths)} 个，前 5 个：")
        for g in maths[:5]:
            print(f"    {g.id} | {g.text[:160]}")
        tabs = [g for s in p.sections for g in s.paragraphs if g.text.startswith("| ")]
        print(f"\n  表格 {len(tabs)} 张，第一张：")
        if tabs:
            print("    " + tabs[0].text[:800].replace("\n", "\n    "))
