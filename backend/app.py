"""后端服务。

    uvicorn app:app --reload --port 8000

接口：
    GET  /health              活着没
    GET  /resolve?q=<DOI>     期刊 DOI → arXiv 版本 / 开放获取链接 / 标题摘要（doi.py）
    POST /analyze             {"arxiv_id": "1706.03762"} → 结构化结果（一次返回）
    POST /analyze/stream      同上，SSE 流式

设计上四个决定：
  1. 接口是同步的。fetch_paper 和 analyze 都是阻塞调用，FastAPI 会把同步
     接口丢进线程池跑，v1 够用。
  2. 错误分两类：arXiv 那边的网络问题、模型那边的问题，都返回 502 并说明
     是哪一边——前端据此给不同提示。404（没 HTML）不是错误，fetch_paper
     内部已经降级了。
  3. CORS 先全开。插件的请求来源是 chrome-extension://<id>，开发期不确定 id，
     上线前收紧到具体的扩展 id。
  4. 缓存（D11）：命中时跳过取文和模型调用，直接回放。流式接口回放时把整个
     结果当一个 delta 推——前端的增量提取器一次就能解析完，不用改前端。
"""

import json
import os
import time

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import ratelimit
from cache import cache_get, cache_key, cache_set, prompt_version
from prompts import ACTIVE_PROMPT
from doi import DoiNotFound, parse_doi, resolve_doi
from fetcher import fetch_paper
from oa import fetch_doi_paper, paper_id
from llm import analyze, analyze_stream
from serialize import paper_to_text
from telemetry import log_request
from verify import check

app = FastAPI(title="arXiv Reader backend", version="0.1.0")

# 允许的来源。上线后设成 chrome-extension://<扩展id>，多个用逗号分隔。
# 没设就全开——开发期方便，但公网上限流才是真正的保护。
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    arxiv_id: str | None = None
    doi: str | None = None          # D25：没有 arXiv 版本的期刊论文，后端下载开放获取 PDF

    def key_id(self) -> str:
        if self.arxiv_id:
            return self.arxiv_id
        d = parse_doi(self.doi or "")
        if not d:
            raise HTTPException(status_code=400, detail="要给 arxiv_id 或合法的 doi")
        return paper_id(d)


def _fetch(req: AnalyzeRequest):
    """arXiv ID 走原来的链路；DOI 走 oa.fetch_doi_paper（有 arXiv 版本时内部也会转回 fetch_paper）。"""
    if req.arxiv_id:
        return fetch_paper(req.arxiv_id)
    try:
        return fetch_doi_paper(parse_doi(req.doi))
    except DoiNotFound:
        raise requests.RequestException("OpenAlex 和 Crossref 都查不到这个 DOI")


@app.get("/health")
def health():
    return {"ok": True, "prompt": ACTIVE_PROMPT, "rate": ratelimit.snapshot()}


@app.get("/resolve")
def resolve(q: str):
    """侧栏粘的不是 arXiv ID 而是 DOI 时先调这个。找到 arXiv 版本 → 前端拿 arxiv_id 照常分析；
    找不到 → 返回标题、摘要和开放获取链接，前端提示。不调模型，不计入限流。"""
    doi = parse_doi(q)
    if not doi:
        raise HTTPException(status_code=400, detail="没认出 DOI")
    try:
        return resolve_doi(doi).to_dict()
    except DoiNotFound:
        raise HTTPException(status_code=404, detail="OpenAlex 和 Crossref 都查不到这个 DOI")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"DOI 查询失败：{e}")


def _paper_meta(paper) -> dict:
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "source": paper.source,                       # "html" | "pdf" | "abstract_only"
        "url": paper.url,                             # DOI 论文的开放获取 PDF（D25）；arXiv 论文为空
        "truncated_sections": paper.truncated_sections,
    }


def _log(paper_meta: dict, meta: dict, verify: dict, cache_hit: bool) -> None:
    """埋点一行。字段对应 stats.py 里统计的那些。"""
    log_request(
        arxiv_id=paper_meta["arxiv_id"],
        source=paper_meta["source"],
        truncated=bool(paper_meta.get("truncated_sections")),
        cache_hit=cache_hit,
        prompt_version=prompt_version(),
        prompt_tokens=meta.get("prompt_tokens"),
        completion_tokens=meta.get("completion_tokens"),
        elapsed_s=meta.get("elapsed_s"),
        quote_rate=verify.get("quote_rate"),
        inline_rate=verify.get("inline_rate"),
    )


