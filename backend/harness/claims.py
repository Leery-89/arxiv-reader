"""把模型输出拆成逐句论断，并把每句需要的材料（段落全文、quote）挂上去。"""

import re

from models import Paper

from .models import Claim

FIELDS = ["research_question", "method", "experiments", "findings", "limitations"]
ID_RE = re.compile(r"\[([A-Za-z0-9.]+)\]")
INFER_RE = re.compile(r"\[推断\]")
NOT_MENTIONED = "原文未明确提及"

# 按中文句末标点切。英文句号不切：论断是中文写的，而 "S3.p1"、"0.87" 里都有点。
SENT_SPLIT = re.compile(r"(?<=[。！？；;])")


def _sentences(text: str) -> list[str]:
    """切句，但没有 ID 的分号分句并入下一句。
    模型常写成"A；B [ID]。"——ID 只放在最后，前半句其实也由它支撑。
    （D17 首轮评测：按分号硬切造成了 19 句假的 uncited）"""
    parts = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    out, buf = [], ""
    for part in parts:
        buf += part
        if ID_RE.search(part) or INFER_RE.search(part) or not part.endswith(("；", ";")):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def paragraph_index(paper: Paper) -> dict[str, str]:
    paras = {p.id: p.text for s in paper.sections for p in s.paragraphs}
    paras["abstract"] = paper.abstract          # 和 serialize.py 里的 [abstract] 对应
    return paras


def split_claims(paper: Paper, result: dict) -> list[Claim]:
    all_paras = paragraph_index(paper)
    claims: list[Claim] = []
    for field in FIELDS:
        block = result.get(field) or {}
        if not isinstance(block, dict):
            continue
        quotes: dict[str, list[str]] = {}
        for ev in block.get("evidence") or []:
            quotes.setdefault(ev.get("id", ""), []).append(ev.get("quote", ""))

        sents = _sentences(block.get("text", ""))
        for i, raw in enumerate(sents):
            ids = list(dict.fromkeys(ID_RE.findall(raw)))      # 去重保序
            text = ID_RE.sub("", INFER_RE.sub("", raw)).strip()
            claims.append(Claim(
                field=field, idx=i, raw=raw, text=text, ids=ids,
                is_inference=bool(INFER_RE.search(raw)),
                paras={k: all_paras[k] for k in ids if k in all_paras},
                quotes={k: quotes.get(k, []) for k in ids},
                all_paras=all_paras,
            ))
    return claims
