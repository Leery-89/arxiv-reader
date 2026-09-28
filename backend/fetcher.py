"""取文与清洗。

⚠️ 这个文件只有骨架 —— 函数签名、docstring、和每一步的要点。
   实现是你的 D3-D4 任务。这是整个项目质量的地基，也是面试
   一定会问到的地方，所以必须你自己写。

建议顺序：
    1. normalize_arxiv_id   （最简单，先热身，顺便验证正则）
    2. fetch_html           （能拿到 HTML 就行）
    3. parse_html           （最花时间的一步）
    4. clean_text           （在 parse 过程中调用）
    5. apply_budget         （先测量超预算比例，再决定投多少精力）
    6. fetch_paper          （把上面串起来）

每写完一个函数就单独测一下，别攒到最后一起调。
"""

import re
import requests
from bs4 import BeautifulSoup
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh) arxiv-reader/0.1",
}

from models import Paper, Section, Paragraph


# 一篇论文送进模型的字符预算。
# 这个数字现在是拍脑袋定的 —— D3 调研跑完后用真实分布校准它，
# 校准过程写进 DECISIONS.md。
CHAR_BUDGET = 150_000

# 超预算时按这个顺序丢弃章节（匹配时忽略大小写，用 in 判断即可）
DROP_ORDER = [
    ["appendix", "supplementary"],
    ["acknowledg"],           # acknowledgment / acknowledgement 两种拼法都能覆盖
    ["related work", "background"],
]


def normalize_arxiv_id(raw: str) -> str:
    """把各种形态的 arXiv ID 归一化成不带版本号的形式。

        "1706.03762v7"  -> "1706.03762"
        "arXiv:2301.07041" -> "2301.07041"
        "cs/0701001v2"  -> "cs/0701001"

    为什么去版本号：缓存按论文存而非按版本存，见 DECISIONS.md D2。
    """
    s = raw.removeprefix("arXiv:")
    return re.sub(r"v\d+$", "", s)


def fetch_html(arxiv_id: str) -> str | None:
    """拉取 arxiv.org/html/<id>，拿不到返回 None。

    要点：
      - 必须设 User-Agent，裸 requests 的默认 UA 容易被拒
      - 设超时（10 秒足够），否则挂起会拖垮整个后端
      - 404 是正常情况（老论文没有 HTML 版），不要抛异常，返回 None
      - 区分「404 没有」和「网络错误」—— 前者该降级，后者该重试

    第三条和第四条的区别很重要：面试问「异常怎么处理」时，
    能说清楚「哪些错误该降级、哪些该重试」是加分项。
    """
    url = f"https://arxiv.org/html/{arxiv_id}"
    resp = requests.get(url, headers=HEADERS, timeout=10)

    if resp.status_code == 200:
        return resp.text
    if resp.status_code == 404:
        return None          # 正常情况：这篇没有 HTML 版，走降级

    # 其他状态码（403、429、5xx）都不该静默处理
    resp.raise_for_status()


def fetch_abstract_only(arxiv_id: str) -> Paper:
    """降级路径：没有 HTML 版时，从 abs 页面只取标题和摘要。

    最早走的是 export.arxiv.org 的 API（Atom XML），D14 评测时发现它
    对浏览器 UA 返回 406、连发也 406（要求间隔 3 秒）。换成直接抓
    arxiv.org/abs/<id>：和 HTML 版同一个域名、没有间隔限制，页面的
    <meta name="citation_*"> 标签里标题摘要都有，顺带还有 DOI（v2 用）。

    记得把 source 设成 "abstract_only" —— 前端要据此提示用户。
    """
    url = f"https://arxiv.org/abs/{arxiv_id}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    def meta(name: str) -> str:
        tag = soup.find("meta", attrs={"name": name})
        return (tag.get("content") or "").strip() if tag else ""

    title = meta("citation_title")
    abstract = meta("citation_abstract")
    if not abstract:                                    # 兜底：页面正文里的摘要块
        bq = soup.select_one("blockquote.abstract")
        abstract = bq.get_text(" ", strip=True).removeprefix("Abstract:").strip() if bq else ""
    abstract = re.sub(r"\s+", " ", abstract)

    return Paper(arxiv_id=arxiv_id, title=title, abstract=abstract, source="abstract_only")

