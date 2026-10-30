/**
 * T01540：multiTone 延迟基线实测脚本（修正版：添加重试机制）。
 *
 * 用法：AGNES_API_KEY=… node scripts/multitone-benchmark.js [N]
 *   N = 样本量（默认 20）
 *   Key 走环境变量 AGNES_API_KEY（或 API_KEY），缺失时打印中文报错并以退出码 2 停止。
 *
 * 输出：开/关 multiTone 各 N 次调用的 P50/P95 对比表 + 候选条数统计。
 */
const https = require('node:https');
const { performance } = require('node:perf_hooks');

const API_BASE = 'https://apihub.agnes-ai.com/v1';
// Key 只从环境变量读取：AGNES_API_KEY 优先，兼容通用 API_KEY；源码里不得出现任何真实 Key。
const API_KEY = process.env.AGNES_API_KEY || process.env.API_KEY || '';
const MODEL = 'agnes-3.0-flash';

if (!API_KEY) {
  console.error('缺少 AGNES_API_KEY 环境变量');
  process.exit(2);
}

const SYSTEM_TEMPLATE = `你是聊天回复助手，为「我」起草一条可直接发送的回复。
约束：
1) 只依据给定上下文，不得编造对方未提及的事实；
2) 语气{style}，不冒犯、不敷衍、不过度承诺；
3) 直接输出正文，不要使用 markdown 格式符；
4) 即使上下文不完整或混乱，也基于已有信息给出最合适的回复——严禁输出「请提供更多信息/聊天记录」之类请求澄清的元话语。`;

const MULTI_TONE_VARIANTS = [
  { tone: 35, hint: '克制档（语气极克制、偏理性：措辞精简，避免感叹号与情绪化表达）' },
  { tone: 58, hint: '中性档（语气中性自然：亲切但不过火）' },
  { tone: 80, hint: '活泼档（语气偏活泼：可用轻松措辞与适度感叹）' },
];

function toneVariantsHint(tones) {
  const parts = tones.map((v, i) => `${i + 1}) ${v.hint}`).join('\n');
  return `\n语气强度（多风格并行）：本次必须按以下语气从上到下各生成一条候选，顺序与上列一致，不得打乱，不得合并成同一语气：\n${parts}`;
}

// 过滤空行：提到模块级，避免在 callLLM 的多层回调里再嵌一层函数（嵌套过深）
function isNonBlankLine(line) {
  return line.trim() !== '';
}

