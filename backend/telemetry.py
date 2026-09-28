"""埋点：每次请求追加一行到 logs/requests.jsonl。

自己写，不接第三方 SDK——一是隐私，二是自己写的你才讲得清楚。
这些行就是以后简历上「平均成本 X、缓存命中率 Y、可溯源率 Z」的来源。

看统计：python stats.py
"""

import json
import os
import time

LOG_DIR = os.getenv("LOG_DIR", "./logs")
LOG_FILE = os.path.join(LOG_DIR, "requests.jsonl")


def log_request(**fields) -> None:
    """记一行。传什么记什么，外加时间戳。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    fields = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **fields}
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(fields, ensure_ascii=False) + "\n")
