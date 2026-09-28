// background.js —— service worker
//
// 【MV3 最容易踩坑的地方，先看懂再往下写】
// service worker 会被浏览器随时回收（空闲几十秒就可能被杀掉）。
//   1. 不要用全局变量存状态 —— 醒来就没了，必须用 chrome.storage
//   2. 不要跑长任务 —— 耗时的活全部交给后端
//
// 职责：转发消息、管侧栏开关、以及 D9 的「跳到原文段落」。

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.error('[bg] 设置侧栏行为失败：', err));
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // content script 报告：当前标签页是哪篇论文
  if (msg.type === 'PAPER_DETECTED') {
    const paper = {
      arxivId: msg.arxivId,
      title: msg.title,
      url: msg.url,
      isHtmlPage: msg.isHtmlPage,
      tabId: sender.tab?.id,
    };
    chrome.storage.session.set({ currentPaper: paper });
    chrome.runtime.sendMessage({ type: 'PAPER_CHANGED', paper }).catch(() => {});
  }

  // 侧栏刚打开，问一次当前论文
  if (msg.type === 'GET_CURRENT_PAPER') {
    chrome.storage.session.get('currentPaper').then((data) => {
      sendResponse(data.currentPaper || null);
    });
    return true;   // 异步响应
  }

  // 侧栏点了某个段落 ID → 跳到原文并高亮（D9）
  if (msg.type === 'JUMP_TO') {
    jumpTo(msg.arxivId, msg.pid).then(sendResponse);
    return true;
  }
});

/**
 * 跳到 /html/ 页面的某一段。
 *
 * 两种情况：
 *   - 当前标签已经是这篇论文的 /html/ 页 → 直接发 HIGHLIGHT 给 content script
 *   - 当前是 /abs/ 或别的页 → 导航到 /html/<id>#<pid>，
 *     content script 加载时看到 hash 会自己高亮（见 content.js）
 */
async function jumpTo(arxivId, pid) {
  const { currentPaper } = await chrome.storage.session.get('currentPaper');
  const tabId = currentPaper?.tabId;
  if (tabId == null) return { ok: false, reason: 'no tab' };

  const samePaper = currentPaper.arxivId === arxivId;

  // 摘要在 /abs/ 和 /html/ 页上都有，当前页是这篇论文就直接高亮，不用跳
  // 其他段落只在 /html/ 页有，当前页是 /html/ 才能直接高亮
  const canHighlightHere = samePaper && (pid === 'abstract' || currentPaper.isHtmlPage);

  if (canHighlightHere) {
    try {
      const r = await chrome.tabs.sendMessage(tabId, { type: 'HIGHLIGHT', pid });
      if (r?.ok) return r;
    } catch {
      // content script 可能还没就绪，退回导航
    }
  }

  // 摘要跳 /abs/（没有 HTML 版的论文只有这一页），其他段落跳 /html/
  const base = pid === 'abstract' ? 'abs' : 'html';
  await chrome.tabs.update(tabId, { url: `https://arxiv.org/${base}/${arxivId}#${encodeURIComponent(pid)}` });
  return { ok: true, navigated: true };
}
