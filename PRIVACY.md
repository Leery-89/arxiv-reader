# Privacy Policy — arXiv Reader

_Last updated: 2026-10-03_

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

Other inputs, each sent only when you explicitly ask for an analysis:

- **A DOI** you paste. The backend looks it up on OpenAlex and Crossref and, if
  there is no arXiv version, downloads a legal open-access copy when one exists.
- **A PDF file** you drag into the panel. The file is sent to the backend,
  parsed in memory and discarded; it is never written to disk.
- **A publisher page you have open** (ACS, ScienceDirect, Springer, Nature,
  Wiley, Science). When you click analyse, the extension reads that page's HTML
  once and sends it to the backend, which extracts the article text in memory
  and discards the HTML. Use this only for articles you are entitled to read;
  some institutional licences restrict sending full text to third-party
  services.

## What the backend stores

- **Analysis results**, keyed by arXiv identifier, DOI, or a hash of the
  uploaded PDF, so that the same paper does not have to be re-analysed. The
  paper text itself, uploaded PDFs and page HTML are not stored.
- **Request logs** with: timestamp, arXiv identifier, token counts, response
  time, and the result of the citation check. Your IP address is held in
  memory only for rate limiting and is not written to the log.

## What we do not do

- We do not collect your name, email, or browsing history. Page content is read
  only from the tab you explicitly ask to analyse, only at that moment.
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
- `scripting` and optional host permissions for the publisher sites listed
  above — requested the first time you analyse a page on that site, used to
  read the page you asked to analyse and to scroll to a cited paragraph.

## Contact

Open an issue at https://github.com/Leery-89/arxiv-reader/issues.