async function callLLM(systemBase, userText, multiTone = false, i = 0, maxRetries = 3) {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const result = await new Promise((resolve, reject) => {
        const startTime = performance.now();
        
        // 构建 system prompt
        let finalSystem = systemBase.replace('{me}', '我').replace('{style}', '亲切');
        if (multiTone) {
          finalSystem += toneVariantsHint(MULTI_TONE_VARIANTS);
        } else {
          finalSystem += '\n语气强度：语气中性自然：亲切但不过火';
        }
        
        finalSystem += '\n\n请直接输出候选内容，每条一行，用 | 分隔内容与理由（如「好的，没问题|语气轻松自然」）。';
        
        const postData = JSON.stringify({
          model: MODEL,
          messages: [
            { role: 'system', content: finalSystem },
            { role: 'user', content: userText },
          ],
          temperature: 0.7,
          max_tokens: 500,
        });

        const url = new URL(`${API_BASE}/chat/completions`);
        const options = {
          hostname: url.hostname,
          port: 443,
          path: url.pathname,
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${API_KEY}`,
            'Content-Length': Buffer.byteLength(postData),
          },
        };

        const req = https.request(options, (res) => {
          let data = '';
          res.on('data', (chunk) => { data += chunk; });
          res.on('end', () => {
            const elapsed = performance.now() - startTime;
            try {
              const json = JSON.parse(data);
              const content = json.choices?.[0]?.message?.content || '';
              // DEBUG: 打印原始响应前 300 字符（仅前 3 次调用且首次尝试）
              if (i < 3 && attempt === 0) {
                console.log(`\n  [DEBUG] Raw response: ${content.slice(0, 300)}`);
              }
              const candidates = content.split('\n').filter(isNonBlankLine);
              resolve({ elapsed, candidates, status: res.statusCode });
            } catch (e) {
              reject(new Error(`Parse error: ${e.message}, raw: ${data.slice(0, 200)}`));
            }
          });
        });

        req.on('error', (e) => reject(e));
        req.write(postData);
        req.end();
      });
      
      // 如果返回了候选，直接成功
      if (result.candidates.length > 0) {
        return result;
      }
      
      // 空响应，等待后重试（指数退避）
      if (attempt < maxRetries - 1) {
        const delay = 1000 * Math.pow(2, attempt);
        await new Promise(r => setTimeout(r, delay));
      }
    } catch (e) {
      if (attempt === maxRetries - 1) {
        throw e;
      }
      const delay = 1000 * Math.pow(2, attempt);
      await new Promise(r => setTimeout(r, delay));
    }
  }
  
  // 所有重试都失败，返回空结果
  return { elapsed: 0, candidates: [], status: 0 };
}

function percentile(arr, p) {
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, idx)];
}

async function runBenchmark(N = 20) {
  console.log(`\n=== T01540 multiTone 延迟基线实测 ===`);
  console.log(`模型: ${MODEL} | 样本量: N=${N} | API: ${API_BASE}\n`);

  const resultsOff = [];
  const resultsOn = [];
  let candidateCountMismatch = 0;
  const userText = '对方：周末去爬山吗？一起呀';

  // 预热 2 次
  console.log('预热中...');
  await callLLM(SYSTEM_TEMPLATE, userText, false);
  await callLLM(SYSTEM_TEMPLATE, userText, true);

  // multiTone 关闭
  console.log(`\n[multiTone OFF] 运行 ${N} 次...`);
  for (let i = 0; i < N; i++) {
    try {
      const { elapsed, candidates } = await callLLM(SYSTEM_TEMPLATE, userText, false, i);
      resultsOff.push(elapsed);
      process.stdout.write(`  #${i + 1}: ${elapsed.toFixed(0)}ms (${candidates.length} 条)\r`);
    } catch (e) {
      console.error(`\n  #${i + 1} FAILED: ${e.message}`);
    }
  }
  console.log();

  // multiTone 开启
  console.log(`\n[multiTone ON] 运行 ${N} 次...`);
  for (let i = 0; i < N; i++) {
    try {
      const { elapsed, candidates } = await callLLM(SYSTEM_TEMPLATE, userText, true, i);
      resultsOn.push(elapsed);
      // 检查候选条数
      if (candidates.length !== 3) {
        candidateCountMismatch++;
      }
      process.stdout.write(`  #${i + 1}: ${elapsed.toFixed(0)}ms (${candidates.length} 条)\r`);
    } catch (e) {
      console.error(`\n  #${i + 1} FAILED: ${e.message}`);
    }
  }
  console.log();

  // 统计
  const p50Off = percentile(resultsOff, 50);
  const p95Off = percentile(resultsOff, 95);
  const p50On = percentile(resultsOn, 50);
  const p95On = percentile(resultsOn, 95);
  const avgOff = resultsOff.reduce((a, b) => a + b, 0) / resultsOff.length;
  const avgOn = resultsOn.reduce((a, b) => a + b, 0) / resultsOn.length;

  console.log('\n=== 实测数据表 ===');
  console.log('| 指标 | multiTone OFF | multiTone ON | 增量 |');
  console.log('|------|--------------|-------------|------|');
  console.log(`| P50 | ${p50Off.toFixed(0)}ms | ${p50On.toFixed(0)}ms | ${((p50On - p50Off) / p50Off * 100).toFixed(1)}% |`);
  console.log(`| P95 | ${p95Off.toFixed(0)}ms | ${p95On.toFixed(0)}ms | ${((p95On - p95Off) / p95Off * 100).toFixed(1)}% |`);
  console.log(`| AVG | ${avgOff.toFixed(0)}ms | ${avgOn.toFixed(0)}ms | ${((avgOn - avgOff) / avgOff * 100).toFixed(1)}% |`);
  console.log(`| 样本量 | ${resultsOff.length} | ${resultsOn.length} | - |`);
  console.log(`| 候选条数≠3 | - | ${candidateCountMismatch}/${resultsOn.length} | - |`);

  // 判定
  const p95Increment = ((p95On - p95Off) / p95Off) * 100;
  const p95AbsIncrement = p95On - p95Off;
  console.log('\n=== 判定 ===');
  if (p95Increment > 30 || p95AbsIncrement > 1500) {
    console.log(`❌ P95 增量超标：${p95Increment.toFixed(1)}% / ${p95AbsIncrement.toFixed(0)}ms`);
    console.log('建议：按 T01349 备选方案降级（变体降为 2 档，或仅对「换一批」场景启用）');
  } else {
    console.log(`✅ P95 增量可接受：${p95Increment.toFixed(1)}% / ${p95AbsIncrement.toFixed(0)}ms`);
    console.log('结论：multiTone 单请求 RTT 不变，可保持当前实现');
  }

  // 候选条数校验
  if (candidateCountMismatch > 0) {
    console.log(`⚠️  候选条数异常：${candidateCountMismatch}/${resultsOn.length} 次调用返回非 3 条`);
  } else {
    console.log('✅ 候选条数恒为 3');
  }
}

const N = Number.parseInt(process.argv[2]) || 20;

// CJS 没有顶层 await：入口保持同步，异步失败在函数内兜底
function start() {
  runBenchmark(N).catch((e) => {
    console.error('Benchmark failed:', e);
    process.exit(1);
  });
}
start();
