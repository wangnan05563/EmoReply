// PC 端功能×体验 UI 全面 E2E 测试（CDP 驱动，DOM 层断言 + 状态截图）。
// 前提：electron 以 --remote-debugging-port=9222 启动（env -u ELECTRON_RUN_AS_NODE）。
// 必须用独立 profile 启动：本套件会清掉向导标志/配置等持久化键（真实用户档案不该被动过），
// 事实源落在 OS 默认配置目录时脚本直接拒跑（exit 3）：
//   env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe . \
//     --remote-debugging-port=9222 --user-data-dir=tmp/e2e-profile
// ⚠ 窗口隐藏（document.hidden）时 Page.captureScreenshot 会永久挂起：脚本已加可见性守卫+10s 超时，
//   但遇到隐藏请重启 electron 实例（常见于上一轮 hide 过窗口 / 远程会话无桌面）。
// 退出码：0 全通过 / 2 有断言失败（产品或环境问题）/ 3 前置不满足（档案不安全）/ 1 脚本或连接异常。
// 用法：node scripts/ui-e2e.js   （输出：逐条 PASS/FAIL 矩阵 + docs/screenshots/e2e-*.png）
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const PORT = 9222;
const OUT = path.join(__dirname, '..', 'docs', 'screenshots');
let pass = 0;
let fail = 0;
const fails = [];

function t(name, cond, extra) {
  if (cond) {
    pass++;
    console.log('  PASS', name);
  } else {
    fail++;
    fails.push(name + (extra ? ' | ' + extra : ''));
    console.log('  FAIL', name, extra || '');
  }
}
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

// ⚠ 窗口隐藏（document.hidden）时 Page.captureScreenshot 会永久挂起 —— 必须先判可见性并加超时。
async function shot(cdp, name) {
  const hidden = await ev(cdp, 'document.hidden');
  if (hidden) {
    console.log('  SKIP-SHOT', name, '(窗口隐藏，无法截图；请重启 Electron 实例)');
    return;
  }
  const cap = cdp.send('Page.captureScreenshot', { format: 'png' });
  const timeout = new Promise((_, rej) => setTimeout(() => rej(new Error('captureScreenshot timeout')), 10000));
  try {
    const { data } = await Promise.race([cap, timeout]);
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(data, 'base64'));
  } catch (e) {
    console.log('  SKIP-SHOT', name, '(' + e.message + ')');
  }
}

async function reload(cdp) {
  await ev(cdp, 'location.reload()');
  await sleep(1500);
}

// 消息入口 = 剪贴板 → 「📋读取」按钮（#msg 为只读展示区，无输入监听）
async function setMsg(cdp, txt) {
  await ev(cdp, `
    (async () => {
      await globalThis.emoReply.writeClipboard(${JSON.stringify(txt)});
      document.querySelector('#btnRead').click();
      await new Promise(r => setTimeout(r, 400));
      return document.querySelector('#msg').textContent;
    })()`);
}

const CLICK_GEN = `document.querySelector('#btnGen').click()`;
const NOTICE = `document.querySelector('#notice').textContent`;
const CANDS = `Array.from(document.querySelectorAll('#candidates .candidate:not(.streaming)'))`;

// T03639 前置（护栏 + 状态复位）与 ui-shots.js 共用，见 scripts/profile-guard.js
const guard = require('./profile-guard');

