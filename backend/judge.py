"""LLM 评审员：用人工标注校准，再用它做规模化标注。

    cd backend
    python judge.py                 # 给 annotate.csv 里所有行打标签，并和人工标签对比
    python judge.py --force         # 忽略缓存重判（改了 JUDGE_PROMPT 之后）
    python judge.py --input eval/annotate_v3.csv   # 给别的标注表打标签（prompt v3 用）

思路（DECISIONS D15）：
  1. 模型只看 quote 和 claim，看不到人工标签——和人标注时的信息完全一样。
  2. 校准集 = 我标的、不含物理和 abstract_only 的 120 条。物理是我非专业标的，
     不能当金标准；abstract_only 本来就不标。
  3. 在校准集上算一致率和 Cohen's kappa。够高，才拿它去标物理和新版本的结果。
  4. 分歧逐条落盘，回头看是评审员错了、我错了、还是标准本身有歧义。

注意：评审员和被评的是同一个模型（deepseek-chat）时，可能偏袒自己的输出。
这是已知的偏差，校准一致率就是用来量化它的。换模型对比：

    python judge.py --model deepseek-reasoner
    python judge.py --model qwen-plus                    # 需要 DASHSCOPE_API_KEY
    python judge.py --compare deepseek-chat deepseek-reasoner qwen-plus
"""

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

JUDGE_MODEL = os.getenv("JUDGE_MODEL", "deepseek-chat")

# 评审员可以换厂商。都是 OpenAI 兼容接口，只换 base_url 和 key。
PROVIDERS = {
    "deepseek": ("DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")),
    "qwen": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/"),   # Anthropic 的 OpenAI 兼容接口
}


def provider_of(model: str) -> str:
    if model.startswith("deepseek"):
        return "deepseek"
    if model.startswith("qwen"):
        return "qwen"
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    sys.exit(f"不认识的评审模型：{model}（支持 deepseek-* / qwen-* / gpt-* / o* / claude-*）")


def make_client(model: str) -> OpenAI:
    key_env, base = PROVIDERS[provider_of(model)]
    key = os.getenv(key_env)
    if not key:
        sys.exit(f"{model} 需要在 .env 里设置 {key_env}")
    headers = {"anthropic-version": "2023-06-01"} if provider_of(model) == "anthropic" else None
    return OpenAI(api_key=key, base_url=base, default_headers=headers)

EVAL_DIR = Path(__file__).parent / "eval"
CACHE_FILE = EVAL_DIR / "judge_cache.jsonl"

# 校准集：人工标签可信的类别
CALIB_CATEGORIES = {"standard", "long", "odd", "science"}
SKIP_CATEGORIES = {"abstract_only"}

# 规则来自 ANNOTATION_GUIDE.md + 标注时对齐的判例。
# 故意不放评测集里的真实例子，否则一致率会虚高（评审员背过答案）。
JUDGE_PROMPT = """你是论文引用标注员。一个论文阅读助手读完论文后写出论断（claim），并为论断引用原文中的一句话（quote）。你要判断：只看这句 quote，能不能得出 claim 中它负责的那部分内容。

## 三档标签
- Y：claim 中可验证的实质内容（名字、数字、结论、因果）全部在 quote 里。
- P：quote 撑住了 claim 的至少一项实质内容，但还有别的实质内容不在 quote 里；或者 claim 把 quote 说强了（如"约一半"写成"大多数"、"可能"写成"证明"）；或者逻辑关系被改了。
- N：quote 没有撑住 claim 的任何实质内容。包括：quote 讲的是另一件事（另一个数据集、另一条局限、另一个基线）；quote 只是框架句或总述（如"我们设计了一些任务"），具体内容一项都没有；claim 写着"原文未明确提及"却又给了引用。

## 细则
1. 多个 [ID]：claim 里有多个方括号 ID 时，论断被拆给了多条引用。只判这条 quote 对应的那一部分（用 quote 的 evidence_id 找到 claim 里对应的片段），其他部分不影响判断。只有一个 ID 时，整句 claim 都由这条 quote 负责。
1b. 同 ID 多条 quote：输入里如果列出了「同一 ID 下的其他 quote」，说明模型为这个段落引了不止一句。把它们和本条合起来，判断这个 ID 整体对 claim 中对应部分的支撑程度，本条的标签就代表这个整体。
2. 解指代不扣分：quote 说 "the baseline"、"our method"、"a good agreement"，claim 写成具体名字或对象，只要没有添加新事实，不降档。
3. 注解不扣分，新事实扣分：claim 多出来的东西如果只是把 quote 里某个词说得更具体，不降档。如果多出来的是能独立验证的新事实（一个数字、一个名字、一个结论、一个"因此"），降到 P。
4. 缺框架不扣分：claim 里"在多种基准上评估"这类框架性的话 quote 没有，但实质内容全在，算 Y。
5. P 与 N 的分界：quote 撑住了至少一项实质内容 → P；只撑住一个笼统框架 → N。
6. 省略不扣分：claim 比 quote 少说了东西，不影响判断。
7. 公式：quote 是公式时，核对公式形状是否真的对应 claim 的文字描述。
8. 不要用你对这篇论文的背景知识补证据。只看这两段文字。
9. 不评价 claim 本身对不对，只评价这句 quote 撑不撑得住它。

## 输出
只输出 JSON：{"label": "Y" | "P" | "N", "reason": "一句话理由"}"""

