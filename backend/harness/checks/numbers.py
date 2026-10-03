"""数字检查：论断里的每个数字，必须出现在它引用的段落里。

三档结果：
    quote 里有                 → 通过
    所引段落里有、quote 里没有  → warn（引用不全；可以直接把段落里那句补成 quote）
    所引段落里都没有            → block（引错了段，或者全文都没有）

规则全部来自 D17 粗筛时看到的误报：
    ResNet-50 / MACE-MP-0 / GPT-4    数字是名字的一部分 → 不检查
    表 4 / Figure 2 / 第 3 节          结构引用 → 不检查
    3D / 2nd / 4o                      数字后面紧跟字母 → 不检查（7B、100K 例外，是数量）
    10 万 ↔ 100,000 / 100K             数量级换算
    60% ↔ 0.6                          百分数换算
    6 ↔ six                            英文数词
    2.5 ↔ 2.47                         按论断的精度四舍五入
    约 300 ↔ 297                       有"约 / 近 / ~"时放宽到 ±10%
    1e-4 ↔ 10^{-4}                     科学计数法和 LaTeX 写法
"""

import re
from functools import lru_cache

from ..models import Claim, Issue

# 一个数字：千分位 / 整数，可选小数
NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
CLAIM_NUM_RE = re.compile(NUM + r"(?:\s*([%％]))?(?:\s*(万|亿))?")
PARA_NUM_RE = re.compile(NUM + r"(?:\s*([%％]|percent))?([kKMB](?![a-zA-Z]))?(?:\s*(thousand|million|billion))?")

SCI_RES = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*\\times\s*10\^\{?(-?\d+)\}?"), lambda m: float(m[1]) * 10 ** int(m[2])),
    (re.compile(r"(?<![\d.])10\^\{?(-?\d+)\}?"), lambda m: 10.0 ** int(m[1])),
    (re.compile(r"(\d+(?:\.\d+)?)[eE](-?\d+)\b"), lambda m: float(m[1]) * 10 ** int(m[2])),
    # 数量级写法：$\mathcal{O}$(1)M hours、O(100)B tokens
    (re.compile(r"\((\d+(?:\.\d+)?)\)\s*([kKMB])(?![a-zA-Z])"), lambda m: float(m[1]) * MAG_SUFFIX[m[2]]),
]

STRUCT_BEFORE = re.compile(
    r"(表|图|节|式|公式|附录|第|Table|Tab\.|Figure|Fig\.|Section|Sec\.|Eq\.|Equation|Appendix|Algorithm)\s*$",
    re.I)
