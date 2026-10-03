"""出版方全文页面 → Paper（D27）。

付费墙论文的服务器端抓取不可行（后端没有订阅、出版方反爬），也不该做。但用户自己在浏览器里
能打开全文（学校 IP、机构登录），插件就在用户点"分析"时把当前页面的 HTML 交给后端解析——
等于读用户自己有权看的那一份，和拖 PDF（D26）同性质，只是少了下载这一步。

解析分两层：
  - 元数据：几乎所有出版方都给 Highwire 的 <meta name="citation_*">（Google Scholar 要求的），
    标题、DOI、摘要从这里取最稳
  - 正文：通用规则——在文章容器里按文档顺序走，h2/h3/h4 开新节，p 是段落，figcaption 是图注，
    到 References / Acknowledgments 之类的标题就停。各家只在 PUBLISHER_RULES 里补容器、
    段落和要跳过的选择器（ACS 的段落是 div.NLM_p，不是 p）

页面上只有摘要（用户其实没有权限）时 source = abstract_only，侧栏照常提示。

段落 ID：元素自己有 id 就用（侧栏跳转时在页面里 getElementById 滚过去）；没有就编 "pp<n>"，
这种段落点出处不跳转。
"""

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from doi import parse_doi
from fetcher import para_text
from models import Paper, Paragraph, Section

# 每家的差异：容器、段落选择器、要先删掉的东西。没列到的出版方走 DEFAULT
DEFAULT = {
    "container": ["article", "main", "[role=main]", "body"],
    "para": "p",
    "drop": ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "button",
             "[role=navigation]", ".references", "#references", ".ref-list"],
}
PUBLISHER_RULES = {
    "pubs.acs.org": {
        # 2026 年改版后地址变成 /ancac3/article/20/37/25290/…，页面结构也换了，先把常见的正文容器都列上，
        # 拿到真实全文页后再收紧
        "container": [".article-body", "[data-widgetname=ArticleFulltext]", ".widget-ArticleFulltext",
                      ".article_content", "#pb-page-content article", "article", "main"],
        "para": "div.NLM_p, p",
        "drop": [".NLM_back", ".article_references", "#references", ".article_supporting-info"],
    },
    "www.sciencedirect.com": {
        "container": ["#body", "article", "main"],
        "para": "div.u-margin-s-bottom, p",
        "drop": ["#references", ".References", ".Appendices .References"],
    },
    "link.springer.com": {
        "container": ["#body", ".c-article-body", "article", "main"],
        "para": "p",
        "drop": ["#Bib1-section", ".c-article-references"],
    },
    "www.nature.com": {
        "container": [".c-article-body", "article", "main"],
        "para": "p",
        "drop": ["#references", ".c-article-references"],
    },
    "onlinelibrary.wiley.com": {
        "container": [".article-section__full", "article", "main"],
        "para": "p",
        "drop": [".article-section__references", "#references-section"],
    },
}
STOP_HEADING_RE = re.compile(
    r"^(?:\d+\.?\s*)?(?:References?|Bibliography|Literature Cited|Acknowledge?ments?|Author Information|"
    r"Notes|Conflicts? of Interest|Supporting Information|Data Availability|Funding|Competing Interests?)\b",
    re.I)
ABSTRACT_HEADING_RE = re.compile(r"^(?:Abstract|Summary)$", re.I)
MIN_FULLTEXT_CHARS = 3000          # 正文不到这么多就当只有摘要（没权限、只显示预览）


def _meta(soup, *names) -> str:
    for n in names:
        tag = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
        if tag and tag.get("content"):
            return re.sub(r"\s+", " ", tag["content"]).strip()
    return ""


def _rules(url: str) -> dict:
    host = urlparse(url).hostname or ""
    r = PUBLISHER_RULES.get(host, {})
    return {"container": r.get("container", []) + DEFAULT["container"],
            "para": r.get("para", DEFAULT["para"]),
            "drop": DEFAULT["drop"] + r.get("drop", [])}


def page_id(url: str, doi: str | None) -> str:
    """缓存 / 埋点 / 前端用的 ID。和 D25 的 doi:<doi> 分开——那条是开放获取副本或摘要，这条是用户看到的全文。"""
    if doi:
        return f"page:{doi}"
    u = urlparse(url)
    return f"page:{u.hostname}{u.path.replace('/article-abstract/', '/article/')}"


def parse_page(url: str, html: str, doi: str | None = None) -> Paper:
    soup = BeautifulSoup(html, "html.parser")
    rules = _rules(url)

    title = _meta(soup, "citation_title", "dc.title", "og:title") or (soup.title.get_text(strip=True) if soup.title else "")
    doi = doi or parse_doi(_meta(soup, "citation_doi", "dc.identifier", "prism.doi") or "") or parse_doi(url)
    abstract = _meta(soup, "citation_abstract", "dc.description", "description", "og:description")

    for sel in rules["drop"]:
        for el in soup.select(sel):
            el.decompose()
    container = next((c for sel in rules["container"] if (c := soup.select_one(sel))), soup)

    sections: list[Section] = []
    cur: Section | None = None
    in_abstract = False
    para_els = set(map(id, container.select(rules["para"])))
    n = 0
    for el in container.find_all(True):
        name = el.name
        if name in ("h2", "h3", "h4"):
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            if STOP_HEADING_RE.match(text):
                break
            if ABSTRACT_HEADING_RE.match(text):
                in_abstract = True
                continue
            in_abstract = False
            cur = Section(level={"h2": 1, "h3": 2, "h4": 3}[name], title=text)
            sections.append(cur)
            continue
        if name == "figcaption" or id(el) in para_els:
            # 嵌套的段落（div.NLM_p 里套 p）只取最外层
            if el.find_parent(lambda p: id(p) in para_els or p.name == "figcaption"):
                continue
            n += 1
            text = para_text(el)
            if len(text) < 2:
                continue
            if in_abstract:
                abstract = abstract if len(abstract) > len(text) else text
                continue
            if cur is None:
                cur = Section(level=1, title="正文")
                sections.append(cur)
            cur.paragraphs.append(Paragraph(id=el.get("id") or f"pp{n}", text=text,
                                            kind="caption" if name == "figcaption" else "body"))

    sections = [s for s in sections if s.paragraphs]
    body_chars = sum(s.char_count() for s in sections)
    if body_chars < MIN_FULLTEXT_CHARS:
        return Paper(arxiv_id=page_id(url, doi), title=title, abstract=abstract, source="abstract_only", url=url)
    return Paper(arxiv_id=page_id(url, doi), title=title, abstract=abstract, sections=sections, source="page", url=url)


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:                      # 浏览器 "另存为 → 仅 HTML" 的文件
        html = open(path, encoding="utf-8", errors="replace").read()
        m = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        url = m.group(1) if m else "https://pubs.acs.org/doi/x"
        p = parse_page(url, html)
        print(f"{path}\n  {p.arxiv_id}  来源 {p.source}  章节 {len(p.sections)}  字数 {p.char_count():,}\n  标题 {p.title}")
        print(f"  摘要 {p.abstract[:200]}")
        for s in p.sections:
            print(f"  {'  ' * (s.level - 1)}{s.title}  ({len(s.paragraphs)} 段)")
        for s in p.sections[:2]:
            for g in s.paragraphs[:2]:
                print(f"    {g.id} | {g.text[:160]}")