PROMPT_VERSION = hashlib.sha256(JUDGE_PROMPT.encode()).hexdigest()[:8]


RESULTS_DIR = EVAL_DIR / "results"
_results_cache: dict = {}


def siblings(r: dict) -> list[str]:
    """同一篇、同一字段、同一段落 ID 下，模型引的其他 quote。
    41.8% 的 evidence 和别的 quote 共用 ID——只看一条会系统性地多判 P。
    标注表只抽了 5 条，兄弟 quote 不一定在表里，所以从完整结果里取。"""
    pid = r["paper_id"]
    if pid not in _results_cache:
        f = RESULTS_DIR / f"{pid}.json"
        _results_cache[pid] = json.loads(f.read_text(encoding="utf-8"))["result"] if f.exists() else {}
    block = _results_cache[pid].get(r["field"]) or {}
    if not isinstance(block, dict):
        return []
    me = r["quote"].strip()
    return [ev.get("quote", "").strip() for ev in block.get("evidence") or []
            if ev.get("id") == r["evidence_id"] and ev.get("quote", "").strip() != me]


def row_key(r: dict, model: str) -> str:
    raw = "|".join([r["paper_id"], r["field"], r["evidence_id"], r["quote"].strip(),
                    r["claim"].strip(), *siblings(r), model, PROMPT_VERSION])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    out = {}
    for line in CACHE_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["key"]] = d
    return out


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError(f"输出里找不到 JSON：{(text or '')[:120]}")
    return json.loads(m.group(0))


# 不同厂商、不同模型接受的参数不一样：
#   推理模型不接受 temperature=0；Claude 要求 max_tokens；有的不支持 JSON 模式。
# 按顺序试，哪组能用就记住，之后同一模型直接用那组（不再浪费失败的调用）。
_PARAM_SETS = [
    {"temperature": 0, "max_tokens": 400, "response_format": {"type": "json_object"}},
    {"temperature": 0, "max_tokens": 400},
    {"max_completion_tokens": 4000},          # OpenAI 推理模型：不收 temperature / max_tokens
    {},
]
_WORKING_PARAMS: dict[str, dict] = {}


def _create(client: OpenAI, model: str, msgs: list):
    if model in _WORKING_PARAMS:
        return client.chat.completions.create(model=model, messages=msgs, **_WORKING_PARAMS[model])
    last = None
    for params in _PARAM_SETS:
        try:
            resp = client.chat.completions.create(model=model, messages=msgs, **params)
            _WORKING_PARAMS[model] = params
            return resp
        except Exception as e:
            # 只有"参数不被接受"（400）才换下一组；模型不存在（404）、key 错（401）等直接抛出
            if getattr(e, "status_code", None) != 400:
                raise
            last = e
    raise last


def judge_one(client: OpenAI, model: str, r: dict) -> dict:
    user = (f"evidence_id: {r['evidence_id']}\n"
            f"字段: {r['field']}\n\n"
            f"claim:\n{r['claim']}\n\n"
            f"quote:\n{r['quote']}")
    sib = siblings(r)
    if sib:
        user += "\n\n同一 ID 下的其他 quote（与上面这条合起来判）：\n" + "\n".join(f"- {q}" for q in sib)
    msgs = [{"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": user}]
    resp = _create(client, model, msgs)
    text = resp.choices[0].message.content
    try:
        d = _extract_json(text)
    except (ValueError, json.JSONDecodeError):
        # reason 里带了没转义的引号之类，JSON 坏了——只要 label 抠得出来就够用
        m = re.search(r'"label"\s*:\s*"([YPNypn])"', text or "")
        if not m:
            raise
        d = {"label": m.group(1), "reason": "(JSON 损坏，仅取 label)"}
    label = str(d.get("label", "")).strip().upper()[:1]
    if label not in ("Y", "P", "N"):
        raise ValueError(f"评审员输出了非法标签：{d}")
    u = resp.usage
    return {"label": label, "reason": d.get("reason", ""),
            "tokens": (u.prompt_tokens + u.completion_tokens) if u else 0}


