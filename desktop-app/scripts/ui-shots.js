// UI 截图佐证自动化（T01418 人工清单第 2 项的部分自动化）：
// 前提：electron 以 --remote-debugging-port=9222 启动（env -u ELECTRON_RUN_AS_NODE）。
// 用 Node22 内置 WebSocket 直连 CDP：Runtime.evaluate 操纵 DOM → Page.captureScreenshot。
// 用法：node scripts/ui-shots.js
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const PORT = 9222;
const OUT = path.join(__dirname, '..', 'docs', 'screenshots');

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

async function main() {
  const wsUrl = await getTarget();
  const cdp = await connect(wsUrl);
  await cdp.send('Page.enable');
  await new Promise((r) => setTimeout(r, 800));

  // 0) 主界面（初始态）
  await shot(cdp, '01-main-default');

  // 1) 强制重跑向导（清 wiz_done → reload）
  await evalJs(cdp, `localStorage.removeItem('wiz_done'); location.reload();`);
  await new Promise((r) => setTimeout(r, 1500));
  await shot(cdp, '02-wizard-step1');
  await evalJs(cdp, `document.querySelector('#wzNext')?.click()`);
  await new Promise((r) => setTimeout(r, 300));
  await shot(cdp, '03-wizard-step2');
  await evalJs(cdp, `document.querySelector('#wzSkip')?.click(); document.querySelector('#wzNext')?.click();`);
  await new Promise((r) => setTimeout(r, 500));

  // 2) 指令栏（内置场景包按钮）
  await evalJs(cdp, `localStorage.setItem('wiz_done','1'); location.reload();`);
  await new Promise((r) => setTimeout(r, 1200));
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

main().catch((e) => {
  console.error('SHOTS FAILED:', e.message || e);
  process.exit(1);
});
