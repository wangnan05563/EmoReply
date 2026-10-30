// T01407 冒烟：generateCore 全管线（降级/云端清洗/护栏/多模型）。
// 用法：node scripts/generate.smoke.js
'use strict';
const assert = require('node:assert');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs');
const { generateCore, stubCandidates } = require('../llm');
const { KnowledgeBase, LexicalRetriever } = require('../kb');

let pass = 0;
function ok(name) {
  pass++;
  console.log(`  ok ${name}`);
}

function sse(content) {
  return (
    `data: ${JSON.stringify({ choices: [{ delta: { content } }] })}\n\n` +
    `data: [DONE]\n\n`
  );
}

async function main() {
  const tmpdb = path.join(os.tmpdir(), `gen-smoke-${Date.now()}.json.enc`);
  const kb = new KnowledgeBase(tmpdb);
  kb.add('persona', '我喜欢跑步和健身');
  const retriever = new LexicalRetriever();
  const deps = { kb, retriever, embedRetriever: null };

  try {
    // 1) 未配置 → 本地模板降级
    const r1 = await generateCore(deps, { message: '在吗？', config: null });
    assert.strictEqual(r1.degraded, true);
    assert.ok(r1.candidates.length >= 1 && r1.candidates[0].text, '降级模板有候选');
    ok('未配置 → 模板降级');

    // 2) 云端成功：SSE 输出 → 清洗后候选（markdown/编号/理由全处理）
    let srv = http.createServer((req, res) => {
      let raw = '';
      req.on('data', (c) => (raw += c));
      req.on('end', () => {
        // 断言知识注入：检索命中的条目应出现在 system 里
        const body = JSON.parse(raw);
        assert.ok(body.messages[0].content.includes('跑步'), '检索条目注入 prompt');
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.end(sse('1. **好呀，周末一起爬山？**|表达热情邀约'));
      });
    });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    const p1 = srv.address().port;
    const r2 = await generateCore(deps, { message: '我们周末去跑步吧？', config: { baseUrl: `http://127.0.0.1:${p1}/v1`, apiKey: 'k', model: 'm' } });
    assert.strictEqual(r2.degraded, false);
    assert.deepStrictEqual(r2.candidates[0], { text: '好呀，周末一起爬山？', rationale: '表达热情邀约' });
    ok('云端成功 → 清洗管线端到端');
    srv.close();

    // 3) 护栏拦截 → 模板降级
    srv = http.createServer((req, res) => {
      let raw = '';
      req.on('data', (c) => (raw += c));
      req.on('end', () => {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.end(sse('这个项目稳赚不赔，放心投'));
      });
    });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    const p2 = srv.address().port;
    const r3 = await generateCore(deps, { message: '投资建议？', config: { baseUrl: `http://127.0.0.1:${p2}/v1`, apiKey: 'k', model: 'm' } });
    assert.strictEqual(r3.degraded, true);
    assert.match(r3.notice, /内容安全/);
    ok('护栏拦截 → 降级');

    // 4) 元话语 → 降级
    srv = http.createServer((req, res) => {
      let raw = '';
      req.on('data', (c) => (raw += c));
      req.on('end', () => {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.end(sse('请提供具体的聊天记录，这样我才能帮你起草回复。'));
      });
    });
    await new Promise((r) => srv.listen(0, '127.0.0.1', r));
    const p3 = srv.address().port;
    const r4 = await generateCore(deps, { message: '你好', config: { baseUrl: `http://127.0.0.1:${p3}/v1`, apiKey: 'k', model: 'm' } });
    assert.strictEqual(r4.degraded, true);
    assert.match(r4.notice, /元话语/);
    ok('元话语输出 → 降级');
    srv.close();
  } finally {
    fs.rmSync(tmpdb, { force: true });
  }

  console.log(`\n${pass} checks passed`);
  process.exit(0); // keep-alive 空闲套接字会挂住事件循环，显式退出
}

// CJS 没有顶层 await：入口保持同步，异步失败在函数内兜底
function start() {
  main().catch((e) => {
    console.error('SMOKE FAILED:', e);
    process.exit(1);
  });
}
start();
