// sidepanel.js —— 侧栏的逻辑
//
// 三件事：认出当前论文；点「开始分析」时走流式接口；一边收一边渲染。
//
// D8 的核心在「增量提取器」那一段：模型返回的是一整个 JSON，流式传输时
// 中途手里永远是半截的。提取器的工作是"像人一样看"——summary 的开引号
// 一出现就逐字显示；某个字段的 {} 配平那一刻就单独解析它。

const BACKEND = 'http://localhost:8000';   // D12 上线时换成服务器地址

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
  result: document.getElementById('result'),
};

let currentPaper = null;
let controller = null;      // 用来中断进行中的请求（用户换论文时）

// ───────────────────────────── 论文识别 ─────────────────────────────

function showPaper(paper) {
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

chrome.runtime.sendMessage({ type: 'GET_CURRENT_PAPER' }, (paper) => {
  if (chrome.runtime.lastError) return;
  showPaper(paper);
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'PAPER_CHANGED') showPaper(msg.paper);
});

// ───────────────────────────── 调后端（流式） ─────────────────────────────

function setBusy(busy, label) {
  els.analyzeBtn.disabled = busy;
  els.analyzeBtn.textContent = busy ? (label || '分析中…') : '开始分析';
  els.status.textContent = busy ? (label || '分析中') : '已就绪';
}

async function analyze() {
  if (!currentPaper) return;

  controller = new AbortController();
  const state = { paper: null, jsonBuf: '', progress: null, meta: null, streaming: true };

  setBusy(true, '取论文…');
  els.result.hidden = true;
  els.result.innerHTML = '';

  try {
    const resp = await fetch(`${BACKEND}/analyze/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ arxiv_id: currentPaper.arxivId }),
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

  if (state.paper?.source === 'abstract_only') {
    frag.appendChild(hint('本文没有 HTML 版，以下分析仅基于摘要。'));
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

  if (state.verify && !state.verify.error) {
    const v = state.verify;
    const parts = [];
    if (v.quote_total) parts.push(`引文命中 ${v.quote_hits}/${v.quote_total}`);
    if (v.inline_total) parts.push(`内嵌 ID 合规 ${v.inline_hits}/${v.inline_total}`);
    frag.appendChild(hint(parts.join(' · ')));
  }
  if (state.meta) {
    const m = state.meta;
    const src = m.cache_hit ? '缓存' : `${m.prompt_tokens ?? '?'} 入 / ${m.completion_tokens ?? '?'} 出`;
    frag.appendChild(hint(`${src} · ${m.elapsed_s}s`));
  }

  els.result.replaceChildren(frag);
  els.result.hidden = false;
}

/** 去掉 LaTeX 的 $ 包裹，先让公式可读；真正渲染公式是以后的事 */
function display(text) {
  return (text || '').replace(/\$([^$]+)\$/g, '$1');
}

/** 点一个段落 ID → 让 background 把标签页带到原文那一段 */
function jumpTo(pid) {
  if (!currentPaper) return;
  chrome.runtime.sendMessage({ type: 'JUMP_TO', arxivId: currentPaper.arxivId, pid });
}

/** 段落 ID 的小标签，可点 */
function idChip(pid) {
  const chip = el('button', 'id-chip', pid);
  chip.type = 'button';
  chip.title = '跳到原文';
  chip.addEventListener('click', () => jumpTo(pid));
  return chip;
}

/** 把正文里的 [S3.p1] 变成可点的标签，其余文字原样 */
function textWithChips(text) {
  const p = el('p');
  const re = /\[([A-Za-z0-9.]+)\]/g;
  let last = 0, m;
  const s = display(text);
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) p.appendChild(document.createTextNode(s.slice(last, m.index)));
    p.appendChild(idChip(m[1]));
    last = re.lastIndex;
  }
  if (last < s.length) p.appendChild(document.createTextNode(s.slice(last)));
  return p;
}

function block(label, text, evidence) {
  const div = el('div', 'block');
  div.appendChild(el('h2', null, label));
  div.appendChild(textWithChips(text));
  for (const ev of evidence) {
    const q = el('div', 'quote');
    q.appendChild(idChip(ev.id));
    q.appendChild(document.createTextNode(' ' + display(ev.quote)));
    div.appendChild(q);
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
