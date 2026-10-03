"""doi.py 的小测试。OpenAlex / Crossref 的响应用手写的最小 JSON 代替，不联网。

    python tests/test_doi.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import doi


def fake(routes: dict):
    """把 doi._get 换成查表：URL 包含 key 就返回 value；记下调用过的 URL。"""
    calls = []

    def _get(url, **params):
        calls.append((url, params))
        for k, v in routes.items():
            if k in url:
                return v
        return None
    doi._get = _get
    doi.resolve_doi.cache_clear()
    return calls


def test_parse_doi_forms():
    assert doi.parse_doi("10.1038/nature14539") == "10.1038/nature14539"
    assert doi.parse_doi("doi: 10.1038/Nature14539.") == "10.1038/nature14539"
    assert doi.parse_doi("https://doi.org/10.1103/PhysRevB.98.155314") == "10.1103/physrevb.98.155314"
    assert doi.parse_doi("1706.03762") is None


def test_arxiv_doi_needs_no_request():
    calls = fake({})
    r = doi.resolve_doi("10.48550/arxiv.1706.03762")
    assert (r.arxiv_id, r.via, calls) == ("1706.03762", "arxiv-doi", [])


def test_openalex_arxiv_location():
    fake({"works/doi:": {
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "abstract_inverted_index": {"The": [0], "dominant": [1], "models": [2]},
        "primary_location": {"source": {"display_name": "NeurIPS"}},
        "locations": [
            {"landing_page_url": "https://papers.nips.cc/x", "pdf_url": None},
            {"landing_page_url": "https://arxiv.org/abs/1706.03762v7", "source": {"display_name": "arXiv"}},
        ],
        "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/1706.03762"},
    }})
    r = doi.resolve_doi("10.5555/3295222.3295349")
    assert r.arxiv_id == "1706.03762" and r.via == "openalex"
    assert r.abstract == "The dominant models"
    assert (r.venue, r.year) == ("NeurIPS", 2017)


def test_title_search_when_not_merged():
    """期刊版和预印本在 OpenAlex 里是两条：按标题搜 arXiv 来源，相似度够才认"""
    calls = fake({
        "works/doi:": {"title": "Deep learning", "locations": [], "best_oa_location": None},
        "/works": {"results": []},
    })
    r = doi.resolve_doi("10.1038/nature14539")
    assert r.arxiv_id is None
    assert not any(u.endswith("/works") for u, _ in calls)          # 标题太短，不搜

    fake({
        "works/doi:": {"title": "Majorana zero modes in InAs-Al hybrid nanowire devices",
                       "locations": [], "best_oa_location": None},
        "/works": {"results": [
            {"title": "Majorana zero modes in a quantum dot", "locations": [
                {"landing_page_url": "https://arxiv.org/abs/1111.11111"}]},
            {"title": "Majorana Zero Modes in InAs–Al Hybrid Nanowire Devices", "locations": [
                {"landing_page_url": "http://arxiv.org/abs/2401.09549"}]},
        ]},
    })
    r = doi.resolve_doi("10.1038/s41586-024-00000-0")
    assert (r.arxiv_id, r.via) == ("2401.09549", "title-search")


def test_crossref_fallback_with_preprint_relation():
    fake({
        "works/doi:": None,
        "api.crossref.org": {"message": {
            "title": ["Some Journal Paper"],
            "abstract": "<jats:p>Abstract We show <jats:italic>x</jats:italic>.</jats:p>",
            "issued": {"date-parts": [[2021, 3]]},
            "container-title": ["Phys. Rev. B"],
            "relation": {"has-preprint": [{"id-type": "doi", "id": "10.48550/arXiv.2101.00001"}]},
        }},
    })
    r = doi.resolve_doi("10.1103/physrevb.1.1")
    assert (r.arxiv_id, r.via, r.year) == ("2101.00001", "crossref-preprint", 2021)
    assert r.abstract == "We show x ."


def test_not_found_anywhere():
    fake({})
    try:
        doi.resolve_doi("10.9999/nope")
        assert False, "应该抛 DoiNotFound"
    except doi.DoiNotFound:
        pass


def test_no_arxiv_but_open_access():
    fake({
        "works/doi:": {"title": "A paper only in a journal with a long title",
                       "locations": [{"landing_page_url": "https://journal.org/x"}],
                       "best_oa_location": {"pdf_url": None, "landing_page_url": "https://europepmc.org/x"}},
        "/works": {"results": []},
    })
    r = doi.resolve_doi("10.1/x")
    assert r.arxiv_id is None and r.oa_url == "https://europepmc.org/x"


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
