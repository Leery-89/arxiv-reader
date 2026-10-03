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
    python pdf_bench.py --engine docling --tables     # D23：版面模型对比（要先 pip install docling）
    python pdf_bench.py --engine docling --ids 1706.03762,2010.11929   # 先跑两篇看速度

D23 加的三项（公式、表格是这一轮要回答的问题）：
  - 公式 LaTeX 召回：HTML 里较长的公式（≥20 字符，近似独立公式）去空格、括号后的字符 3-gram，
    在 PDF 抽出的 $...$ 里出现的比例。PyMuPDF 不出 LaTeX，这项按定义是 0
  - 表格单元格召回（--tables）：HTML 表格（D22 起有）的单元格，有多少原样作为 PDF 表格的单元格出现
  - 表格数字召回（--tables）：HTML 表格里的数字在 PDF 全文里有没有——两种引擎都能公平比
  表格的标准答案要现抓 HTML（快照是 D22 之前存的，没有表格），抓过的缓存在 eval/html_cache/
"""

import argparse
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from fetcher import apply_budget, fetch_html, parse_html
from pdf import fetch_pdf, parse_pdf
from snapshot import load_snapshot

EVAL_DIR = Path(__file__).parent / "eval"
PAPERS_FILE = EVAL_DIR / "papers.txt"
PDF_CACHE = EVAL_DIR / "pdf_cache"
HTML_CACHE = EVAL_DIR / "html_cache"
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


def norm_latex(t: str) -> str:
    t = re.sub(r"\\(?:left|right|big+l?|Big+l?|mathrm|text|mathbf|operatorname|displaystyle|[,;:!])", "", t)
    return re.sub(r"[\s{}]", "", t)


def latex_recall(html, pdfp) -> float | None:
    h = [norm_latex(m[1:-1]) for m in MATH_RE.findall(body_text(html, False)) if len(m) >= 22]
    # 只认整段就是一个 $...$ 的段落（Docling 独立公式的输出形式）：正文里的 "$0.14" 这类美元符号
    # 会被 MATH_RE 配成假公式，D23 关公式的全量里因此冒出过 95% 的假数字
    p = norm_latex(" ".join(g.text[1:-1] for s in pdfp.sections for g in s.paragraphs
                            if MATH_RE.fullmatch(g.text)))
    hg = Counter(x[i:i + 3] for x in h for i in range(len(x) - 2))
    if not hg:
        return None
    pg = Counter(p[i:i + 3] for i in range(len(p) - 2))
    return sum(min(c, pg[g]) for g, c in hg.items()) / sum(hg.values())


def table_cells(paper) -> list[str]:
    out = []
    for s in paper.sections:
        for g in s.paragraphs:
            for line in g.text.split("\n"):
                if line.startswith("| "):
                    out += [re.sub(r"[^a-z0-9.]+", "", c.lower()) for c in line.strip("| ").split(" | ")]
    return [c for c in out if c]


def html_tables_paper(pid: str):
    """现抓 HTML 当表格的标准答案（快照没有表格），缓存原始 HTML。"""
    f = HTML_CACHE / f"{pid.replace('/', '_')}.html"
    if f.exists():
        raw = f.read_text(encoding="utf-8")
    else:
        raw = fetch_html(pid)
        if raw is None:
            return None
        HTML_CACHE.mkdir(exist_ok=True)
        f.write_text(raw, encoding="utf-8")
        time.sleep(1)
    return parse_html(raw, pid)


def table_scores(gold, pdfp) -> dict:
    h = table_cells(gold)
    if not h:
        return {"tab_cells": None, "tab_nums": None}
    pc = Counter(table_cells(pdfp))
    cell_r = sum(min(c, pc[x]) for x, c in Counter(h).items()) / len(h)
    nums = [n for c in h for n in NUM_RE.findall(c)]
    pool = Counter(NUM_RE.findall(body_text(pdfp, False)))
    return {"tab_cells": cell_r, "tab_nums": num_recall(nums, pool)}


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
        "latex": latex_recall(html, pdfp),
        "chars": (len(h_txt), len(p_txt)),
    }


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--by-paper", action="store_true")
    ap.add_argument("--engine", choices=["pymupdf", "docling"], default="pymupdf")
    ap.add_argument("--no-formulas", action="store_true", help="docling 不开公式模型（看它占多少时间）")
    ap.add_argument("--tables", action="store_true", help="现抓 HTML 比表格")
    ap.add_argument("--ids", default="", help="只跑这几篇，逗号分隔")
    args = ap.parse_args()

    if args.engine == "docling":
        from pdf_docling import parse_pdf_docling

        def parse(data, pid, title="", abstract=""):
            return parse_pdf_docling(data, pid, title, abstract, formulas=not args.no_formulas)
    else:
        parse = parse_pdf
    only = {x.strip() for x in args.ids.split(",") if x.strip()}

    rows, rescued = [], []
    for cat, pid in load_papers(PAPERS_FILE):
        if only and pid not in only:
            continue
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
            p = parse(data, pid)
            rescued.append((pid, p))
            continue
        if data is None:
            print(f"  {pid}: 没有 PDF")
            continue
        t1 = time.time()
        p = parse(data, pid, html.title, html.abstract)
        parse_secs = time.time() - t1
        if html.truncated_sections:       # HTML 快照被预算截过，PDF 也截；没截过就两边都比全文
            p = apply_budget(p)
        r = compare(html, p)
        r.update(cat=cat, pid=pid, secs=time.time() - t0, parse_secs=parse_secs, n_sec=len(p.sections))
        if args.tables:
            try:
                gold = html_tables_paper(pid)
            except Exception as e:
                print(f"  {pid}: HTML 抓取失败 {str(e)[:80]}")
                gold = None
            r.update(table_scores(gold, p) if gold else {"tab_cells": None, "tab_nums": None})
        rows.append(r)
        print(f"  {pid:12} 解析 {parse_secs:5.1f} 秒  正文召回 {(r['recall'] or 0) * 100:5.1f}%", flush=True)

    keys = [("recall", "正文召回"), ("precision", "正文精确"), ("num_text", "数字召回·正文"),
            ("num_math", "数字召回·公式"), ("sections", "章节召回"), ("latex", "公式LaTeX")]
    if args.tables:
        keys += [("tab_cells", "表格单元格"), ("tab_nums", "表格数字")]
    print(f"\n══ PDF 解析 vs HTML（{args.engine}，{len(rows)} 篇）══")
    print(f"  {'':10}" + "".join(f"{name:>12}" for _, name in keys))
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["cat"]].append(r)
    for cat, rs in sorted(by_cat.items()) + [("全部", rows)]:
        print(f"  {cat:10}" + "".join(f"{mean(r[k] for r in rs) * 100:11.1f}%" for k, _ in keys))
    print(f"  平均每篇解析耗时 {mean(r['parse_secs'] for r in rows):.1f} 秒（含下载 {mean(r['secs'] for r in rows):.1f} 秒）")

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
