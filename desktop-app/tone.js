// T01408 个人语气学习：聊天记录 → 高频句式提取 → RAG 语料（纯函数，可冒烟）。
// 提取策略（与 m1-poc rag.py 精神同构）：
// 1) 按行切分消息（聊天记录导出天然一行一条）；
// 2) 过滤噪声：URL/纯数字/图片占位/超短（<4 字）/超长（>60 字，聊天口语不会太长）；
// 3) 高频句式打分：句尾语气词（哈/呀/啦/呗/哦/呢/嘛/～）加权、口语词（咱们/回头/改天/麻烦/安排）加权；
// 4) 去重（归一化后相同只留首现）+ 按分取 topN。
'use strict';

const TAIL_PARTICLES = /[哈呀啦呗哦呢嘛～~]\s*$/;
const COLLOQUIAL = /(咱们|回头|改天|麻烦|安排|弄好|搞定|妥了|稳了|没问题|放心|直接|顺便)/;
const NOISE_LINE = /^(?:\d+[:.、]|http|www\.|\[图片\]|\[视频\]|\[文件\]|\[语音\]|\[链接\]|\d{1,2}:\d{2})/i;

function lineScore(s) {
  let score = 1;
  if (TAIL_PARTICLES.test(s)) score += 1;
  if (COLLOQUIAL.test(s)) score += 1;
  if (/[？?]$/.test(s)) score += 0.5; // 提问式口吻也是个人特色
  return score;
}

/**
 * 提取语气语料。返回 [{text, score}]（text 已含「语气参考」前缀由调用方决定，此处为原句）。
 * topN 默认 10；不足 3 条可用语料时返回空数组（样本太少不注入，避免噪声）。
 */
function extractToneCorpus(chatText, { topN = 10, minLines = 3 } = {}) {
  const seen = new Set();
  const scored = [];
  for (let raw of String(chatText || '').split(/\r?\n/)) {
    let s = raw.trim();
    if (!s || NOISE_LINE.test(s)) continue;
    s = s.replace(/^[^：:]{1,12}[：:]\s*/, ''); // 剥「张三: 」类发送者前缀
    s = s.trim();
    if (s.length < 4 || s.length > 60) continue;
    const key = s.replaceAll(/[\s，。！!？?~～、]/g, '');
    if (!key || seen.has(key)) continue;
    seen.add(key);
    scored.push({ text: s, score: lineScore(s) });
  }
  if (scored.length < minLines) return [];
  scored.sort((a, b) => b.score - a.score || a.text.length - b.text.length);
  return scored.slice(0, topN);
}

module.exports = { extractToneCorpus, lineScore, NOISE_LINE };
