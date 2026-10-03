"""PDF 解析评测：以 HTML 版为标准答案，量 PDF 抽出来的正文丢了多少、多了多少（路线图 A.3）。

评测集里有 HTML 的 31 篇同时也有 PDF，所以每篇都有现成的标准答案（eval/papers/ 的快照）。
只看正文（摘要两边都从 meta 取，不比）：
  - 正文召回：HTML 正文（去掉公式）的连续 5 词组合，有多少出现在 PDF 正文里
  - 正文精确：PDF 正文的 5 词组合，有多少出现在 HTML 正文里（低 = 页眉页脚、参考文献、表格碎片混进来了）
  - 数字召回（正文）：HTML 正文里的数字，PDF 正文里有没有——数字是 harness 和评审最依赖的
  - 数字召回（公式）：HTML 公式（LaTeX）里的数字，PDF 正文里有没有——公式抽取质量的粗代理
  - 章节召回：HTML 的章节标题（去编号）有多少被 PDF 识别成了章节
另外把没有 HTML 的几篇也跑一遍，看 PDF 能捞回多少（这是做 PDF 的本来目的）。

    cd backend
    python pdf_bench.py                 # PDF 缓存在 eval/pdf_cache/（不进 Git）
    python pdf_bench.py --by-paper
"""

import argparse
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from fetcher import apply_budget
from pdf import fetch_pdf, parse_pdf
from snapshot import load_snapshot

EVAL_DIR = Path(__file__).parent / "eval"
PAPERS_FILE = EVAL_DIR / "papers.txt"
PDF_CACHE = EVAL_DIR / "pdf_cache"
MATH_RE = re.compile(r"\$[^$]*\$")
NUM_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def load_papers(path: Path) -> list[tuple[str, str]]:
    """同 eval_run.load_papers；不 import eval_run，免得为了读清单要配 API key。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" in line:
            cat, pid = (x.strip() for x in line.split(":", 1))
            if cat and pid:
                out.append((cat, pid))
    return out


def pdf_bytes(pid: str) -> bytes | None:
    f = PDF_CACHE / f"{pid.replace('/', '_')}.pdf"
    if f.exists():
        return f.read_bytes()
    data = fetch_pdf(pid)
    if data:
        PDF_CACHE.mkdir(exist_ok=True)
        f.write_bytes(data)
        time.sleep(1)                     # 对 arxiv.org 客气一点
    return data


def body_text(paper, strip_math: bool) -> str:
    """正文 + 附录都比：两边对"哪节算附录"的判断常常不同（Nature 的 Methods、补充材料），
    只比 kind == body 会把这种分歧算成丢失。预算的差别在 main() 里对齐。"""
    t = " ".join(g.text for s in paper.sections for g in s.paragraphs)
    return MATH_RE.sub(" ", t) if strip_math else t


def grams(text: str, n: int = 5) -> set:
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def num_recall(nums: list[str], pool: Counter) -> float | None:
    if not nums:
        return None
    need = Counter(nums)
    return sum(min(c, pool[n]) for n, c in need.items()) / sum(need.values())


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z]+", " ", re.sub(r"^[A-Z0-9.]+\s+", "", t).lower()).strip()


def compare(html, pdfp) -> dict:
    h_txt, p_txt = body_text(html, True), body_text(pdfp, False)
    hg, pg = grams(h_txt), grams(p_txt)
    pool = Counter(NUM_RE.findall(p_txt))
    h_math = " ".join(MATH_RE.findall(body_text(html, False)))
    h_titles = {norm_title(s.title) for s in html.sections if s.paragraphs} - {""}
    p_titles = {norm_title(s.title) for s in pdfp.sections}
    return {
        "recall": len(hg & pg) / len(hg) if hg else None,
        "precision": len(hg & pg) / len(pg) if pg else None,
        "num_text": num_recall(NUM_RE.findall(h_txt), pool),
        "num_math": num_recall(NUM_RE.findall(h_math), pool),
        "sections": len(h_titles & p_titles) / len(h_titles) if h_titles else None,
        "chars": (len(h_txt), len(p_txt)),
    }


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-paper", action="store_true")
    args = ap.parse_args()

    rows, rescued = [], []
    for cat, pid in load_papers(PAPERS_FILE):
        t0 = time.time()
        try:
            data = pdf_bytes(pid)
        except Exception as e:
            print(f"  {pid}: 下载失败 {str(e)[:80]}")
            continue
        html = load_snapshot(pid)
        if cat == "abstract_only" or html is None or html.source != "html":
            if data is None:
                rescued.append((pid, None))
                continue
            p = parse_pdf(data, pid)
            rescued.append((pid, p))
            continue
        if data is None:
            print(f"  {pid}: 没有 PDF")
            continue
        p = parse_pdf(data, pid, html.title, html.abstract)
        if html.truncated_sections:       # HTML 快照被预算截过，PDF 也截；没截过就两边都比全文
            p = apply_budget(p)
        r = compare(html, p)
        r.update(cat=cat, pid=pid, secs=time.time() - t0, n_sec=len(p.sections))
        rows.append(r)

    keys = [("recall", "正文召回"), ("precision", "正文精确"), ("num_text", "数字召回·正文"),
            ("num_math", "数字召回·公式"), ("sections", "章节召回")]
    print(f"\n══ PDF 解析 vs HTML（PyMuPDF 基线，{len(rows)} 篇）══")
    print(f"  {'':10}" + "".join(f"{name:>12}" for _, name in keys))
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)
    for cat, rs in sorted(by_cat.items()) + [("全部", rows)]:
        print(f"  {cat:10}" + "".join(f"{mean(r[k] for r in rs) * 100:11.1f}%" for k, _ in keys))
    print(f"  平均每篇解析耗时（含下载）{mean(r['secs'] for r in rows):.1f} 秒")

    if args.by_paper:
        print("\n  逐篇：")
        for r in sorted(rows, key=lambda r: r["recall"] or 0):
            h, p = r["chars"]
            print(f"    {r['pid']:12} {r['cat']:9} 章节 {r['n_sec']:3}  字 {h // 1000:4}k → {p // 1000:4}k  "
                  + "  ".join(f"{name} {(r[k] or 0) * 100:5.1f}%" for k, name in keys))

    print(f"\n══ 没有 HTML 的论文：PDF 能捞回多少（{len(rescued)} 篇）══")
    for pid, p in rescued:
        if p is None:
            print(f"    {pid:12} 没有 PDF")
        else:
            print(f"    {pid:12} 章节 {len(p.sections):3}  正文 {p.char_count() // 1000:4}k 字  "
                  f"前几个章节：{' / '.join(s.title[:24] for s in p.sections[:4])}")


if __name__ == "__main__":
    main()
