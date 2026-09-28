"""汇总埋点：python stats.py

读 logs/requests.jsonl，打印你简历上要用的那几个数。
"""

import json
import os
import statistics
from collections import Counter

from telemetry import LOG_FILE

# ⚠️ 按 DeepSeek 官网当前定价填（元 / 百万 token）。
#    定价会变，写简历前去 platform.deepseek.com 核一遍再引用。
PRICE_IN_PER_M = 1.0
PRICE_OUT_PER_M = 2.0


def main() -> None:
    if not os.path.exists(LOG_FILE):
        print("还没有日志。先跑几次分析。")
        return

    with open(LOG_FILE, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    total = len(rows)
    hits = [r for r in rows if r.get("cache_hit")]
    misses = [r for r in rows if not r.get("cache_hit")]
    print(f"请求总数    {total}")
    print(f"缓存命中    {len(hits)}/{total} = {len(hits)/total:.0%}" if total else "")
    print(f"来源分布    {dict(Counter(r.get('source') for r in rows))}")
    print(f"prompt 版本 {dict(Counter(r.get('prompt_version') for r in rows))}")
    print()

    if not misses:
        print("（全部命中缓存，没有模型调用数据）")
        return

    def col(key):
        return [r[key] for r in misses if r.get(key) is not None]

    p_in, p_out, lat = col("prompt_tokens"), col("completion_tokens"), col("elapsed_s")
    cost = [(i * PRICE_IN_PER_M + o * PRICE_OUT_PER_M) / 1_000_000 for i, o in zip(p_in, p_out)]

    print("—— 未命中缓存的请求（真正调了模型的）——")
    print(f"  次数          {len(misses)}")
    if p_in:
        print(f"  输入 token    均值 {statistics.mean(p_in):,.0f}   中位 {statistics.median(p_in):,.0f}")
        print(f"  输出 token    均值 {statistics.mean(p_out):,.0f}   中位 {statistics.median(p_out):,.0f}")
    if cost:
        print(f"  单次成本      均值 {statistics.mean(cost):.4f} 元   最大 {max(cost):.4f} 元")
    if lat:
        print(f"  耗时          均值 {statistics.mean(lat):.1f}s   P95 {sorted(lat)[int(len(lat)*0.95)-1 if len(lat)>1 else 0]:.1f}s")

    q = col("quote_rate")
    i = col("inline_rate")
    if q:
        print(f"  引文命中率    均值 {statistics.mean(q):.1%}   最低 {min(q):.1%}")
    if i:
        print(f"  内嵌 ID 合规  均值 {statistics.mean(i):.1%}   最低 {min(i):.1%}")

    trunc = sum(1 for r in misses if r.get("truncated"))
    print(f"  触发截断      {trunc}/{len(misses)}")


if __name__ == "__main__":
    main()
