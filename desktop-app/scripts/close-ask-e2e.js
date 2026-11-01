// T03777：关闭交互专项 E2E —— 弹窗询问 / 记住选择 / 按策略直发 / 设置改口径 / 记住退出时真退出。
// 覆盖用户单里的三条体验路径：
//   A 未记住 → 点关闭弹窗 → 选「最小化到托盘」（不勾记住=只执行这一次；勾记住=写穿落盘）
//   B 已记住 → 点关闭不弹窗，直接按配置执行
//   C 设置中心改成「直接退出应用」→ 下次点关闭进程真退出
// 前提：electron 以 --remote-debugging-port=9222 + 独立 --user-data-dir + --force-show 启动
//      （--force-show 必需：从别的会话继承来的 SW_HIDE 会让 document.hidden 恒真，B 段判据失效）。
// 用法：node scripts/close-ask-e2e.js tmp/e2e-profile
// ⚠ C 段末条断言会让应用真退出、CDP 随之断开，所以「直接退出」必须是最后一步。
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const guard = require('./profile-guard');

const PORT = 9222;
const PROFILE = process.argv[2] || '';
const ACTION_KEY = 'emoreply_close_action';
const REMEMBER_KEY = 'emoreply_close_remember';
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
    // 应用退出（L7 的正常结局）会让连接断开：必须把挂起的请求判成失败，
    // 否则 Promise 永不落定、事件循环空掉后 node 静默 exit(0)，末条断言直接凭空消失。
    ws.onclose = () => {
      for (const { rej2 } of pending.values()) rej2(new Error('CDP 连接已关闭'));
      pending.clear();
    };
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
async function reload(cdp) {
  await ev(cdp, 'location.reload()');
  await sleep(1500);
}
// 真实按键（走 Chromium 输入栈，不是 DOM 合成事件）：Esc 收起弹层
async function pressEscape(cdp) {
  const key = { type: 'rawKeyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 };
  await cdp.send('Input.dispatchKeyEvent', key);
  await cdp.send('Input.dispatchKeyEvent', { ...key, type: 'keyUp' });
  await sleep(250);
}
// 托盘单击的等价路径：二次启动同 profile 拿不到单实例锁 → second-instance → showWindowFromTray
function wakeBySecondInstance() {
  const { spawnSync } = require('node:child_process');
  const exe = path.join(__dirname, '..', 'node_modules', 'electron', 'dist', 'electron.exe');
  spawnSync(exe, ['.', '--user-data-dir=' + PROFILE], {
    cwd: path.join(__dirname, '..'),
    env: { ...process.env, ELECTRON_RUN_AS_NODE: undefined },
    timeout: 30000,
  });
  return sleep(1500);
}
function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(path.join(PROFILE, 'settings.json'), 'utf8')).values || {};
  } catch (e) {
    return { __error: e.message };
  }
}
// 弹层快照：一次取回「回执 + 弹层可见性 + 窗口隐藏态 + 关闭按钮文案」
// 弹层可见性读 computedStyle 而非 style.display：初始态由 CSS 规则给 display:none，
// 内联 style.display 此时是空串——按内联值判会误报（reload 后必现）。
const ASK_SNAP = `(async (mode) => ({
  mode,
  shown: getComputedStyle(document.querySelector('#closeAsk')).display,
  hidden: document.hidden,
  tip: document.querySelector('#winClose').dataset.tip,
  aria: document.querySelector('#winClose').getAttribute('aria-label'),
  remember: localStorage.getItem(${JSON.stringify(REMEMBER_KEY)}),
  action: localStorage.getItem(${JSON.stringify(ACTION_KEY)}),
}))`;

