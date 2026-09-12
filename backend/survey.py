"""调研脚本：arXiv HTML 版的覆盖率和页面大小。

跑法：
    python survey.py

回答两个问题：
    1. 有 HTML 版的论文占多少？按年份看趋势  → 决定降级路径有多重要
    2. 有 HTML 的页面有多大？                 → 决定预算逻辑值不值得精写

⚠️ 这是你的第一个脚本。建议的做法：
    先什么都别改，直接跑一遍，看它工作。
    然后再去填 make_ids() 和 summarize() 两个函数。
    每改一点就跑一次 —— 这个习惯比任何语法都重要。
"""

import csv
import statistics
import time

import requests


# ─────────────────────────────────────────────────────────────
# 1. 造 ID   ← 你来写
# ─────────────────────────────────────────────────────────────

def make_ids() -> list[str]:
    """生成一批 arXiv ID，覆盖 2012–2026 年。

    思路：每年取 1 月和 7 月，每个月取前两个编号（.0001 和 .0002）。
    因为编号是顺序发的，每月的前几个一定存在。

    ⚠️ 2015 年之前是 4 位序号（"1207.0001"），
       2015 年起是 5 位序号（"1501.00001"）。要分情况拼。

    提示：
      - 年份取后两位：2017 → "17"，用 f"{year % 100:02d}"
      - 月份补零：1 → "01"，用 f"{month:02d}"
      - 序号补零：1 → "0001" 或 "00001"，同样是 :04d 或 :05d

    现在这里是一个写死的短列表，让脚本能先跑起来。
    跑通一次之后，把它换成真正的三层循环。
    """
    ids=[]
    for year in range(2012,2027):
         for month in [1,7]:
            for n in [1,2]:
                yy=f"{year%100:02d}"
                mm=f"{month:02d}"
                if year < 2015:
                    nn=f"{n:04d}"
                else:
                    nn=f"{n:05d}"
                ids.append(f"{yy}{mm}.{nn}")
    return  ids


# ─────────────────────────────────────────────────────────────
# 2. 探测一个 ID   ← 已写好，读懂它
# ─────────────────────────────────────────────────────────────

HEADERS = {
    # 裸 requests 的默认 User-Agent 容易被拒，装成浏览器
    "User-Agent": "Mozilla/5.0 (Macintosh) arxiv-reader-survey/0.1",
}


def probe(arxiv_id: str) -> dict:
    """请求一个 ID 的 HTML 页，返回一条记录。

    这个函数只做一件事：探测。不 sleep、不打印、不写文件。
    那些是主流程的事 —— 一个函数只干一件事，才好单独测。
    """
    url = f"https://arxiv.org/html/{arxiv_id}"
    year = 2000 + int(arxiv_id[:2])   # "1706.03762"[:2] → "17" → 2017

    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        status = resp.status_code
        length = len(resp.text) if status == 200 else 0
    except requests.RequestException as e:
        # 超时、断网等 —— 记成 -1，别让一次失败炸掉整个循环
        print(f"    网络错误: {e}")
        status, length = -1, 0

    return {"id": arxiv_id, "year": year, "status": status, "length": length}


# ─────────────────────────────────────────────────────────────
# 3. 汇总   ← 你来写
# ─────────────────────────────────────────────────────────────

def summarize(results: list[dict]) -> None:
    """打印两组数字：每年的覆盖率；200 响应的长度分布。

    第一组的思路：
      用一个字典按年份分组。key 是年份，value 是 [总数, 成功数]。
      遍历 results，每条往对应年份的两个计数器上加。
      最后按年份排序打印，比如：
          2017   2/2   100%
          2012   0/2     0%

    第二组的思路：
      把 status == 200 的 length 收进一个列表，
      用 min()、statistics.median()、max() 算三个数打印。
      列表可能是空的（一个 200 都没有），要处理这种情况。

    现在只是把原始记录打印出来。跑通后换成真正的统计。
    """
    # 第一组：按年份分组算覆盖率
    by_year = {}
    for r in results:
        y = r["year"]
        if y not in by_year:
            by_year[y] = [0, 0]      # [总数, 200 的数]
        by_year[y][0] += 1
        if r["status"] == 200:
            by_year[y][1] += 1

    for y in sorted(by_year):
        total, ok = by_year[y]
        print(f"{y}  {ok}/{total}  {ok / total:.0%}")

    # 第二组：200 响应的长度分布
    lengths = [r["length"] for r in results if r["status"] == 200]
    if lengths:
        print(f"最小 {min(lengths):,}  中位 {statistics.median(lengths):,.0f}  最大 {max(lengths):,}")   # min / median / max，你来填
    else:
        print("没有任何 200 响应")


# ─────────────────────────────────────────────────────────────
# 4. 主流程   ← 已写好
# ─────────────────────────────────────────────────────────────

def main() -> None:
    ids = make_ids()
    print(f"共 {len(ids)} 个 ID，每个间隔 3 秒，预计 {len(ids) * 3 // 60} 分钟\n")

    results = []
    for i, arxiv_id in enumerate(ids, start=1):
        r = probe(arxiv_id)
        results.append(r)
        print(f"[{i:>2}/{len(ids)}] {r['id']:<14} {r['status']:>4}  {r['length']:>8,}")
        time.sleep(3)

    # 存成 CSV，下次不用重跑
    with open("survey_results.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "year", "status", "length"])
        writer.writeheader()
        writer.writerows(results)
    print("\n已保存 survey_results.csv\n")

    summarize(results)


# 这一行的意思：只有直接运行这个文件时才执行 main()，
# 被别的文件 import 时不执行。所有脚本都这么写，先照抄，以后就懂了。
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        with open("survey_results.csv") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["year"] = int(r["year"])
            r["status"] = int(r["status"])
            r["length"] = int(r["length"])
        summarize(rows)
    else:
        main()