# ───────────────────────── 一次返回 ─────────────────────────

@app.post("/analyze")
def analyze_paper(req: AnalyzeRequest, request: Request):
    key = cache_key(req.key_id())
    cached = cache_get(key)
    if cached:
        _log(cached["paper"], cached["result"]["_meta"], cached["result"]["_meta"]["verify"], cache_hit=True)
        cached["result"]["_meta"]["cache_hit"] = True
        return {**cached["paper"], "result": cached["result"]}

    ratelimit.check(request)          # 只对真正要调模型的请求计数
    try:
        paper = _fetch(req)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"取论文失败：{e}")

    text = paper_to_text(paper)
    try:
        result = analyze(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"模型调用失败：{e}")

    result["_meta"]["verify"] = check(paper, result)
    result["_meta"]["cache_hit"] = False

    paper_meta = _paper_meta(paper)
    cache_set(key, {"paper": paper_meta, "result": result})
    _log(paper_meta, result["_meta"], result["_meta"]["verify"], cache_hit=False)

    return {**paper_meta, "result": result}


# ───────────────────────── 流式 ─────────────────────────

def sse(event: str, data) -> str:
    """拼一条 SSE 消息。格式是固定的：event 行、data 行、空行。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/analyze/stream")
def analyze_paper_stream(req: AnalyzeRequest, request: Request):
    """事件序列：
        status {stage}        fetching → thinking
        paper  {...}          论文元信息，前端可以先显示来源提示
        delta  {t}  × N       模型输出片段（缓存命中时只有一片，是整个结果）
        meta   {...}          token 用量、耗时、cache_hit
        verify {...}          校验结果
        done   {}
    出错：error {detail}
    """
    try:
        key_id = req.key_id()
    except HTTPException as e:
        detail = e.detail
        return StreamingResponse(iter([sse("error", {"detail": detail})]), media_type="text/event-stream")

    def gen():
        key = cache_key(key_id)
        cached = cache_get(key)

        # ── 命中：回放 ──
        if cached:
            t0 = time.time()
            result = cached["result"]
            meta = {**result["_meta"], "cache_hit": True, "elapsed_s": 0.0}
            verify = result["_meta"].get("verify", {})
            body = {k: v for k, v in result.items() if k != "_meta"}

            yield sse("paper", cached["paper"])
            yield sse("delta", {"t": json.dumps(body, ensure_ascii=False)})
            meta["elapsed_s"] = round(time.time() - t0, 3)
            yield sse("meta", meta)
            yield sse("verify", verify)
            yield sse("done", {})
            _log(cached["paper"], meta, verify, cache_hit=True)
            return

        # ── 未命中：先过限流，再正常走一遍 ──
        try:
            ratelimit.check(request)
        except HTTPException as e:
            yield sse("error", {"detail": e.detail})
            return

        yield sse("status", {"stage": "fetching"})
        try:
            paper = _fetch(req)
        except requests.RequestException as e:
            yield sse("error", {"detail": f"取论文失败：{e}"})
            return

        paper_meta = _paper_meta(paper)
        yield sse("paper", paper_meta)

        yield sse("status", {"stage": "thinking"})
        text = paper_to_text(paper)
        buf: list[str] = []
        meta: dict = {}
        try:
            for piece in analyze_stream(text):
                if isinstance(piece, dict):
                    meta = {**piece, "cache_hit": False}
                    yield sse("meta", meta)
                else:
                    buf.append(piece)
                    yield sse("delta", {"t": piece})
        except Exception as e:
            yield sse("error", {"detail": f"模型调用失败：{e}"})
            return

        try:
            result = json.loads("".join(buf))
        except json.JSONDecodeError:
            yield sse("verify", {"error": "最终 JSON 无法解析"})
            yield sse("done", {})
            return

        verify = check(paper, result)
        yield sse("verify", verify)
        yield sse("done", {})

        # 流结束后再写缓存和日志，不拖慢响应
        result["_meta"] = {**meta, "verify": verify}
        cache_set(key, {"paper": paper_meta, "result": result})
        _log(paper_meta, meta, verify, cache_hit=False)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