APPROX_BEFORE = re.compile(r"(约|大约|近|将近|接近|~|≈|\\sim|\\approx)\s*$")
MAG_SUFFIX = {"k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9}
WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
WORD_RE = re.compile(r"\b(" + "|".join(WORDS) + r")\b", re.I)
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z$\\(\[])")


def _val(intpart: str, frac: str | None) -> float:
    return float(intpart.replace(",", "") + ("." + frac if frac else ""))


# ───────────────────────── 论断侧：要检查哪些数字 ─────────────────────────

def claim_numbers(text: str) -> list[dict]:
    """返回 [{token, values, approx}]。values 是可接受的 (取值, 精度) 列表——考虑了换算，
    精度是小数位数（None = 只接受精确相等），换算后精度跟着变。"""
    out = []
    covered = []                        # 科学计数法已经吃掉的区间
    for rx, fn in SCI_RES:
        for m in rx.finditer(text):
            covered.append(m.span())
            out.append({"token": m[0], "values": [(fn(m), None)], "approx": False})

    for m in CLAIM_NUM_RE.finditer(text):
        s, e = m.span()
        if any(a <= s < b for a, b in covered):
            continue
        before, after = text[max(0, s - 12):s], text[e:e + 1]
        if re.search(r"[A-Za-zͰ-Ͽ][-_]?$", before):       # ResNet-50, MACE-MP-0, GPT-4, α2
            continue
        if re.search(r"[A-Za-z][\w.\-]*\d[-_]$", before):              # MD17-10k, Llama-3.1-8B：名字里的第二段数字
            continue
        if before.endswith((".", ",")) and re.search(r"\d[.,]$", before):   # 被千分位/小数切开的尾巴
            continue
        if STRUCT_BEFORE.search(before):                              # 表 4、Figure 2
            continue
        if after and re.match(r"[A-Za-z]", after) and not re.match(r"[kKMB](?![a-zA-Z])", text[e:e + 2]):
            continue                                                  # 3D、2nd、4o（7B、100K 保留）
        v = _val(m[1], m[2])
        d = len(m[2]) if m[2] else 0
        # 换算之后精度跟着变：48.6% → 0.486 是 3 位小数，不能再按 1 位四舍五入
        vals = [(v, d)]
        if m[3]:
            vals.append((v / 100, d + 2))
        if m[4]:
            k = 4 if m[4] == "万" else 8
            vals = [(v * 10 ** k, d - k)]
        if re.match(r"[kKMB](?![a-zA-Z])", text[e:e + 2]):
            mag = MAG_SUFFIX[text[e]]
            vals.append((v * mag, d - len(str(int(mag))) + 1))
        out.append({"token": m[0].strip(), "values": vals,
                    "approx": bool(APPROX_BEFORE.search(before))})
    return out


# ───────────────────────── 段落侧：段落里有哪些数 ─────────────────────────

@lru_cache(maxsize=4096)
def para_values(text: str) -> tuple[tuple[float, int], ...]:
    """段落里所有数值，附带所在句子的序号（用于补引）。缓存：同一段会被多句论断查。"""
    sents = SENT_RE.split(text)
    out = []
    for si, sent in enumerate(sents):
        for rx, fn in SCI_RES:
            for m in rx.finditer(sent):
                out.append((fn(m), si))
        for m in PARA_NUM_RE.finditer(sent):
            v = _val(m[1], m[2])
            out.append((v, si))
            if m[3]:
                out.append((v / 100, si))
            if m[4]:
                out.append((v * MAG_SUFFIX[m[4]], si))
            if m[5]:
                out.append((v * MAG_SUFFIX[m[5].lower()], si))
        for m in WORD_RE.finditer(sent):
            w = float(WORDS[m[1].lower()])
            out.append((w, si))
            mag = re.match(r"\s+(thousand|million|billion)", sent[m.end():], re.I)
            if mag:
                out.append((w * MAG_SUFFIX[mag[1].lower()], si))
    return tuple(out)


def _sentence(text: str, si: int) -> str:
    return SENT_RE.split(text)[si].strip()


def _match(num: dict, v: float) -> bool:
    for c, d in num["values"]:
        if abs(v - c) <= 1e-9 * max(1.0, abs(c)):
            return True
        if num["approx"] and c and abs(v - c) <= 0.1 * abs(c):
            return True
        # 按论断的精度四舍五入：论断写 2.5，段落写 2.47，算对上
        if d is not None and v and round(v, d) == round(c, d) and abs(v - c) < 10 ** (-d):
            return True
    return False


def _find(num: dict, text: str) -> int | None:
    """在一段文字里找这个数；找到返回句子序号，找不到返回 None。"""
    for v, si in para_values(text):
        if _match(num, v):
            return si
    return None


# ───────────────────────── 检查入口 ─────────────────────────

def check_numbers(claim: Claim) -> list[Issue]:
    if not claim.ids:
        return []
    issues = []
    for num in claim_numbers(claim.text):
        quote_text = " ".join(q for qs in claim.quotes.values() for q in qs)
        if _find(num, quote_text) is not None:
            continue

        hit = None
        for pid, para in claim.paras.items():
            si = _find(num, para)
            if si is not None:
                hit = (pid, _sentence(para, si))
                break
        if hit:
            issues.append(Issue("number", "warn", f"{num['token']} 在 [{hit[0]}] 里，但不在 quote 里",
                                token=num["token"], suggest=hit[1], found_in=[hit[0]]))
            continue

        elsewhere = [pid for pid, para in claim.all_paras.items()
                     if pid not in claim.paras and _find(num, para) is not None]
        cited = ", ".join(f"[{i}]" for i in claim.ids)
        if elsewhere:
            detail = f"{num['token']} 不在 {cited} 中，出现在 " + ", ".join(f"[{i}]" for i in elsewhere[:3])
        else:
            detail = f"{num['token']} 全文都没有找到"
        issues.append(Issue("number", "block", detail, token=num["token"], found_in=elsewhere[:5]))
    return issues
