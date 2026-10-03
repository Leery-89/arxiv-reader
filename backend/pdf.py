"""PDF → Paper：没有 HTML 版的论文不再只剩摘要（路线图 A.3）。

基线用 PyMuPDF：纯 CPU、不装模型，Railway 上能跑。
  - 标题和摘要仍从 abs 页面的 meta 取（比从 PDF 版面里猜可靠）
  - 正文按 PyMuPDF 的文本块切段落；字号明显大于正文、或"编号 + 短句"的块当章节标题
  - References 之后的内容不进正文；之后再出现 Appendix 标题则按附录收
  - 页眉页脚（多页重复出现的短块、纯页码）丢掉
  - 段落 ID 用"页-块"：pg3.b4 = 第 3 页第 4 块。PDF 没有 LaTeXML 那样的原生 ID，
    跳转只能定位到页（侧栏用 /pdf/<id>#page=3）

已知短板：公式是 PDF 里的字形，不是 LaTeX，抽出来会乱码（∑、上下标错位）。
是否要对含公式的页另走视觉模型，等 pdf_bench.py 的数字出来再定。

    python pdf.py 1706.03762          # 解析一篇，打印章节和前几段肉眼看
"""

import re
import sys
from collections import Counter

import pymupdf
import requests

from fetcher import HEADERS, fetch_abstract_only
from models import Paper, Paragraph, Section

HEADING_RE = re.compile(
    r"^(?:(?:\d+|[A-Z](?:\.\d+)+|[IVX]+\.)\.?\s+[A-Z][^\n]{0,80}"   # 3 Method / A.2 Details / IV. RESULTS
    r"|\d+(?:\.\d+)+\.?\s+[A-Za-z0-9][^\n]{0,80}"                       # 2.2. pre-processing / 4.2. 2-steps Training
    r"|S\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{0,80}"                             # S1.5. Software（补充材料）
    r"|(?i:Abstract|Introduction|Related Work|Conclusions?|Discussion|Methods?|Results|"
    r"Acknowledge?ments?|References|Bibliography|Appendix(?:\s+[A-Z])?[^\n]{0,60}|"
    r"Supplementa(?:ry|l)(?:\s+[A-Za-z]+){0,3}))$"
)
# 附录里的 "A Extra Results"：单个大写字母开头，只在 References 之后认（正文里会和 "A Very Large Title" 混）
APPX_LETTER_RE = re.compile(r"^[A-Z](?:\.\d+)*\.?\s+[A-Z][^\n]{0,80}$")
REFS_RE = re.compile(r"^(?:(?:Supplementa(?:ry|l)\s+)?References(?:\s+and\s+Notes)?|Bibliography|Literature Cited)$", re.I)
# 参考文献条目长得像"编号 + 大写开头"的标题："34. G. Tesauro, Artificial Intelligence …"（Science 预印本）
REF_ENTRY_RE = re.compile(r"^\d{1,3}\.\s+(?:[A-Z]\.\s?)+[A-Z][a-z]")
APPENDIX_RE = re.compile(r"^(?:(?i:Appendix|Supplementa(?:ry|l))|S\d|[A-Z](?:\.\d+)*\.?\s+[A-Z])")
# 没有 "References" 标题的参考文献（revtex、Nature 模板常见）：块里成串的 [12] 编号、(2019) 年份、J. Smith 式缩写
REF_MARK_RE = re.compile(r"\[\d{1,3}\]|\((?:19|20)\d\d\)")
INITIAL_RE = re.compile(r"\b[A-Z]\.\s")


def _ref_like(text: str) -> bool:
    return len(REF_MARK_RE.findall(text)) >= 2 and len(INITIAL_RE.findall(text)) >= 3
CAPTION_RE = re.compile(r"^(?:Figure|Fig\.|Table|Algorithm)\s*\d+[.:]", re.I)
ARXIV_STAMP_RE = re.compile(r"^arXiv:\S+\s+\[[\w.-]+\]")   # 左侧竖排的 arXiv 编号水印
BOLD_FONT_RE = re.compile(r"Bold|Medi|Semibold|Black|Heavy|CMBX|BX\d", re.I)
PAGE_NUM_RE = re.compile(r"^\s*(?:\d{1,3}|Page \d+( of \d+)?)\s*$")


