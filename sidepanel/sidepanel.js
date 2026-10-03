// sidepanel.js —— 侧栏的逻辑
//
// 三件事：认出当前论文；点「开始分析」时走流式接口；一边收一边渲染。
//
// D8 的核心在「增量提取器」那一段：模型返回的是一整个 JSON，流式传输时
// 中途手里永远是半截的。提取器的工作是"像人一样看"——summary 的开引号
// 一出现就逐字显示；某个字段的 {} 配平那一刻就单独解析它。

const BACKEND = 'https://arxiv-reader-production.up.railway.app';

const FIELDS = [
  ['research_question', '研究问题'],
  ['method',            '方法'],
  ['experiments',       '实验设计'],
  ['findings',          '主要结论'],
  ['limitations',       '局限性'],
];

const els = {
  status: document.getElementById('status'),
  empty: document.getElementById('empty'),
  panel: document.getElementById('panel'),
  paperId: document.getElementById('paperId'),
  paperTitle: document.getElementById('paperTitle'),
  analyzeBtn: document.getElementById('analyzeBtn'),
  manualForm: document.getElementById('manualForm'),
  manualInput: document.getElementById('manualInput'),
  manualError: document.getElementById('manualError'),
  pdfDrop: document.getElementById('pdfDrop'),
  pdfInput: document.getElementById('pdfInput'),
  result: document.getElementById('result'),
};

let currentPaper = null;
let controller = null;      // 用来中断进行中的请求（用户换论文时）

// ───────────────────────────── 论文识别 ─────────────────────────────
//
// 侧栏自己读活动标签页的 URL，不经过 background。
// 侧栏是扩展页面，有完整的 tabs 权限；只要它开着就不会被回收——
// 比"问 background、background 查、再答回来"少一次往返、少一层能出错的东西。

const ARXIV_RE = /arxiv\.org\/(abs|html|pdf)\/((?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?\/\d{7}))/;

// 出版方全文页（D27）：用户自己有权限打开的付费论文，点"分析"时读当前页面交给后端。
// 和 manifest 的 optional_host_permissions 保持一致；权限在第一次分析时才申请
const PUBLISHER_HOSTS = ['pubs.acs.org', 'www.sciencedirect.com', 'link.springer.com',
  'www.nature.com', 'onlinelibrary.wiley.com', 'www.science.org'];

function publisherPaperFromTab(tab) {
  let u;
  try { u = new URL(tab.url); } catch { return null; }
  if (!PUBLISHER_HOSTS.includes(u.hostname)) return null;
  let doi = decodeURIComponent(u.pathname).match(/(10\.\d{4,9}\/[^?#\s]+)/)?.[1];
  const nature = u.hostname === 'www.nature.com' && u.pathname.match(/^\/articles\/([\w.-]+)$/);
  if (!doi && nature) doi = `10.1038/${nature[1]}`;
  const pii = u.hostname === 'www.sciencedirect.com' && /\/pii\//.test(u.pathname);
  if (!doi && !pii) return null;                     // 期刊首页、目录页之类
  doi = doi?.toLowerCase().replace(/\/$/, '');
  const title = (tab.title || '').replace(/\s*[|\-–]\s*(ACS Publications|ScienceDirect|SpringerLink|Nature|Wiley Online Library|Science).*$/i, '').trim();
  return {
    arxivId: doi ? `page:${doi}` : `page:${u.hostname}${u.pathname}`,
    title: title || doi || u.hostname, url: tab.url, isHtmlPage: false, tabId: tab.id,
    page: true, pageDoi: doi || null,
  };
}

/** 从 tab 对象认出论文；不是 arXiv 页面也不是出版方全文页返回 null */
function paperFromTab(tab) {
  const m = tab?.url?.match(ARXIV_RE);
  if (!m) return tab?.url ? publisherPaperFromTab(tab) : null;
  // arXiv 标题形如 "[1706.03762] Attention Is All You Need"，去掉前缀
  const title = (tab.title || '').replace(/^\[[^\]]+\]\s*/, '').trim();
  return { arxivId: m[2], title: title || m[2], url: tab.url, isHtmlPage: m[1] === 'html', tabId: tab.id };
}

function showPaper(paper) {
  // 同一篇论文不重画（标题更新、换到同一篇的另一个标签页都会触发）。
  // 结果属于论文，不属于标签页——只更新 tab 相关字段，结果留着。
  if (paper && paper.arxivId === currentPaper?.arxivId) {
    Object.assign(currentPaper, { tabId: paper.tabId, url: paper.url, isHtmlPage: paper.isHtmlPage, manual: false });
    if (paper.title && paper.title !== paper.arxivId && paper.title !== currentPaper.title) {
      currentPaper.title = paper.title;
      els.paperTitle.textContent = paper.title;
    }
    return;
  }

  currentPaper = paper;
  controller?.abort();       // 换论文了，上一篇还在分析就掐掉

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
  els.result.hidden = true;
  els.result.innerHTML = '';
  setBusy(false);
}

async function syncActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    const paper = paperFromTab(tab);
    // 手动粘进来的论文不因为切到别的标签页就丢掉；切到另一篇 arXiv 才换
    if (!paper && currentPaper?.manual) return;
    showPaper(paper);
  } catch (e) {
    console.error('[panel] 读标签页失败：', e);
    showPaper(null);
  }
}

