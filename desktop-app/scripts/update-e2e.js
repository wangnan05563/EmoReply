// T03996：桌面端「在线更新」专项 E2E（CDP 真实点击 + 真实 GitHub 往返）。
// 前提：electron 以 --remote-debugging-port=9222 + 独立 --user-data-dir 启动（同 win-ctrl-e2e.js）。
// 覆盖：设置中心入口/控件存在性 / 内置仓库与当前版本双侧一致 / 未检查时下载禁用 /
//       检查更新的真实网络结论（预期由生产逻辑 compareVersions 预测，不写死「仓库一定没发布」）/
//       界面无手填更新源入口 / 未下载不落残留包。
// 与原生托盘的关系：菜单项→openUpdatePanel 的绑定由 tray-menu.smoke.js 锁死；
//       「主进程 send → 渲染层响应」这条通道本身已由 close-ask-e2e 用原生 Alt+F4 验过同构路径。
// ⚠ 本套件会点「稍后再说」收起首启向导（写穿 emoreply_wizard_done），所以必须用**全新且独立**的
//   profile 跑；要在同一 profile 上跑 ui-e2e 的 A 段（依赖干净首启态），就把 ui-e2e 排在它前面。
// ⚠ 排在 ui-e2e 之后跑时，窗口通常是隐藏的（ui-e2e 的「回填」用例会走 paste:back → 主进程 win.hide()）：
//   折叠/展开与命中测试在隐藏态照样有效，截图才不可用；因此本套件一律判「可见性没被我的动作改变」，
//   不判「窗口必须可见」——后者测的是前序套件的残留状态，不是本单的功能。
// ⚠ 区块开合也是被带进来的状态（ui-e2e 有一轮 document.querySelectorAll('details').forEach(d => d.open = true)）：
//   所以 U1 不赌初始态，统一走「真实鼠标收起 → 再展开」，两种到达顺序用例数与结论都一致。
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const guard = require('./profile-guard');
const { createUpdater, compareVersions, REPO } = require('../updater');

