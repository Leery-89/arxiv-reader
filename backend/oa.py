"""DOI 论文 → Paper：没有 arXiv 版本时，下载开放获取 PDF 解析正文（D25，路线图 C）。

    paper = fetch_doi_paper("10.1126/science.aar6404")

  - 有 arXiv 版本 → 直接 fetcher.fetch_paper，和粘 arXiv ID 完全一样
  - 没有 → 按 doi.resolve_doi 给的开放获取 PDF 直链逐个试，第一个能解析出正文的用 pdf.parse_pdf
  - 都不行 → 只用 OpenAlex / Crossref 的标题摘要（source = abstract_only）

只用标了开放获取的副本（OpenAlex 的 is_oa / best_oa_location，Crossref 的 CC 许可），不碰付费墙、
不找"影子图书馆"。PDF 只在内存里解析，不落盘、不转发给用户——前端点出处时打开的是原链接。

下载的是第三方给的任意 URL，所以：
  - 只许 http/https，主机解析出的地址必须是公网（防 SSRF 打到 Railway 内网或云元数据地址）
  - 重定向手动跟，每一跳都重新检查主机；最多 4 跳
  - 流式读，超过 40 MB 就停；开头不是 %PDF 的（出版方的落地页、登录页）跳过
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

from doi import DoiResult, resolve_doi
from fetcher import HEADERS, apply_budget, fetch_paper
from models import Paper

MAX_BYTES = 40 * 1024 * 1024
MAX_REDIRECTS = 4
MAX_CANDIDATES = 3
TIMEOUT = 20


class UnsafeURL(Exception):
    pass


def _check_public(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise UnsafeURL(url)
    for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeURL(f"{u.hostname} → {ip}")


def download_pdf(url: str) -> bytes | None:
    """下载一个 PDF；不是 PDF、太大、不安全都返回 None（换下一个候选），网络错误照常抛。"""
    for _ in range(MAX_REDIRECTS + 1):
        try:
            _check_public(url)
        except (UnsafeURL, socket.gaierror):
            return None
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=False)
        if resp.is_redirect:
            url = urljoin(url, resp.headers.get("Location", ""))
            resp.close()
            continue
        if resp.status_code != 200:
            resp.close()
            return None
        buf = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            buf += chunk
            if len(buf) > MAX_BYTES:
                resp.close()
                return None
            if len(buf) >= 5 and not buf.startswith(b"%PDF"):
                resp.close()
                return None
        return bytes(buf) if buf.startswith(b"%PDF") else None
    return None


def paper_id(doi: str) -> str:
    """DOI 论文在缓存、埋点、前端里用的 ID。加前缀，和 arXiv ID 不会撞。"""
    return f"doi:{doi.lower()}"


def _abstract_paper(r: DoiResult) -> Paper:
    return Paper(arxiv_id=paper_id(r.doi), title=r.title or r.doi, abstract=r.abstract,
                 source="abstract_only", url=f"https://doi.org/{r.doi}")


def fetch_doi_paper(doi: str) -> Paper:
    r = resolve_doi(doi)
    if r.arxiv_id:
        return fetch_paper(r.arxiv_id)

    from pdf import parse_pdf          # 放这里：pdf.py import fetcher，免得 import 链变长
    for url in r.oa_pdfs[:MAX_CANDIDATES]:
        try:
            data = download_pdf(url)
        except requests.RequestException:
            continue
        if data is None:
            continue
        paper = parse_pdf(data, paper_id(r.doi), r.title, r.abstract)
        if paper.sections:
            paper.url = url
            return apply_budget(paper)
    return apply_budget(_abstract_paper(r))


if __name__ == "__main__":
    import sys
    for d in sys.argv[1:]:
        p = fetch_doi_paper(d)
        print(f"{d}  →  {p.arxiv_id}  来源 {p.source}  章节 {len(p.sections)}  字数 {p.char_count():,}")
        print(f"  标题 {p.title}\n  原文 {p.url or '(arXiv)'}")
        for s in p.sections[:6]:
            print(f"  [{s.kind[0]}] {s.title}  ({len(s.paragraphs)} 段)")
