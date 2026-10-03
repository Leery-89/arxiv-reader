"""解析器版本进缓存 key 和快照（D18）。不联网、不调模型。

    python tests/test_versions.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cache
from models import Paper, Paragraph, Section
from snapshot import load_snapshot, save_snapshot, snapshot_parser_version


def test_cache_key_changes_with_parser_version():
    old = cache.PARSER_VERSION
    try:
        k1 = cache.cache_key("1706.03762")
        cache.PARSER_VERSION = old + 1
        k2 = cache.cache_key("1706.03762")
    finally:
        cache.PARSER_VERSION = old
    assert k1 != k2


def test_snapshot_roundtrip_keeps_paper_and_records_version():
    paper = Paper(arxiv_id="0000.00001", title="t", abstract="a",
                  sections=[Section(level=1, title="Intro", paragraphs=[Paragraph(id="S1.p1", text="x")])])
    with tempfile.TemporaryDirectory() as d:
        save_snapshot(paper, Path(d))
        assert load_snapshot("0000.00001", Path(d)) == paper
        assert snapshot_parser_version("0000.00001", Path(d)) == cache.PARSER_VERSION


def test_old_snapshot_without_version_is_v1():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "0000.00002.json").write_text(json.dumps(
            {"arxiv_id": "0000.00002", "title": "t", "abstract": "a", "sections": []}), encoding="utf-8")
        assert load_snapshot("0000.00002", Path(d)).title == "t"
        assert snapshot_parser_version("0000.00002", Path(d)) == 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✓ {name}")
