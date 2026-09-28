"""限流：公网之后任何拿到地址的人都能烧你的 API key，这是唯一的闸。

两层：
    每个 IP 每小时最多 IP_LIMIT 次
    全局每天最多 GLOBAL_LIMIT 次        ← 这是钱包的底线

只对真正调模型的请求计数——缓存命中不花钱，不算。

内存实现，重启清零。v1 够用；要跨实例就换 Redis。
"""

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

IP_LIMIT = int(os.getenv("RATE_IP_PER_HOUR", "20"))
GLOBAL_LIMIT = int(os.getenv("RATE_GLOBAL_PER_DAY", "300"))

_per_ip: dict[str, deque] = defaultdict(deque)
_global: deque = deque()


def client_ip(request: Request) -> str:
    # Railway / 大多数平台把真实 IP 放在这个头里；没有就用直连地址
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def check(request: Request) -> None:
    """超限直接抛 429，调用方不用处理。"""
    now = time.time()
    ip = client_ip(request)

    q = _per_ip[ip]
    while q and q[0] < now - 3600:
        q.popleft()
    while _global and _global[0] < now - 86400:
        _global.popleft()

    if len(_global) >= GLOBAL_LIMIT:
        raise HTTPException(429, "今日总额度已用完，明天再来")
    if len(q) >= IP_LIMIT:
        raise HTTPException(429, f"每小时最多 {IP_LIMIT} 次，稍后再试")

    q.append(now)
    _global.append(now)


def snapshot() -> dict:
    """给 /health 看的：现在用了多少"""
    now = time.time()
    return {
        "global_today": sum(1 for t in _global if t >= now - 86400),
        "global_limit": GLOBAL_LIMIT,
        "ips_active": sum(1 for q in _per_ip.values() if q and q[-1] >= now - 3600),
    }
