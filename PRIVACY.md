# Privacy Policy — arXiv Reader

_Last updated: 2026-09-28_

arXiv Reader is a Chrome extension that summarises arXiv papers. This page
explains what it sends, what it stores, and what it does not do.

## What the extension sends

When you click **开始分析** on an arXiv paper page, the extension sends the
paper's **arXiv identifier** (for example `1706.03762`) to our backend at
`arxiv-reader-production.up.railway.app`. Nothing else from the page or your
browser is sent.

The backend fetches the paper's public text from arxiv.org, sends that text to
a third-party language-model API (DeepSeek) to generate the summary, and
returns the result to the extension.

## What the backend stores

- **Analysis results**, keyed by arXiv identifier, so that the same paper does
  not have to be re-analysed. These contain only content derived from the
  public paper.
- **Request logs** with: timestamp, arXiv identifier, token counts, response
  time, and the result of the citation check. Your IP address is held in
  memory only for rate limiting and is not written to the log.

## What we do not do

- We do not collect your name, email, browsing history, or any page content
  other than the arXiv identifier of the paper you explicitly ask to analyse.
- We do not use cookies or tracking pixels.
- We do not sell or share data with anyone. The only third party that sees
  paper text is the language-model API used to generate the summary.

## Permissions

- `tabs` — to read the current tab's URL and title so the panel knows which
  paper you are viewing.
- `sidePanel` — to show the reading guide in Chrome's side panel.
- `host_permissions` for `arxiv.org` — to highlight a paragraph on the page
  when you click a citation.
- `host_permissions` for our backend — to request the analysis.

## Contact

Open an issue at https://github.com/Leery-89/arxiv-reader/issues.
