"""DOI → arXiv 版本（路线图 4：期刊 DOI 入口）。

用户粘的往往是期刊 DOI（10.1038/…），而后端只认 arXiv ID。很多期刊论文在 arXiv 上有预印本，
找到它就能走现成的 HTML 全文链路。找不到时，把开放获取副本的链接和摘要交给前端，
"下载开放获取版并分析"是下一步（路线图 C，只用合法开放获取的版本）。

查找顺序：
  1. 10.48550/arXiv.<id> 本身就是 arXiv 的 DOI，直接取 ID，不发请求
  2. OpenAlex 按 DOI 查：一次拿到标题、摘要、所有收录位置（locations）。
     其中来源是 arXiv 的位置，landing_page_url 里就有 arXiv ID；best_oa_location 是开放获取副本
  3. OpenAlex 没收录 → Crossref 按 DOI 查标题摘要；relation.has-preprint 有时直接写着 arXiv DOI
  4. 前两步都没 arXiv 位置 → 用标题在 OpenAlex 里搜 arXiv 来源的论文，标题相似度 ≥ 0.9 才认。
     期刊版和预印本 OpenAlex 经常没合并成一条，这一步捞的是这种

为什么用 OpenAlex 打主力：免费、不要 key、一次请求给全 arXiv 位置 + 开放获取链接 + 摘要；
Crossref 只有出版方填的元数据，has-preprint 填得很少。设了 CONTACT_EMAIL 就带上 mailto，
进两家的 polite pool（限速更宽）。
"""

import difflib
import os
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import requests

from fetcher import HEADERS

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
ARXIV_SOURCE = "S4306400194"            # OpenAlex 里 arXiv 这个来源的 ID
TITLE_MATCH = 0.9

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})", re.I)
TIMEOUT = 10


class DoiNotFound(Exception):
    pass


@dataclass
class DoiResult:
    doi: str
    title: str = ""
    abstract: str = ""
    year: int | None = None
    venue: str = ""
    arxiv_id: str | None = None
    oa_url: str | None = None           # 开放获取副本（优先 PDF），没有就是 None；给人点的
    oa_pdfs: list[str] = field(default_factory=list)   # 所有标了开放获取的 PDF 直链，按 best 优先；给 oa.py 下载用
    via: str = ""                       # 怎么找到的 arXiv 版本：arxiv-doi / openalex / crossref-preprint / title-search

    def to_dict(self) -> dict:
        return asdict(self)


def parse_doi(text: str) -> str | None:
    """从 "doi:10.1/x"、"https://doi.org/10.1/x"、裸 DOI 里取出 DOI；结尾的标点去掉。"""
    m = DOI_RE.search(text.strip())
    if not m:
        return None
    return m.group(1).rstrip(".,;)]}").lower()


def _params(**kw) -> dict:
    email = os.getenv("CONTACT_EMAIL")
    return {**kw, "mailto": email} if email else kw


def _get(url: str, **params) -> dict | None:
    resp = requests.get(url, params=_params(**params), headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _arxiv_id_from_url(url: str | None) -> str | None:
    m = ARXIV_URL_RE.search(url or "")
    return m.group(1) if m else None


def _abstract_from_inverted(inv: dict | None) -> str:
    """OpenAlex 的摘要存成倒排索引 {词: [位置…]}，还原成句子。"""
    if not inv:
        return ""
    pos = {i: w for w, idx in inv.items() for i in idx}
    return " ".join(pos[i] for i in sorted(pos))


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"<[^>]+>", "", t or "").lower()).strip()


def title_similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm_title(a), _norm_title(b)).ratio()


def _from_openalex(work: dict, doi: str) -> DoiResult:
    r = DoiResult(
        doi=doi,
        title=work.get("title") or work.get("display_name") or "",
        abstract=_abstract_from_inverted(work.get("abstract_inverted_index")),
        year=work.get("publication_year"),
        venue=((work.get("primary_location") or {}).get("source") or {}).get("display_name") or "",
    )
    for loc in work.get("locations") or []:
        aid = _arxiv_id_from_url(loc.get("landing_page_url")) or _arxiv_id_from_url(loc.get("pdf_url"))
        if aid:
            r.arxiv_id, r.via = aid, "openalex"
            break
    best = work.get("best_oa_location") or {}
    r.oa_url = best.get("pdf_url") or best.get("landing_page_url")
    for loc in [best, *(work.get("locations") or [])]:
        url = loc.get("pdf_url")
        if url and (loc is best or loc.get("is_oa")) and url not in r.oa_pdfs:
            r.oa_pdfs.append(url)
    return r


def _from_crossref(msg: dict, doi: str) -> DoiResult:
    title = (msg.get("title") or [""])[0]
    abstract = re.sub(r"<[^>]+>", " ", msg.get("abstract") or "")      # JATS 标签
    abstract = re.sub(r"\s+", " ", abstract).strip().removeprefix("Abstract ").strip()
    year = ((msg.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    r = DoiResult(doi=doi, title=title, abstract=abstract, year=year,
                  venue=(msg.get("container-title") or [""])[0])
    for rel in (msg.get("relation") or {}).get("has-preprint") or []:
        m = ARXIV_DOI_RE.match(rel.get("id", ""))
        if m:
            r.arxiv_id, r.via = m.group(1), "crossref-preprint"
            break
    for link in msg.get("link") or []:          # 出版方给的全文链接；只有标了开放许可才算开放获取
        if link.get("content-type") == "application/pdf" and msg.get("license"):
            if any("creativecommons.org" in (lic.get("URL") or "") for lic in msg["license"]):
                r.oa_url = link.get("URL")
                r.oa_pdfs.append(r.oa_url)
                break
    return r


def _search_arxiv_by_title(title: str) -> str | None:
    if len(_norm_title(title)) < 15:            # 太短的标题（"Editorial"）搜出来全是噪声
        return None
    q = re.sub(r"[,:|()]", " ", title)
    data = _get(f"{OPENALEX}/works", search=q, filter=f"locations.source.id:{ARXIV_SOURCE}", per_page=5)
    for work in (data or {}).get("results") or []:
        if title_similar(title, work.get("title") or "") < TITLE_MATCH:
            continue
        for loc in work.get("locations") or []:
            aid = _arxiv_id_from_url(loc.get("landing_page_url")) or _arxiv_id_from_url(loc.get("pdf_url"))
            if aid:
                return aid
    return None


@lru_cache(maxsize=512)
def resolve_doi(doi: str) -> DoiResult:
    """DOI → DoiResult。DOI 两家都查不到抛 DoiNotFound；网络错误照常抛 requests 的异常。"""
    doi = doi.lower()
    m = ARXIV_DOI_RE.match(doi)
    if m:
        return DoiResult(doi=doi, arxiv_id=m.group(1), via="arxiv-doi")

    work = _get(f"{OPENALEX}/works/doi:{doi}")
    if work:
        r = _from_openalex(work, doi)
    else:
        msg = (_get(f"{CROSSREF}/works/{doi}") or {}).get("message")
        if not msg:
            raise DoiNotFound(doi)
        r = _from_crossref(msg, doi)

    if not r.arxiv_id and r.title:
        aid = _search_arxiv_by_title(r.title)
        if aid:
            r.arxiv_id, r.via = aid, "title-search"
    return r


if __name__ == "__main__":
    import json
    import sys
    for arg in sys.argv[1:]:
        d = parse_doi(arg)
        print(json.dumps(resolve_doi(d).to_dict() if d else {"error": "不是 DOI"}, ensure_ascii=False, indent=2))
