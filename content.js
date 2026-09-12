// content.js —— 运行在 arXiv 页面里的脚本
//
// 它只干一件事：认出当前页面是哪篇论文，把 arXiv ID 报给扩展。
// 注意它拿不到扩展的 UI，也存不了东西，只能通过消息往外传。

/**
 * 从当前 URL 里抠出 arXiv ID。
 *
 * arXiv 的 URL 有好几种长相：
 *   https://arxiv.org/abs/2301.07041
 *   https://arxiv.org/abs/2301.07041v2      ← 带版本号
 *   https://arxiv.org/html/2301.07041v1
 *   https://arxiv.org/pdf/2301.07041
 *   https://arxiv.org/abs/cs/0701001        ← 2007 年前的老格式
 *
 * 返回不带版本号的 ID，因为缓存要按论文而不是按版本来存。
 */
function extractArxivId(url) {
  // 新格式：4 位数字 + 点 + 4~5 位数字
  const modern = url.match(/arxiv\.org\/(?:abs|html|pdf)\/(\d{4}\.\d{4,5})/);
  if (modern) return modern[1];

  // 老格式：学科分类 + 斜杠 + 7 位数字
  const legacy = url.match(/arxiv\.org\/(?:abs|html|pdf)\/([a-z-]+(?:\.[A-Z]{2})?\/\d{7})/);
  if (legacy) return legacy[1];

  return null;
}

/** 顺手抓一下标题，侧栏可以先显示出来，不用等后端 */
function extractTitle() {
  // abs 页面的标题在这个元素里
  const el = document.querySelector('h1.title');
  if (el) return el.textContent.replace(/^Title:\s*/, '').trim();

  // html 版本的结构不一样
  const h1 = document.querySelector('h1');
  return h1 ? h1.textContent.trim() : document.title;
}

const arxivId = extractArxivId(window.location.href);

if (arxivId) {
  chrome.runtime.sendMessage({
    type: 'PAPER_DETECTED',
    arxivId,
    title: extractTitle(),
    url: window.location.href,
  });
}
