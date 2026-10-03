"""RAG 对比实验的检索端（D20）。线上不用，只给 eval_run.py 做对比。

D5 选了长上下文：整篇论文送进模型。这里做对照组——只送检索出来的一部分段落，
其余（prompt v3、模型、温度、评审员）全部不变，唯一的变量是"模型看到多少原文"。

    块：     D4 的原生段落就是块（[S3.p1] 一段一块），摘要也是一块。不另切，溯源 ID 不变。
    检索：   两种，都是每个字段一条英文查询 + 论文标题：
             bm25  纯 Python，不调 API、可复现（关键词检索的基线）
             dense OpenAI 向量检索（语义检索）；向量缓存到 eval/emb_cache.jsonl，重跑不花钱
    选块：   五个字段轮流各取下一个最相关的块，直到字数用完预算（占全文的比例）。
             轮流取保证每个字段都分到证据，不会被"结果"类段落占满。
    输出：   一个只含选中段落的 Paper，章节和顺序保持原文，再交给 paper_to_text；
             开头加一行说明"这是检索段落、不是全文"——真实 RAG 系统也会这样告诉模型。

    RAG=bm25  RAG_BUDGET=0.25 PROMPT=v3 python eval_run.py --tag rag_bm25_25
    RAG=dense RAG_BUDGET=0.25 PROMPT=v3 python eval_run.py --tag rag_dense_25
    python rag.py --recall      # 不调生成模型：检索出的段落覆盖了长上下文 v3 所引段落的多少
"""

import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

from models import Paper

# 每个字段的检索查询。论文是英文，查询也用英文；词取自各类段落里的高频说法。
FIELD_QUERIES = {
    "research_question": "problem challenge motivation question goal address important we study aim",
    "method": "method approach propose framework model architecture algorithm design module",
    "experiments": "experiment dataset benchmark setup evaluate evaluation baseline training implementation",
    "findings": "result outperform improve achieve performance accuracy show demonstrate gain",
    "limitations": "limitation future work however fail cannot restrict drawback assume leave",
}

_STOP = set("""a an the of to in on for and or with by from as at is are was were be been this that these
those it its we our their which can also than into via using use used based such""".split())
_TOKEN = re.compile(r"[a-z][a-z0-9\-]+")


def tokenize(text: str) -> list[str]:
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    return [_stem(t) for t in toks]


def _stem(t: str) -> str:
    """极简词干：去掉常见复数和词尾，让 results/result、evaluated/evaluate 能对上。"""
    for suf in ("ations", "ation", "ings", "ing", "ies", "es", "ed", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 4:
            return t[: -len(suf)]
    return t


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(len(d) for d in docs) / max(1, len(docs))
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for d, tf in zip(self.docs, self.tf):
            s, norm = 0.0, self.k1 * (1 - self.b + self.b * len(d) / self.avgdl)
            for t in query:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm)
            out.append(s)
        return out


def chunks(paper: Paper) -> list[tuple[str, str]]:
    """[(段落 ID, 用来检索的文字)]。段落文字前面带上章节标题——"Limitations" 这种标题本身就是最强的信号。"""
    out = [("abstract", paper.abstract)]
    for sec in paper.sections:
        for p in sec.paragraphs:
            out.append((p.id, f"{sec.title}\n{p.text}"))
    return out


EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "text-embedding-3-small")
EMB_CACHE = Path(__file__).parent / "eval" / "emb_cache.jsonl"
_emb: dict[str, list[float]] | None = None


def embed(texts: list[str]) -> list[list[float]]:
    """OpenAI 向量，按 (模型, 文本) 缓存到磁盘。"""
    global _emb
    if _emb is None:
        _emb = {}
        if EMB_CACHE.exists():
            for line in EMB_CACHE.read_text(encoding="utf-8").splitlines():
                d = json.loads(line)
                _emb[d["k"]] = d["v"]
    keys = [hashlib.sha256(f"{EMBED_MODEL}|{t}".encode()).hexdigest()[:20] for t in texts]
    todo = [(k, t) for k, t in zip(keys, texts) if k not in _emb]
    if todo:
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv()
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        with EMB_CACHE.open("a", encoding="utf-8") as f:
            for i in range(0, len(todo), 100):
                batch = todo[i:i + 100]
                # 超长段落截断：向量模型有输入上限，开头几千字已经足够判断主题
                # 空文本（有的论文摘要为空）接口会拒收，换成占位符；它和任何查询都不相关
                resp = client.embeddings.create(model=EMBED_MODEL,
                                                input=[t[:8000].strip() or "(empty)" for _, t in batch])
                for (k, _), d in zip(batch, resp.data):
                    _emb[k] = d.embedding
                    f.write(json.dumps({"k": k, "v": d.embedding}) + "\n")
    return [_emb[k] for k in keys]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def _field_scores(paper: Paper, cs: list[tuple[str, str]], method: str) -> dict[str, list[float]]:
    if method == "bm25":
        bm = BM25([tokenize(t) for _, t in cs])
        title = tokenize(paper.title)
        return {f: bm.scores(tokenize(q) + title) for f, q in FIELD_QUERIES.items()}
    if method == "dense":
        vecs = embed([t for _, t in cs])
        qvecs = embed([f"{paper.title}. {q}" for q in FIELD_QUERIES.values()])
        return {f: [_cos(qv, v) for v in vecs] for f, qv in zip(FIELD_QUERIES, qvecs)}
    raise ValueError(f"未知的检索方式：{method}（bm25 / dense）")


