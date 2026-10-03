"""oa.py 的小测试：DOI 论文走开放获取 PDF。不联网（resolve / 下载 / 解析都换成假的）。

    python tests/test_oa.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import oa
from doi import DoiResult
from models import Paper, Paragraph, Section


def setup(result: DoiResult, pdfs: dict):
    oa.resolve_doi = lambda d: result
    oa.download_pdf = lambda url: pdfs.get(url)
    calls = []

    def fake_fetch_paper(aid):
        calls.append(aid)
        return Paper(arxiv_id=aid, title="from arxiv", abstract="")
    oa.fetch_paper = fake_fetch_paper
    return calls


def test_arxiv_version_goes_to_normal_path():
    calls = setup(DoiResult(doi="10.1/x", arxiv_id="1512.03385"), {})
    p = oa.fetch_doi_paper("10.1/x")
    assert calls == ["1512.03385"] and p.arxiv_id == "1512.03385"


def test_first_parsable_pdf_wins():
    import pdf
    good = Paper(arxiv_id="", title="", abstract="", sections=[
        Section(level=1, title="1 Introduction", paragraphs=[Paragraph(id="pg1.b1", text="Hello")])])
    pdf.parse_pdf = lambda data, pid, title, abstract: (
        Paper(arxiv_id=pid, title=title, abstract=abstract) if data == b"%PDF-empty"
        else Paper(**{**good.__dict__, "arxiv_id": pid, "title": title, "abstract": abstract}))
    r = DoiResult(doi="10.1126/science.aar6404", title="AlphaZero", abstract="abs",
                  oa_pdfs=["https://a/1.pdf", "https://b/2.pdf", "https://c/3.pdf"])
    setup(r, {"https://a/1.pdf": None, "https://b/2.pdf": b"%PDF-empty", "https://c/3.pdf": b"%PDF-good"})
    p = oa.fetch_doi_paper(r.doi)
    assert p.arxiv_id == "doi:10.1126/science.aar6404"
    assert p.url == "https://c/3.pdf"                  # 1 下载不了、2 解析不出正文，用 3
    assert p.title == "AlphaZero" and p.sections


def test_no_pdf_falls_back_to_abstract():
    setup(DoiResult(doi="10.1038/nature14539", title="Deep learning", abstract="x"), {})
    p = oa.fetch_doi_paper("10.1038/nature14539")
    assert (p.source, p.abstract, p.url) == ("abstract_only", "x", "https://doi.org/10.1038/nature14539")


def test_private_hosts_rejected():
    for url in ["http://127.0.0.1/a.pdf", "http://169.254.169.254/latest", "file:///etc/passwd",
                "http://10.0.0.5/x.pdf", "ftp://example.com/x.pdf"]:
        try:
            oa._check_public(url)
            assert False, url
        except oa.UnsafeURL:
            pass


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
