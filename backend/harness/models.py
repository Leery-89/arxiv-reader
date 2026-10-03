"""harness 的数据结构。设计见 DECISIONS.md D17。

检查的单位是"一句论断"（D15：标注单位从 quote 改为论断），
所以 Claim 把这句话需要的全部材料打包在一起：引用的 ID、对应段落全文、模型给的 quote。
检查函数只吃 Claim，不碰网络和文件——这样每个检查都能用几行例子做单元测试。
"""

from dataclasses import dataclass, field


@dataclass
class Claim:
    field: str                          # research_question / method / ...
    idx: int                            # 该字段里的第几句（从 0 数）
    raw: str                            # 原句，带 [ID]
    text: str                           # 去掉 [ID] 后的句子，检查用这个
    ids: list[str]                      # 引用的段落 ID，按出现顺序去重
    is_inference: bool = False          # 模型标了 [推断]（prompt v3 起）
    paras: dict[str, str] = field(default_factory=dict)        # ID → 段落全文（找不到的 ID 不在里面）
    quotes: dict[str, list[str]] = field(default_factory=dict)  # ID → 模型给的 quote，可能多条
    all_paras: dict[str, str] = field(default_factory=dict)    # 全文所有段落：判断"引错了段"还是"全文都没有"


@dataclass
class Issue:
    check: str                          # "number" / "entity" / "hedge"
    severity: str                       # "warn"：所引段落里有、quote 里没有（引用不全）
                                        # "block"：所引段落里都没有（错引或幻觉）
    detail: str                         # 给人看的说明，如 "1121 不在 [S3.p2] 中"
    token: str = ""                     # 触发问题的原文片段，如 "1121"
    found_in: list[str] = field(default_factory=list)   # block 时：在哪些未被引用的段落里找到了
    suggest: str = ""                   # warn 时：段落里含该数字的那句，可直接补成 quote


@dataclass
class ClaimResult:
    claim: Claim
    status: str
    """verified     所有检查通过（或 warn 已自动补引）
       flagged      有 block，等评审员判（judge=None 时就停在这）
       partial      评审员判 P
       unsupported  评审员判 N
       inference    模型自己标了 [推断]
       uncited      既没有 ID 也没标推断——不该出现，出现了就是 prompt 没管住
    """
    issues: list[Issue] = field(default_factory=list)
    auto_quotes: dict[str, list[str]] = field(default_factory=dict)  # 确定性补引补进来的句子
    judge_label: str | None = None
    judge_conf: float | None = None     # 给 Jev 留的位置
    repaired: str | None = None


@dataclass
class HarnessConfig:
    """开关面板：决定跑哪几层。消融实验只换这个，不改检查逻辑。"""
    numbers: bool = True
    entities: bool = False              # 还没实现
    hedges: bool = False                # 还没实现
    auto_requote: bool = True           # warn 时用段落里的句子补 quote
    judge: str | None = None            # 评审模型；None = 不调评审，block 停在 flagged
    repair: bool = False                # 还没实现
    policy: bool = False                # 还没实现


@dataclass
class HarnessReport:
    arxiv_id: str
    config: HarnessConfig
    results: list[ClaimResult]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out
