"""结果缓存：同一篇论文第二个人打开，秒开且零成本。

存哪：backend/cache/<key>.json，一篇一个文件，能直接打开看。
      重启不丢。目录已在 .gitignore 里。

key 是什么（这是这个文件唯一的设计决定）：
    arXiv ID + prompt 哈希 + 模型名 + 温度  →  sha256 取前 16 位

    为什么不只用 arXiv ID：改了 prompt 之后，同一篇论文返回的还是旧
    结果，消融实验就做不了了。把 prompt 内容哈希进 key，prompt 一改
    key 自动变，旧缓存自然失效，不用手动清。
"""

import hashlib
import json
import os

from config import MODEL, TEMPERATURE
from prompts import SYSTEM_PROMPT

CACHE_DIR = os.getenv("CACHE_DIR", "./cache")


def prompt_version() -> str:
    """prompt 内容的短哈希。埋点里也记它，方便按 prompt 版本分组统计。"""
    return hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:8]


def cache_key(arxiv_id: str) -> str:
    raw = f"{arxiv_id}|{prompt_version()}|{MODEL}|{TEMPERATURE}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cache_get(key: str) -> dict | None:
    path = os.path.join(CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cache_set(key: str, data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