def judge_one_retry(client: OpenAI, model: str, r: dict, tries: int = 3) -> dict:
    """偶发的格式错误、网络抖动，重试两次再放弃。"""
    for i in range(tries):
        try:
            return judge_one(client, model, r)
        except Exception:
            if i == tries - 1:
                raise


# ───────────────────────── 一致性指标 ─────────────────────────

LABELS = ["Y", "P", "N"]


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """两组标签的 Cohen's kappa：扣掉"碰巧一致"之后的一致程度。
    0 = 和瞎猜一样，1 = 完全一致；一般 0.6 以上算"较好"，0.8 以上算"很好"。"""
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[l] * cb[l] for l in LABELS) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def report(name: str, pairs: list[tuple[str, str]]):
    """pairs: [(人工, 评审员)]"""
    if not pairs:
        return
    h, j = [p[0] for p in pairs], [p[1] for p in pairs]
    acc = sum(x == y for x, y in pairs) / len(pairs)
    print(f"\n── {name}（{len(pairs)} 条）──")
    print(f"  一致率 {acc * 100:.1f}%    kappa {cohen_kappa(h, j):.3f}")
    ch, cj = Counter(h), Counter(j)
    print(f"  标签分布   人工 Y/P/N = {ch['Y']}/{ch['P']}/{ch['N']}    "
          f"评审员 Y/P/N = {cj['Y']}/{cj['P']}/{cj['N']}")
    print("  混淆矩阵（行=人工，列=评审员）")
    print("         " + "".join(f"{l:>6}" for l in LABELS))
    for lh in LABELS:
        print(f"    {lh}    " + "".join(f"{sum(1 for x, y in pairs if x == lh and y == lj):>6}" for lj in LABELS))


# ───────────────────────── 主流程 ─────────────────────────

def safe(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]", "_", model)


def run_judge(model: str, path: Path, force: bool, workers: int) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    todo_rows = [r for r in rows if r["category"] not in SKIP_CATEGORIES]
    if not todo_rows:
        sys.exit("没有可评的行")

    cache = {} if force else load_cache()
    todo = [r for r in todo_rows if row_key(r, model) not in cache]
    print(f"评审员 {model}  prompt {PROMPT_VERSION}   共 {len(todo_rows)} 条，"
          f"缓存命中 {len(todo_rows) - len(todo)}，需要调用 {len(todo)}")

    if todo:
        client = make_client(model)
        tokens = 0
        with CACHE_FILE.open("a", encoding="utf-8") as cf, ThreadPoolExecutor(workers) as pool:
            futs = {pool.submit(judge_one_retry, client, model, r): r for r in todo}
            fails_in_a_row = 0
            for i, fut in enumerate(as_completed(futs), 1):
                r = futs[fut]
                try:
                    d = fut.result()
                    fails_in_a_row = 0
                except Exception as e:
                    fails_in_a_row += 1
                    print(f"  失败 {r['paper_id']} {r['evidence_id']}: {str(e)[:160]}")
                    if fails_in_a_row >= 10:
                        print(f"  连续失败 10 次，停止 {model}（多半是模型名或 key 的问题，看上面的报错）")
                        for f in futs:
                            f.cancel()
                        break
                    continue
                tokens += d["tokens"]
                rec = {"key": row_key(r, model), "model": model, **d}
                cache[rec["key"]] = rec
                cf.write(json.dumps(rec, ensure_ascii=False) + "\n")
                cf.flush()
                if i % 20 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)}")
        print(f"本次 token {tokens}")

    for r in rows:
        c = cache.get(row_key(r, model)) if r["category"] not in SKIP_CATEGORIES else None
        r["judge_label"] = c["label"] if c else ""
        r["judge_reason"] = c["reason"] if c else ""
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(EVAL_DIR / "annotate.csv"))
    ap.add_argument("--model", default=JUDGE_MODEL, help="评审模型，如 deepseek-chat / deepseek-reasoner / qwen-plus")
    ap.add_argument("--compare", nargs="+", metavar="MODEL", help="多个评审模型横向对比（各自跑完后汇总）")
    ap.add_argument("--force", action="store_true", help="忽略缓存重判")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    path = Path(args.input)

    if args.compare:
        compare(args.compare, path, args.force, args.workers)
        return

    model = args.model
    rows = run_judge(model, path, args.force, args.workers)

    out_path = path.with_name(f"{path.stem}_judged.{safe(model)}.csv")
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"→ {out_path.name}")

    def pairs(cond):
        return [(r["label"].strip().upper(), r["judge_label"]) for r in rows
                if cond(r) and r["label"].strip().upper() in LABELS and r["judge_label"] in LABELS]

    report("校准集：standard / long / odd / science", pairs(lambda r: r["category"] in CALIB_CATEGORIES))
    for cat in sorted(CALIB_CATEGORIES):
        p = pairs(lambda r, c=cat: r["category"] == c)
        if p:
            acc = sum(x == y for x, y in p) / len(p)
            print(f"    {cat:10} {len(p):3} 条  一致率 {acc * 100:5.1f}%  kappa {cohen_kappa([x for x, _ in p], [y for _, y in p]):.3f}")
    report("物理（人工为非专业标注，仅供参考）", pairs(lambda r: r["category"] == "physics"))

    print("\n── 评审员给出的支撑率 ──")
    for name, cond in [("校准集", lambda r: r["category"] in CALIB_CATEGORIES),
                       ("物理", lambda r: r["category"] == "physics")]:
        js = [r["judge_label"] for r in rows if cond(r) and r["judge_label"] in LABELS]
        if js:
            c = Counter(js)
            n = len(js)
            print(f"  {name:6} {n:3} 条   Y {c['Y'] / n * 100:5.1f}%  P {c['P'] / n * 100:5.1f}%  "
                  f"N {c['N'] / n * 100:5.1f}%")

    dis = [r for r in rows if r["category"] in CALIB_CATEGORIES | {"physics"}
           and r["label"].strip().upper() in LABELS and r["judge_label"] in LABELS
           and r["label"].strip().upper() != r["judge_label"]]
    cols = ["paper_id", "category", "field", "evidence_id", "label", "judge_label",
            "quote", "claim", "notes", "judge_reason", "verdict"]
    dis_path = EVAL_DIR / f"judge_disagreements.{safe(model)}.csv"
    with dis_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in dis:
            w.writerow({**r, "verdict": ""})
    print(f"\n分歧 {len(dis)} 条 → {dis_path.name}"
          f"（verdict 列可填：人错 / 评审错 / 标准歧义）")