def para_text(div) -> str:
    """把一个段落元素转成干净文本：公式换成 LaTeX，引用标记删掉。"""
    for m in div.find_all("math"):
        latex = m.get("alttext", "")
        m.replace_with(f"${latex}$")       # 用 $ 包起来，模型一眼认出是公式

    for c in div.find_all("cite"):
        c.decompose()

    text = div.get_text(" ", strip=True)
    text = re.sub(r"\s+([.,;:)])", r"\1", text) 
    text = re.sub(r"\s+", " ", text)
    return text


def parse_html(html: str, arxiv_id: str) -> Paper:
    """把 arXiv 的 HTML 解析成 Paper 对象。这是最花时间的一步。

    做法：
      1. 用 BeautifulSoup 解析
      2. 找到标题、摘要
      3. 遍历章节元素，建 Section；章节序号用来生成段落 ID
      4. 每个章节里遍历段落和图表说明，建 Paragraph
         - 正文段落 kind="body"，ID 形如 "s2.p3"
         - 图表说明 kind="caption"，ID 形如 "s2.c1"
      5. 参考文献单独收进 paper.references，不进 sections

    ⚠️ 别照抄任何教程里的 class 名。打开 arxiv.org/html/1706.03762，
       F12 看真实的 DOM 结构 —— arXiv 的 HTML 由 LaTeX 转换而来，
       class 命名有很规整的前缀，自己看一眼就明白了。

       这一步自己看 DOM 而不是搜答案，是有意义的：面试官问
       「你怎么确定选择器的」，「我看了 DOM 结构」和「我抄的教程」
       是两种回答。
    """
    
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one("h1.ltx_title_document")
    if title_el:
        for note in title_el.select(".ltx_note"):   # 标题里的脚注（作者贡献说明之类）
            note.decompose()
    title = title_el.get_text(" ", strip=True) if title_el else ""

    abstract_el = soup.select_one("div.ltx_abstract p.ltx_p")
    abstract = abstract_el.get_text(" ", strip=True) if abstract_el else ""

    sections = []
    for sec in soup.select("section"):
        classes = sec.get("class", [])

        if "ltx_bibliography" in classes:
            continue                          # 参考文献，第四阶段单独处理

        if "ltx_section" in classes:
            level = 1
        elif "ltx_subsection" in classes:
            level = 2
        elif "ltx_subsubsection" in classes:
            level = 3
        elif "ltx_paragraph" in classes: 
            level = 4
        elif "ltx_appendix" in classes:           # ← 加这两行
            level = 1
        else:
            continue                          # 附录等其他 section，先跳过
        
        in_appendix = "ltx_appendix" in classes or sec.find_parent("section", class_="ltx_appendix") is not None
        kind = "appendix" if in_appendix else "body"

        # recursive=False：只取直接子元素，否则父节会把子节的标题和段落一并捞走
        heading = sec.find(["h2", "h3", "h4","h5","h6"], recursive=False)
        sec_title = heading.get_text(" ", strip=True) if heading else ""

        paragraphs = []
        for child in sec.find_all(["div", "figure"], recursive=False):
            classes = child.get("class", [])

            if "ltx_para" in classes:
                paragraphs.append(Paragraph(id=child.get("id", ""), text=para_text(child)))

            elif "ltx_figure" in classes or "ltx_table" in classes:
                cap = child.find("figcaption")
                if cap:
                    paragraphs.append(Paragraph(
                        id=child.get("id", ""),
                        text=para_text(cap),
                        kind="caption",
                    ))

        sections.append(Section(level=level, title=sec_title,kind=kind, paragraphs=paragraphs))
        orphans = [d for d in soup.select("div.ltx_para") if d.find_parent("section") is None]
        has_body = any(s.kind == "body" for s in sections)
        if orphans and not has_body:
            body = [Paragraph(id=d.get("id", ""), text=para_text(d)) for d in orphans]
            sections.insert(0, Section(level=1, title="正文", paragraphs=body))
    bib = soup.select_one("section.ltx_bibliography")
    references = [li.get_text(" ", strip=True) for li in bib.select("li.ltx_bibitem")] if bib else []

    return Paper(arxiv_id=arxiv_id, title=title, abstract=abstract, sections=sections)


