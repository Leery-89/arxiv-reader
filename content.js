// content.js —— 运行在 arXiv 页面里的脚本
//
// 两件事：
//   1. 认出当前页面是哪篇论文，把 arXiv ID 报给扩展
//   2. 在 /html/ 页面上，收到「高亮某段」的指令时滚过去并高亮（D9）

/**
 * 从当前 URL 里抠出 arXiv ID。
 *
 *   https://arxiv.org/abs/2301.07041
 *   https://arxiv.org/abs/2301.07041v2      ← 带版本号
 *   https://arxiv.org/html/2301.07041v1
 *   https://arxiv.org/pdf/2301.07041
 *   https://arxiv.org/abs/cs/0701001        ← 2007 年前的老格式
 *
 * 返回不带版本号的 ID，因为缓存要按论文而不是按版本来存。
 */
function extractArxivId(url) {
  const modern = url.match(/arxiv\.org\/(?:abs|html|pdf)\/(\d{4}\.\d{4,5})/);
  if (modern) return modern[1];
  const legacy = url.match(/arxiv\.org\/(?:abs|html|pdf)\/([a-z-]+(?:\.[A-Z]{2})?\/\d{7})/);
  if (legacy) return legacy[1];
  return null;
}

function extractTitle() {
  const el = document.querySelector('h1.title');
  if (el) return el.textContent.replace(/^Title:\s*/, '').trim();
  const h1 = document.querySelector('h1');
  return h1 ? h1.textContent.trim() : document.title;
}

const arxivId = extractArxivId(window.location.href);
const isHtmlPage = /arxiv\.org\/html\//.test(window.location.href);

if (arxivId) {
  chrome.runtime.sendMessage({
    type: 'PAPER_DETECTED',
    arxivId,
    title: extractTitle(),
    url: window.location.href,
    isHtmlPage,
  });
}

// ───────────────────────────── 高亮（D9） ─────────────────────────────
//
// 段落 ID 就是 LaTeXML 给元素的 id（D4 沿用原生 ID 的决定在这里兑现）：
// getElementById 一行定位，不需要任何模糊匹配。

const HIGHLIGHT_CSS = `
  .arxiv-reader-hl {
    background: rgba(15, 110, 92, 0.16) !important;
    outline: 2px solid rgba(15, 110, 92, 0.55);
    outline-offset: 4px;
    border-radius: 3px;
    transition: background 0.6s ease;
  }
`;

function ensureStyle() {
  if (document.getElementById('arxiv-reader-style')) return;
  const s = document.createElement('style');
  s.id = 'arxiv-reader-style';
  s.textContent = HIGHLIGHT_CSS;
  document.head.appendChild(s);
}

function highlight(pid) {
  // 摘要是个逻辑 ID：/html/ 页里是 .ltx_abstract，/abs/ 页里是 blockquote.abstract
  const target = pid === 'abstract'
    ? document.querySelector('.ltx_abstract, blockquote.abstract')
    : document.getElementById(pid);
  if (!target) return false;

  ensureStyle();
  document.querySelectorAll('.arxiv-reader-hl').forEach((n) => n.classList.remove('arxiv-reader-hl'));
  target.classList.add('arxiv-reader-hl');
  target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return true;
}

// 所有 arXiv 论文页都监听高亮指令：/html/ 页能高亮任何段落，/abs/ 页只能高亮摘要
if (arxivId) {
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === 'HIGHLIGHT') {
      sendResponse({ ok: highlight(msg.pid) });
    }
  });

  // 跳转过来时 URL 带 #S3.p1，页面一加载就高亮
  if (location.hash.length > 1) {
    // 等一拍让浏览器先完成自己的锚点跳转
    setTimeout(() => highlight(decodeURIComponent(location.hash.slice(1))), 300);
  }
}
