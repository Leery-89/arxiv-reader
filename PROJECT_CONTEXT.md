# arXiv Reader · 项目交接

> 新开对话时把这份文件给 Claude，就能接上进度。更新于 2026-10-03。
> 在线版（带架构图和路线图）：https://claude.ai/code/artifact/4d352976-6feb-40d1-a370-c62963e78768
> 每条决策的完整理由、代价、备选见 `DECISIONS.md`；prompt 改版记录见 `backend/prompts_history.md`。

## 项目概览

Chrome 侧栏插件：把 arXiv 论文变成结构化导读，每句论断都标注段落 ID，并对照原文校验。
这是为 2027 年初国内大厂 AI/LLM 应用日常实习准备的主项目：2026 年 11–12 月投递，2027 年 Term 1（UNSW）gap。

| 组件 | 状态 | 位置 |
|---|---|---|
| 插件 | v0.3.0（侧栏可直接粘链接 / ID / arXiv DOI） | Chrome 商店已送审；朋友用 zip 试用 |
| 后端 | FastAPI + DeepSeek，SSE 流式，缓存、限流、埋点 | https://arxiv-reader-production.up.railway.app |
| 代码 | DECISIONS.md（D1–D19）、prompts_history.md | https://github.com/Leery-89/arxiv-reader |
| 本地 | `~/Desktop/arxiv-assistant/`，conda 环境 `arxiv` | Mac + iTerm |

简历要讲的是一条完整的评测链：36 篇有意为难的评测集 → 人工标注 → 校准 LLM 评审员 → harness 拦幻觉 → prompt 版本对比 → Qwen post-training。

## 关键数字

最有说服力的一对：机器校验"引用存在"98.0%，人工判"引用完全支撑论断"50.8%。人工指标都是校准集 120 条（不含物理、abstract_only），单人标注。

| 指标 | 数值 | 说明 |
|---|---|---|
| HTML 覆盖率 | 80% | 60 篇调研；其余降级为摘要 |
| quote 命中率 | 98.0%（835 条） | 物理最差 94.1%，最差单篇 75% |
| 内嵌 ID 合规率 | 96.6% | 25 个 ID 正文有、evidence 没给 quote |
| 人工 Y/P/N（单条 quote 抽样） | 36.7% / 50.0% / 13.3% | 抽样把同段多条 quote 拆开了 |
| 人工 Y/P/N（同 ID 合起来判） | **50.8% / 41.7% / 7.5%** | 当前主数字 |
| 评审员 deepseek-chat vs 人工 | kappa **0.614**，一致 78.3% | 有兄弟 quote 0.423 / 无 0.681；对 N 偏宽松（9 条抓 5 条） |
| harness 数字检查 | 拦截 2.2%，确定性补引 52 句 | 552 句带引用；拦下的基本是引错段 |
| 成本 / 延迟 | ≈¥0.015/篇，首字 ≈1 秒 | ≈1 万输入 / 2.3k 输出 token |

AI+科学类变化最大：Y 55% → 75%、N 15% → 0（每 ID 平均引 1.47 句）。物理人工标签不可用（非专业标注，与评审员 kappa 0.03），盲标文件已备好待朋友标。

## 架构与文件地图

线上：插件 →（SSE）→ FastAPI 后端 → `fetcher.py`（HTML / 摘要降级）→ `serialize.py`（全文 + 段落 ID）→ `llm.py`（DeepSeek）→ `verify.py`（quote 命中、ID 合规）。

离线评测（直接 import 后端模块，不走 HTTP）：

| 文件 | 作用 |
|---|---|
| `eval/papers.txt` | 36 篇、6 类评测清单 |
| `eval_run.py` | 批量跑；`--tag v3` 存到 `results_v3/`，同时存论文快照 |
| `snapshot.py` / `eval/papers/` | 论文快照（模型当时看到的原文） |
| `eval/annotate.csv` | 人工标注（已裁决 + 重标）；`*.pre_*.csv` 是历史版本 |
| `judge.py` | LLM 评审员；`--compare` 多模型对比；支持 deepseek / qwen / gpt / claude |
| `harness/` | 拆句 + 数字检查 + 自动补引；`harness_eval.py` 离线评 |
| `eval_versions.py` | v2 vs v3 全量对比（论断级 / ID 级） |
| `tests/` | `test_numbers.py`、`test_harness.py`、`test_fetcher.py` |
| `list_models.py` | 列出各家 API 可用的模型名 |