def fetch_pdf(arxiv_id: str) -> bytes | None:
    """拉 arxiv.org/pdf/<id>；404 返回 None（同 fetch_html 的约定），其他错误抛出。"""
    resp = requests.get(f"https://arxiv.org/pdf/{arxiv_id}", headers=HEADERS, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    if not resp.content.startswith(b"%PDF"):
        return None
    return resp.content


def _block_text(block: dict) -> tuple[str, float, bool, int]:
    """块 → (文本, 主字号, 是否加粗, 行数)。行尾连字符拼回去，其余换行变空格。"""
    lines, sizes, bold = [], Counter(), Counter()
    for line in block.get("lines", []):
        t = "".join(s["text"] for s in line["spans"])
        for s in line["spans"]:
            n = len(s["text"].strip())
            sizes[round(s["size"], 1)] += n
            bold[bool(s["flags"] & 16) or bool(BOLD_FONT_RE.search(s["font"]))] += n
        lines.append(t.strip())
    text = ""
    for t in lines:
        if not t:
            continue
        if text.endswith("-") and t[:1].islower():
            text = text[:-1] + t
        else:
            text = f"{text} {t}" if text else t
    size = sizes.most_common(1)[0][0] if sizes else 0.0
    # 行数按视觉行算：小型大写字母的标题里，编号和文字常被拆成同一高度的两"行"
    rows = {round(l["bbox"][1]) for l in block.get("lines", []) if "".join(s["text"] for s in l["spans"]).strip()}
    return re.sub(r"\s+", " ", text).strip(), size, bold[True] > bold[False], len(rows)


def _is_heading(text: str, size: float, bold: bool, body: float, nlines: int = 1) -> bool:
    """章节标题的版面特征，任一即可：
      - 字号明显大于正文
      - 编号 + 短标题，且加粗 / 略大 / 单独占一行
    最后一条是实测加的：ICLR、AASTeX、IEEE 的标题用小型大写字母，字号和正文一样甚至更小、
    也不加粗（ViT "1 INTRODUCTION" 9.6pt，正文 10pt），只靠"自成一块、只有一行"能认出来。"""
    if len(text) > 90 or CAPTION_RE.match(text) or "=" in text:
        return False
    if text.endswith((",", ";", ":")) or text.endswith(".") and len(text.split()) > 8:
        return False
    if size >= body * 1.15:
        return bool(re.search(r"[A-Za-z]{3}", text))
    if not HEADING_RE.match(text) or size < body * 0.7:
        return False
    return bold or size > body * 1.02 or nlines == 1


def _level(text: str) -> int:
    m = re.match(r"^([A-Z]|\d+)((?:\.\d+)*)", text)
    return 1 + min(m.group(2).count("."), 2) if m else 1


def parse_pdf(data: bytes, arxiv_id: str, title: str = "", abstract: str = "") -> Paper:
    pymupdf.TOOLS.mupdf_display_errors(False)      # "could not parse color space" 之类，只影响图片
    doc = pymupdf.open(stream=data, filetype="pdf")

    # 第一遍：所有块 + 字号分布，定正文字号；统计每页都出现的短块（页眉页脚）
    blocks, size_hist, repeats = [], Counter(), Counter()
    for pno, page in enumerate(doc, 1):
        h = page.rect.height
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            text, size, bold, nlines = _block_text(b)
            if not text or ARXIV_STAMP_RE.match(text):
                continue
            y0, y1 = b["bbox"][1], b["bbox"][3]
            margin = y1 < h * 0.07 or y0 > h * 0.93
            blocks.append((pno, text, size, bold, nlines, margin))
            size_hist[size] += len(text)
            if margin and len(text) < 120:
                repeats[re.sub(r"\d+", "#", text)] += 1
    body = size_hist.most_common(1)[0][0] if size_hist else 10.0
    n_pages = len(doc)

    # 第二遍：切章节
    sections: list[Section] = []
    references: list[str] = []
    cur: Section | None = None
    state = "front"                       # front → body → refs → appendix
    seen_abstract = False
    counter: Counter = Counter()
    run = 0                               # 连续像参考文献的块数
    for pno, text, size, bold, nlines, margin in blocks:
        if PAGE_NUM_RE.match(text):
            continue
        if margin and repeats[re.sub(r"\d+", "#", text)] >= max(3, n_pages // 3):
            continue
        head = (_is_heading(text, size, bold, body, nlines) and (state != "front" or HEADING_RE.match(text))
                and not REF_ENTRY_RE.match(text))
        if not head and state in ("refs", "appendix") and bold and APPX_LETTER_RE.match(text):
            head = True
        if head:
            if REFS_RE.match(text):
                state = "refs"
                continue
            if state == "refs" and not APPENDIX_RE.match(text):
                references.append(text)
                continue
            if re.match(r"^abstract$", text, re.I):
                state = "front"           # 摘要从 meta 取，PDF 里的摘要块跳过
                seen_abstract = True
                continue
            supp = re.match(r"^(?:(?i:appendix|supplementa(?:ry|l))|S\d)", text)
            kind = "appendix" if state in ("refs", "appendix") or supp else "body"
            state = "appendix" if kind == "appendix" else "body"
            cur = Section(level=_level(text), title=text, kind=kind)
            sections.append(cur)
            continue
        if state == "front":
            # 正文没有章节标题的版式（Science / Nature 的预印本：Abstract 之后直接是正文，一路到 References）：
            # 摘要标题之后出现正文字号的长段落，就开一个隐含的"正文"节。摘要本身（常是小一号字）跳过
            if not (seen_abstract and len(text) >= 200 and abs(size - body) < body * 0.1
                    and not margin and text[:60] not in abstract):
                continue
            state = "body"
            cur = Section(level=1, title="正文")
            sections.append(cur)
        if state == "refs":
            references.append(text)
            continue
        # 连续 3 块像参考文献 → 从这里起是参考文献，已经收进正文的那两块挪过去
        run = run + 1 if _ref_like(text) else 0
        if run >= 3 and cur is not None:
            moved = [g.text for g in cur.paragraphs[-(run - 1):]] if run > 1 else []
            del cur.paragraphs[len(cur.paragraphs) - len(moved):]
            references.extend(moved + [text])
            state = "refs"
            continue
        if size < body * 0.85 and not CAPTION_RE.match(text):
            continue                      # 脚注、页边小字
        counter[pno] += 1
        para = Paragraph(id=f"pg{pno}.b{counter[pno]}", text=text,
                         kind="caption" if CAPTION_RE.match(text) else "body")
        cur.paragraphs.append(para)

    sections = [s for s in sections if s.paragraphs]
    if not sections:
        # 一个标题都没认出来：整篇收成一节，只要像段落的块（正文字号、够长），作者单位页眉自然被筛掉
        paras = [(pno, t) for pno, t, size, _, _, margin in blocks
                 if not margin and len(t) >= 200 and abs(size - body) < body * 0.1]
        counter = Counter()
        sec = Section(level=1, title="正文")
        for pno, t in paras:
            counter[pno] += 1
            sec.paragraphs.append(Paragraph(id=f"pg{pno}.b{counter[pno]}", text=t))
        sections = [sec] if paras else []
    if not title or not abstract:
        title = title or (doc.metadata or {}).get("title", "") or arxiv_id
    return Paper(arxiv_id=arxiv_id, title=title, abstract=abstract, sections=sections,
                 references=references, source="pdf")


def fetch_pdf_paper(arxiv_id: str) -> Paper | None:
    """abs 页面取标题摘要 + PDF 取正文；PDF 拿不到或解析不出正文返回 None（调用方降级到摘要）。"""
    data = fetch_pdf(arxiv_id)
    if data is None:
        return None
    meta = fetch_abstract_only(arxiv_id)
    paper = parse_pdf(data, arxiv_id, meta.title, meta.abstract)
    return paper if paper.sections else None


if __name__ == "__main__":
    for aid in sys.argv[1:]:
        p = fetch_pdf_paper(aid)
        if p is None:
            print(f"{aid}: 没有可用的 PDF 正文")
            continue
        print(f"{aid}  {p.title}\n  章节 {len(p.sections)}  字数 {p.char_count():,}  参考文献 {len(p.references)}")
        for s in p.sections:
            print(f"  {'  ' * (s.level - 1)}[{s.kind[0]}] {s.title}  ({len(s.paragraphs)} 段)")
        for s in p.sections[:2]:
            for g in s.paragraphs[:2]:
                print(f"    {g.id} | {g.text[:160]}")
