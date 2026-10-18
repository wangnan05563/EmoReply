// T01400 截图 OCR 取文（FR：截图 OCR 上下文）——tesseract.js 离线引擎（chi_sim best 模型）。
// 语言数据：assets/ocr/chi_sim.traineddata.gz（best 版 19MB 压缩形态，conf 94-95；int 版 66 已弃用）。
//   压缩分发：模型内容与官方 .traineddata 逐字节一致（gunzip 后 sha256 相同），安装包省 23MB；
//   若 .gz 缺失则自动回落未压缩文件（gzip 自动识别，tesseract.js 按 langPath 拼 .gz 后缀读取）。
// 与 Electron 解耦的纯 Node 模块：main 进程懒初始化 worker（首次约 1-2s，此后复用）。
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { createWorker } = require('tesseract.js');

let workerPromise = null;
let langDir = null; // 由 main 注入（打包/开发路径不同）

const EMOJI_RANGES = [[0x1f000, 0x1faff], [0x2600, 0x27bf], [0x2190, 0x21ff], [0x2b00, 0x2bff]];
const CONSECUTIVE_REPEAT = /(.)\1{2,}/g;

function isEmojiCh(ch) {
  const cp = ch.codePointAt(0);
  return EMOJI_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi);
}

function isMeaningfulCh(ch) {
  return /[0-9A-Za-z]/.test(ch) || (/[\u4e00-\u9fff]/.test(ch)) || isEmojiCh(ch);
}

/**
 * T01554：乱码文本检测（与 Android isGarbledText、m1-poc is_garbled_text 同构）。
 * 截图 OCR 会把控件描边、进度条、噪点误识成「■■■」「???」之类无效行，
 * 混入会话上下文会污染生成质量。四条判据任一命中即视为乱码：
 * ① 控制字符（C0/C1，排除 \t\n\r）；② 有意义字符占比 < 70%；
 * ③ 连续 3+ 个相同非文字符号；④ 纯符号行且长度 > 2。
 */
function isGarbledText(line) {
  const t = String(line || '').trim();
  if (!t) return true;
  // ① 控制字符
  for (const ch of t) {
    if (/[\u0000-\u001f\u007f-\u009f]/.test(ch) && !'\t\n\r'.includes(ch)) return true;
  }
  const chars = [...t].filter((c) => !/\s/.test(c));
  // ② 有意义字符占比
  if (chars.length && chars.filter(isMeaningfulCh).length / chars.length < 0.7) return true;
  // ③ 连续重复的非文字符号（汉字叠字如「好好好」受 isMeaningfulCh 保护，不算乱码）
  const compact = t.replace(/\s/g, '');
  for (const m of compact.matchAll(CONSECUTIVE_REPEAT)) {
    if (!isMeaningfulCh(m[1])) return true;
  }
  // ④ 纯符号行
  if (t.length > 2 && ![...t].some(isMeaningfulCh)) return true;
  return false;
}

/** 单行噪声过滤（与 m1-poc preprocess.py 的行级过滤同精神）：
 * 纯数字/时间戳、单字符、URL、媒体占位、乱码行、全文无汉字的行剔除。 */
function isNoiseLine(line) {
  const s = String(line ?? '').trim();
  if (!s) return true;
  if (/^(?:\d{1,2}:\d{2}|https?:\/\/|www\.)/i.test(s)) return true;
  if (/^\[?(?:图片|视频|文件|语音|链接)\]?$/.test(s)) return true;
  if (isGarbledText(s)) return true; // T01554：OCR 乱码行（■■■ / ??? / 纯符号 / 控制字符）
  if (!/[\u4e00-\u9fff]/.test(s) && s.replaceAll(/[a-zA-Z0-9]/g, '').length < 2) return true; // 无汉字且字母数字<2
  return false;
}

/** 初始化/复用 worker。 */
async function getWorker() {
  if (!workerPromise) {
    if (!langDir) throw new Error('OCR 未初始化（缺语言数据路径）');
    // 优先 .gz 压缩模型（安装包小 23MB），无压缩文件时回落
    const useGzip = fs.existsSync(path.join(langDir, 'chi_sim.traineddata.gz'));
    workerPromise = createWorker('chi_sim', 1, {
      langPath: langDir,
      gzip: useGzip,
      // 关键：tesseract.js 默认（cacheMethod 未设）在加载后会把解压后的 42MB .traineddata
      // 回写到 cachePath（缺省为 CWD）——既抵消瘦身成果又污染安装目录/工作区，故显式关闭。
      cacheMethod: 'none',
      logger: () => {}, // 静默进度
    });
  }
  return workerPromise;
}

/** 注入语言数据路径（main 在 whenReady 时调用）。 */
function initOcr(dir) {
  langDir = dir;
}

/**
 * 识别图片（路径或 PNG Buffer）→ 按行返回 {lines, text}。
 * 噪声行过滤后保留顺序；全噪声时 lines 为空（调用方提示重截）。
 */
async function recognizeImage(image, { psm = '6' } = {}) {
  const worker = await getWorker();
  await worker.setParameters({ tessedit_pageseg_mode: psm });
  const { data } = await worker.recognize(image);
  const rawLines = String(data.text || '')
    .split(/\r?\n/)
    .map((l) => l.replaceAll(/\s+/g, ' ').trim())
    .filter((l) => l && !isNoiseLine(l));
  return { lines: rawLines, text: rawLines.join('\n'), confidence: data.confidence };
}

module.exports = { initOcr, recognizeImage, isNoiseLine, isGarbledText };
