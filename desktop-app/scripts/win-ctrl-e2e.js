// T01566：PC 端「关闭到托盘 + 置顶切换」专项 E2E（CDP DOM 断言 + 主进程状态回读）。
// 前提：electron 以 --remote-debugging-port=9222 + 独立 --user-data-dir 启动（同 ui-e2e.js 头部说明）。
// 覆盖：按钮存在性 / 置顶态切换（类+图标+文案）/ winPin 落盘 / 关闭=隐藏到托盘（进程不退出）/
//       二次实例唤起=托盘唤回路径 / 重启后置顶状态持久化。
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const PORT = 9222;
const PROFILE = process.argv[2] || ''; // 独立 profile 目录（用于 winPin 落盘断言）
let pass = 0, fail = 0;
const fails = [];
function t(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS', name); }
  else { fail++; fails.push(name + (extra ? ' | ' + extra : '')); console.log('  FAIL', name, extra || ''); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getTarget() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json`);
  const pages = (await res.json()).filter((p) => p.type === 'page');
  // 窗口隐藏时 title 仍可见；page 不存在 = 进程已退（验收红线）
  const page = pages.find((p) => /EmoReply/i.test(p.title || p.url || ''));
  if (!page) throw new Error('未找到 EmoReply 页面：' + pages.map((p) => p.title).join(','));
  return page.webSocketDebuggerUrl;
}
function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    const send = (method, params = {}) =>
      new Promise((res2, rej2) => { const m = ++id; pending.set(m, { res2, rej2 }); ws.send(JSON.stringify({ id: m, method, params })); });
    ws.onopen = () => resolve({ send, close: () => ws.close() });
    ws.onerror = () => reject(new Error('WS error'));
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
async function ev(cdp, expr) {
  const r = await cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('page eval: ' + (r.exceptionDetails.text || '?'));
  return r.result?.value;
}

async function main() {
  const cdp = await connect(await getTarget());
  await cdp.send('Page.enable');
  await sleep(600);
  // ⚠ 不改写 wizard_done：T01541 写穿会污染 settings.json（ui-e2e A 段依赖干净首启态）。
  //   win-ctrl 在 z=60、向导遮罩 z=50，按钮点击不受遮挡影响，无需关向导。
  const cdp2 = cdp;
  await sleep(400);

  console.log('K. T01566 关闭/置顶按钮');
  // K1 按钮存在
  t('K1 控制栏含置顶+关闭按钮', await ev(cdp2, `!!document.querySelector('#winPin') && !!document.querySelector('#winClose')`));

  // K2 初始态=已置顶（悬浮窗默认语义保持：winPin 缺省置顶；accent 态 + 图钉图标 + 文案）
  await sleep(500); // 等 getPin 水合
  const initOn = await ev(cdp2, `(() => ({ on: document.querySelector('#winPin').classList.contains('on'),
    icon: (document.querySelector('#winPin svg use')||{}).getAttribute?.('href'),
    tip: document.querySelector('#winPin').dataset.tip }))()`);
  t('K2 初始=已置顶（高亮类+图钉图标+「已置顶」文案）', initOn.on === true && initOn.icon === '#i-pin' && /已置顶/.test(initOn.tip || ''), JSON.stringify(initOn));

  // K3 点击取消置顶 → 主进程状态翻转 + 图标/文案切「未置顶」
  await ev(cdp2, `document.querySelector('#winPin').click()`);
  await sleep(600);
  const offState = await ev(cdp2, `(async () => ({ pin: await globalThis.emoReply.getPin(),
    on: document.querySelector('#winPin').classList.contains('on'),
    icon: (document.querySelector('#winPin svg use')||{}).getAttribute?.('href'),
    tip: document.querySelector('#winPin').dataset.tip }))()`);
  t('K3 点击→取消置顶：主进程 false + 去高亮 + pin-off 图标 + 文案', offState.pin === false && offState.on === false && offState.icon === '#i-pin-off' && /未置顶/.test(offState.tip || ''), JSON.stringify(offState));

  // K4 winPin 持久化落盘（settings.json 防抖 150ms 已覆盖 sleep）
  if (PROFILE) {
    try {
      const s = JSON.parse(fs.readFileSync(path.join(PROFILE, 'settings.json'), 'utf8'));
      t('K4 winPin=0 落盘（OS 级配置目录，重启不丢）', s.values && s.values.winPin === '0', 'winPin=' + (s.values || {}).winPin);
    } catch (e) { t('K4 winPin=0 落盘（OS 级配置目录，重启不丢）', false, e.message); }
  }

  // K5 再次点击恢复置顶（往返切换）
  await ev(cdp2, `document.querySelector('#winPin').click()`);
  await sleep(600);
  const back = await ev(cdp2, `(async () => ({ pin: await globalThis.emoReply.getPin(),
    on: document.querySelector('#winPin').classList.contains('on') }))()`);
  t('K5 再次点击→恢复置顶：主进程 true + 高亮回归', back.pin === true && back.on === true, JSON.stringify(back));

  // K6 关闭=隐藏到托盘：真实按钮点击 → document.hidden=true；closeWindow 回执 tray/degraded（进程不退）
  await ev(cdp2, `document.querySelector('#winClose').click()`);
  await sleep(900);
  const k6 = await ev(cdp2, `(async () => ({ hidden: document.hidden, mode: await globalThis.emoReply.closeWindow() }))()`);
  t('K6 点关闭→窗口隐藏到托盘（进程/页面存活；mode=tray）', k6 && k6.hidden === true && k6.mode === 'tray', JSON.stringify(k6));

  // K7 唤回路径：二次启动同 profile → second-instance → showWindowFromTray（托盘单击同路径）
  if (!PROFILE) {
    t('K7 托盘/二次启动唤回', false, '未传 profile 路径，跳过');
  } else {
    const { spawnSync } = require('node:child_process');
    const exe = path.join(__dirname, '..', 'node_modules', 'electron', 'dist', 'electron.exe');
    spawnSync(exe, ['.', '--user-data-dir=' + PROFILE], {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, ELECTRON_RUN_AS_NODE: undefined },
      timeout: 30000,
    });
    // 第二实例拿到锁失败会立即退出（spawnSync 返回即完成「唤起第一实例」动作）
    await sleep(1500);
    const shown = await ev(cdp2, `(async () => ({ hidden: document.hidden, pin: await globalThis.emoReply.getPin() }))()`);
    t('K7 托盘/二次启动唤回：窗口恢复可见且置顶态保持', shown && shown.hidden === false && shown.pin === true, JSON.stringify(shown));
  }

  await cdp2.close();
  console.log(`\n===== T01566 E2E: PASS ${pass} / FAIL ${fail} =====`);
  if (fails.length) fails.forEach((f) => console.log('  FAIL> ' + f));
  process.exit(fail ? 2 : 0);
}
main().catch((e) => { console.error('E2E FAILED:', e.message || e); process.exit(1); });