// 打开时查一次；切标签、当前标签导航或标题变化时再查
syncActiveTab();
chrome.tabs.onActivated.addListener(syncActiveTab);
chrome.tabs.onUpdated.addListener((_tabId, info, tab) => {
  if (tab.active && (info.status === 'complete' || info.url || info.title)) syncActiveTab();
});

// ───────────────────────────── 手动输入 ─────────────────────────────
//
// 试用反馈（DECISIONS D14b）：不在 arXiv 页面读论文的人，侧栏碰不到。
// 给一个不依赖当前标签页的入口：粘链接、裸 ID 或 arXiv 的 DOI 都认。

const BARE_ID_RE = /(?:^|[^\d])((?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?\/\d{7}))(?:v\d+)?(?:$|[^\d])/;

/** 从用户粘的任意文本里认出 arXiv ID；认不出返回 null */
function parseManual(text) {
  const t = text.trim();
  if (!t) return null;
  const m = t.match(ARXIV_RE) || t.replace(/^doi:\s*/i, '').replace(/^10\.48550\/arXiv\./i, '').match(BARE_ID_RE);
  if (!m) return null;
  return m[m.length - 1];
}

const DOI_RE = /\b10\.\d{4,9}\/\S+/i;

function showManualError(...parts) {
  // parts：字符串或 {text, href}；链接用 DOM 拼，不拼 HTML 字符串（标题来自外部接口）
  els.manualError.replaceChildren(...parts.map((p) => {
    if (typeof p === 'string') return document.createTextNode(p);
    const a = document.createElement('a');
    a.href = p.href; a.textContent = p.text; a.target = '_blank'; a.rel = 'noopener';
    return a;
  }));
  els.manualError.hidden = false;
}

function startManual(id, title, extra = {}) {
  els.manualError.hidden = true;
  els.manualInput.value = '';
  currentPaper = null;                                   // 强制重画
  showPaper({ arxivId: id, title: title || id, url: null, isHtmlPage: false, tabId: null, manual: true, ...extra });
  analyze();                                             // 粘完直接开始，少点一次
}

