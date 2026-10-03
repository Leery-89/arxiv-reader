"""RAG 检索端（D20）的小测试。只测 BM25，不联网。

    python tests/test_rag.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Paper, Paragraph, Section
from rag import NOTE, chunks, rag_text, retrieve


def _paper():
    def sec(title, texts, prefix):
        return Section(level=1, title=title,
                       paragraphs=[Paragraph(id=f"{prefix}.p{i}", text=t) for i, t in enumerate(texts, 1)])
    return Paper(arxiv_id="0000.00001", title="SparseFormer",
                 abstract="We study sparse attention.",
                 sections=[sec("Introduction", ["Long documents are a challenge for attention.", "Filler text " * 20], "S1"),
                           sec("Method", ["We propose a sparse attention architecture.", "More filler " * 20], "S2"),
                           sec("Limitations", ["A limitation is that we only test English.", "Even more filler " * 20], "S3")])


def test_budget_respected_and_ids_real():
    p = _paper()
    total = sum(len(t) for _, t in chunks(p))
    keep = retrieve(p, 0.3)
    ids = {cid for cid, _ in chunks(p)}
    assert keep <= ids
    # 轮流取时最后一块可能超出，但不会超过预算加一个最长块
    assert sum(len(t) for cid, t in chunks(p) if cid in keep) <= 0.3 * total + max(len(t) for _, t in chunks(p))


def test_limitations_paragraph_found():
    # "Limitations" 章节标题 + limitation 关键词，应该排在填充段落前面
    assert "S3.p1" in retrieve(_paper(), 0.3)


def test_rag_text_tells_model_it_is_partial():
    text = rag_text(_paper(), 0.3)
    assert text.startswith(NOTE)
    assert "Filler text" not in text or "Even more filler" not in text   # 确实丢了东西


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"✓ {name}")
