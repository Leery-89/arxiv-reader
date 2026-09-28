// content.js —— 运行在 arXiv 页面里的脚本
//
// 只有一个职责：收到「高亮某段」的指令时滚过去并高亮。
// 论文识别不在这里做——那是 background 读标签页 URL 的事（见 DECISIONS.md）。
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
