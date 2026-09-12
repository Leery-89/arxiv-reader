"""把 Paper 变成带段落 ID 的纯文本，送给模型。

输出格式：
    # 标题

    ## Abstract
    摘要

    ## 1 Introduction
    [S1.p1] 第一段
    [S1.p2] 第二段

    ### 3.1 Encoder and Decoder Stacks
    [S3.SS1.p1] ...

每段前面的 [ID] 是溯源的关键：模型看得见 ID，才能在回答里引用 ID。
参考文献不放进去（见 DECISIONS.md）；被预算丢掉的章节在末尾告诉模型。
"""

from models import Paper


def paper_to_text(paper: Paper) -> str:
    lines: list[str] = []

    lines.append(f"# {paper.title}")
    lines.append("")
    lines.append("## Abstract")
    lines.append(paper.abstract)

    for sec in paper.sections:
        lines.append("")
        hashes = "#" * (sec.level + 1)        # level 1 → ##，level 2 → ###
        lines.append(f"{hashes} {sec.title}")
        for p in sec.paragraphs:
            lines.append(f"[{p.id}] {p.text}")

    if paper.truncated_sections:
        lines.append("")
        lines.append("（以下章节因篇幅未包含：" + "、".join(paper.truncated_sections) + "）")

    return "\n".join(lines)