def apply_budget(paper: Paper, budget: int = CHAR_BUDGET) -> Paper:
    """超预算时按 DROP_ORDER 依次丢弃章节，并记录到 truncated_sections。

    ⚠️ 写之前先做调研：你那 30 篇样本里，真正超预算的有几篇？
       如果只有一两篇，这个函数写最简版本就行（甚至先直接 return paper），
       把时间留给更重要的地方。先测量，再决定投入。

    全丢完还超预算的话再考虑硬截断 —— 但先看看有没有这种情况。
    """
    if paper.char_count() <= budget:
        return paper

    # 第一轮：整体丢掉附录
    dropped = [s.title for s in paper.sections if s.kind == "appendix"]
    paper.sections = [s for s in paper.sections if s.kind != "appendix"]

    # 第二轮：还超的话按 DROP_ORDER 逐组丢
    for keywords in DROP_ORDER:
        if paper.char_count() <= budget:
            break
        hit = [s for s in paper.sections if any(k in s.title.lower() for k in keywords)]
        dropped += [s.title for s in hit]
        paper.sections = [s for s in paper.sections if s not in hit]

    paper.truncated_sections = dropped
    return paper

def fetch_paper(raw_id: str) -> Paper:
    """对外的唯一入口：给一个 arXiv ID，返回可用的 Paper。

        paper = fetch_paper("1706.03762v7")

    把上面几步串起来，包含降级逻辑：
        归一化 ID -> 拉 HTML -> 拿到就解析，拿不到就降级取摘要 -> 套预算
    """
    arxiv_id = normalize_arxiv_id(raw_id)

    html = fetch_html(arxiv_id)
    if html is not None:
        paper = parse_html(html, arxiv_id)
    else:
        paper = fetch_abstract_only(arxiv_id)

    return apply_budget(paper)


if __name__ == "__main__":
    # 五篇不同领域的论文，D4 验收时逐个跑，肉眼读输出。
    # 别跳过肉眼检查 —— 输入是脏的，后面 prompt 写得再好也救不回来，
    # 而脏在哪里只有眼睛能看出来。
    SAMPLES = [
        ("1706.03762", "NLP · Transformer"),
        ("2010.11929", "CV · ViT"),
        ("1301.0001", "确认 404，测降级"),
        ("2005.14165", "NLP · GPT-3，超长，正好测预算逻辑"),
        ("1207.0580", "2012 年老论文，测降级路径"),
    ]

    for arxiv_id, note in SAMPLES:
        print("=" * 60)
        print(f"{arxiv_id}  —  {note}")
        try:
            paper = fetch_paper(arxiv_id)
            print(f"  来源      : {paper.source}")
            print(f"  标题      : {paper.title}")
            print(f"  章节数    : {len(paper.sections)}")
            print(f"  字符数    : {paper.char_count():,}")
            print(f"  估算 token: {paper.est_tokens():,}")
            if paper.truncated_sections:
                print(f"  已丢弃    : {', '.join(paper.truncated_sections)}")
            # 抽第一节前两段出来肉眼看
            if paper.sections:
                s = paper.sections[0]
                print(f"\n  [{s.title}]")
                for p in s.paragraphs[:2]:
                    print(f"    {p.id} | {p.text[:180]}...")
        except NotImplementedError as e:
            print(f"  待实现: {e}")
        print()