/** 期刊 DOI：后端查 arXiv 版本（OpenAlex / Crossref，见 backend/doi.py） */
async function resolveDoi(text) {
  els.manualError.hidden = true;
  els.status.textContent = '查 DOI…';
  let r;
  try {
    const resp = await fetch(`${BACKEND}/resolve?q=${encodeURIComponent(text)}`);
    r = await resp.json();
    if (!resp.ok) throw new Error(r.detail || resp.status);
  } catch (err) {
    showManualError(`DOI 查询失败：${err.message}`);
    return;
  } finally {
    els.status.textContent = '已就绪';
  }
  if (r.arxiv_id) {
    startManual(r.arxiv_id, r.title);
    return;
  }
  if (r.oa_pdfs?.length) {
    // 没有 arXiv 版本但有开放获取 PDF：后端下载解析（D25）。ID 加 doi: 前缀，和 arXiv ID 不撞
    startManual(`doi:${r.doi}`, r.title, { doi: r.doi });
    return;
  }
  const title = r.title ? `「${r.title}」` : '这篇';
  if (r.abstract) {
    startManual(`doi:${r.doi}`, r.title, { doi: r.doi });   // 只有摘要也能分析，侧栏会提示"仅基于摘要"
    return;
  }
  showManualError(`${title}在 arXiv 上没有找到版本，也没有开放获取副本和摘要。`,
    r.oa_url ? { text: '开放获取页面', href: r.oa_url } : { text: '出版方页面', href: `https://doi.org/${r.doi}` });
}

// ── 本地 PDF（D26）：付费墙论文让有权限的人自己下载后拖进来。文件只发给后端解析，不保存 ──
const MAX_PDF = 40 * 1024 * 1024;

function startUpload(file) {
  if (!file) return;
  if (!/\.pdf$/i.test(file.name) && file.type !== 'application/pdf') {
    showManualError('只支持 PDF 文件');
    return;
  }
  if (file.size > MAX_PDF) {
    showManualError('PDF 超过 40 MB');
    return;
  }
  const title = file.name.replace(/\.pdf$/i, '');
  startManual(`pdf:${file.name}`, title, { file, pdfUrl: URL.createObjectURL(file) });
}

els.pdfInput.addEventListener('change', () => startUpload(els.pdfInput.files[0]));
els.pdfDrop.addEventListener('dragover', (e) => { e.preventDefault(); els.pdfDrop.classList.add('over'); });
els.pdfDrop.addEventListener('dragleave', () => els.pdfDrop.classList.remove('over'));
els.pdfDrop.addEventListener('drop', (e) => {
  e.preventDefault();
  els.pdfDrop.classList.remove('over');
  startUpload(e.dataTransfer.files[0]);
});

els.manualForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = els.manualInput.value.trim();
  // 非 arXiv 的 DOI 先走后端：10.1103/PhysRevB.1234.56789 这种里面的 "1234.56789" 会被误认成 arXiv ID
  if (DOI_RE.test(text) && !/10\.48550\//i.test(text)) {
    resolveDoi(text);
    return;
  }
  const id = parseManual(text);
  if (id) {
    startManual(id);
    return;
  }
  showManualError('没认出 arXiv ID 或 DOI。试试 2408.13687、10.1038/nature14539 这样的格式，或者整条链接');
});

// ───────────────────────────── 调后端（流式） ─────────────────────────────

function setBusy(busy, label) {
  els.analyzeBtn.disabled = busy;
  els.analyzeBtn.textContent = busy ? (label || '分析中…') : '开始分析';
  els.status.textContent = busy ? (label || '分析中') : '已就绪';
}