const PORT = 9222;
const PROFILE = process.argv[2] || '';
let pass = 0, fail = 0;
const fails = [];
function t(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS', name); }
  else { fail++; fails.push(name + (extra ? ' | ' + extra : '')); console.log('  FAIL', name, extra || ''); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function getTarget() {
  const pages = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).filter((p) => p.type === 'page');
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
    ws.onmessage = (ev2) => {
      const msg = JSON.parse(ev2.data);
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

/**
 * 真人式点击：滚到可视区中央 → 先做 elementFromPoint 命中测试 → 再发 Input 域的鼠标事件。
 * 不用 el.click()：它绕过绘制与命中测试，折叠 details 里的元素照样能「点通」，
 * 那样测出来的是 DOM 层绿灯、用户侧空白（T03996 实现时真踩过）。
 * 命中判据只认「画在该点上的元素是目标本身或目标的子孙」——不能反过来写
 * `top.contains(el)`：零尺寸/未绘制的节点也被 body 包含，那样等于没判（联跑时
 * 首启向导已收起，#wzSkip 塌成 0x0 就是这么被放过去的）。
 * 返回 { hit, x, y }；hit=false 表示目标没被绘制/被遮挡，调用方据此判失败。
 */
async function realClick(cdp, sel) {
  const geo = await ev(cdp, `(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return { missing: true };
    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    const b = el.getBoundingClientRect();
    const x = b.x + b.width / 2, y = b.y + b.height / 2;
    const top = document.elementFromPoint(x, y);
    return { x, y, iw: innerWidth, ih: innerHeight, w: b.width, h: b.height,
      hit: b.width > 0 && b.height > 0 && !!top && (top === el || el.contains(top)),
      hitId: top ? (top.id || top.tagName) : null };
  })()`);
  if (geo.missing) throw new Error('选择器不存在：' + sel);
  if (!geo.hit) return geo;
  for (const type of ['mousePressed', 'mouseReleased']) {
    await cdp.send('Input.dispatchMouseEvent', { type, x: geo.x, y: geo.y, button: 'left', clickCount: 1, buttons: type === 'mousePressed' ? 1 : 0 });
  }
  return geo;
}

async function main() {
  if (!PROFILE) {
    console.error('用法：node scripts/update-e2e.js <e2e 专用 profile 绝对路径>\n（必须显式传入，护栏要拿它比对事实源）');
    process.exit(3);
  }
  const cdp = await connect(await getTarget());
  await cdp.send('Page.enable');
  await sleep(600);
  const cfgFile = await guard.readCfgFile((expr) => ev(cdp, expr));
  if (guard.isDefaultProfileFile(cfgFile)
    || !path.resolve(cfgFile).startsWith(path.resolve(PROFILE) + path.sep)) {
    console.error(guard.guardHint(cfgFile));
    process.exit(3);
  }

  console.log('U. T03996 在线更新');

  // U0 先关掉首启向导遮罩（z=50 盖住主界面；本套件不写 wizard 键，只点「稍后再说」）。
  // 向导只在干净 profile 上出现：排在后面的套件跑时它已经收起，#wzSkip 塌成 0x0，
  // 这时该判的是「遮罩不挡路」，而不是假装还能点得到（假点会一路把后面的命中测试带崩）。
  const wizardOn = await ev(cdp, `!!document.querySelector('#wizard')?.classList.contains('on')`);
  if (wizardOn) {
    const g0 = await realClick(cdp, '#wzSkip');
    t('U0 首启向导「稍后再说」可被真人点击（命中测试通过）', g0.hit === true, JSON.stringify(g0));
    await sleep(400);
  } else {
    t('U0 首启向导未遮罩（前序套件已收起），跳过点击步', await ev(cdp,
      `!document.querySelector('#wizard')?.classList.contains('on')`));
  }

  // U1 入口存在：真人点设置齿轮 → 设置中心里有「在线更新」区块与三个控件
  const g1 = await realClick(cdp, '#openSettings');
  t('U1a 齿轮按钮命中并可点', g1.hit === true, JSON.stringify(g1));
  await sleep(300);
  t('U1b 设置中心含在线更新区块（检查/下载/打开目录 + 状态行 + 确认行）', await ev(cdp, `(() => {
    const ids = ['updateBox','btnUpdCheck','btnUpdDownload','btnUpdOpen','updStatus','updBar','updConfirm','btnUpdYes','btnUpdNo'];
    return ids.every((i) => !!document.getElementById(i)) && document.querySelector('#settingsPage').classList.contains('on');
  })()`));

  // U1c 展开态用真实鼠标驱动，并且展开后**面板本体被绘制且可命中**：
  //     这一条是本轮实现时真踩到的坑——区块嵌在折叠的「设置」卡里时，DOM 查询、offsetParent
  //     全都正常，唯独不绘制也不接受命中测试，用户看到的是空白（el.click() 会把这个死角全绿放过去）。
  //     开合是外部可带入的状态（ui-e2e 有一轮「展开全部折叠块再扫遮挡」，跑在它后面时这里本就是开的），
  //     所以不赌初始态：统一走「展开 → 收起 → 再展开」，三个终态各自都有断言，用例数也与前序套件无关。
  const isOpen = () => ev(cdp, `document.querySelector('#updateBox').open === true`);
  if (!(await isOpen())) {
    const g = await realClick(cdp, '#updateBox > summary');
    t('U1c 真实点击 summary 展开区块', g.hit === true && await isOpen(), JSON.stringify(g));
  } else {
    t('U1c 区块初始已展开（前序套件留下的状态），仍按真实鼠标复核一遍', true, 'open=true');
  }
  await sleep(200);
  const gc = await realClick(cdp, '#updateBox > summary');
  t('U1c2 真实点击 summary 收起区块（收起后本体不再被绘制）',
    gc.hit === true && !(await isOpen()) && await ev(cdp, `(() => { const d = document.querySelector('#updateBox');
      const b = d.getBoundingClientRect(), x = b.x + b.width / 2, y = b.y + b.height / 2;
      const top = document.elementFromPoint(x, y); return !top || !top.closest || !top.closest('#updateBox') || top.tagName === 'SUMMARY'; })()`), JSON.stringify(gc));
  await sleep(200);
  const go = await realClick(cdp, '#updateBox > summary');
  t('U1c3 再点一次重新展开（开合双向都吃真实鼠标）', go.hit === true && await isOpen(), JSON.stringify(go));
  await sleep(200);
  const gu = await ev(cdp, `(() => { const d = document.querySelector('#updateBox');
    d.scrollIntoView({ block: 'center', behavior: 'instant' });
    const b = d.getBoundingClientRect(); const x = b.x + b.width / 2, y = b.y + b.height / 2;
    const top = document.elementFromPoint(x, y);
    return { open: d.open, hit: !!top && !!top.closest?.('#updateBox'), hitId: top ? (top.id || top.tagName) : null }; })()`);
  t('U1d 展开后区块被绘制且可命中（不是只存在于 DOM）', gu?.open === true && gu?.hit === true, JSON.stringify(gu));

  // U2 双侧一致：界面显示的仓库=代码常量、版本号=package.json 的 version（内置不可手改的实证）
  const pkgVersion = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8')).version;
  const shown = await ev(cdp, `({ repo: document.querySelector('#updRepo').textContent.trim(),
    cur: document.querySelector('#updCurrent').textContent.trim() })`);
  t('U2a 界面仓库名 = updater.js 内置常量', shown?.repo === REPO, JSON.stringify(shown));
  t('U2b 界面当前版本 = package.json version（主进程读盘，非硬编码字面量）', shown?.cur === pkgVersion, JSON.stringify({ shown: shown?.cur, pkgVersion }));

  // U3 未检查前不可下载、进度条与确认行收起
  t('U3 未检查时下载禁用、无进度条、无确认行', await ev(cdp, `document.querySelector('#btnUpdCheck').disabled === false
    && document.querySelector('#btnUpdDownload').disabled === true
    && document.querySelector('#updBar').style.display === 'none'
    && document.querySelector('#updConfirm').style.display === 'none'`));

  // U4 界面不提供更新源输入口（硬编码内置、不开放手动输入）
  t('U4 区块内无任何输入控件（不能手填仓库/地址）', await ev(cdp,
    `document.querySelectorAll('#updateBox input, #updateBox textarea, #updateBox select').length === 0`));

  // U5 真实网络：用生产逻辑预测结论，再点按钮比对（不写死「仓库尚无 Release」）
  const upd = createUpdater({ currentVersion: shown.cur, dir: path.join(os.tmpdir(), 'emo-update-e2e-meta') });
  let expect;
  try {
    const r = await fetch(upd.apiUrl, { headers: { 'User-Agent': 'emoreply-e2e-probe', Accept: 'application/vnd.github+json' }, signal: AbortSignal.timeout(15000) });
    if (r.status === 404) expect = { state: 'no_release' };
    else if (!r.ok) expect = { state: 'error', hint: 'http_' + r.status };
    else {
      const d = await r.json();
      const cmp = compareVersions(shown.cur, d.tag_name || d.name);
      expect = { state: cmp === null ? 'error' : cmp >= 0 ? 'latest' : 'newer', latest: d.tag_name || d.name };
    }
  } catch (e) { expect = { state: 'error', hint: 'network' }; }

  const g5 = await realClick(cdp, '#btnUpdCheck'); // 真人点击（含命中测试），不用 el.click()
  t('U5a 检查按钮命中并可点', g5.hit === true, JSON.stringify(g5));
  const status = await ev(cdp, `(async () => {
    const el = document.querySelector('#updStatus');
    for (let i = 0; i < 90; i++) {
      if (el.textContent && !/正在检查/.test(el.textContent)) break;
      await new Promise((r) => setTimeout(r, 200));
    }
    return { text: el.textContent, ok: el.classList.contains('ok'),
      dl: document.querySelector('#btnUpdDownload').disabled };
  })()`);
  const text = status?.text || '';
  if (expect.state === 'no_release') {
    t('U5b 真实结论=no_release：文案说明「还没有发布任何版本」且不报成失败',
      /还没有发布任何版本/.test(text) && status.ok === true && status.dl === true, JSON.stringify({ text, status, expect }));
  } else if (expect.state === 'latest') {
    t('U5b 真实结论=已是最新：文案带版本号、下载仍禁用',
      /已是最新/.test(text) && text.includes(shown.cur) && status.dl === true, JSON.stringify({ text, status, expect }));
  } else if (expect.state === 'newer') {
    t('U5b 真实结论=有新版：文案报出 ' + expect.latest + ' 且下载解禁',
      /发现新版本/.test(text) && status.dl === false, JSON.stringify({ text, status, expect }));
  } else {
    t('U5b 真实结论=错误分支须有友好文案（含原因，不许空/不许技术堆栈）',
      text.length > 8 && !/undefined|\[object/.test(text), JSON.stringify({ text, expect }));
  }

  // U6 检查完仍不应留下任何下载包（没点下载就没有落盘）
  const updDir = path.join(PROFILE, 'updates');
  const leftovers = fs.existsSync(updDir) ? fs.readdirSync(updDir) : [];
  t('U6 仅检查不下载：updates 目录无残留安装包', leftovers.filter((f) => /\.exe$/i.test(f)).length === 0,
    'left=' + JSON.stringify(leftovers));

  // U7 重复检查幂等（不叠加监听、不卡在进行态）
  const g7 = await realClick(cdp, '#btnUpdCheck');
  t('U7a 二次点击仍可命中（结论变长后没把按钮挤出可视区）', g7.hit === true, JSON.stringify(g7));
  await sleep(1500);
  const again = await ev(cdp, `({ text: document.querySelector('#updStatus').textContent,
    busy: /正在检查/.test(document.querySelector('#updStatus').textContent) })`);
  t('U7 再次检查给出同样结论、不滞留在「正在检查」', again?.busy === false && again?.text?.length > 0, JSON.stringify(again));

  // U8 边界守卫走真实 IPC：没查出新版就点下载 / 没下载就点安装，必须被主进程拒掉且程序不退。
  // 「没退」不能用 document.hidden === false 判：窗口可见性是被前序套件带进来的（上一轮 hide 过
  // 窗口它就是 true，与本次动作无关）。真正的不变量是「拒绝之后页面照常应答、可见性没被改变」。
  const vis0 = await ev(cdp, `({ hidden: document.hidden, alive: !!document.querySelector('#btnUpdCheck') })`);
  const guards = await ev(cdp, `(async () => ({
    dl: await globalThis.emoReply.downloadUpdate(),
    inst: await globalThis.emoReply.installUpdate(),
  }))()`);
  const vis1 = await ev(cdp, `({ hidden: document.hidden, alive: !!document.querySelector('#btnUpdCheck'),
    status: document.querySelector('#updStatus').textContent })`);
  t('U8a 未查出新版时下载被主进程拒绝（且有文案）', guards?.dl?.state === 'error' && !!guards?.dl?.message, JSON.stringify(guards?.dl));
  t('U8b 无安装包时安装被拒（不启动任何进程）', guards?.inst?.state === 'error' && /还没有下载/.test(guards?.inst?.message || ''), JSON.stringify(guards?.inst));
  t('U8c 两次被拒后页面仍在应答、可见性未被改变（安装失败绝不退出、也不藏窗）',
    vis1?.alive === true && vis1?.hidden === vis0?.hidden && vis0?.alive === true, JSON.stringify({ vis0, vis1 }));

  await cdp.close();
  console.log(`\n===== T03996 UPDATE E2E: PASS ${pass} / FAIL ${fail} =====`);
  if (fails.length) fails.forEach((f) => console.log('  FAIL> ' + f));
  process.exit(fail ? 2 : 0);
}
// CJS 没有顶层 await：入口保持同步，异步失败在函数内兜底
function start() {
  main().catch((e) => { console.error('E2E FAILED:', e.message || e); process.exit(1); });
}
start();