def retrieve(paper: Paper, budget: float, method: str = "bm25") -> set[str]:
    """按预算（占全文字数的比例）选出段落 ID。"""
    cs = chunks(paper)
    lengths = {cid: len(t) for cid, t in cs}
    limit = budget * sum(lengths.values())
    # 0 分的块不进候选：一个字段查不到东西时，宁可不取，也不拿无关段落占预算
    ranked = {f: [cs[i][0] for i in sorted(range(len(cs)), key=lambda i: -sc[i]) if sc[i] > 0]
              for f, sc in _field_scores(paper, cs, method).items()}

    chosen, used, pos = set(), 0, {f: 0 for f in ranked}
    while used < limit and any(pos[f] < len(ranked[f]) for f in ranked):
        for f in ranked:                        # 五个字段轮流取
            while pos[f] < len(ranked[f]) and ranked[f][pos[f]] in chosen:
                pos[f] += 1
            if pos[f] < len(ranked[f]) and used < limit:
                cid = ranked[f][pos[f]]
                chosen.add(cid)
                used += lengths[cid]
    return chosen


NOTE = "（检索模式：以下只包含与各部分相关的检索段落，不是全文。）"


def retrieved_paper(paper: Paper, budget: float, method: str = "bm25") -> Paper:
    """只保留检索到的段落，章节顺序不变；没有段落入选的章节整节去掉。"""
    keep = retrieve(paper, budget, method)
    sections = []
    for sec in paper.sections:
        ps = [p for p in sec.paragraphs if p.id in keep]
        if ps:
            sections.append(replace(sec, paragraphs=ps))
    abstract = paper.abstract if "abstract" in keep else "（未检索到）"
    return replace(paper, abstract=abstract, sections=sections)


def rag_text(paper: Paper, budget: float, method: str = "bm25") -> str:
    """检索模式下送给模型的文本：开头告诉模型这不是全文。"""
    from serialize import paper_to_text
    return NOTE + "\n\n" + paper_to_text(retrieved_paper(paper, budget, method))


def from_env() -> tuple[str, float] | None:
    """RAG=bm25 / dense 时返回 (检索方式, 预算比例)，否则 None（= 长上下文）。"""
    method = os.getenv("RAG", "").strip().lower()
    if not method:
        return None
    if method not in ("bm25", "dense"):
        raise ValueError(f"RAG={method} 不认识，只能是 bm25 或 dense")
    return method, float(os.getenv("RAG_BUDGET", "0.25"))


def recall_report(budgets=(0.1, 0.25, 0.5), methods=("bm25", "dense")):
    """检索召回：长上下文 v3 引用过的段落，有多少能被检索到。
    这是检索端的上限指标——检索不到的段落，RAG 的生成端无论如何引不到。
    （长上下文引的段落不是唯一正确答案，所以这是近似指标。）"""
    import random
    from eval_run import PAPERS_FILE, load_papers
    from snapshot import load_snapshot

    fields = list(FIELD_QUERIES)
    papers = [(c, p) for c, p in load_papers(PAPERS_FILE) if c != "abstract_only"]
    cited = {}
    for _, pid in papers:
        rec = json.loads((Path(__file__).parent / "eval" / "results_v3" / f"{pid}.json").read_text(encoding="utf-8"))
        cited[pid] = {f: {e["id"] for e in (rec["result"].get(f) or {}).get("evidence") or []} for f in fields}

    print(f"检索召回（长上下文 v3 所引段落中被检索到的比例，{len(papers)} 篇）")
    for b in budgets:
        line = f"  预算 {b:4.0%}"
        for m in ("random",) + tuple(methods):
            hit = tot = 0
            for _, pid in papers:
                paper = load_snapshot(pid)
                if m == "random":          # 随机选段落、同样字数，作对照
                    cs = chunks(paper)
                    random.Random(0).shuffle(cs)
                    limit, used, keep = b * sum(len(t) for _, t in cs), 0, set()
                    for cid, t in cs:
                        if used >= limit:
                            break
                        keep.add(cid)
                        used += len(t)
                else:
                    keep = retrieve(paper, b, m)
                for f in fields:
                    tot += len(cited[pid][f])
                    hit += len(cited[pid][f] & keep)
            line += f"   {m} {hit / tot * 100:5.1f}%"
        print(line)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--recall", action="store_true")
    ap.add_argument("--methods", nargs="+", default=["bm25", "dense"])
    args = ap.parse_args()
    if args.recall:
        recall_report(methods=tuple(args.methods))