def compare(models: list[str], path: Path, force: bool, workers: int):
    """多个评审员横向比：各自和人工的一致性，评审员之间的一致性，多数投票的一致性。"""
    # 缺 key 的模型跳过，不要让一个没配好的拖垮整次对比
    ready = []
    for m in models:
        key_env = PROVIDERS[provider_of(m)][0]
        if os.getenv(key_env):
            ready.append(m)
        else:
            print(f"跳过 {m}：.env 里没有设置 {key_env}")
    models = ready
    if not models:
        sys.exit("没有可用的评审模型")

    results = {}
    for m in models:
        print()
        results[m] = run_judge(m, path, force, workers)

    base = results[models[0]]
    calib = [i for i, r in enumerate(base) if r["category"] in CALIB_CATEGORIES
             and r["label"].strip().upper() in LABELS
             and all(results[m][i]["judge_label"] in LABELS for m in models)]
    human = [base[i]["label"].strip().upper() for i in calib]
    lab = {m: [results[m][i]["judge_label"] for i in calib] for m in models}

    print(f"\n══ 横向对比（校准集 {len(calib)} 条，所有评审员都判出结果的行）══")
    print(f"  {'评审员':22} {'和人工一致':>10} {'kappa':>8}   Y/P/N")
    for m in models:
        c = Counter(lab[m])
        acc = sum(x == y for x, y in zip(human, lab[m])) / len(calib)
        print(f"  {m:22} {acc * 100:9.1f}% {cohen_kappa(human, lab[m]):8.3f}   {c['Y']}/{c['P']}/{c['N']}")

    if len(models) >= 2:
        print("\n  评审员之间（kappa）")
        for i, a in enumerate(models):
            for b in models[i + 1:]:
                print(f"    {a} × {b}: {cohen_kappa(lab[a], lab[b]):.3f}")

    if len(models) >= 3:
        vote = []
        for k in range(len(calib)):
            c = Counter(lab[m][k] for m in models).most_common()
            vote.append(c[0][0] if c[0][1] > 1 else "P")   # 三方全不同时取中间档
        acc = sum(x == y for x, y in zip(human, vote)) / len(calib)
        print(f"\n  多数投票        和人工一致 {acc * 100:.1f}%   kappa {cohen_kappa(human, vote):.3f}")

    c = Counter(human)
    print(f"\n  人工 Y/P/N = {c['Y']}/{c['P']}/{c['N']}")


if __name__ == "__main__":
    main()
