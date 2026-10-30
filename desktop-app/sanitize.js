// 候选回复的输出清洗（T01392 / M1.2）——与 m1-poc sanitize.py 逐函数同构的 JS 移植。
//
// SRS 附录 C 两条实测约束：
// - C3：LLM 输出的 markdown 格式符（**、*、链接）直接注入聊天框会变成噪声正文；
// - C25：注入文本须剥离 HTML/标记，防标记污染。
// 清洗只剥离格式符号、不改写文字——改写会触发 C10 的语义丢失风险。
//
// ⚠ 顺序铁律（T01126 真机实测固化，见 docs/t01391-m1-architecture-review.md §2）：
//   先剥 markdown 再剥标签前缀——标签前缀含 bullet [-*+•]，
//   对未清洗文本会误剥 markdown 星号（「**好呀**」→「*好呀」事故）。
//
// ⚠ 安全侧约定（SonarQube S5852）：候选文本来自外部 LLM 输出，正则一旦含
//   「量化的闭包类 + 后继限定」（`[^\]]*\]`、`[\s|]+$`、`(.+?)…`）就可能被构造串拖成指数回溯。
//   这里把被点名的模式全部改写为显式线性扫描——字符串解析结果与原正则逐字符等价，
//   且成本与输入长度成线性（详见各函数内的回溯穷举注释）。
'use strict';

