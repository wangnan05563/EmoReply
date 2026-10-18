'use strict';
// EmoReply 桌面端本地 HTTP API —— 压测用 SUT（无界面、纯 Node）。
//
// 忠实镜像 desktop-app/main.js 的 startHttpApi()：
//   GET  /health                 -> {ok,version}
//   POST /generate {text,style?} -> {candidates,rationale,degraded,notice}
//   GET/POST 其它路径            -> 404
//   缺 text / 非法 JSON          -> 400
//
// 复用 llm.generateCore 全管线（RAG 词面检索注入 -> 护栏 -> 候选清洗 -> 降级），
// 以「未配置云端 Key」的降级模式运行：离线、确定性、不触碰任何外部网络。
// 这是对产品本地 /generate 接口延迟与吞吐的真实测量（仅云端 LLM 调用路径因需
// 用户凭据+出网而未覆盖，其延迟由上游模型主导，非本机代码瓶颈）。

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { generateCore } = require('../llm');
const { KnowledgeBase, LexicalRetriever } = require('../kb');

const PORT = 17777;
const HOST = '127.0.0.1';

// ---- 知识库（临时加密落盘，仅压测用；使 RAG 词面检索分支非空，更贴近真实请求）----
const KB_PATH = path.join(__dirname, 'tmp', 'perf-kb.json');
const kb = new KnowledgeBase(KB_PATH, 'perf-test-passphrase');
const SEED = [
  ['relation', '我一般周末才回消息，工作日比较忙', null],
  ['persona', '语气轻松，遇到问句先确认再给方案', null],
  ['platform', 'EmoReply 是聊天回复草稿助手', null],
];
for (const [cat, text, contact] of SEED) {
  try {
    if (!kb.list().some((e) => e.text === text)) kb.add(cat, text, contact);
  } catch (e) {
    console.warn('[sut] 语料入库失败（忽略）：', e?.message);
  }
}
const retriever = new LexicalRetriever(kb);
const embedRetriever = null; // 未配置 embedding 模型 -> 走词面档

const server = http.createServer((req, res) => {
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  if (req.method === 'GET' && req.url === '/health') {
    res.end(JSON.stringify({ ok: true, version: 'perf-sut' }));
    return;
  }
  if (req.method !== 'POST' || req.url !== '/generate') {
    res.statusCode = 404;
    res.end(JSON.stringify({ error: 'not found（POST /generate）' }));
    return;
  }
  let raw = '';
  req.on('data', (c) => (raw += c));
  req.on('end', async () => {
    let body = {};
    try {
      body = JSON.parse(raw || '{}');
    } catch (e) {
      console.warn('[sut] 请求体 JSON 解析失败：', e?.message);
      res.statusCode = 400;
      res.end(JSON.stringify({ error: '请求体不是合法 JSON' }));
      return;
    }
    const text = String(body.text || '').trim();
    if (!text) {
      res.statusCode = 400;
      res.end(JSON.stringify({ error: '缺少 text 字段' }));
      return;
    }
    try {
      const result = await generateCore(
        { kb, retriever, embedRetriever },
        {
          config: null, // 无 apiKey -> 降级模板，离线确定性
          models: null,
          message: text,
          style: body.style,
          me: body.me,
          contact: body.contact || null,
        },
      );
      res.end(JSON.stringify(result));
    } catch (e) {
      res.statusCode = 500;
      res.end(JSON.stringify({ error: e.message }));
    }
  });
});

server.on('error', (e) => {
  console.error('[sut] 启动失败：', e.message);
  process.exit(1);
});

server.listen(PORT, HOST, () => {
  fs.writeFileSync(path.join(__dirname, 'sut.pid'), String(process.pid), 'utf8');
  console.log(`[sut] listening on http://${HOST}:${PORT} (POST /generate) pid=${process.pid}`);
});

const stop = () => {
  try {
    fs.rmSync(path.join(__dirname, 'sut.pid'), { force: true });
  } catch (e) {
    console.warn('[sut] 清理 sut.pid 失败（忽略）：', e?.message);
  }
  server.close(() => process.exit(0));
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
