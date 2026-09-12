// background.js —— service worker
//
// 【这是 MV3 最容易踩坑的地方，先看懂再往下写】
// service worker 会被浏览器随时回收（空闲几十秒就可能被杀掉）。
// 所以：
//   1. 不要在这里用全局变量存状态 —— 醒来就没了，必须用 chrome.storage
//   2. 不要在这里跑长任务 —— 耗时的活全部交给后端，前端只负责收结果
//
// 它在这个项目里的职责很窄：转发消息 + 管侧栏开关。

// 点击工具栏图标时打开侧栏
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.error('[bg] 设置侧栏行为失败：', err));
});

// 收到 content script 的消息：记下当前论文，并通知侧栏刷新
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'PAPER_DETECTED') {
    const paper = {
      arxivId: msg.arxivId,
      title: msg.title,
      url: msg.url,
      tabId: sender.tab?.id,
    };

    // 用 storage 而不是全局变量 —— 见文件顶部的说明
    chrome.storage.session.set({ currentPaper: paper });

    // 侧栏可能开着也可能没开。没开的时候这条消息没人接，
    // 会抛 "Receiving end does not exist"，catch 掉即可，不是错误。
    chrome.runtime.sendMessage({ type: 'PAPER_CHANGED', paper }).catch(() => {});
  }

  // 侧栏刚打开时会主动问一次当前是哪篇论文
  if (msg.type === 'GET_CURRENT_PAPER') {
    chrome.storage.session.get('currentPaper').then((data) => {
      sendResponse(data.currentPaper || null);
    });
    return true; // 关键：告诉 Chrome 这是异步响应，否则 sendResponse 会失效
  }
});
