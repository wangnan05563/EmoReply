// T01393：LLM 流式调用（与 Electron 解耦，纯 Node —— 可独立冒烟测试）。
// SSE 手写解析：\n\n（或 \r\n\r\n）分帧、data: 前缀、[DONE] 终止、choices[0].delta.content 累积。
// 非 SSE 响应（端点不支持 stream，content-type 非 text/event-stream）→ 整体解析回退。
'use strict';
const https = require('node:https');
const http = require('node:http');
const { URL } = require('node:url');

// 统一的 LLM 拒绝原因：S6671 要求 Promise 拒绝原因为 Error 对象；
// code/userMessage 挂在错误上，调用方（llm:generate handler）取 userMessage 展示
function rejectLlm(code, userMessage, cause) {
  const err = new Error(userMessage, { cause });
  err.code = code;
  err.userMessage = userMessage;
  return err;
}

function rejectFromStatus(res) {
  const map = {
    402: '云端生成配额不足（余额用尽），已降级为本地模板',
    401: 'API Key 无效或无权限，已降级为本地模板',
    403: 'API Key 无效或无权限，已降级为本地模板',
    429: '请求过于频繁（限流），已降级为本地模板',
  };
  return rejectLlm(res.statusCode, map[res.statusCode] || `云端生成失败（${res.statusCode}），已降级为本地模板`);
}

function sseParser(onFrame) {
  let buffer = '';
  return function push(chunk) {
    buffer += chunk;
    let idx;
    while ((idx = buffer.search(/\r?\n\r?\n/)) >= 0) {
      const m = /^\r?\n\r?\n/.exec(buffer.slice(idx));
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + m[0].length);
      onFrame(frame);
    }
  };
}

/**
 * 去掉 baseUrl 尾部的所有 `/`，与安卓侧 `baseUrl.trimEnd('/')` 同口径。
 * 不用 `.replace(/\/+$/, '')`：该形态被 Sonar S5852 判为回溯超线性；非字符串入参
 * 依旧在 `.endsWith` 处抛出，与原实现（`.replace` 缺失）的失败路径一致。
 */
function trimTrailingSlashes(url) {
  let s = url;
  while (s.endsWith('/')) s = s.slice(0, -1);
  return s;
}

/**
 * 流式生成。resolve({ text, nonStream })；拒绝原因为 rejectLlm 构造的 Error。
 * onDelta(delta, full) 每个 content 增量回调一次（full 为累积全文）。
 */
