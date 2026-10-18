// T01402 冒烟：EmbeddingRetriever——本地 /embeddings 假服务端验证排序/联系人加权/缓存/回落。
// 用法：node scripts/kb.smoke.js
'use strict';
const assert = require('node:assert');
const http = require('node:http');
const { EmbeddingRetriever, KnowledgeBase, cosine } = require('../kb');

let pass = 0;
function ok(name) {
  pass++;
  console.log(`  ok ${name}`);
}

// 2 维语义空间：x 轴=运动话题，y 轴=工作话题（可手工构造排序与加权断言）
const VEC = {
  '我喜欢跑步和健身': [0.9, 0.1],
  '公司的项目进度很紧': [0.1, 0.9],
  '查询': null, // 动态
};

async function main() {
  // 余弦基础
  assert.strictEqual(cosine([1, 0], [1, 0]), 1);
  assert.strictEqual(cosine([1, 0], [0, 1]), 0);
  ok('cosine 基础');

  const server = http.createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      const { input } = JSON.parse(raw);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          data: input.map((t) => ({
            embedding:
              t === '（查）最近有什么运动建议吗'
                ? [0.8, 0.2] // 查询向量贴近运动条目
                : VEC[t] || [0.5, 0.5],
          })),
        }),
      );
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const config = { baseUrl: `http://127.0.0.1:${port}/v1`, apiKey: 'k', model: 'm' };
  const embeddingModel = 'emb-test';

  // 组装知识库（不落盘：构造对象绕开构造函数——直接用临时文件更简单）
  const os = require('node:os');
  const path = require('node:path');
  const fs = require('node:fs');
  const tmpdb = path.join(os.tmpdir(), `kb-smoke-${Date.now()}.json.enc`);
  const kb = new KnowledgeBase(tmpdb);
  const idSport = kb.add('persona', '我喜欢跑步和健身');
  const idWork = kb.add('persona', '公司的项目进度很紧');
  const idRel = kb.add('relation', '约老张打球要提前一天', '老张');

  try {
    const er = new EmbeddingRetriever();

    // 1) 向量排序：运动话题查询命中运动条目
    const r1 = await er.search(kb, '（查）最近有什么运动建议吗', { config, embeddingModel });
    assert.strictEqual(r1[0].entry.id, idSport, '向量档排序命中运动条目');
    ok('向量检索排序');

    // 2) 缓存生效：第二次调用不再为已缓存条目发请求（input 只含查询）
    let lastInputLen = 0;
    server.removeAllListeners('request');
    server.on('request', (req, res) => {
      let raw = '';
      req.on('data', (c) => (raw += c));
      req.on('end', () => {
        lastInputLen = JSON.parse(raw).input.length;
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ data: [{ embedding: [0.8, 0.2] }] }));
      });
    });
    await er.search(kb, '（查）最近有什么运动建议吗', { config, embeddingModel });
    assert.strictEqual(lastInputLen, 1, '第二次只 embed 查询（条目全缓存）');
    ok('条目向量缓存');

    // 3) 配置缺失 → null（调用方回落词面档）
    const r3 = await er.search(kb, '查询', { config: null, embeddingModel: null });
    assert.strictEqual(r3, null, '未配置返回 null');
    ok('未配置回落信号');

    // 4) 词面档仍可用（回归）——词面档返回 entry 本身（非 {entry,score} 包装）
    const lr = new (require('../kb').LexicalRetriever)();
    const r4 = lr.search(kb, '跑步', {});
    assert.ok(r4.length >= 1 && r4[0].id === idSport, '词面档命中');
    ok('词面档回归');

    // 5) 联系人加权（relation 条目 sameContact boost）
    kb.add('relation', '老张喜欢约球', null); // 无绑定联系人
    const r5 = await er.search(kb, '老张约球', { contact: '老张', config, embeddingModel });
    // 两条 relation 都可能命中，绑定老张的应排前
    assert.strictEqual(r5[0].entry.id === idRel || r5[0].entry.text.includes('提前一天'), true, '同联系人加权生效');
    ok('联系人加权');
  } finally {
    server.close();
    fs.rmSync(tmpdb, { force: true });
  }

  console.log(`\n${pass} checks passed`);
}

main().catch((e) => {
  console.error('SMOKE FAILED:', e);
  process.exit(1);
});