async function main() {
  const wsUrl = await getTarget();
  const cdp = await connect(wsUrl);
  const evp = (expr) => ev(cdp, expr);
  await cdp.send('Page.enable');
  await sleep(800);
  const preHidden = await ev(cdp, 'document.hidden');
  if (preHidden) {
    console.error('前置检查失败：窗口处于隐藏状态（document.hidden=true），截图不可用。');
    console.error('处理：关闭所有 electron 进程后重新启动 `env -u ELECTRON_RUN_AS_NODE electron . --remote-debugging-port=9222`。');
  }

  const cfgFile = await guard.readCfgFile(evp);
  if (guard.isDefaultProfileFile(cfgFile)) {
    console.error(guard.guardHint(cfgFile));
    await cdp.close();
    process.exit(3);
  }
  const resetHint = await guard.resetProfileState(evp, sleep, () => reload(cdp));
  console.log('  档案事实源：' + cfgFile + (resetHint ? '\n  ⚠ ' + resetHint : ''));

  // ============ A. 启动 / 三步向导 ============
  console.log('A. 启动/向导');
  const wizShown = await ev(cdp, `(() => { const w = document.querySelector('#wizard'); return !!w && getComputedStyle(w).display !== 'none'; })()`);
  t('A1 向导在未完成时显示', wizShown, resetHint);
  await shot(cdp, 'e2e-01-wizard');
  // 向导为单面板三步内联（无独立「下一步」）：选「自定义…」应让模型名/接口地址输入框显形
  await ev(cdp, `
    (function(){
      const s = document.querySelector('#wzPreset');
      s.value = 'custom|';
      s.dispatchEvent(new Event('change', {bubbles:true}));
    })()`);
  await sleep(400);
  t('A2 选「自定义…」后模型名/接口地址输入框显形', await ev(cdp, `(() => { const a = document.querySelector('#wzModel'), b = document.querySelector('#wzBaseUrl'); return !!a.offsetParent && !!b.offsetParent; })()`));
  await shot(cdp, 'e2e-02-wizard-step2');
  await ev(cdp, `document.querySelector('#wzTest').click()`);
  await sleep(300);
  const wzStatus = await ev(cdp, `document.querySelector('#wzStatus').textContent`);
  t('A3 未填 Key 点「保存并测试」给出明确提示（不静默失败）', (wzStatus || '').includes('请先粘贴'), 'status=' + wzStatus);
  t('A3b 提示使用告警样式', ((await ev(cdp, `document.querySelector('#wzStatus').className`)) || '').includes('bad'));
  await ev(cdp, `document.querySelector('#wzSkip').click()`);
  await sleep(400);
  t('A4 「稍后再说」关闭向导', await ev(cdp, `!document.querySelector('#wizard') || getComputedStyle(document.querySelector('#wizard')).display === 'none'`));
  await reload(cdp);
  t('A5 刷新后不再弹向导（持久化）', await ev(cdp, `!document.querySelector('#wizard') || getComputedStyle(document.querySelector('#wizard')).display === 'none'`));

  // ============ B. 生成链路（未配置 Key → 降级）============
  console.log('B. 生成链路/降级');
  await setMsg(cdp, '今晚一起吃饭吗？');
  await ev(cdp, CLICK_GEN);
  await sleep(2500);
  const candCount = await ev(cdp, `${CANDS}.length`);
  t('B1 降级生成产出候选（>=1）', candCount >= 1, 'count=' + candCount);
  t('B2 notice 提示本地模板降级', ((await ev(cdp, NOTICE)) || '').includes('本地模板'));
  const cardChecks = await ev(cdp, `${CANDS}.slice(0,3).map(c => ({
    text: !!c.querySelector('.cand-text') && c.querySelector('.cand-text').textContent.length > 0,
    role: c.getAttribute('role') === 'button',
    tab: c.tabIndex === 0,
    aria: (c.getAttribute('aria-label') || '').length > 0,
    idx: !!c.dataset.idx,
    acts: c.querySelectorAll('.act').length
  }))`);
  t('B3 候选卡结构完整（文本/role/tabIndex/aria/序号/三动作）', cardChecks.every((c) => c.text && c.role && c.tab && c.aria && c.idx && c.acts >= 3), JSON.stringify(cardChecks).slice(0, 120));
  await shot(cdp, 'e2e-03-candidates');

  // 要不要回判定（纯表情拦截 + 二次旁路）
  await setMsg(cdp, '👍');
  await ev(cdp, CLICK_GEN);
  await sleep(1200);
  const n1 = await ev(cdp, NOTICE);
  t('B4 纯表情被本地判定拦截（压误判）', (n1 || '').includes('未生成'), 'notice=' + n1);
  await shot(cdp, 'e2e-04-judge-bypass');
  await ev(cdp, CLICK_GEN);
  await sleep(2500);
  t('B5 再次点击旁路判定强制生成', (await ev(cdp, `${CANDS}.length`)) >= 1);

  // 换一条（exclude 重生成）
  await setMsg(cdp, '周末有空吗？');
  await ev(cdp, CLICK_GEN);
  await sleep(2500);
  const regenBtn = await ev(cdp, `(() => { const b = Array.from(document.querySelectorAll('#candidates .act')).find(x => (x.dataset.tip || x.textContent).includes('换一条')); if (b) b.click(); return !!b; })()`);
  await sleep(2500);
  t('B6 「换一条」可触发重生成且恢复可交互', regenBtn === true && (await ev(cdp, `!document.querySelector('#btnGen').disabled`)));

  // ============ C. 指令栏 ============
  console.log('C. 指令栏');
  const cmdCount = await ev(cdp, `document.querySelectorAll('#cmdBar button').length`);
  t('C1 内置指令包渲染（>=3）', cmdCount >= 3, 'count=' + cmdCount);
  await ev(cdp, `(() => { const b = document.querySelector('#cmdBar button'); if (b) b.click(); return !!b; })()`);
  await sleep(1200);
  t('C2 未配置云端时指令给出明确提示', ((await ev(cdp, NOTICE)) || '').includes('未配置云端'));
  // ⚠ 指令文本框是「全量列表」语义：覆盖写会永久顶掉内置包（播种标志使其不再回填）。
  // 故此处采用「追加 + 事后还原」，并顺带断言内置包未被覆盖。
  const origCmds = await ev(cdp, `localStorage.getItem('emoreply_commands')`);
  await ev(cdp, `
    (function(){
      const ta = document.querySelector('#cfgCommands');
      ta.value = ta.value + '\\n测试指令E2E|把消息改得更简短';
      ta.dispatchEvent(new Event('input', {bubbles:true}));
      document.querySelector('#btnSaveCfg').click();
    })()`);
  await sleep(800);
  t('C3 自定义指令保存后出现在指令栏', await ev(cdp, `Array.from(document.querySelectorAll('#cmdBar button')).some(b => b.textContent.includes('测试指令E2E'))`));
  const builtinLeft = await ev(cdp, `Array.from(document.querySelectorAll('#cmdBar button')).filter(b => /委婉拒绝|高情商道歉|温和催促|安慰共情|得体感谢/.test(b.textContent)).length`);
  t('C4 追加自定义指令不覆盖内置场景包（5 条仍在）', builtinLeft === 5, 'builtinLeft=' + builtinLeft);
  await shot(cdp, 'e2e-05-cmdbar');
  // 清理：还原原始指令列表（含内置包），并重载使指令栏按还原后的列表重渲染
  await ev(cdp, `(function(){ localStorage.setItem('emoreply_commands', ${JSON.stringify(String(origCmds || '[]'))}); })()`);
  await reload(cdp);
  t('C5 清理后内置场景包恢复', (await ev(cdp, `document.querySelectorAll('#cmdBar button').length`)) >= 5);

  // ============ D. 键盘导航（无障碍）============
  console.log('D. 键盘导航');
  // C 段指令点击清空了候选区，重新生成一轮再测导航
  await setMsg(cdp, '今晚一起吃饭吗？');
  await ev(cdp, CLICK_GEN);
  await sleep(2500);
  await ev(cdp, `window.__clicks = []; document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('.act'); if (b) window.__clicks.push((b.dataset.tip || b.textContent).trim().slice(0, 3)); }, true);`);
  await ev(cdp, `
    (function(){
      const cards = document.querySelectorAll('#candidates .candidate');
      cards[0] && cards[0].focus();
    })()`);
  await ev(cdp, `document.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowDown', bubbles:true}))`);
  await sleep(200);
  const focusedIdx = await ev(cdp, `(() => { const cards = Array.from(document.querySelectorAll('#candidates .candidate')); return cards.indexOf(document.activeElement); })()`);
  t('D1 ArrowDown 聚焦移到下一条候选', focusedIdx === 1, 'idx=' + focusedIdx);
  await ev(cdp, `document.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowUp', bubbles:true}))`);
  await sleep(200);
  t('D2 ArrowUp 回到第一条', (await ev(cdp, `(() => { const cards = Array.from(document.querySelectorAll('#candidates .candidate')); return cards.indexOf(document.activeElement); })()`)) === 0);
  await ev(cdp, `document.dispatchEvent(new KeyboardEvent('keydown', {key:'2', bubbles:true}))`);
  await sleep(300);
  t('D3 数字键 2 直选第二条候选（触发其动作）', ((await ev(cdp, `window.__clicks.length`)) >= 1), 'clicks=' + (await ev(cdp, `window.__clicks.length`)));

  // ============ E. 设置项 ============
  console.log('E. 设置');
  t('E1 API Key 输入为掩码（type=password）', await ev(cdp, `document.querySelector('#cfgApiKey').type === 'password'`));
  const swCount = await ev(cdp, `document.querySelectorAll('#accentSwatches .sw').length`);
  t('E2 强调色色板 6 档', swCount === 6, 'count=' + swCount);
  const beforeAccent = await ev(cdp, `getComputedStyle(document.documentElement).getPropertyValue('--c-accent').trim()`);
  await ev(cdp, `document.querySelectorAll('#accentSwatches .sw')[3].click()`);
  await sleep(300);
  const afterAccent = await ev(cdp, `getComputedStyle(document.documentElement).getPropertyValue('--c-accent').trim()`);
  t('E3 点击色板切换强调色（css 变量生效）', beforeAccent !== afterAccent && /7c3aed/i.test(afterAccent), beforeAccent + '->' + afterAccent);
  t('E4 强调色持久化（localStorage）', ((await ev(cdp, `localStorage.getItem('emoreply_accent')`)) || '').toLowerCase() === '#7c3aed');
  await shot(cdp, 'e2e-06-accent-purple');
  await ev(cdp, `document.querySelectorAll('#accentSwatches .sw')[0].click()`);

  await ev(cdp, `
    (function(){
      const sel = document.querySelector('#cfgUiScale');
      sel.value = '1.15';
      sel.dispatchEvent(new Event('change', {bubbles:true}));
    })()`);
  await sleep(300);
  t('E5 界面缩放大档生效（body zoom=1.15）', ((await ev(cdp, `document.body.style.zoom`)) || '') === '1.15');
  t('E6 缩放持久化', ((await ev(cdp, `localStorage.getItem('emoreply_ui_scale')`)) || '') === '1.15');
  await shot(cdp, 'e2e-07-scale-large');
  await ev(cdp, `
    (function(){
      const sel = document.querySelector('#cfgUiScale');
      sel.value = '1';
      sel.dispatchEvent(new Event('change', {bubbles:true}));
    })()`);

  // FR-9.1 暗色模式
  const themeOpts = await ev(cdp, `Array.from(document.querySelector('#cfgTheme').options).map(o => o.value)`);
  t('E7 主题三档可选（浅/深/跟随系统）', JSON.stringify(themeOpts) === '["light","dark","auto"]', JSON.stringify(themeOpts));
  await ev(cdp, `
    (function(){
      const sel = document.querySelector('#cfgTheme');
      sel.value = 'dark';
      sel.dispatchEvent(new Event('change', {bubbles:true}));
    })()`);
  await sleep(400);
  t('E8 切深色 → data-theme=dark 且页面底色转深', await ev(cdp, `
    (() => {
      const dark = document.documentElement.getAttribute('data-theme') === 'dark';
      const bg = getComputedStyle(document.body).backgroundColor;
      const m = bg.match(/\\d+/g) || [];
      const lum = m.length >= 3 ? (+m[0] + +m[1] + +m[2]) / 3 : 255;
      return dark && lum < 60;
    })()`), 'bg=' + (await ev(cdp, `getComputedStyle(document.body).backgroundColor`)));
  t('E9 深色持久化（localStorage）', ((await ev(cdp, `localStorage.getItem('emoreply_theme')`)) || '') === 'dark');
  await shot(cdp, 'e2e-10-theme-dark');
  await reload(cdp);
  t('E10 刷新后深色保持', await ev(cdp, `document.documentElement.getAttribute('data-theme') === 'dark'`));
  await ev(cdp, `
    (function(){
      const sel = document.querySelector('#cfgTheme');
      sel.value = 'light';
      sel.dispatchEvent(new Event('change', {bubbles:true}));
    })()`);
  await sleep(300);
  t('E11 可切回浅色', await ev(cdp, `document.documentElement.getAttribute('data-theme') === null`));

  await ev(cdp, `document.querySelectorAll('details').forEach(d => d.open = true)`);
  await sleep(300);
  await shot(cdp, 'e2e-08-settings');

  // ============ F. 历史 / 知识库 / 隐私口径 ============
  console.log('F. 历史/知识库/隐私');
  await ev(cdp, `
    (function(){
      const kb = document.querySelector('#kbText');
      kb.value = 'E2E测试词条-周末一般很闲';
      kb.dispatchEvent(new Event('input', {bubbles:true}));
      document.querySelector('#btnKbSave').click();
    })()`);
  await sleep(1200);
  const kbHas = await ev(cdp, `(document.querySelector('#kbList')||{textContent:''}).textContent.includes('E2E测试词条')`);
  t('F1 知识库新增词条可见', kbHas === true);
  const kbCount1 = await ev(cdp, `document.querySelector('#kbCount') ? document.querySelector('#kbCount').textContent : ''`);
  t('F2 知识库计数标签更新', (kbCount1 || '').length > 0, 'kbCount=' + kbCount1);
  await ev(cdp, `
    (function(){
      const rows = Array.from(document.querySelectorAll('#kbList button'));
      const del = rows.find(b => (b.dataset.tip || b.title || b.textContent || '').includes('删'));
      if (del) del.click();
    })()`);
  await sleep(800);

  // 隐私口径：降级模板不写历史（即使显式开启历史）
  await ev(cdp, `
    (function(){
      const cb = document.querySelector('#cfgHistory');
      if (cb && !cb.checked) cb.click();
      document.querySelector('#btnSaveCfg').click();
    })()`);
  await sleep(600);
  await setMsg(cdp, '这条降级消息不应进历史E2E');
  await ev(cdp, CLICK_GEN);
  await sleep(2500);
  await ev(cdp, `document.querySelector('#btnHistoryRefresh') && document.querySelector('#btnHistoryRefresh').click()`);
  await sleep(800);
  const histHas = await ev(cdp, `((document.querySelector('#historyList')||{}).textContent || '').includes('不应进历史E2E')`);
  t('F3 隐私口径：降级模板不写入历史', histHas === false);
  await ev(cdp, `
    (function(){
      const cb = document.querySelector('#cfgHistory');
      if (cb && cb.checked) cb.click();
      document.querySelector('#btnSaveCfg').click();
    })()`);
  await shot(cdp, 'e2e-09-history-kb');

  // ============ G. 其他入口 ============
  console.log('G. 其他');
  t('G1 截图取文按钮存在且可用', await ev(cdp, `!!document.querySelector('#btnOcr') && !document.querySelector('#btnOcr').disabled`));
  t('G2 撤回按钮存在', await ev(cdp, `!!document.querySelector('#btnUndo')`));
  t('G3 美化按钮存在', await ev(cdp, `!!document.querySelector('#btnBeautify')`));
  const styleOpts = await ev(cdp, `Array.from(document.querySelector('#style').options).map(o => o.value)`);
  t('G4 语气四档可选', styleOpts.length >= 4, JSON.stringify(styleOpts));

  // ============ H. 剪贴板交付链路（复制 / 回填 / 撤回 / 美化）============
  console.log('H. 剪贴板交付');
  await setMsg(cdp, '明天上午方便开个短会吗？');
  await ev(cdp, CLICK_GEN);
  await sleep(2500);
  // 先把剪贴板置为可辨识的「原文」，用于验证撤回能恢复
  await ev(cdp, `globalThis.emoReply.writeClipboard('【撤回基准原文E2E】')`);
  const firstCand = await ev(cdp, `${CANDS}[0].querySelector('.cand-text').textContent`);
  await ev(cdp, `
    (function(){
      const c = ${CANDS}[0];
      const b = Array.from(c.querySelectorAll('.act')).find(x => (x.dataset.tip || x.textContent).includes('复制'));
      if (b) b.click();
    })()`);
  await sleep(800);
  const clipAfterCopy = await ev(cdp, `globalThis.emoReply.readClipboard()`);
  t('H1 「📋 复制」把候选正文写入剪贴板', clipAfterCopy === firstCand, 'clip=' + String(clipAfterCopy).slice(0, 40));
  t('H2 复制后卡片给出成功反馈', ((await ev(cdp, `document.body.textContent`)) || '').includes('已复制'));
  await ev(cdp, `document.querySelector('#btnUndo').click()`);
  await sleep(600);
  const clipAfterUndo = await ev(cdp, `globalThis.emoReply.readClipboard()`);
  t('H3 「撤回」恢复到覆盖前原文', clipAfterUndo === '【撤回基准原文E2E】', 'clip=' + String(clipAfterUndo));
  t('H4 撤回后给出提示', ((await ev(cdp, `document.querySelector('#notice').textContent`)) || '').includes('撤回'));
  // 美化：未配置云端 → 应明确提示而非静默
  await ev(cdp, `document.querySelector('#btnBeautify').click()`);
  await sleep(2500);
  const beautyNotice = await ev(cdp, `document.querySelector('#notice').textContent`);
  t('H5 美化在未配置云端时给出明确提示', (beautyNotice || '').length > 0, 'notice=' + beautyNotice);

  // ============ I. 多模型套件 / Ollama ============
  console.log('I. 多模型/端侧');
  await ev(cdp, `
    (function(){
      const ta = document.querySelector('#cfgModels');
      ta.value = 'Alpha|https://api.example.com/v1|gpt-4o-mini|sk-test-alpha\\nBeta|https://api.example.com/v1|gpt-4o|sk-test-beta';
      ta.dispatchEvent(new Event('input', {bubbles:true}));
      document.querySelector('#btnSaveCfg').click();
    })()`);
  await sleep(800);
  const modelStatus = await ev(cdp, `document.querySelector('#cfgModelsStatus').textContent`);
  t('I1 多模型套件保存后状态区给出反馈', (modelStatus || '').length > 0, 'status=' + modelStatus);
  const savedModels = await ev(cdp, `JSON.parse(localStorage.getItem('emoreply_models')||'[]').map(m => m.name)`);
  t('I2 两套模型落盘（localStorage）', (savedModels || []).length === 2, JSON.stringify(savedModels));
  await ev(cdp, `document.querySelector('#btnAddOllama').click()`);
  await sleep(500);
  t('I3 一键添加 Ollama 预设行', ((await ev(cdp, `document.querySelector('#cfgModels').value`)) || '').includes('localhost:11434'));
  await ev(cdp, `document.querySelector('#btnCheckOllama').click()`);
  await sleep(4000);
  const ollamaMsg = await ev(cdp, `document.querySelector('#cfgModelsStatus').textContent`);
  t('I4 Ollama 连通检查有明确结论（本机未装则报错文案）', (ollamaMsg || '').length > 0, 'msg=' + ollamaMsg);
  // 还原：清空模型套件，避免脏数据
  await ev(cdp, `
    (function(){
      const ta = document.querySelector('#cfgModels');
      ta.value = '';
      ta.dispatchEvent(new Event('input', {bubbles:true}));
      document.querySelector('#btnSaveCfg').click();
    })()`);
  await sleep(500);

  // ============ J. 输入边界 / 空态 ============
  console.log('J. 输入边界');
  await ev(cdp, `globalThis.emoReply.writeClipboard(''); document.querySelector('#btnRead').click();`);
  await sleep(500);
  await ev(cdp, CLICK_GEN);
  await sleep(1500);
  const emptyNotice = await ev(cdp, `document.querySelector('#notice').textContent`);
  t('J1 空消息点生成给出明确提示（不静默/不崩）', (emptyNotice || '').length > 0, 'notice=' + emptyNotice);
  t('J2 空消息不产出候选（且清空旧候选）', (await ev(cdp, `${CANDS}.length`)) === 0, 'count=' + (await ev(cdp, `${CANDS}.length`)) + ' notice=' + emptyNotice);
  const longMsg = '长'.repeat(3000);
  await setMsg(cdp, longMsg);
  await ev(cdp, CLICK_GEN);
  await sleep(3000);
  t('J3 超长消息（3000 字）可完成生成不卡死', (await ev(cdp, `${CANDS}.length`)) >= 1);
  t('J4 生成结束后按钮恢复可交互', await ev(cdp, `!document.querySelector('#btnGen').disabled`));

  await cdp.close();
  console.log(`\n===== E2E RESULT: PASS ${pass} / FAIL ${fail} =====`);
  if (fails.length) fails.forEach((f) => console.log('  FAIL> ' + f));
  process.exit(fail ? 2 : 0);
}

main().catch((e) => {
  console.error('E2E FAILED:', e.message || e);
  process.exit(1);
});
