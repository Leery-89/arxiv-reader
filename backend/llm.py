"""调用模型。

DeepSeek 的接口和 OpenAI 兼容，所以直接用 openai 这个库，只换 base_url。
API key 从 .env 读，绝不写在代码里。
"""

import json
import os
import time

from dotenv import load_dotenv
from openai import OpenAI

from prompts import build_messages

load_dotenv()   # 读 .env 到环境变量

_client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)

MODEL = "deepseek-chat"

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
        temperature=0,                              # 评测需要可复现，先用 0
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