async function analyze() {
  if (!currentPaper) return;

  // 出版方页面：权限申请必须在用户点击的同步阶段发出，所以放在第一个 await
  let pageHtml = null;
  if (currentPaper.page) {
    const origin = `${new URL(currentPaper.url).origin}/*`;
    let granted = false;
    try { granted = await chrome.permissions.request({ origins: [origin] }); } catch { /* 拒绝或不在列表 */ }
    if (!granted) {
      renderError('需要读取这个页面的权限才能分析。正文只在你点"分析"时读取一次，发给后端解析，不保存。');
      return;
    }
    try {
      const [{ result }] = await chrome.scripting.executeScript({
        target: { tabId: currentPaper.tabId },
        func: () => document.documentElement.outerHTML,
      });
      pageHtml = result;
    } catch (e) {
      renderError(`读取页面失败：${e.message}`);
      return;
    }
  }

  controller = new AbortController();
  const state = { paper: null, jsonBuf: '', progress: null, meta: null, streaming: true };

  setBusy(true, '取论文…');
  els.result.hidden = true;
  els.result.innerHTML = '';

  try {
    const { file } = currentPaper;
    const resp = pageHtml != null
      ? await fetch(`${BACKEND}/analyze/page/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: currentPaper.url, html: pageHtml, doi: currentPaper.pageDoi }),
          signal: controller.signal,
        })
      : file
      ? await fetch(`${BACKEND}/analyze/pdf/stream?name=${encodeURIComponent(file.name)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/pdf' },
          body: file,
          signal: controller.signal,
        })
      : await fetch(`${BACKEND}/analyze/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(currentPaper.doi ? { doi: currentPaper.doi } : { arxiv_id: currentPaper.arxivId }),
          signal: controller.signal,
        });
    if (!resp.ok) throw new Error(`后端返回 ${resp.status}`);

    // 逐块读响应体，按 SSE 的「空行分隔」切成事件
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let sseBuf = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      sseBuf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = sseBuf.indexOf('\n\n')) !== -1) {
        const raw = sseBuf.slice(0, idx);
        sseBuf = sseBuf.slice(idx + 2);
        handleEvent(parseSse(raw), state);
      }
    }
  } catch (e) {
    if (e.name === 'AbortError') return;     // 用户换论文，静默
    renderError(e.message);
  } finally {
    state.streaming = false;
    setBusy(false);
  }
}

/** 把一段 "event: x\ndata: {...}" 解析成 {event, data} */
function parseSse(raw) {
  let event = 'message', data = '';
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  return { event, data: data ? JSON.parse(data) : {} };
}

function handleEvent({ event, data }, state) {
  switch (event) {
    case 'status':
      setBusy(true, data.stage === 'fetching' ? '取论文…' : '模型阅读中…');
      break;
    case 'paper':
      state.paper = data;
      if (currentPaper && data.url && !currentPaper.file) currentPaper.pdfUrl = data.url;   // DOI 论文的开放获取 PDF（D25）
      if (currentPaper?.file && data.arxiv_id && !data.arxiv_id.startsWith('pdf:')) {
        // 上传的 PDF 带 arXiv 水印，后端改用了 arXiv 的 HTML 全文：之后按 arXiv 论文处理（出处能高亮）
        currentPaper.arxivId = data.arxiv_id;
        delete currentPaper.file;
        els.paperId.textContent = data.arxiv_id;
      }
      if (currentPaper?.file && data.title) {                          // 上传的 PDF：用解析出的标题替换文件名
        currentPaper.title = data.title;
        els.paperTitle.textContent = data.title;
      }
      if (data.title && currentPaper && currentPaper.title === currentPaper.arxivId) {
        currentPaper.title = data.title;
        els.paperTitle.textContent = data.title;
      }
      renderProgress(state);
      break;
    case 'delta':
      state.jsonBuf += data.t;
      state.progress = extractProgress(state.jsonBuf);
      renderProgress(state);
      break;
    case 'meta':
      state.meta = data;
      renderProgress(state);
      break;
    case 'verify':
      state.verify = data;
      renderProgress(state);
      break;
    case 'error':
      renderError(data.detail || '未知错误');
      break;
    case 'done':
      state.streaming = false;
      renderProgress(state);
      break;
  }
}

els.analyzeBtn.addEventListener('click', analyze);

// ───────────────────────────── 增量提取器 ─────────────────────────────
//
// 输入：到目前为止收到的半截 JSON 文本
// 输出：{ summary, summaryDone, fields: { method: {...}, ... } }
//
// 只做两件事，都不依赖完整 JSON：
//   1. 找到 "summary": " 之后的内容，逐字给出（找到闭引号就标记完成）
//   2. 对五个字段，找到 "key": { 之后数括号，配平了就 JSON.parse 那一段

/** 从 start（开引号后的第一个字符）扫到闭引号。要跳过转义符。 */
function scanString(buf, start) {
  let i = start;
  while (i < buf.length) {
    const c = buf[i];
    if (c === '\\') { i += 2; continue; }       // \" \\ \n 之类，跳两个字符
    if (c === '"') return { end: i, complete: true };
    i++;
  }
  return { end: buf.length, complete: false };
}

/** 从 start（指向 '{'）扫到配平的 '}'。字符串里的括号不算数。 */
function scanObject(buf, start) {
  let depth = 0, i = start;
  while (i < buf.length) {
    const c = buf[i];
    if (c === '"') {
      const r = scanString(buf, i + 1);
      if (!r.complete) return { complete: false };
      i = r.end + 1;
      continue;
    }
    if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return { end: i, complete: true };
    }
    i++;
  }
  return { complete: false };
}

/** 把 JSON 字符串里的转义还原成可显示的文本；半截的就尽力而为 */
function unescapeJson(s) {
  try { return JSON.parse('"' + s + '"'); }
  catch { return s.replace(/\\n/g, '\n').replace(/\\"/g, '"'); }
}

function extractProgress(buf) {
  const out = { summary: null, summaryDone: false, fields: {} };

  // summary：一个字符串，开引号一出现就能显示
  const k = buf.indexOf('"summary"');
  if (k !== -1) {
    const colon = buf.indexOf(':', k);
    const q = colon === -1 ? -1 : buf.indexOf('"', colon + 1);
    if (q !== -1) {
      const r = scanString(buf, q + 1);
      out.summary = unescapeJson(buf.slice(q + 1, r.end));
      out.summaryDone = r.complete;
    }
  }

  // 五个字段：各是一个对象，配平了才解析
  for (const [key] of FIELDS) {
    const at = buf.indexOf(`"${key}"`);
    if (at === -1) continue;
    const brace = buf.indexOf('{', at);
    if (brace === -1) continue;
    const r = scanObject(buf, brace);
    if (!r.complete) continue;
    try { out.fields[key] = JSON.parse(buf.slice(brace, r.end + 1)); }
    catch { /* 配平了但没解析出来，等下一片 */ }
  }

  return out;
}

// ───────────────────────────── 渲染 ─────────────────────────────

/** 每收到一片就整体重画一次。节点很少，重画比精细更新省心。 */
function renderProgress(state) {
  const frag = document.createDocumentFragment();
  const p = state.progress || { summary: null, summaryDone: false, fields: {} };

  // ── 徽章：第一眼就该看到的东西。流式进行中显示"校验中"，结束后显示数字 ──
  frag.appendChild(verifyBadge(state));

  if (state.paper?.source === 'abstract_only' && !currentPaper?.page) {
    frag.appendChild(hint('本文没有 HTML 版，以下分析仅基于摘要。'));
  }
  if (state.paper?.source === 'page') {
    frag.appendChild(hint('正文读自你当前打开的出版方页面，只在点"分析"时读取一次，不保存。'));
  }
  if (state.paper?.source === 'abstract_only' && currentPaper?.page) {
    frag.appendChild(hint('这个页面上只看到摘要，以下分析仅基于摘要。可能是当前网络没有订阅：换到学校网络或登录机构账号后再点分析。'));
  }
  if (state.paper?.source === 'pdf') {
    const from = state.paper.arxiv_id?.startsWith('pdf:') ? '你上传的 PDF'
      : state.paper.url ? `开放获取副本（${new URL(state.paper.url).hostname}）` : 'PDF';
    const lead = from === '你上传的 PDF' ? '' : '本文没有 HTML 版，';
    frag.appendChild(hint(`${lead}正文从${from}解析：公式可能不完整，点出处只能跳到所在页。`));
  }
  if (state.paper?.truncated_sections?.length) {
    frag.appendChild(hint(`因篇幅未包含：${state.paper.truncated_sections.join('、')}`));
  }

  if (p.summary != null) {
    frag.appendChild(block('导读', p.summary + (p.summaryDone ? '' : ' ▍'), []));
  }

  for (const [key, label] of FIELDS) {
    const f = p.fields[key];
    if (f) frag.appendChild(block(label, f.text || '', f.evidence || []));
    else if (state.streaming) frag.appendChild(pendingBlock(label));
  }

  if (state.meta) {
    const m = state.meta;
    const src = m.cache_hit ? '缓存' : `${m.prompt_tokens ?? '?'} 入 / ${m.completion_tokens ?? '?'} 出`;
    frag.appendChild(hint(`${src} · ${m.elapsed_s}s`));
  }

  els.result.replaceChildren(frag);
  els.result.hidden = false;
}

/**
 * 顶部徽章。这是产品定位的一句话——"每个论断都可点击跳原文，并已自动校验"。
 * 朋友说"跟翻译差不多"，就是因为这句话之前藏在底部小字里。
 */
function verifyBadge(state) {
  const v = state.verify;
  const badge = el('div', 'badge');

  if (!v || v.error) {
    badge.classList.add('badge-pending');
    badge.appendChild(el('span', 'badge-icon', '◌'));
    badge.appendChild(el('span', 'badge-main', state.streaming ? '正在校验每条论断的出处…' : '校验未完成'));
    return badge;
  }

  const total = v.quote_total || 0;
  const hits = v.quote_hits || 0;
  const allGood = total > 0 && hits === total;

  badge.classList.add(allGood ? 'badge-ok' : 'badge-warn');
  badge.appendChild(el('span', 'badge-icon', allGood ? '✓' : '△'));

  const main = el('span', 'badge-main');
  main.textContent = total === 0
    ? '本次分析没有引用原文'
    : allGood
      ? `${total} 条引文全部在原文中找到出处`
      : `${hits}/${total} 条引文在原文中找到出处`;
  badge.appendChild(main);

  const sub = el('span', 'badge-sub', '点击任意段落 ID 可跳转到原文并高亮');
  badge.appendChild(sub);
  return badge;
}

/**
 * 点一个段落 ID → 跳到原文并高亮。
 *   - 当前标签已经是这篇论文的合适页面 → 直接发 HIGHLIGHT 给 content script
 *   - 否则导航过去，URL 带 #<pid>，content script 加载时看到 hash 自己高亮
 * 摘要在 /abs/ 和 /html/ 都有；其他段落只在 /html/ 有。
 * 从 PDF 解析的论文（D21）段落 ID 是 pg3.b4：没有段落锚点，只能打开 PDF 跳到第 3 页，不高亮。
 */
async function jumpTo(pid) {
  if (!currentPaper) return;
  const { arxivId, tabId, isHtmlPage, doi, pdfUrl, file } = currentPaper;
  if (currentPaper.page) {                 // 出版方页面（D27）：元素有 id 就滚过去高亮；pp<n> 这种编的 ID 不跳
    if (/^pp\d+$/.test(pid) || tabId == null) return;
    await chrome.tabs.update(tabId, { active: true });
    await chrome.scripting.executeScript({
      target: { tabId },
      args: [pid],
      func: (id) => {
        const el = id === 'abstract'
          ? document.querySelector('.article_abstract, #abstract, #Abs1, .abstract, [id^=abstract]')
          : document.getElementById(id);
        if (!el) return false;
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const old = el.style.outline;
        el.style.outline = '2px solid rgba(15, 110, 92, 0.55)';
        setTimeout(() => { el.style.outline = old; }, 2500);
        return true;
      },
    }).catch(() => {});
    return;
  }
  const page = /^pg(\d+)\.b\d+$/.exec(pid);
  if (file && !page) return;              // 上传的 PDF 没有摘要页可跳
  if (file && page && arxivId.startsWith('pdf:')) {
    const url = `${pdfUrl}#page=${page[1]}`;   // 本地文件的 blob 地址，Chrome 自带的 PDF 阅读器打开
    if (tabId == null) currentPaper.tabId = (await chrome.tabs.create({ url })).id;
    else await chrome.tabs.update(tabId, { url });
    return;
  }
  if (doi && !page) {                     // DOI 论文没有 arXiv 页面：摘要等出处打开出版方页面
    const url = `https://doi.org/${doi}`;
    if (tabId == null) currentPaper.tabId = (await chrome.tabs.create({ url })).id;
    else await chrome.tabs.update(tabId, { url });
    return;
  }
  if (page) {
    const url = doi && pdfUrl ? `${pdfUrl.split('#')[0]}#page=${page[1]}` : `https://arxiv.org/pdf/${arxivId}#page=${page[1]}`;
    if (tabId == null) {
      currentPaper.tabId = (await chrome.tabs.create({ url })).id;
      currentPaper.isHtmlPage = false;
    } else {
      await chrome.tabs.update(tabId, { url });
    }
    return;
  }
  const canHighlightHere = pid === 'abstract' || isHtmlPage;

  if (canHighlightHere) {
    try {
      const r = await chrome.tabs.sendMessage(tabId, { type: 'HIGHLIGHT', pid });
      if (r?.ok) return;
    } catch {
      // content script 可能没注入（页面在装插件前就开着），退回导航
    }
  }

  const base = pid === 'abstract' ? 'abs' : 'html';
  const url = `https://arxiv.org/${base}/${arxivId}#${encodeURIComponent(pid)}`;
  if (tabId == null) {
    // 手动粘进来的论文没有对应标签页：新开一个，之后的跳转都复用它
    const tab = await chrome.tabs.create({ url });
    currentPaper.tabId = tab.id;
    currentPaper.isHtmlPage = base === 'html';
    return;
  }
  await chrome.tabs.update(tabId, { url });
}

/** 段落 ID 的小标签，可点 */
function idChip(pid) {
  const chip = el('button', 'id-chip', pid);
  chip.type = 'button';
  chip.title = '跳到原文';
  chip.addEventListener('click', () => jumpTo(pid));
  return chip;
}

/** 推断句的标记：灰色、不可点——它没有出处可跳（D19） */
function inferTag() {
  const tag = el('span', 'infer-tag', '推断');
  tag.title = '对原文的归纳或延伸，原文没有直接这样说';
  return tag;
}

/** 把正文里的 [S3.p1] 变成可点的标签，[推断] 变成灰色标记，其余文字原样 */
function textWithChips(text) {
  const p = el('p');
  const re = /\[(?:([A-Za-z0-9.]+)|推断)\]/g;
  let last = 0, m;
  const s = text || '';
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) appendMath(p, s.slice(last, m.index));
    p.appendChild(m[1] ? idChip(m[1]) : inferTag());
    last = re.lastIndex;
  }
  if (last < s.length) appendMath(p, s.slice(last));
  return p;
}

