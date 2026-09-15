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

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fetcher import fetch_paper
from serialize import paper_to_text
from llm import analyze

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
