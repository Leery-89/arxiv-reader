// background.js —— service worker
//
// 现在只剩一个职责：让点工具栏图标打开侧栏。
//
// 论文识别和跳转高亮都在 sidepanel.js 里——侧栏是扩展页面，有完整的 tabs
// 权限，而且只要开着就不会被回收。经过 service worker 中转只会多一层
// 冷启动和消息往返的不确定性（见 DECISIONS.md D12）。
//
// 【MV3 提醒】service worker 空闲几十秒就会被回收，不要在这里存状态、
// 不要跑长任务。这个文件现在什么都不存，正好。

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.error('[bg] 设置侧栏行为失败：', err));
});
