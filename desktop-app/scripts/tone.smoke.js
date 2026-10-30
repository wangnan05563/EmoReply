// T01408 冒烟：tone.js 提取器 + kb 注入往返。node scripts/tone.smoke.js
'use strict';
const assert = require('node:assert');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const { extractToneCorpus } = require('../tone');
const { KnowledgeBase } = require('../kb');

let pass = 0;
function ok(name) {
  pass++;
  console.log(`  ok ${name}`);
}

const SAMPLE = [
  '张三: 这个咱们直接安排就行哈',
  '张三: 没问题，回头我弄好发你呀',
  'https://example.com/some/link',
  '[图片]',
  '12:30',
  '张三: 麻烦帮忙看下这个，稳了的',
  '张三: 嗯',
  '张三: 这个咱们直接安排就行哈', // 重复行（去重）
  '这是一条特别特别特别特别特别特别特别特别特别特别特别特别特别特别特别特别特别特别特别特别长的行已经明显超过六十个字了所以应该被过滤掉不算语料',
];

async function main() {
  // 1) 提取：噪声过滤 + 去重 + 语气词加分排序
  const corpus = extractToneCorpus(SAMPLE.join('\n'));
  assert.strictEqual(corpus.length, 3, '噪声/重复/超长过滤后剩 3 条');
  assert.strictEqual(corpus[0].text, '这个咱们直接安排就行哈', '三加分句排最前（语气词+咱们+安排）；并列按长度取短');
  ok('提取：过滤/去重/排序');

  // 2) 样本不足 → 空数组
  assert.deepStrictEqual(extractToneCorpus('嗯\n哦\n好'), [], '样本不足不注入');
  ok('样本不足保护');

  // 3) 注入 kb（relation 类绑定联系人）往返
  const tmpdb = path.join(os.tmpdir(), `tone-${Date.now()}.json.enc`);
  const kb = new KnowledgeBase(tmpdb);
  corpus.forEach((c) => kb.add('relation', '语气参考（本人惯用）：' + c.text, '张三'));
  const entries = kb.list().filter((e) => e.contact === '张三');
  assert.strictEqual(entries.length, 3, '语料入库并绑定联系人');
  fs.rmSync(tmpdb, { force: true });
  ok('语料注入 kb（relation+联系人）');

  console.log('\n' + pass + ' checks passed');
  process.exit(0);
}

// CJS 没有顶层 await：入口保持同步，异步失败在函数内兜底
function start() {
  main().catch((e) => {
    console.error('SMOKE FAILED:', e);
    process.exit(1);
  });
}
start();
