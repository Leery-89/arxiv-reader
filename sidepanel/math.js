/**
 * 公式渲染（D18）：文本里 $...$ 包着的是 arXiv 给的原始 LaTeX，用 KaTeX 渲染，不自己转换。
 *   - KaTeX 认不出的写法（作者自定义宏等）→ 原样显示 LaTeX 源码，不显示红色报错
 *   - trust 关闭：\href、\url 这类会生成链接的命令不执行
 *   - KaTeX 没加载到 → 退回去掉 $ 的纯文本（旧行为）
 * 评测集 quote 里 531 个不同公式，KaTeX 直接渲染 513 个；加下面三个 LaTeXML 宏后 523 个，
 * 剩下 8 个是 \parbox 或被截断的半截公式，显示源码。
 */
// pandoc 的规则：$ 后紧跟非空白、闭合 $ 前是非空白且后面不是数字——"$5 and $10" 这种金额不会被当成公式
const MATH_RE = /\$(?=\S)([^$]*?\S)\$(?!\d)/g;

// 只影响显示，不改存下来的文本（D18）
const MATH_MACROS = {
  '\\SIUnitSymbolMicro': '\\text{µ}',   // siunitx 的 μ（μm、μs）
  '\\penalty': '',                        // 断行提示，显示时无意义
  '\\mbox': '\\text',
};

/** 把含 $...$ 的文本追加到 parent：公式渲染成 KaTeX，其余是文本节点 */
function appendMath(parent, text) {
  const s = text || '';
  let last = 0, m;
  MATH_RE.lastIndex = 0;
  while ((m = MATH_RE.exec(s)) !== null) {
    if (m.index > last) parent.appendChild(document.createTextNode(s.slice(last, m.index)));
    parent.appendChild(mathNode(m[1]));
    last = MATH_RE.lastIndex;
  }
  if (last < s.length) parent.appendChild(document.createTextNode(s.slice(last)));
  return parent;
}

function mathNode(tex) {
  const span = document.createElement('span');
  if (typeof katex === 'undefined') {
    span.textContent = tex;
    return span;
  }
  try {
    katex.render(tex, span, { throwOnError: true, trust: false, strict: 'ignore', output: 'html', macros: { ...MATH_MACROS } });
    span.className = 'math';
    span.title = tex;
  } catch {
    span.textContent = tex;
    span.className = 'math-raw';
  }
  return span;
}
