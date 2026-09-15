"""后端服务。

    uvicorn app:app --reload --port 8000

两个接口：
    GET  /health            活着没
    POST /analyze           {"arxiv_id": "1706.03762"} → 结构化结果

设计上三个决定：
  1. 接口是同步的。fetch_paper 和 analyze 都是阻塞调用，FastAPI 会把同步
     接口丢进线程池跑，v1 够用。D8 改流式时再动。
  2. 错误分两类：arXiv 那边的网络问题、模型那边的问题，都返回 502 并说明
     是哪一边——前端据此给不同提示。404（没 HTML）不是错误，fetch_paper
     内部已经降级了。
  3. CORS 先全开。插件的请求来源是 chrome-extension://<id>，开发期不确定 id，
     上线前收紧到具体的扩展 id。
"""

import json

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from fetcher import fetch_paper
from serialize import paper_to_text
from llm import analyze, analyze_stream

app = FastAPI(title="arXiv Reader backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # TODO(D12)：收紧到 chrome-extension://<扩展id>
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    arxiv_id: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/analyze")
def analyze_paper(req: AnalyzeRequest):
    # 第一段：取论文
    try:
        paper = fetch_paper(req.arxiv_id)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"arXiv 请求失败：{e}")

    # 第二段：调模型
    text = paper_to_text(paper)
    try:
        result = analyze(text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"模型调用失败：{e}")

    # 把前端需要的论文元信息一起带回去
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "source": paper.source,                       # "html" | "abstract_only"
        "truncated_sections": paper.truncated_sections,
        "result": result,
    }


# ───────────────────────── 流式接口（D8） ─────────────────────────

def sse(event: str, data) -> str:
    """拼一条 SSE 消息。格式是固定的：event 行、data 行、空行。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/analyze/stream")
def analyze_paper_stream(req: AnalyzeRequest):
    """同 /analyze，但用 SSE 一路推进度和模型输出片段。

    事件序列：
        status {stage: fetching}     开始取论文
        paper  {...元信息}           论文取到了，前端可以先显示来源提示
        status {stage: thinking}     开始调模型
        delta  {t: "片段"}  × N      模型输出，一片一片来
        meta   {token 用量、耗时}
        done   {}
    任何一步出错：
        error  {detail: "..."}
    """
    def gen():
        yield sse("status", {"stage": "fetching"})
        try:
            paper = fetch_paper(req.arxiv_id)
        except requests.RequestException as e:
            yield sse("error", {"detail": f"arXiv 请求失败：{e}"})
            return

        yield sse("paper", {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "source": paper.source,
            "truncated_sections": paper.truncated_sections,
        })

        yield sse("status", {"stage": "thinking"})
        text = paper_to_text(paper)
        try:
            for piece in analyze_stream(text):
                if isinstance(piece, dict):
                    yield sse("meta", piece)
                else:
                    yield sse("delta", {"t": piece})
        except Exception as e:
            yield sse("error", {"detail": f"模型调用失败：{e}"})
            return

        yield sse("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