async function main() {
  // 护栏：本单会写「记住退出应用」并真的把进程打死，绝不能跑在真实用户档案上
  if (!PROFILE) {
    console.error('用法：node scripts/close-ask-e2e.js <e2e 专用 profile 目录>\n'
      + '（必须显式传入，且必须是被 --user-data-dir 使用的独立目录）');
    process.exit(3);
  }
  const cdp0 = await connect(await getTarget());
  await cdp0.send('Page.enable');
  const cfgFile = await guard.readCfgFile((expr) => ev(cdp0, expr));
  const profileRoot = path.resolve(PROFILE) + path.sep;
  if (guard.isDefaultProfileFile(cfgFile) || !path.resolve(cfgFile).startsWith(profileRoot)) {
    console.error(guard.guardHint(cfgFile));
    process.exit(3);
  }
  await sleep(400);

  console.log('L. T03777 关闭交互');
  // 复位：只清本单两枚键（不动 wizard/主题等，避免污染 ui-e2e 的首启判据）
  await ev(cdp0, `localStorage.removeItem(${JSON.stringify(ACTION_KEY)}); localStorage.removeItem(${JSON.stringify(REMEMBER_KEY)}); true`);
  await sleep(400);
  await reload(cdp0);

  // L1 未记住 → 点关闭：主进程拦下并回执 'ask'，弹层出现，窗口不动
  const l1 = await ev(cdp0, `(async () => (${ASK_SNAP})(await globalThis.emoReply.closeWindow()))()`);
  t('L1 未记住点关闭→弹窗询问（ask + 弹层可见 + 窗口未藏 + 文案为「每次询问」）',
    l1.mode === 'ask' && l1.shown === 'block' && l1.hidden === false && /每次询问/.test(l1.tip || ''), JSON.stringify(l1));

  // L2 Esc 收起弹层 = 不关闭（用户随时可反悔，且不留任何配置）
  await pressEscape(cdp0);
  const l2 = await ev(cdp0, `({ shown: getComputedStyle(document.querySelector('#closeAsk')).display, hidden: document.hidden,
    remember: localStorage.getItem(${JSON.stringify(REMEMBER_KEY)}) })`);
  t('L2 Esc 取消：弹层收起、窗口仍可见、配置未写',
    l2.shown === 'none' && l2.hidden === false && l2.remember === null, JSON.stringify(l2));

  // L3 只选不改配置：真实按钮弹出 → 不勾「记住」直接点「最小化到托盘」→ 立即执行这一次动作，但两枚键仍为空
  await ev(cdp0, `document.querySelector('#winClose').click()`);
  await sleep(400);
  const l3 = await ev(cdp0, `(async () => {
    const shown = getComputedStyle(document.querySelector('#closeAsk')).display;
    document.querySelector('#closeAskTray').click();
    await new Promise((r) => setTimeout(r, 700));
    return { shown, hidden: document.hidden,
      remember: localStorage.getItem(${JSON.stringify(REMEMBER_KEY)}),
      action: localStorage.getItem(${JSON.stringify(ACTION_KEY)}) };
  })()`);
  t('L3 不勾记住只选托盘→本次隐藏生效但配置不落（下次照旧询问）',
    l3.shown === 'block' && l3.hidden === true && l3.remember === null && l3.action === null, JSON.stringify(l3));
  const l3d = readSettings();
  t('L3b 未记住时磁盘也不留痕（settings.json 无 close_remember）',
    l3d.__error ? /ENOENT/.test(String(l3d.__error)) : l3d[REMEMBER_KEY] === undefined, JSON.stringify(l3d));

  // L4 场景 A 后半：唤回窗口 → 再点关闭仍弹窗（证明 L3 真的没记住）→ 这次勾上「记住」
  await wakeBySecondInstance();
  const l4 = await ev(cdp0, `(async () => {
    const first = await globalThis.emoReply.closeWindow();
    await new Promise((r) => setTimeout(r, 250));
    const shown = document.querySelector('#closeAsk').style.display;
    document.querySelector('#closeAskRemember').checked = true;
    document.querySelector('#closeAskTray').click();
    await new Promise((r) => setTimeout(r, 400));
    return { first, shown, hidden: document.hidden,
      remember: localStorage.getItem(${JSON.stringify(REMEMBER_KEY)}),
      action: localStorage.getItem(${JSON.stringify(ACTION_KEY)}) };
  })()`);
  t('L4 场景A：再点关闭依旧询问 → 勾选记住+选托盘 → 隐藏且两键写穿',
    l4.first === 'ask' && l4.shown === 'block' && l4.hidden === true
    && l4.remember === '1' && l4.action === 'minimize_to_tray', JSON.stringify(l4));
  const l4d = readSettings();
  t('L4b 关闭配置落 OS 级配置目录（重启不丢）',
    l4d[ACTION_KEY] === 'minimize_to_tray' && l4d[REMEMBER_KEY] === '1', JSON.stringify(l4d));

  // L5 场景 B：已记住 → 点关闭不弹窗、直接收进托盘（reload 顺带验证「重启后配置仍生效」）
  await wakeBySecondInstance();
  await reload(cdp0);
  const l5 = await ev(cdp0, `(async () => (${ASK_SNAP})(await globalThis.emoReply.closeWindow()))()`);
  t('L5 场景B：已记住时点关闭→无弹层直接收进托盘，按钮文案随策略变成「托盘」',
    l5.mode === 'tray' && l5.shown === 'none' && l5.hidden === true && /托盘/.test(l5.tip || ''), JSON.stringify(l5));

  // L6 设置中心改口径：切「直接退出应用」→ 镜像与磁盘即时同步，关闭按钮文案/无障碍名跟着变
  await wakeBySecondInstance();
  await ev(cdp0, `document.querySelector('#openSettings').click()`);
  await sleep(400);
  const l6 = await ev(cdp0, `(async () => {
    const radio = document.querySelector('input[name="cfgCloseMode"][value="exit_app"]');
    const checkedBefore = document.querySelector('input[name="cfgCloseMode"]:checked')?.value;
    radio.click();
    await new Promise((r) => setTimeout(r, 400));
    return { checkedBefore, now: document.querySelector('input[name="cfgCloseMode"]:checked')?.value,
      tip: document.querySelector('#winClose').dataset.tip,
      aria: document.querySelector('#winClose').getAttribute('aria-label'),
      action: localStorage.getItem(${JSON.stringify(ACTION_KEY)}),
      remember: localStorage.getItem(${JSON.stringify(REMEMBER_KEY)}) };
  })()`);
  t('L6 场景C：设置切「直接退出」→ 单选同步 + 文案/无障碍名改口 + 两键写穿',
    l6.checkedBefore === 'minimize_to_tray' && l6.now === 'exit_app' && /退出/.test(l6.tip || '')
    && l6.aria === '关闭并退出应用' && l6.action === 'exit_app' && l6.remember === '1', JSON.stringify(l6));
  const l6d = readSettings();
  t('L6b 设置页的选择落盘（close_action=exit_app）', l6d[ACTION_KEY] === 'exit_app' && l6d[REMEMBER_KEY] === '1', JSON.stringify(l6d));

  // L7 末条：已记住 exit → 点关闭进程真退出（CDP 断开 / target 消失才算通过）
  // 退出会掐断这条 IPC 的回执，所以拿不到值不算失败——判据只在「target 是否消失」。
  let l7mode = '(回执随进程退出中断)';
  try {
    l7mode = await Promise.race([ev(cdp0, `globalThis.emoReply.closeWindow()`), sleep(4000).then(() => '(回执超时)')]);
  } catch (e) { l7mode = '连接中断：' + (e.message || e); }
  const gone = !(await pollTarget(15000));
  t('L7 记住「直接退出」后点关闭→进程退出（EmoReply target 消失）', gone, 'mode=' + l7mode);

  console.log(`\n===== T03777 E2E: PASS ${pass} / FAIL ${fail} =====`);
  if (fails.length) fails.forEach((f) => console.log('  FAIL> ' + f));
  try { cdp0.close(); } catch (e) { /* 连接多半已随应用退出断掉 */ }
  // 先摘掉 socket 再等一拍才 exit：L7 会让渲染进程连同 WS 一起消失，
  // 若 process.exit 抢在 undici 投递关闭事件之前，Windows 上的 node 会命中
  // libuv 断言（src/win/async.c UV_HANDLE_CLOSING）并abort —— 断言全绿也拿不到退出码 0。
  await sleep(500);
  process.exit(fail ? 2 : 0);
}

async function pollTarget(timeoutMs) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    try {
      await getTarget();
    } catch (e) {
      return false; // 页面没了 = 进程已退
    }
    await sleep(500);
  }
  return true;
}

function start() {
  main().catch((e) => { console.error('E2E FAILED:', e.message || e); process.exit(1); });
}
start();
