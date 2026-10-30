// T01393 冒烟：本地 HTTP 服务端模拟 OpenAI 兼容流式端点，验证 callLlmStream 三条路径。
// 用法：node scripts/llm.smoke.js
'use strict';
const assert = require('node:assert');
const http = require('node:http');
const { callLlmStream, sseParser } = require('../llm');

let pass = 0;
function ok(name) {
  pass++;
  console.log(`  ok ${name}`);
}

const sseBody = (frames) =>
  frames
    .map((f) => (typeof f === 'string' ? f : `data: ${JSON.stringify(f)}`))
    .join('\n\n') + '\n\n';

async function main() {
  const server = http.createServer((req, res) => {
    req.resume(); // 请求体不参与断言（按 url 路由），消费掉即可触发 end
    req.on('end', () => {
      // callLlmStream 会在 baseUrl 后追加 /chat/completions —— 用 includes 匹配路由段
      if (req.url.includes('/sse')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        // 分帧边界测试：把完整 SSE 体切成小块（含 \r\n\r\n 帧、跨块帧、心跳注释帧）
        const full =
          sseBody([
            { choices: [{ delta: { content: '好呀' } }] },
            ': keep-alive', // 心跳注释帧（非 JSON）
            { choices: [{ delta: { content: '，周末' } }] },
          ]) +
          'data: {"choices":[{"delta":{"content":"一起？"}}]}\r\n\r\n' + // \r\n 帧
          'data: [DONE]\n\n';
        let i = 0;
        const timer = setInterval(() => {
          if (i >= full.length) {
            clearInterval(timer);
            res.end();
            return;
          }
          res.write(full.slice(i, i + 7)); // 7 字节小块，制造跨块截断
          i += 7;
        }, 5);
      } else if (req.url.includes('/json')) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ choices: [{ message: { content: ' 整体返回候选 ' } }] }));
      } else if (req.url.includes('/rate')) {
        res.writeHead(429, { 'Content-Type': 'application/json' });
        res.end('{}');
      } else if (req.url.includes('/empty')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.end('data: [DONE]\n\n');
      }
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  const cfg = { baseUrl: base, apiKey: 'sk-test', model: 'test-model' };

  try {
    // 1) SSE 流式：跨块分帧 + \r\n 帧 + 心跳注释帧
    const deltas = [];
    const r1 = await callLlmStream({ ...cfg, baseUrl: base + '/sse' }, 's', 'u', (_d, full) => deltas.push(full));
    assert.strictEqual(r1.nonStream, false, 'SSE 路径 nonStream=false');
    assert.strictEqual(r1.text, '好呀，周末一起？', 'SSE 全文累积');
    assert.deepStrictEqual(deltas, ['好呀', '好呀，周末', '好呀，周末一起？'], 'onDelta 增量序列');
    ok(String.raw`SSE 流式：分帧/心跳/\r\n/增量回调`);

    // 2) sseParser 纯函数：\r\n\r\n 与混合换行
    const seen = [];
    const push = sseParser((f) => seen.push(f));
    push('data: a\r\n\r\ndata: b\n\ndata: c');
    push('\n\ndata: d\n\n');
    // 帧 a、b 在第一次 push 内完成；'data: c' 无终止符留缓冲，第二次 push 供给 '\n\n' 后冲出 c、d
    assert.strictEqual(seen.length, 4, 'sseParser 帧数（尾帧由下次 push 冲出）');
    ok('sseParser 纯函数分帧');

    // 3) 非 SSE 回退：整体解析
    const r2 = await callLlmStream({ ...cfg, baseUrl: base + '/json' }, 's', 'u', null);
    assert.strictEqual(r2.nonStream, true, '回退路径 nonStream=true');
    assert.strictEqual(r2.text, '整体返回候选', '回退路径取 message.content（含 trim）');
    ok('非 SSE 端点整体解析回退');

    // 4) 429 限流 → 中文 userMessage
    await assert.rejects(
      () => callLlmStream({ ...cfg, baseUrl: base + '/rate' }, 's', 'u', null),
      (e) => e.userMessage === '请求过于频繁（限流），已降级为本地模板' && e.code === 429,
      '429 映射',
    );
    ok('429 限流 → userMessage 映射');

    // 5) SSE 空内容 → 拒绝
    await assert.rejects(
      () => callLlmStream({ ...cfg, baseUrl: base + '/empty' }, 's', 'u', null),
      (e) => e.userMessage === '生成服务未返回内容',
      '空内容拒绝',
    );
    ok('SSE 空内容拒绝');
  } finally {
    server.close();
  }

  console.log(`\n${pass} checks passed`);
}

// ---- T01398：多模型并行对比（fanoutGenerate）----
const { fanoutGenerate } = require('../llm');
async function fanoutSmoke() {
  const server = http.createServer((req, res) => {
    req.resume(); // 请求体不参与断言（按 url 路由），消费掉即可触发 end
    req.on('end', () => {
      if (req.url.includes('/a')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.end('data: {"choices":[{"delta":{"content":"A家候选"}}]}\n\ndata: [DONE]\n\n');
      } else if (req.url.includes('/b')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.end('data: {"choices":[{"delta":{"content":"B家候选"}}]}\n\ndata: [DONE]\n\n');
      } else {
        res.writeHead(429);
        res.end('{}');
      }
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const mk = (tag, path) => ({ name: tag, baseUrl: `http://127.0.0.1:${port}${path}`, apiKey: 'k', model: 'm' });
  try {
    const deltas = [];
    const r = await fanoutGenerate([mk('A', '/a'), mk('B', '/b')], 's', 'u', (name, _d, full) => deltas.push([name, full]));
    assert.deepStrictEqual(
      r.map((x) => [x.name, x.ok, x.text]),
      [['A', true, 'A家候选'], ['B', true, 'B家候选']],
      '两套全成',
    );
    // 并行下跨模型回调顺序不确定，按模型分组断言增量序列
    const byModel = {};
    for (const [name, full] of deltas) {
      byModel[name] ??= [];
      byModel[name].push(full);
    }
    assert.deepStrictEqual(byModel['A'], ['A家候选'], 'A 增量序列');
    assert.deepStrictEqual(byModel['B'], ['B家候选'], 'B 增量序列');
    pass++;
    console.log('  ok fanout：两套并行成功 + onDelta 按模型分流');

    const r2 = await fanoutGenerate([mk('A', '/a'), mk('BAD', '/rate')], 's', 'u', null);
    assert.strictEqual(r2[0].ok, true);
    assert.strictEqual(r2[1].ok, false, '失败套不拖垮整体');
    assert.match(r2[1].error, /限流/, '失败原因可读');
    pass++;
    console.log('  ok fanout：部分失败隔离');
  } finally {
    server.close();
  }
  console.log(`  (fanout 新增 2 例，累计 ${pass})`);
}

// CJS 没有顶层 await：入口保持同步，异步失败在函数内兜底
function start() {
  main().then(fanoutSmoke).catch((e) => {
    console.error('SMOKE FAILED:', e);
    process.exit(1);
  });
}
start();
