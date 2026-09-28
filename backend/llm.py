"""调用模型。

DeepSeek 的接口和 OpenAI 兼容，所以直接用 openai 这个库，只换 base_url。
API key 从 .env 读，绝不写在代码里。
"""

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

from config import MODEL, TEMPERATURE
from prompts import build_messages

load_dotenv()   # 读 .env 到环境变量

_client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)

# 五个必须出现的字段，少一个就当模型输出不合格
REQUIRED_FIELDS = ["research_question", "method", "experiments", "findings", "limitations"]


def analyze(paper_text: str) -> dict:
    """把序列化后的论文送给模型，返回解析好的结果。

    返回值除了模型的 JSON，还额外挂了一个 "_meta"：
    token 用量和耗时。D11 做成本和延迟优化时全靠它。
    """
    t0 = time.time()

    resp = _client.chat.completions.create(
        model=MODEL,
        messages=build_messages(paper_text),
        response_format={"type": "json_object"},   # 保证返回合法 JSON
        temperature=TEMPERATURE,
    )

    elapsed = time.time() - t0
    raw = resp.choices[0].message.content

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        # json_object 模式下理论上不会发生，但发生了必须知道
        raise RuntimeError(f"模型返回的不是合法 JSON: {e}\n前 300 字: {raw[:300]}")

    missing = [f for f in REQUIRED_FIELDS if f not in result]
    if missing:
        raise RuntimeError(f"模型输出缺字段: {missing}")

    result["_meta"] = {
        "model": MODEL,
        "prompt_tokens": resp.usage.prompt_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "elapsed_s": round(elapsed, 2),
    }
    return result


def analyze_stream(paper_text: str):
    """流式版本。

    和 analyze() 发一模一样的请求，只多两个参数：
      stream=True                             → 模型生成一个 token 就推一个
      stream_options={"include_usage": True}  → 最后一片带上 token 用量

    这是一个生成器：一路 yield 字符串片段（模型输出的原文），
    最后 yield 一个 dict 作为 meta。调用方按类型区分。

    注意：中途拿到的片段拼起来是半截 JSON，这里不解析，
    解析交给前端的增量提取器（见 sidepanel.js）。
    """
    t0 = time.time()

    stream = _client.chat.completions.create(
        model=MODEL,
        messages=build_messages(paper_text),
        response_format={"type": "json_object"},
        temperature=TEMPERATURE,
        stream=True,
        stream_options={"include_usage": True},
    )

    usage = None
    for chunk in stream:
        if chunk.usage:                       # 只有最后一片有 usage
            usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content

    yield {
        "model": MODEL,
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "elapsed_s": round(time.time() - t0, 2),
    }
