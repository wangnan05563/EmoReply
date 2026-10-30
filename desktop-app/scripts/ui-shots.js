// UI 截图佐证自动化（T01418 人工清单第 2 项的部分自动化）：
// 前提：electron 以 --remote-debugging-port=9222 + 独立 --user-data-dir 启动（env -u ELECTRON_RUN_AS_NODE）。
//   本脚本会清掉向导标志等持久化键以复现「首启向导」，事实源落在 OS 默认配置目录时直接拒跑（T03639）。
// 用 Node22 内置 WebSocket 直连 CDP：Runtime.evaluate 操纵 DOM → Page.captureScreenshot。
// 用法：node scripts/ui-shots.js
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const guard = require('./profile-guard');

const PORT = 9222;
const OUT = path.join(__dirname, '..', 'docs', 'screenshots');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getTarget() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json`);
  const pages = await res.json();
  const page = pages.find((p) => p.type === 'page' && /EmoReply/i.test(p.title || ''));
  if (!page) throw new Error('未找到 EmoReply 页面：' + pages.map((p) => p.title).join(','));
  return page.webSocketDebuggerUrl;
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    function send(method, params = {}) {
      return new Promise((res2, rej2) => {
        const msgId = ++id;
        pending.set(msgId, { res2, rej2 });
        ws.send(JSON.stringify({ id: msgId, method, params }));
      });
    }
    ws.onopen = () => resolve({ send, close: () => ws.close() });
    ws.onerror = (e) => reject(new Error('WS error'));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        const { res2, rej2 } = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? rej2(new Error(JSON.stringify(msg.error))) : res2(msg.result);
      }
    };
  });
}

async function shot(cdp, name) {
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
  fs.mkdirSync(OUT, { recursive: true });
  const file = path.join(OUT, name + '.png');
  fs.writeFileSync(file, Buffer.from(data, 'base64'));
  console.log('shot:', name, '->', file);
}

async function evalJs(cdp, expr) {
  await cdp.send('Runtime.evaluate', { expression: expr });
}

// 需要回读页面值（护栏取 settings.stats、复位后验残留）
async function ev(cdp, expr) {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('page eval: ' + (r.exceptionDetails.text || '?'));
  return r.result?.value;
}

async function main() {
  const wsUrl = await getTarget();
  const cdp = await connect(wsUrl);
  const evp = (expr) => ev(cdp, expr);
  await cdp.send('Page.enable');
  await sleep(800);

  // 护栏先于任何清键动作：脚本会把向导标志/配置写穿删除，真实用户档案不该被动
  const cfgFile = await guard.readCfgFile(evp);
  if (guard.isDefaultProfileFile(cfgFile)) {
    console.error(guard.guardHint(cfgFile));
    await cdp.close();
    process.exit(3);
  }

  // 0) 主界面（初始态）
  await shot(cdp, '01-main-default');

  // 1) 强制重跑向导：清 emoreply_wizard_done 并等写穿落盘（旧版清的 wiz_done 是废键，
  //    产品从不读它，所以这里历史上一张向导截图都没拍到——T03639 复核时一并修正）
  const resetHint = await guard.resetProfileState(evp, sleep, async () => {
    await evalJs(cdp, `location.reload()`);
    await sleep(1500);
  });
  if (resetHint) console.error('  ⚠ ' + resetHint);
  await shot(cdp, '02-wizard-step1');
  // 向导为单面板内联三步（无独立「下一步」按钮）：选「自定义…」让模型名/接口地址显形
  await evalJs(cdp, `
    (function(){
      const s = document.querySelector('#wzPreset');
      s.value = 'custom|';
      s.dispatchEvent(new Event('change', {bubbles:true}));
    })()`);
  await sleep(400);
  await shot(cdp, '03-wizard-step2');

  // 2) 指令栏（内置场景包按钮）：走真实「稍后再说」出口，标志写穿后不再弹向导
  await evalJs(cdp, `document.querySelector('#wzSkip')?.click()`);
  await sleep(500);
  await evalJs(cdp, `location.reload()`);
  await sleep(1200);
  await shot(cdp, '04-cmd-bar');

  // 3) 设置区全展开（多模型/快捷指令/语气学习/色板/缩放）
  // T01532：RAG/设置/历史已收进设置中心覆盖层，截图前先开层（display:none 下 scrollIntoView 无效）
  await evalJs(cdp, `
    document.getElementById('settingsPage')?.classList.add('on');
    document.querySelectorAll('details.collapsible').forEach(d => d.open = true);
    document.querySelector('#settingsBox')?.closest('details')?.scrollIntoView();
  `);
  await new Promise((r) => setTimeout(r, 300));
  await shot(cdp, '05-settings-expanded');

  // 4) 知识库 + 历史区（T01532：同在设置中心层内）
  await evalJs(cdp, `
    document.querySelector('#historyBox')?.scrollIntoView();
  `);
  await new Promise((r) => setTimeout(r, 300));
  await shot(cdp, '06-history-kb');

  // 5) 候选卡模拟（注入两条假候选看样式：来源徽标+语气标签+三动作）
  // 先关设置中心覆盖层：它盖在主界面上，不关掉时 #candidates 的 scrollIntoView 无效，
  // 07 与 06 会拍成同一张（T03639 复核发现）
  await evalJs(cdp, `document.getElementById('settingsPage')?.classList.remove('on')`);
  await sleep(200);
  await evalJs(cdp, `
    (function(){
      const box = document.querySelector('#candidates');
      box.innerHTML = '';
      const mk = (txt, src, tone) => {
        const d = document.createElement('div');
        d.className = 'candidate pop';
        d.innerHTML = '<div class="cand-head">' +
          (src ? '<span class="src">' + src + '</span>' : '') +
          (tone ? '<span class="tone-tag">' + tone + '</span>' : '') +
          '</div><div class="cand-text">' + txt + '</div>' +
          '<div class="cand-actions"><button class="act">📋 复制</button><button class="act">⏎ 回填</button><button class="act">🔄 换一条</button></div>';
        box.appendChild(d);
      };
      mk('好的呀，那我们周六上午十点在山脚集合～', 'GPT', '表达热情邀约');
      mk('收到！我确认下时间再回你哈', 'Ollama', '给对方台阶+明确时间点');
    })();
  `);
  await new Promise((r) => setTimeout(r, 600));
  await evalJs(cdp, `document.querySelector('#candidates').scrollIntoView();`);
  await new Promise((r) => setTimeout(r, 200));
  await shot(cdp, '07-candidates-styled');

  await cdp.close();
  console.log('ALL SHOTS DONE');
  process.exit(0);
}

// CJS 没有顶层 await：入口保持同步，异步失败在函数内兜底
function start() {
  main().catch((e) => {
    console.error('SHOTS FAILED:', e.message || e);
    process.exit(1);
  });
}
start();