function callLlmStream({ baseUrl, apiKey, model }, system, user, onDelta) {
  return new Promise((resolve, reject) => {
    const endpoint = trimTrailingSlashes(baseUrl) + '/chat/completions';
    const body = JSON.stringify({
      model,
      temperature: 0.7,
      max_tokens: 1500,
      stream: true, // T01393：流式（单候选语义，n:2 并行对比归 M2.1）
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: user },
      ],
    });
    let url;
    try {
      url = new URL(endpoint);
    } catch (e) {
      return reject(rejectLlm(0, '接口地址格式错误，请检查设置', e));
    }
    const isHttp = url.protocol === 'http:';
    const req = (isHttp ? http : https).request(
      {
        hostname: url.hostname,
        port: url.port || (isHttp ? 80 : 443),
        path: url.pathname + url.search,
        method: 'POST',
        timeout: 60000,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiKey}`,
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        if (res.statusCode < 200 || res.statusCode > 299) {
          res.resume(); // 消费掉错误体再拒绝，连接可复用
          return reject(rejectFromStatus(res));
        }
        const isSse = String(res.headers['content-type'] || '').includes('text/event-stream');
        if (!isSse) {
          // 回退：端点不支持 stream → 整体解析（取首条 message.content）
          let data = '';
          res.on('data', (c) => (data += c));
          res.on('end', () => {
            try {
              const choice = (JSON.parse(data).choices || [])[0];
              const text = choice?.message?.content?.trim();
              if (!text) return reject(rejectLlm(res.statusCode, '生成服务未返回内容'));
              resolve({ text, nonStream: true });
            } catch (e) {
              reject(rejectLlm(res.statusCode, '生成服务返回格式异常', e));
            }
          });
          return;
        }
        // SSE 解析
        let full = '';
        const push = sseParser((frame) => {
          for (const line of frame.split(/\r?\n/)) {
            if (!line.startsWith('data:')) continue;
            const payload = line.slice(5).trim();
            if (!payload || payload === '[DONE]') continue;
            try {
              const delta = JSON.parse(payload).choices?.[0]?.delta?.content;
              if (delta) {
                full += delta;
                try {
                  onDelta?.(delta, full);
                } catch (e) {
                  console.warn('[stream] onDelta 回调异常（不阻断）：', e?.message);
                }
              }
            } catch (e) {
              console.debug('[llm] 非 JSON SSE payload（心跳/注释帧等），跳过：', e?.message); // 心跳/注释帧等非 JSON payload，跳过
            }
          }
        });
        res.on('data', push);
        res.on('end', () => {
          if (!full) return reject(rejectLlm(res.statusCode, '生成服务未返回内容'));
          resolve({ text: full.trim(), nonStream: false });
        });
      },
    );
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', (e) =>
      reject(
        rejectLlm(0, `网络异常（${e.message === 'timeout' ? '超时' : '连接失败'}），已降级为本地模板`, e),
      ),
    );
    req.write(body);
    req.end();
  });
}

/**
 * T01398 多模型并行对比（FR-PC2 扩展）：2~N 套端点同题并行，结果带来源。
 * 返回 results: [{name, ok, text?, error?}]——单套失败不拖垮整体（allSettled 语义）。
 * onDelta(name, delta, full) 按模型分流推流。
 */
async function fanoutGenerate(models, system, user, onDelta) {
  const jobs = models.map((m) =>
    callLlmStream(m, system, user, (delta, full) => {
      try {
        onDelta?.(m.name, delta, full);
      } catch (e) {
        console.warn('[fanout] onDelta 回调异常（不阻断）：', e?.message);
      }
    })
      .then((r) => ({ name: m.name, ok: true, text: r.text, nonStream: r.nonStream }))
      .catch((e) => ({ name: m.name, ok: false, error: e.userMessage || e.message })),
  );
  return Promise.all(jobs); // 内部已 catch，全量 settle
}

const { filterDuplicatePairs, similarity } = require('./sanitize');
module.exports = { callLlmStream, rejectLlm, rejectFromStatus, sseParser, fanoutGenerate, generateCore, screenCandidate, stubCandidates, buildPrompt };

// ---- T01407：生成核心管线（与 Electron 解耦，main IPC 与本地 HTTP 服务共用）----
const { sanitizeCandidates } = require('./sanitize');

const STYLE_HINTS = { 正式: '正式', 亲切: '亲切', 简短: '简短', 幽默: '幽默' };

// T02224：候选条数是请求时就定死的「数量契约」——提示词措辞与输出侧理由配对
// （sanitize.pairUnmarkedReasons）必须同源，否则配对会算错。
const DEFAULT_CANDIDATES = 2;

// T01082：输出侧护栏（营销套话/违规承诺/辱骂 → 命中过滤）
const BAD_OUTPUT = [
  /(?:保证|包)[^，。！!？\s]{0,6}(?:赚|盈利|收益|回本)|稳赚不赔|内部渠道/,
  /(?:刷单|代开|伪造|洗钱|绕过(?:风控|监管))/,
  /(?:去死|滚|废物|垃圾东西)/,
  /(?:^|[，,。！!？?\s~～])亲[，,~～！!。]|宝贝|么么哒|亲亲[~～!！]|尊敬的用户/,
];

function screenCandidate(text) {
  return BAD_OUTPUT.some((re) => re.test(String(text || '')));
}

function stubCandidates(message) {
  const m = String(message || '');
  if (m.includes('？') || m.includes('?')) return ['好的，我看一下，晚点回复你哈', '收到，我想想再回你～'];
  if (m.includes('谢谢') || m.includes('感谢')) return ['不客气～'];
  return ['收到，谢谢～', '好嘞，知道了'];
}

function buildPrompt(message, style, me, knowledge, exclude) {
  let system =
    '你是聊天回复助手，为「' + (me || '我') + '」起草一条可直接发送的回复。\n' +
    '约束：\n' +
    '1) 只依据给定上下文，不得编造对方未提及的事实；\n' +
    '2) 语气' + (STYLE_HINTS[style] || '亲切') + '，不冒犯、不敷衍、不过度承诺；\n' +
    '3) 直接输出正文，不要使用 markdown 格式符。\n' +
    `4) 给出 ${DEFAULT_CANDIDATES} 个候选；每条按「内容|理由」输出，理由一句话说明情商点，可以省略。`;
  if (knowledge?.length) {
    system +=
      '\n5) 参考资料（已按相关性筛选；仅在与当前消息相关时参考，不得据此编造对方未提及的事实）：' +
      knowledge.join('；') + '。';
  }
  if (exclude?.length) {
    system += '\n6) 换一批：不要与这些已给回复雷同（意思相近也算）：' + exclude.join('；') + '。';
  }
  return { system, user: '对方消息：\n' + message };
}

/** 本地模板降级结果（stubCandidates 包装为候选 + 降级标记）。 */
function stubResult(message, notice) {
  return { candidates: stubCandidates(message).map((t) => ({ text: t, rationale: null })), notice, degraded: true };
}

/** 相似度去重（≥0.8 视为雷同，保留首现）。 */
function dedupeBySimilarity(candidates) {
  const seen = [];
  return candidates.filter((c) => {
    if (seen.some((s) => similarity(c.text, s) >= 0.8)) return false;
    seen.push(c.text);
    return true;
  });
}

/** 多模型分支：过滤护栏命中 → 清洗带来源 → 去重；清洗后为空返回 null（降级）。 */
function collectFanoutCandidates(results) {
  const okRes = results.filter((r) => r.ok && !screenCandidate(r.text));
  if (!okRes.length) {
    return {
      candidates: null,
      notice: '全部模型生成失败——' + results.map((r) => r.name + '：' + r.error).join('；'),
      degraded: true,
    };
  }
  const candidates = dedupeBySimilarity(
    okRes.flatMap((r) => sanitizeCandidates(r.text, { expected: DEFAULT_CANDIDATES }).map((c) => ({ ...c, source: r.name }))),
  );
  if (!candidates.length) return null;
  const failed = results.filter((r) => !r.ok).map((r) => r.name);
  return {
    candidates,
    notice: failed.length ? '部分模型失败：' + failed.join('、') : null,
    degraded: false,
    sources: okRes.map((r) => r.name),
  };
}

/**
 * 检索注入（T01402：向量档优先失败回落词面档）。失败返回空数组（不阻断生成）。
 */
async function retrieveKnowledge({ kb, retriever, embedRetriever }, message, contact, config) {
  try {
    if (config?.embeddingModel && embedRetriever) {
      const hits = await embedRetriever.search(kb, message, {
        contact: contact || null, config, embeddingModel: config.embeddingModel,
      });
      return hits
        ? hits.map((h) => h.entry.text)
        : retriever.search(kb, message, { contact: contact || null }).map((e) => e.text);
    }
    if (retriever) {
      return retriever.search(kb, message, { contact: contact || null }).map((e) => e.text);
    }
  } catch (e) {
    console.warn('[llm] 知识检索失败，忽略注入：', e?.message);
  }
  return [];
}

/**
 * 生成核心（T01407 抽取）：检索注入 → LLM（多模型并行/单发/模板降级）→ 护栏 → 清洗。
 * deps: { kb, retriever, embedRetriever }；onChunk(modelOrNull, full) 流式回调（HTTP 触发时可省）。
 * 返回 { candidates:[{text,rationale,source?}], notice, degraded, sources? }。
 */
async function generateCore(deps, params, onChunk) {
  const { config, models, message, style, me, contact, exclude } = params || {};
  const knowledge = await retrieveKnowledge(deps, message, contact, config);
  const { system, user } = buildPrompt(message, style, me, knowledge, exclude);
  const suites = (Array.isArray(models) ? models : []).filter((m) => m?.baseUrl && m.apiKey && m.model);
  const singleReady = config?.baseUrl && config.apiKey && config.model;
  if (!suites.length && !singleReady) {
    return stubResult(message, '未配置云端生成，当前为本地模板（设置里填 Key 后启用）');
  }
  try {
    if (suites.length >= 2) {
      const results = await fanoutGenerate(suites, system, user, (name, _d, full) => onChunk?.(name, full));
      const collected = collectFanoutCandidates(results);
      if (collected?.degraded) return collected; // 全部模型生成失败
      if (!collected) {
        return stubResult(message, '候选清洗后为空（疑似元话语输出），已降级为本地模板');
      }
      return collected;
    }
    const { text } = await callLlmStream(singleReady ? config : suites[0], system, user, (_d, full) => onChunk?.(null, full));
    if (screenCandidate(text)) {
      return stubResult(message, '候选未通过内容安全检查，已降级为本地模板');
    }
    const candidates = filterDuplicatePairs(sanitizeCandidates(text, { expected: DEFAULT_CANDIDATES }), exclude);
    if (!candidates.length) {
      return stubResult(message, '候选清洗后为空（疑似元话语输出），已降级为本地模板');
    }
    return { candidates, notice: null, degraded: false };
  } catch (err) {
    return stubResult(message, err.userMessage || err.message);
  }
}
