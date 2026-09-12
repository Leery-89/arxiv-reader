// sidepanel.js —— 侧栏的逻辑
//
// D1–D2 阶段它只做两件事：认出当前论文、把标题显示出来。
// 「开始分析」按钮现在是空的，D5–D7 接后端时再填。

const els = {
  status: document.getElementById('status'),
  empty: document.getElementById('empty'),
  panel: document.getElementById('panel'),
  paperId: document.getElementById('paperId'),
  paperTitle: document.getElementById('paperTitle'),
  analyzeBtn: document.getElementById('analyzeBtn'),
  result: document.getElementById('result'),
};

/** 把界面切换到「有论文」或「没论文」两种状态 */
function render(paper) {
  if (!paper) {
    els.empty.hidden = false;
    els.panel.hidden = true;
    els.status.textContent = '等待论文';
    return;
  }

  els.empty.hidden = true;
  els.panel.hidden = false;
  els.paperId.textContent = paper.arxivId;
  els.paperTitle.textContent = paper.title || '(无标题)';
  els.status.textContent = '已就绪';

  // 换论文了，把上一篇的结果清掉
  els.result.hidden = true;
  els.result.innerHTML = '';
}

// 侧栏打开时，主动问一次当前是哪篇论文
chrome.runtime.sendMessage({ type: 'GET_CURRENT_PAPER' }, (paper) => {
  if (chrome.runtime.lastError) {
    // service worker 可能正在冷启动，忽略这次，等下面的推送
    console.debug('[panel]', chrome.runtime.lastError.message);
    return;
  }
  render(paper);
});

// 用户切到另一篇论文时，background 会推送过来
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'PAPER_CHANGED') render(msg.paper);
});


// ───────────────────────────────────────────────────────────
// TODO(D5–D7)：接后端
//
// 这里不要急着写。等 D5–D6 后端的 /analyze 跑通了再回来填。
// 届时要处理的事情，先记在这里当提纲：
//
//   1. fetch POST 到 http://localhost:8000/analyze，body 带 arxivId
//   2. 用 SSE 或 ReadableStream 接流式响应，边收边渲染（D8）
//   3. 超时、网络失败、模型返回非法 JSON —— 三种错误分别给不同提示
//   4. 渲染时每个字段下面挂它的 quote（用 .quote 这个 class）
// ───────────────────────────────────────────────────────────

els.analyzeBtn.addEventListener('click', () => {
  els.analyzeBtn.disabled = true;
  els.analyzeBtn.textContent = '后端还没接（D5–D7）';
  setTimeout(() => {
    els.analyzeBtn.disabled = false;
    els.analyzeBtn.textContent = '开始分析';
  }, 1600);
});