## 决策索引

| 编号 | 决策 |
|---|---|
| D1–D4 | 解析 arXiv HTML，段落带稳定 ID；没有 HTML 降级取摘要 |
| D5 | 长上下文而非 RAG；可以综合不许添加；无依据写"原文未明确提及" |
| D6–D10 | 流式 + 前端增量 JSON；证据 = 段落 ID + 逐字 quote，可跳回原文 |
| D11–D13 | 缓存、埋点、限流、上线 Railway 与 Chrome 商店 |
| D14 / D14b | 36 篇 6 类评测集；下载 PDF 的用户用不上侧栏 → 加独立入口 |
| D15 | 评审员先用人工标注校准再规模化；裁决、同 ID 合并判，kappa 0.614 |
| D16 | Post-training：过滤式蒸馏 SFT → DPO → GRPO |
| D17 | Harness 分层（草案）；数字检查已完成；推断要标出；候选 Jev 级联 |
| D18 | 公式原文不动，只在比较时归一化，显示用 KaTeX；2true294 例外修复 |
| D19 | prompt v3 + 全量评测；v2/v3 用环境变量 `PROMPT` 切换（待写进 DECISIONS.md） |

## 路线图

主线：① 标注收尾 + 评审员校准（进行中）→ ② prompt v3 + 论断级评测（**完成即投简历**）→ D · RAG 对比 → ③ Harness 分层（穿插 A 输入覆盖、B 阅读体验）→ ④ Qwen post-training → C · 网页版交互式 Agent（DOI → 分析 + 合法开放获取的原文，MCP 调度工具）。

## 当前进行中与下一步

- [ ] 评审员五模型对比：`python judge.py --compare deepseek-chat gpt-6.1-sol claude-sonnet-5-5 claude-opus-5-5 claude-fable-5-1`
- [ ] 按 kappa 和 N 召回定主评审员，或三家厂商投票
- [ ] `PROMPT=v3 python eval_run.py --tag v3`（约 10 分钟，¥0.5）
- [ ] `python eval_versions.py --tags v2 v3 --dry-run`，再 `--judges <选定>`
- [ ] 结果写进 DECISIONS.md（D19），更新简历

v3 相对 v2：要素覆盖（数字、名称、比较结论、因果两端都要落在 quote 里）、同段可引多句、指代开头的 quote 连被指代句一起引、不许放大语气、因果两端都要引用、引入 `[推断]`。线上仍是 v2，侧栏支持 `[推断]` 后再切。

## 环境与约定

| 事项 | 约定 |
|---|---|
| 提交 | 每轮 `git add -A` → `git commit -m "…"` → `git push`，信息写做了什么 + 关键数字 |
| 测试 | 每修一个误报加一条用例 |
| .env | 项目根目录；DEEPSEEK（主力）+ OPENAI / ANTHROPIC / DASHSCOPE（评审员）；key 不发到任何聊天 |
| 模型名 | 从 `list_models.py` 复制：Anthropic 用连字符 `claude-sonnet-5-5`，OpenAI 用点 `gpt-6.1-sol` |
| 标注表 | Numbers 用"导出为 CSV"；纯数字 paper_id 会丢末尾 0，重标表加了 `id_` 前缀 |
| prompt | 改版先记进 prompts_history.md |
| 部署 | Railway 从 GitHub 自动部署，PORT=8000 |

## 已知问题与待办

- [ ] `deepseek-chat` 不在 DeepSeek 模型列表里（只剩 deepseek-flash / deepseek-v4-pro），可能是兼容旧名；v3 对比做完前不换生成模型
- [ ] 解析器版本号写进缓存 key 和快照（D18）
- [ ] 物理 35 条等朋友盲标（`eval/annotate_physics_blind.csv`）
- [ ] 侧栏支持 `[推断]` 样式
- [ ] harness 下一层：名字检查、限定词守恒；评审员接到 flagged 上
- [ ] 数字检查：`\text{\times}{10}^{-07}` 写法；取整不加"约"会被拦
- [ ] 申请 Jev 早期访问