const EMPHASIS = /(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1/gs;
const CODE_FENCE = /```[a-zA-Z0-9]*\n?/g;
const INLINE_CODE = /`([^`]*)`/g;
const HEADING = /^[ \t]{0,3}#{1,6}[ \t]*/gm;
const LIST_MARK = /^[ \t]{0,3}(?:[-*+]|\d+\.)[ \t]+/gm;
const HTML_TAG = /<\/?[a-zA-Z][^>]*>/g;
const STRAY_MARK = /(?<!\w)[*_~]{1,3}(?!\w)/g;
const BLANK_RUN = /\n{3,}/g;
// T01126：LLM 把结构化标题混进候选（「## 候选回复」「理由：xxx」）
// T02224：「内容」也是标签词——提示词契约本身叫「内容|理由」，模型会把字段名当行首标签
// 吐回来（真机实测候选「内容：对方回复较为简短…」）；长词在前保证「回复内容」优先整体匹配。
const LABEL_WORDS = '(?:候选回复|参考回复|回复内容|内容|回复|候选|理由|原因|解释)';
const LABEL_LINE = new RegExp(String.raw`^[ \t#*_>]*${LABEL_WORDS}\s*[:：]?\s*[#*_\s]*$`);
// 悬空管道符：行尾的 |（「内容|」）或行首的 |（「|理由」）；全/半角空格一并容忍
const DANGLING_PIPE_TAIL = /\|[ \t\u3000]*$/;
const LEADING_PIPE = /^[ \t\u3000]*\|/;

// ===== 字符谓词（与正则字符类逐一对齐，避免语义漂移）=====
// ⚠ JS 的 `\s` 是 Unicode 空白全集（含 \u00a0、\u3000、\u2028…），不是 ASCII 空格表；
//   手写枚举一定会漏，故直接用无量化器的 `/^\s$/` 单字符判定（S5852 只针对量化闭包，单字符类无风险）。
const SPACE_TEST = /^\s$/;
// `[ \t　]` 是显式三字符类，与 `\s` 不等价（不含换行），必须分开维护。
const SPACE_OR_IDEO = new Set([' ', '\t', '　']);
const MD_MODIFIERS = new Set([' ', '\t', '#', '*', '_', '>']);
const LABEL_MODIFIERS = new Set([' ', '\t', '#', '*', '_']);
const BULLET_CHARS = new Set(['-', '*', '+', '•']);
const PAREN_CLOSE = new Set(['）', ')']);

function isSpace(ch) {
  return ch !== undefined && SPACE_TEST.test(ch);
}
function isSpaceOrIdeo(ch) {
  return ch !== undefined && SPACE_OR_IDEO.has(ch);
}
function isColon(ch) {
  return ch === ':' || ch === '：';
}
function isDigit(ch) {
  return ch !== undefined && ch >= '0' && ch <= '9';
}
/** 正则 `\d` 无 unicode 标志 = 仅 ASCII 0-9。 */
function scanDigits(s, k) {
  let j = k;
  while (j < s.length && isDigit(s[j])) j++;
  return j > k ? j : -1;
}
/** 正则 `\s*` 的跳过结果（返回消费后的位置）。 */
function skipSpace(s, k) {
  let j = k;
  while (j < s.length && isSpace(s[j])) j++;
  return j;
}

// ===== 「内容|理由」标签词表（LABEL_LINE/LABEL_PREFIX/前缀剥离共用）=====
const LABEL_WORDS_LIST = ['候选回复', '参考回复', '回复内容', '内容', '回复', '候选', '理由', '原因', '解释'];
const RATIONALE_WORDS = ['理由', '原因', '解释', 'rationale', 'explanation'];
const NUMBERED_WORDS = ['选项', '候选', '方案'];
const DIGIT_STOP = new Set(['.', '、', ')']);

function matchWordAt(s, k, words) {
  for (const w of words) {
    if (s.startsWith(w, k)) return w.length;
  }
  return -1;
}

/** 大小写无关的关键词匹配（原正则带 i；中文不受影响，故统一按小写比对）。 */
function matchWordInsensitive(s, k, words) {
  const slice = s.slice(k, k + 12).toLowerCase();
  for (const w of words) {
    if (slice.startsWith(w)) return w.length;
  }
  return -1;
}

/**
 * markdown 链接 → 链接文字（原 `/\[([^\]]*)\]\([^)]*\)/g` 的线性等价实现）。
 * 引擎行为对齐：lazy `[^\]]*` 停在首个 `]`，随后必须紧跟 `(`，`[^)]*` 停在首个 `)`；
 * 任一环节不满足则该 `[` 无解（中间再出现 `[` 同样无解，因为 `[^\]]` 匹配不了它）。
 */
/**
 * markdown 链接 → 链接文字（原 `/\[([^\]]*)\]\([^)]*\)/g` 的线性等价实现）。
 * 引擎行为对齐：lazy `[^\]]*` 停在首个 `]`，随后必须紧跟 `(`，`[^)]*` 停在首个 `)`；
 * 任一环节不满足则该 `[` 无解（中间再出现 `[` 同样无解，因为 `[^\]]` 匹配不了它）。
 */
function mdLinkParts(s, open) {
  let j = open + 1;
  while (j < s.length && s[j] !== ']') {
    if (s[j] === '[') return null;
    j++;
  }
  if (j >= s.length || s[j + 1] !== '(') return null;
  let k = j + 2;
  while (k < s.length && s[k] !== ')') k++;
  return k < s.length ? { textEnd: j, parenEnd: k } : null;
}

function stripMdLinks(s) {
  let out = '';
  let i = 0;
  while (i < s.length) {
    const open = s.indexOf('[', i);
    if (open < 0) return out + s.slice(i);
    const link = mdLinkParts(s, open);
    if (!link) {
      out += s.slice(i, open + 1);
      i = open + 1;
      continue;
    }
    out += s.slice(i, open) + s.slice(open + 1, link.textEnd);
    i = link.parenEnd + 1;
  }
  return out;
}

/** 关键词 + `\s*` + 冒号 + `\s*` 的跳过终点；冒号缺失返回 -1。 */
function skipColonRun(s, afterKeyword) {
  const beforeColon = skipSpace(s, afterKeyword);
  if (!isColon(s[beforeColon])) return -1;
  return skipSpace(s, beforeColon + 1);
}

function skipSet(s, k, set) {
  let i = k;
  while (i < s.length && set.has(s[i])) i++;
  return i;
}

/** 从 k 起找集合中的首个字符；未命中返回 -1。 */
function indexOfAny(s, k, set) {
  for (let i = k; i < s.length; i++) {
    if (set.has(s[i])) return i;
  }
  return -1;
}

/**
 * T01720：行首「理由：xxx」标签行 → 理由正文；不是标签行返回 null。
 * 原 `/^[ \t#*_>]*(?:[-*+•][ \t\u3000]?)?(?:理由|原因|解释|rationale|explanation)\s*[:：]\s*(.+)$/i`。
 * `*` 同属修饰符类与 bullet 类，所以「`***　理由：`」存在让回一个 `*` 当 bullet 的解——
 * 必须按引擎的回溯顺序（修饰符由长到短、可选组先吞后放）逐个起点试，不能只看极大前缀。
 * 冒号后只剩空白时 `(.+)$` 靠 `\s*` 让出最后一个字符仍算命中（trim 后为空），冒号后什么都没有才不命中。
 */
function findRationaleLineHead(s) {
  for (const p of rationaleStartCandidates(s)) {
    const hit = matchRationaleAt(s, p);
    if (hit !== null) return hit;
  }
  return null;
}

/** 按引擎的回溯顺序产出关键词可能起点：修饰符前缀由长到短，可选 bullet 先吞一个空白再放回。 */
function rationaleStartCandidates(s) {
  const positions = [];
  const prefixEnd = skipSet(s, 0, MD_MODIFIERS);
  for (let k = prefixEnd; k >= 0; k--) {
    if (BULLET_CHARS.has(s[k])) {
      if (isSpaceOrIdeo(s[k + 1])) positions.push(k + 2);
      positions.push(k + 1);
    }
    positions.push(k);
  }
  return positions;
}

/** 位置 p 起「关键词 + 冒号 + 正文」；任一环节不成立返回 null。 */
function matchRationaleAt(s, p) {
  const wordLen = matchWordInsensitive(s, p, RATIONALE_WORDS);
  if (wordLen < 0) return null;
  const beforeColon = skipSpace(s, p + wordLen);
  if (!isColon(s[beforeColon])) return null;
  const afterColon = beforeColon + 1;
  if (afterColon >= s.length) return null;
  let k = afterColon;
  while (k < s.length - 1 && isSpace(s[k])) k++;
  return s.slice(k).trim();
}

/**
 * T01126：内联「… 理由：…」→ 首个正则匹配 {text, rationale}；不匹配返回 null。
 * 原 `/^(.+?)\s*(?:理由|原因|解释)\s*[:：]\s*(.+)$/s`：lazy 头部逐字扩展，
 * 等价于「按位置升序尝试每个关键词起点，取第一个整体成功的」。
 * 关键差异已对齐：`\s*[:：]` 取不到冒号 → 继续试更晚的关键词；
 * 冒号命中但后面只剩空白 → 匹配仍成立（理由 trim 后为空），由调用方决定回落。
 */
function matchInlineRationale(s) {
  const words = ['理由', '原因', '解释'];
  const at = [];
  for (const w of words) {
    for (let p = s.indexOf(w, 1); p >= 0; p = s.indexOf(w, p + 1)) at.push([p, w.length]);
  }
  at.sort((x, y) => x[0] - y[0]);
  for (const [p, len] of at) {
    const beforeColon = skipSpace(s, p + len);
    if (!isColon(s[beforeColon])) continue;
    const afterColon = beforeColon + 1;
    if (afterColon >= s.length) continue; // `(.+)` 无字符可取 → 该位置整体失败，继续扩展头部
    let k = afterColon;
    while (k < s.length - 1 && isSpace(s[k])) k++;
    return { text: s.slice(0, p), rationale: s.slice(k) };
  }
  return null;
}

/**
 * T01720：把「理由：xxx」续行接回上一条候选行，理由行自身不产出候选。
 * 常规补 | 交给 splitRationale 统一解析；上一行以悬空管道符收尾直接续接；
 * 上一行已含 |（已接过度理由）用「；」续接进理由段；无上一行则丢弃。
 */
function mergeRationaleLines(lines) {
  const out = [];
  for (const ln of lines) {
    const rationale = findRationaleLineHead(ln);
    if (rationale !== null) {
      if (out.length && rationale) {
        const prev = out.at(-1);
        if (DANGLING_PIPE_TAIL.test(prev)) out[out.length - 1] = prev + rationale;
        else if (prev.includes('|')) out[out.length - 1] = `${prev}；${rationale}`;
        else out[out.length - 1] = `${prev}|${rationale}`;
      }
      continue;
    }
    out.push(ln);
  }
  return out;
}

/** 「内容|」尾部悬空管道符（原 `/[\s|]+$/`，py 版为 `[\s|]+\Z`）的线性剥除。 */
function trimTrailingPipe(s) {
  let end = s.length;
  while (end > 0 && (s[end - 1] === '|' || isSpace(s[end - 1]))) end--;
  return s.slice(0, end);
}

// T01554：启发式拆分用的强标签词 + 冒号（与 RATIONALE_WORDS 同词表，仅 ASCII 大小写敏感场景）
const EDGE_PUNCT_CHARS = new Set(['：', ':', '。', '，', ',', '.']);

/** `[\s：:。，,.]`——空白并入 `\s` 全集判定，与正则口径一致。 */
function isEdgePunct(ch) {
  return ch !== undefined && (isSpace(ch) || EDGE_PUNCT_CHARS.has(ch));
}

/** 串首/串尾的 `[\s：:。，,.]+` 段（原单条双向 alternation 正则的线性等价）。 */
function stripEdgeRuns(s) {
  let start = 0;
  while (start < s.length && isEdgePunct(s[start])) start++;
  let end = s.length;
  while (end > start && isEdgePunct(s[end - 1])) end--;
  return s.slice(start, end);
}

/** 剥掉「关键词 + 空白 + 冒号 + `[\s：:]*`」（原 `^${pat.source}` 锚定替换 + 二次冒号剥离）。 */
function stripKeywordPrefix(s, kwLen) {
  const colon = skipSpace(s, kwLen);
  if (!isColon(s[colon])) return s; // 调用方已确认冒号存在，此处只是防御
  let i = colon + 1;
  while (i < s.length && (isSpace(s[i]) || isColon(s[i]))) i++;
  return s.slice(i);
}

// T01159：表情收敛（表情是正文一部分，清洗不动它；只压重复、限数量）
const MOD = '\u{1F3FB}-\u{1F3FF}\uFE0F\u20E3';
const ZWJ = '\u200D';
const BASE =
  '\u{1F000}-\u{1FAFF}' +
  '\u{1F1E6}-\u{1F1FF}' +
  '\u2600-\u27BF\u2B00-\u2BFF' +
  '\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9\u21AA' +
  '\u231A\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA' +
  '\u25AA\u25AB\u25B6\u25C0\u25FB-\u25FE' +
  '\u2934\u2935\u3030\u303D\u3297\u3299';
// ⚠ astral 区间字符类必须带 u 标志，否则按代理对拆分导致 Range out of order
const EMOJI_UNIT = new RegExp(`(?:[${BASE}][${MOD}]*(?:${ZWJ}[${BASE}][${MOD}]*)*)`, 'gu');
const EMOJI_STRIP = new RegExp(EMOJI_UNIT.source, 'gu');
const EMOJI_REPEAT = new RegExp(String.raw`(${EMOJI_UNIT.source})(?:[ \t\u3000]*\1)+`, 'gu');
const SPACE_RUN = /[ \t\u3000]{2,}/g;
// 元话语候选过滤（用户反馈 2026-09-27）：只匹配「要求补充聊天上下文」「能力自述」类
// S5843：原单条超复杂正则按顶层 alternation 等价拆为三条顺序 test（无锚点，拆分语义不变）
const META_ASK =
  /请[^，。；！?？]{0,15}(?:提供|补充|给出|发送给我|告诉我|描述|说明)[^，。；！?？]{0,20}(?:聊天记录|上下文|最近(?:说)?的话|更多信息|详细内容|聊天内容)/;
const META_HELPLESS = /(?:这样我才能帮你|才能帮你(?:起草|回复|生成)|无法(?:生成|起草)(?:回复|候选))/;
const META_SELF_REF = /作为一个?(?:AI|ai|人工智能)助手?|作为一个?语言模型/;

function stripMarkdown(text) {
  let out = String(text || '');
  out = out.replaceAll(CODE_FENCE, '');
  out = stripMdLinks(out);
  out = out.replaceAll(INLINE_CODE, '$1');
  out = out.replaceAll(EMPHASIS, '$2');
  out = out.replaceAll(HEADING, '');
  out = out.replaceAll(LIST_MARK, '');
  out = out.replaceAll(STRAY_MARK, '');
  return out;
}

function stripMarkup(text) {
  return String(text || '').replaceAll(HTML_TAG, '');
}

/** 注入输入框前的最终清洗：先 markdown 再 markup，空行收敛 + 尾部悬空管道符剥离。 */
function cleanCandidate(text) {
  let out = stripMarkup(stripMarkdown(text));
  out = out.replaceAll(BLANK_RUN, '\n\n');
  out = trimTrailingPipe(out);
  return out.trim();
}

/** 编号前缀（「选项1（x）：」）在 k 处的贪婪匹配终点；失败返回 -1。
/** 编号前缀（「选项1（x）：」）在 k 处的匹配终点；失败返回 -1。
 *  原 `(?:选项|候选|方案)\s*\d+\s*(?:[（(][^）)]*[）)])?\s*[：:.．]` 的回溯路径已穷举：
 *  `\d+` 回退无第二条解（退到的位置后继仍是数字，既不是括号也不是终止符）；
 *  括号组贪婪但 `[^）)]` 匹配不了闭括号，故停在首个闭括号；跳过括号组则必需的
 *  `[：:.．]` 落在开括号上必然失败——所以整条模式只有单一线性解。 */
function matchNumberedRun(s, k) {
  const wordLen = matchWordAt(s, k, NUMBERED_WORDS);
  if (wordLen < 0) return -1;
  const afterWord = skipSpace(s, k + wordLen);
  const digits = scanDigits(s, afterWord);
  if (digits < 0) return -1;
  const afterDigits = skipSpace(s, digits);
  if (s[afterDigits] === '（' || s[afterDigits] === '(') {
    const close = indexOfAny(s, afterDigits + 1, PAREN_CLOSE);
    if (close < 0) return -1;
    const afterParen = skipSpace(s, close + 1);
    return isNumberedStop(s[afterParen]) ? afterParen + 1 : -1;
  }
  return isNumberedStop(s[afterDigits]) ? afterDigits + 1 : -1;
}

/** `[：:.．]`——编号前缀的必需终止符。 */
function isNumberedStop(ch) {
  return ch === '：' || ch === ':' || ch === '.' || ch === '．';
}

/** 行首编号前缀（原 LINE_OPTION_PREFIX 的 `^[ \t　]*(?:选项|候选|方案)…[：:.．]\s*`）。 */
function stripNumberedPrefix(s) {
  const end = matchNumberedRun(s, skipSet(s, 0, SPACE_OR_IDEO));
  return end < 0 ? s : s.slice(skipSpace(s, end));
}

/** 行首项目符号（原 LINE_BULLET_PREFIX 的 `^[ \t　]*(?:[-*+•]|\d+[.、)]|[（(]\d+[)）])\s*`）。 */
function stripBulletPrefix(s) {
  const i = skipSet(s, 0, SPACE_OR_IDEO);
  const ch = s[i];
  if (BULLET_CHARS.has(ch)) return s.slice(skipSpace(s, i + 1));
  const openParen = ch === '（' || ch === '(';
  if (!openParen && !isDigit(ch)) return s;
  const digits = scanDigits(s, openParen ? i + 1 : i);
  if (digits < 0) return s;
  // 两种形态的闭符集合不同：`\d+[.、)]` 不含全角），`[（(]\d+[)）]` 两种都要
  const closer = s[digits];
  const closed = openParen ? PAREN_CLOSE.has(closer) : DIGIT_STOP.has(closer);
  return closed ? s.slice(skipSpace(s, digits + 1)) : s;
}

/** 行首结构前缀剥离：与正则管线同序（编号前缀在前、项目符号在后，各自 replace 一次）。 */
function stripLineStructuralPrefix(s) {
  return stripBulletPrefix(stripNumberedPrefix(s));
}

function stripOptionPrefix(line) {
  return stripLineStructuralPrefix(stripLabelPrefix(line));
}

/** LABEL_PREFIX（`^[ \t#*_]*标签词\s*[:：]\s*`）的线性等价：只在「关键词 + 冒号」同时成立时剥。 */
function stripLabelPrefix(line) {
  const i = skipSet(line, 0, LABEL_MODIFIERS);
  const wordLen = matchWordAt(line, i, LABEL_WORDS_LIST);
  if (wordLen < 0) return line;
  const rest = skipColonRun(line, i + wordLen);
  return rest < 0 ? line : line.slice(rest);
}

function isLabelLine(line) {
  return LABEL_LINE.test(String(line || '').trim());
}

// T01554：启发式拆分用的强标签词。相比 kt/Android 原实现做了两点收紧——
// 原实现的「说明」弱词 + 不要求关键词后跟冒号，会把「好的，我说明一下情况」
// 误砍成候选「好的，我」+理由「说明一下情况」；把完整句子拦腰截断比原 bug 更糟，故：
//   1) 只留强标签词（理由/原因/解释/rationale/explanation），去掉口语动词「说明」；
//   2) 关键词后必须紧跟冒号（：/:），排除「说明一下」「说明书」这类句中用法。
// 注：中文「理由：」大多已被内联兜底（T01126）先行命中，本处的实际增量是
// 英文关键词场景（rationale/explanation），与 py _heuristic_rationale_split 同口径。

/** T01554：把「短候选 + 理由：长解释」按理由关键词切开；不合规则返回 null（宁缺毋滥）。 */
function heuristicRationaleSplit(s) {
  for (const kw of RATIONALE_WORDS) {
    const at = findKeywordWithColon(s, kw);
    if (at <= 0) continue;
    const text = stripEdgeRuns(s.slice(0, at));
    const tail = s.slice(at);
    // 长度判定用「含标签的原文」（判断后面是否真有一段解释），
    // 返回值剥掉「理由：」/「rationale: 」前缀——与中文内联兜底的正文口径一致，
    // UI 展示理由时无需再剥一次标签词。
    if (text && text.length <= 20 && tail.length > 5) {
      return [stripOptionPrefix(cleanCandidate(text)), stripKeywordPrefix(tail, kw.length)];
    }
  }
  return null;
}

/** 大小写无关地找首个「关键词 + 空白 + 冒号」，返回关键词起点；未命中返回 -1。 */
function findKeywordWithColon(s, kw) {
  const lower = s.toLowerCase();
  const needle = kw.toLowerCase();
  let at = lower.indexOf(needle);
  while (at >= 0) {
    if (isColon(s[skipSpace(s, at + needle.length)])) return at;
    at = lower.indexOf(needle, at + 1);
  }
  return -1;
}

/**
 * FR-3.3：解析「内容|理由」，理由缺失返回 null。
 * 内部顺序 = 顺序铁律现场：cleanCandidate（含 markdown 剥离）先于前缀剥离。
 */
function splitRationale(raw) {
  const s = String(raw || '').trim();
  if (!s) return ['', null];
  const piped = splitFromLastPipe(s);
  if (piped) return piped;
  const inline = matchInlineRationale(s);
  if (inline) {
    const text = inline.text.trim();
    const rationale = inline.rationale.trim();
    if (text && rationale) return [stripOptionPrefix(cleanCandidate(text)), rationale];
  }
  const split = heuristicRationaleSplit(s);
  if (split) return split;
  return [cleanCandidate(s), null];
}

/** 末个「|」切开；无管道符或理由为空返回 null（交回内联/启发式兜底）。 */
function splitFromLastPipe(s) {
  const idx = s.lastIndexOf('|');
  if (idx < 0) return null;
  const text = s.slice(0, idx).trim();
  const rationale = s.slice(idx + 1).trim();
  if (!text) return null;
  if (!rationale) return [stripOptionPrefix(cleanCandidate(text)), null];
  // T01554：分隔符错位检测——text ≤3 字符而 rationale >10 字符且含汉字，
  // 疑似「|」位置错（LLM 把短确认词放前面），交换两者避免解释句被当候选。
  if ([...text].length <= 3 && [...rationale].length > 10 && /[\u4e00-\u9fff]/.test(rationale)) {
    return [stripOptionPrefix(cleanCandidate(rationale)), text];
  }
  return [stripOptionPrefix(cleanCandidate(text)), rationale];
}

/** 把被换行拆开的「内容|理由」重新接回一行（「内容|」+「理由」/「内容」+「|理由」）。 */
function joinDanglingPipe(lines) {
  const out = [];
  for (const ln of lines) {
    if (out.length && (DANGLING_PIPE_TAIL.test(out.at(-1)) || LEADING_PIPE.test(ln))) {
      out[out.length - 1] += ln;
    } else {
      out.push(ln);
    }
  }
  return out;
}

/**
 * 把一条 LLM 输出拆成单候选行列表（只拆结构，不改写文字）。
 * 多行逐行拆；单行含内联编号按编号拆；标题行（「## 候选回复」）整行丢弃。
 */
function expandCandidates(raw) {
  const s = String(raw || '').trim();
  if (!s) return [];
  const lines = s.split('\n').map((l) => l.trim()).filter((l) => l && !isLabelLine(l));
  const merged = mergeRationaleLines(lines); // T01720：理由续行接回上一条候选，不产出独立候选
  if (merged.length > 1) return joinDanglingPipe(merged).map(stripOptionPrefix);
  const numbered = splitByNumbered(s);
  if (numbered.length > 1) return numbered.map(stripOptionPrefix);
  if (merged.length) return [stripOptionPrefix(merged[0])];
  return [];
}

/** 内联编号切分（原 `s.split(NUMBERED)` 的线性等价）：命中项整体丢弃，返回非空段。 */
function splitByNumbered(s) {
  const parts = [];
  let from = 0;
  let start = matchNumberedStartAt(s, 0);
  while (start >= 0) {
    pushTrimmedPart(parts, s.slice(from, start));
    from = matchNumberedRun(s, start);
    start = matchNumberedStartAt(s, from);
  }
  pushTrimmedPart(parts, s.slice(from));
  return parts;
}

function pushTrimmedPart(parts, chunk) {
  const t = chunk.trim();
  if (t) parts.push(t);
}

/** 从 from 起找下一处编号前缀的关键词起点（原 NUMBERED 的 `(?:^|\s)` 锚点）。 */
function matchNumberedStartAt(s, from) {
  for (let p = from; p < s.length; p++) {
    if (p > 0 && !isSpace(s[p - 1])) continue;
    if (matchWordAt(s, p, NUMBERED_WORDS) > 0 && matchNumberedRun(s, p) >= 0) return p;
  }
  return -1;
}

/** 元话语判定（请求澄清/自我指称）→ 命中丢弃；普通「请告诉我地址」不误杀。 */
function isMetaCandidate(text) {
  const s = String(text || '');
  return META_ASK.test(s) || META_HELPLESS.test(s) || META_SELF_REF.test(s);
}

function countEmojiUnits(text) {
  const m = String(text || '').match(EMOJI_UNIT);
  return m ? m.length : 0;
}

/** 表情收敛：连续重复压成一个，总数超 maxUnits 丢弃靠后的；只删表情不动文字。 */
function normalizeEmoji(text, maxUnits = 2) {
  if (!text) return text || '';
  let out = text.replaceAll(EMOJI_REPEAT, '$1');
  const units = [...out.matchAll(EMOJI_STRIP)];
  if (units.length <= maxUnits) return out;
  for (let k = units.length - 1; k >= maxUnits; k--) {
    const u = units[k];
    out = out.slice(0, u.index) + out.slice(u.index + u[0].length);
  }
  return out.replaceAll(SPACE_RUN, ' ').trim();
}

/**
 * T02224 根治「理由栏被识别成候选回复」：用数量契约取代词表补丁（与 py `pair_unmarked_reasons`
 * / kt `pairUnmarkedReasons` 同构）。
 *
 * 真机现场：模型把「内容|理由」的分隔符整个丢掉，候选与理由退化成无标记的交替纯文本行
 * （4 行 = 2 候选 + 2 理由 → 界面「候选 4 条」，点第 2 条把分析语注入输入框）。
 * 词表只能认出**带标签**的理由行，无标签必然漏——这是历次补丁（T01126/T01136/T01554/T01720）
 * 反复复发的根因。改用模型破坏不了的约束：候选条数 N 在请求时就定死了。
 * 展开行数 > N 且全行无任何理由标记时，按鸽笼原理多出来的行不可能都是候选，
 * 只能是「正文, 理由」相邻成对 → 配成「正文|理由」交回 splitRationale，再按 N 截断。
 * 条件不满足时原样返回（宁可不配，也不把两条真候选误吞成理由）。
 */
function pairUnmarkedReasons(lines, expected = 0) {
  if (expected <= 0 || expected >= lines.length || lines.length % 2 !== 0) return lines;
  if (lines.some(hasRationaleMark)) return lines;
  const out = [];
  for (let i = 0; i + 1 < lines.length && out.length < expected; i += 2) {
    out.push(`${lines[i]}|${lines[i + 1]}`);
  }
  return out;
}

/** 行内是否已带理由结构标记：管道分隔、「… 理由：…」内联、「理由：」标签行。 */
function hasRationaleMark(line) {
  return line.includes('|') || matchInlineRationale(line) !== null || findRationaleLineHead(line) !== null;
}

/**
 * T01392 主管线：LLM 一条完整输出（一个 choice.content）→ 清洗后的候选对列表。
 * 顺序铁律在此固化；调用方（main.js llm:generate）对每个 choice 调一次后合并。
 * 返回 [{text, rationale}]；text 为空（纯标签/纯元话语）的条目已剔除。
 * T02224：expected>0 时先按数量契约配对无标记理由行（0=不启用，保持旧行为）。
 */
function sanitizeCandidates(rawContent, { maxEmojiUnits = 2, expected = 0 } = {}) {
  const out = [];
  for (const line of pairUnmarkedReasons(expandCandidates(rawContent), expected)) {
    const [text, rationale] = splitRationale(line);
    if (!text) continue;
    if (isMetaCandidate(text)) continue;
    out.push({ text: normalizeEmoji(text, maxEmojiUnits), rationale });
  }
  // T02224：面板按 N 张卡片设计，超发一律截断——多出来的从来不是「更多选择」，
  // 而是漏网的理由行（真机实测「候选 6 条」= 3 候选 + 3 理由）
  return expected > 0 ? out.slice(0, expected) : out;
}

// ===== T01395：换一批去重（与 py/kt 同构：归一化 + LCS 相似度 0.8 阈值）=====
const PUNCT = /[\s，。！？、；：,.!?;:~～\-—…"'()（）[\]【】]/g;

/** 比较用归一化：去表情、去标点空白、统一小写（「好呀😊」≈「好呀~」）。 */
function normalizeForCompare(text) {
  return String(text || '').replaceAll(EMOJI_STRIP, '').replaceAll(PUNCT, '').toLowerCase();
}

/** LCS 比率相似度 = 2*LCS/(len(a)+len(b))；候选短文本 O(n*m) 可接受。 */
function similarity(a, b) {
  a = normalizeForCompare(a);
  b = normalizeForCompare(b);
  if (!a && !b) return 1;
  if (!a || !b) return 0;
  let prev = new Array(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    const cur = [0];
    for (let j = 1; j <= b.length; j++) {
      cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    }
    prev = cur;
  }
  return (2 * prev[b.length]) / (a.length + b.length);
}

function isDuplicate(text, exclude, threshold = 0.8) {
  if (!text) return true;
  return (exclude || []).some((e) => similarity(text, e) >= threshold);
}

/** 丢弃与 exclude 重复的候选（{text,...} 对数组）；exclude 为空时原样返回。 */
function filterDuplicatePairs(pairs, exclude, threshold = 0.8) {
  if (!exclude?.length) return pairs.slice();
  return pairs.filter((p) => !isDuplicate(p.text, exclude, threshold));
}

module.exports = {
  stripMarkdown,
  stripMarkup,
  cleanCandidate,
  splitRationale,
  expandCandidates,
  mergeRationaleLines,
  pairUnmarkedReasons,
  isMetaCandidate,
  isLabelLine,
  normalizeEmoji,
  countEmojiUnits,
  sanitizeCandidates,
  normalizeForCompare,
  similarity,
  isDuplicate,
  filterDuplicatePairs,
};

// ===== T01401：要不要回判定（本地启发式，压误判优先）=====
// 口径：只有「明显无需回复」才判 noReply=true——宁可多生成（误放行成本低）
// 也不误拦真实消息（FR-2.4 误判成本不对称在判定侧的同构应用）。
// 典型无需回复：对方仅发表情/极短确认（嗯、好的、收到、👍）。
// S5843/S5869：原单条超复杂正则（复杂度 76、字符类含重复「！」）等价改写为
// 「确认词 + 尾缀段」解析，语言 = 首词 (尾缀段+ 内层词*)*，整体行为不变。
// 原正则首词组与内层组词表并不相同：噢/喔/表情仅可作首词，内层词不含它们；
// 「哦」同属内层词与尾缀字符：T+ 段贪婪取最大不损可行性（哦可并入 T+），
// 串以「哦」开头时首词可取连续「哦」的任意前缀长度，逐一尝试。
const NO_REPLY_WORD_AH = /^(?:嗯+|哦+)$/; // 首词/内层词共有的语气词
const NO_REPLY_WORD_AO = /^(?:噢+|喔+)$/; // 仅首词允许（原内层组缺失）
const NO_REPLY_WORD_B = /^(?:好+的?|好的+|行+|中|收到|了解|ok|OK|Ok)$/; // 确认语
const NO_REPLY_EMOJI = /^(?:👍|🙏|👌|💪|🌹|☕)$/; // 表情确认（仅首词）
const NO_REPLY_TAIL_RUN = /^[啦咯哦哈呀呗~～。.!！\s、,，]+/; // 语气尾缀段（S5869：已去重「！」）
const NO_REPLY_TAIL_CHAR = /[啦咯哦哈呀呗~～。.!！\s、,，]/;

function isFirstConfirmWord(w) {
  return (
    NO_REPLY_WORD_AH.test(w) ||
    NO_REPLY_WORD_AO.test(w) ||
    NO_REPLY_WORD_B.test(w) ||
    NO_REPLY_EMOJI.test(w)
  );
}

/** 重复字符型内层词（嗯+/行+ 等）在位置 k 处可匹配的各词长。 */
function pushRepeatLengths(lens, s, k, ch) {
  let r = 0;
  while (s[k + r] === ch) r++;
  for (let t = 1; t <= r; t++) lens.push(t);
}

/** 「好」系内层词（好+的?/好的+）在位置 k 处可匹配的各词长（存在回溯多解，需穷举）。 */
function pushHaoLengths(lens, s, k) {
  let a = 0;
  while (s[k + a] === '好') a++;
  for (let t = 1; t <= a; t++) {
    lens.push(t); // 好+的?（的 缺省）
    if (s[k + t] === '的') lens.push(t + 1); // 好+的?（回溯吞一个 的）
  }
  let d = 0;
  while (s[k + 1 + d] === '的') d++;
  for (let t = 1; t <= d; t++) lens.push(1 + t); // 好的+
}

// 两字固定内层词（整词匹配，命中即词长 2）
const NO_REPLY_INNER_PAIR = { 收: '收到', 了: '了解', o: 'ok' };
const NO_REPLY_INNER_PAIR_UPPER = new Set(['OK', 'Ok']); // 「O」开头的两种大小写

/** 内层组在位置 k 处可匹配的词长（好/的 组合存在回溯多解，需穷举）。 */
function innerWordLengthsAt(s, k) {
  const lens = [];
  const c = s[k];
  if (c === '嗯' || c === '行') {
    pushRepeatLengths(lens, s, k, c);
  } else if (c === '好') {
    pushHaoLengths(lens, s, k);
  } else if (c === '中') {
    lens.push(1);
  } else if (NO_REPLY_INNER_PAIR[c] === s.slice(k, k + 2)) {
    lens.push(2);
  } else if (c === 'O' && NO_REPLY_INNER_PAIR_UPPER.has(s.slice(k, k + 2))) {
    lens.push(2);
  }
  return lens;
}

/** 尾缀段之后的非尾缀区域能否切分为内层词序列（内层组为 *，允许多词连排）。 */
function segmentableAsInnerWords(region) {
  const m = region.length;
  const ok = new Array(m + 1).fill(false);
  ok[m] = true;
  for (let k = m - 1; k >= 0; k--) {
    if (innerWordLengthsAt(region, k).some((len) => ok[k + len])) ok[k] = true;
  }
  return ok[0];
}

/** 首个确认词之后的剩余部分是否形如 (尾缀段+ 内层词*)* 且恰好耗尽。 */
function isConfirmTailOnly(rest) {
  let i = 0;
  const n = rest.length;
  while (i < n) {
    const tm = NO_REPLY_TAIL_RUN.exec(rest.slice(i));
    if (!tm) return false; // 每段必须以尾缀字符开头
    i += tm[0].length;
    if (i >= n) break;
    let j = i;
    while (j < n && !NO_REPLY_TAIL_CHAR.test(rest[j])) j++;
    if (!segmentableAsInnerWords(rest.slice(i, j))) return false;
    i = j;
  }
  return true;
}

function isNoReplyConfirmText(s) {
  let end = s.search(NO_REPLY_TAIL_CHAR);
  if (end === -1) end = s.length;
  let maxFirst = end;
  if (end === 0) {
    const nonOh = s.search(/[^哦]/);
    maxFirst = nonOh === -1 ? s.length : nonOh;
  }
  for (let p = 1; p <= maxFirst; p++) {
    if (!isFirstConfirmWord(s.slice(0, p))) continue;
    if (isConfirmTailOnly(s.slice(p))) return true;
  }
  return false;
}
const EMOJI_ONLY = new RegExp(`^(?:[${BASE}][${MOD}]*)+$`, 'u'); // astral 区间必须 u 标志（见 T01392 坑）

/**
 * 本地启发式：明显无需回复 → {noReply:true, reason}；其余一律放行（noReply:false）。
 * reason 面向用户展示（明示为什么不用回）。
 */
function heuristicNoReply(message) {
  const s = String(message || '').trim();
  if (!s) return { noReply: false, reason: null }; // 空消息交给上层流程处理
  if (EMOJI_ONLY.test(s)) return { noReply: true, reason: '对方只发了一个表情，无需回复' };
  if (isNoReplyConfirmText(s)) return { noReply: true, reason: '对方只是简短确认（嗯/好的/收到类），无需回复' };
  return { noReply: false, reason: null };
}

module.exports.heuristicNoReply = heuristicNoReply;
