"""列出你的 API key 能用的模型名（OpenAI / Anthropic / DeepSeek / Qwen）。不打印 key。

    python list_models.py
"""
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PROVIDERS = [
    ("OpenAI", "OPENAI_API_KEY", "https://api.openai.com/v1", ("gpt", "o1", "o3", "o4")),
    ("Anthropic", "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/", ("claude",)),
    ("DeepSeek", "DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), ("deepseek",)),
    ("Qwen", "DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", ("qwen",)),
]

for name, env, base, prefixes in PROVIDERS:
    key = os.getenv(env)
    if not key:
        print(f"── {name}：.env 里没有 {env}，跳过\n")
        continue
    try:
        headers = {"anthropic-version": "2023-06-01"} if name == "Anthropic" else None
        ids = sorted(m.id for m in OpenAI(api_key=key, base_url=base, default_headers=headers).models.list())
        ids = [i for i in ids if i.startswith(prefixes)] or ids
        print(f"── {name}（{len(ids)} 个）")
        for i in ids:
            print("   ", i)
    except Exception as e:
        print(f"── {name}：查询失败 {type(e).__name__}: {str(e)[:150]}")
    print()
