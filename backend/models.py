"""数据结构定义。

这个文件是整个取文清洗模块的「契约」——先把它定下来，
后面 prompt 拼接、引用溯源、截断策略全都围着它转。

设计上有两个刻意的选择，都记在 DECISIONS.md 里了：

1. Paragraph 带稳定 ID（如 "s2.p3"）。
   D9-D10 做引用溯源时，让模型返回段落 ID 而不是原文字符串，
   定位就是 O(1) 查表，不用做模糊匹配。现在加成本为零。

2. sections 是扁平列表 + level 字段，不是嵌套树。
   下游只需要序列化成 prompt，树结构要写递归，换不来好处。
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Paragraph:
    """正文里的一个段落。"""

    id: str
    """稳定标识，格式 "s{节序号}.p{段序号}"，如 "s2.p3"。
    图表说明用 "s2.c1" 这样的 c 前缀区分（caption）。"""

    text: str
    """清洗后的纯文本。公式以 LaTeX 源码形式内联保留。"""

    kind: Literal["body", "caption"] = "body"
    """段落类型。caption 单独标出来，方便 prompt 里区别对待，
    也方便后面想改主意时一行代码就能把它们过滤掉。"""


@dataclass
class Section:
    """论文的一个章节。"""

    level: int
    """标题层级：1 = 一级标题（如 "3 Method"），2 = 二级（如 "3.1 Encoder"）。"""

    title: str

    kind: Literal["body", "appendix"] = "body"

    paragraphs: list[Paragraph] = field(default_factory=list)

    def char_count(self) -> int:
        return sum(len(p.text) for p in self.paragraphs)


@dataclass
class Paper:
    """一篇论文取文清洗后的完整结果。"""

    arxiv_id: str
    """已去掉版本号，如 "1706.03762"（不是 "1706.03762v7"）。
    缓存按论文而非版本存 —— 见 DECISIONS.md D2。"""

    title: str
    abstract: str
    sections: list[Section] = field(default_factory=list)

    references: list[str] = field(default_factory=list)
    """参考文献单独存放，默认不进 prompt。
    用户追问「这篇引用了谁」时再按需拼进去 —— 见 DECISIONS.md。"""

    source: Literal["html", "pdf", "page", "abstract_only"] = "html"
    """取文来源。pdf 表示没有 HTML 版、正文从 PDF 解析（段落 ID 是 pg3.b4 这种页-块编号）；
    page 表示正文来自用户浏览器里打开的出版方全文页面（D27，用户自己有权限看的那份）；
    abstract_only 表示 HTML 和 PDF 都没拿到，降级只拿到了摘要。
    前端据此提示用户，埋点据此统计降级比例。一个字段三个用途。"""

    url: str = ""
    """原文地址。只有 DOI 进来、正文取自开放获取 PDF 的论文才填（D25），前端点出处时跳到这里的第 N 页；
    arXiv 论文留空，前端自己拼 arxiv.org 的地址。"""

    truncated_sections: list[str] = field(default_factory=list)
    """因超出预算被丢弃的章节名。空列表表示全文完整。
    这个也要透传到前端 —— 用户有权知道模型没看到哪些内容。"""

    def char_count(self) -> int:
        return len(self.abstract) + sum(s.char_count() for s in self.sections)

    def est_tokens(self) -> int:
        """粗略估算 token 数。

        英文约 4 字符 1 token，LaTeX 公式的 token 密度更高，
        所以这里按 3.5 折算，宁可高估。
        真实数字等 D11 埋点上线后用 API 返回的 usage 校准。
        """
        return int(self.char_count() / 3.5)
