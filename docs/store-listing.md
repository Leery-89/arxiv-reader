# Chrome Web Store 上架文案

提交表单时逐项复制。所有内容都对应仓库里的真实行为，不要夸大。

---

## Store listing

**Name**: arXiv Reader

**Summary**（132 字符以内）:
Structured reading guide for arXiv papers — every claim traced to a paragraph and verified against the source.

**Description**:
arXiv Reader turns any arXiv paper into a structured reading guide in Chrome's side panel: research question, method, experiments, findings, and limitations, plus a short overview — all in Chinese.

What makes it different from a summary tool:

• Every claim carries a paragraph ID. Click it and the page scrolls to that paragraph and highlights it.
• Every quoted piece of evidence is checked against the source text after generation. The panel shows how many citations were verified.
• If the paper does not discuss something (e.g. limitations), the guide says so instead of guessing.

How it works: click the toolbar icon on any arxiv.org/abs, /html or /pdf page, then click 开始分析. The summary streams in within a second or two; the full guide takes about ten seconds. Re-opening a paper is instant.

Open source: https://github.com/Leery-89/arxiv-reader — including a design log explaining every decision and the prompt version history.

Known limitations: formulas are shown as LaTeX source; papers without an arXiv HTML version (about 1 in 5) fall back to abstract-only analysis and the panel says so.

**Category**: Productivity
**Language**: Chinese (Simplified)

---

## Privacy practices

**Single purpose**:
Generate a structured, source-verified reading guide for the arXiv paper the user is currently viewing.

**Permission justifications**:

- `tabs`: Read the active tab's URL and title to identify which arXiv paper the user is viewing. No other tab data is read.
- `sidePanel`: Display the reading guide in Chrome's side panel.
- Host permission `https://arxiv.org/*`: Inject a small script that scrolls to and highlights a paragraph when the user clicks a citation in the panel.
- Host permission `https://arxiv-reader-production.up.railway.app/*`: Send the arXiv identifier to our backend and receive the analysis.

**Remote code**: No. All extension code is in the package.

**Data usage** (勾选项):
- ☑ Website content — the arXiv identifier of the page the user chooses to analyse
- ☐ 其他全部不勾

**Certifications** (三个都勾):
- ☑ I do not sell or transfer user data to third parties, outside of the approved use cases
- ☑ I do not use or transfer user data for purposes that are unrelated to my item's single purpose
- ☑ I do not use or transfer user data to determine creditworthiness or for lending purposes

**Privacy policy URL**:
https://github.com/Leery-89/arxiv-reader/blob/main/PRIVACY.md

---

## 截图要求

至少 1 张，最多 5 张。尺寸 **1280×800** 或 640×400，PNG。建议三张：
1. 论文页 + 侧栏，徽章绿色，导读可见
2. 某个字段"查看出处"展开，引文和 ID 标签可见
3. 点了 ID 之后原文段落高亮的样子

Mac 截图后用预览打开 → 工具 → 调整大小 → 1280×800（勾"缩放比例"可能会裁剪，不勾的话先把浏览器窗口拉成 16:10 再截）。