function block(label, text, evidence) {
  const div = el('div', 'block');
  div.appendChild(el('h2', null, label));
  div.appendChild(textWithChips(text));

  if (evidence.length) {
    // 默认收起。让用户主动点开发现"每句话都有依据"，比全铺开更有冲击力
    const toggle = el('button', 'ev-toggle', `查看出处 (${evidence.length})`);
    toggle.type = 'button';
    const list = el('div', 'ev-list');
    list.hidden = true;

    for (const ev of evidence) {
      const q = el('div', 'quote');
      q.appendChild(idChip(ev.id));
      appendMath(q, ' ' + (ev.quote || ''));
      list.appendChild(q);
    }

    toggle.addEventListener('click', () => {
      list.hidden = !list.hidden;
      toggle.textContent = list.hidden ? `查看出处 (${evidence.length})` : '收起出处';
    });

    div.appendChild(toggle);
    div.appendChild(list);
  }
  return div;
}

function pendingBlock(label) {
  const div = el('div', 'block pending');
  div.appendChild(el('h2', null, label));
  div.appendChild(el('p', 'pending-text', '…'));
  return div;
}

function hint(text) { return el('div', 'hint-line', text); }

function renderError(message) {
  const div = el('div', 'error');
  div.appendChild(el('strong', null, '分析失败'));
  div.appendChild(el('p', null, message));
  els.result.replaceChildren(div);
  els.result.hidden = false;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
