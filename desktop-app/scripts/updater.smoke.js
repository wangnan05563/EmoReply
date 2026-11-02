// T03996 冒烟测试：node scripts/updater.smoke.js
// 覆盖：版本号三口径比较、安装包挑选、文件名清洗、检查更新的每条分支、
// 下载的进度/完整性/域名白名单、安装启动的参数形态（不过 shell）。
// fetch/spawn/dir 全部注入，纯 Node 无需 Electron——运行时由 main.js 传真值。
'use strict';
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createUpdater, compareVersions, versionKey, pickAsset, sanitizeName, REPO } = require('../updater');

let pass = 0;
function ok(cond, name) { assert.ok(cond, name); pass++; console.log(`  ok ${name}`); }
function eq(a, b, name) { assert.deepStrictEqual(a, b, name); pass++; console.log(`  ok ${name}`); }
const silentLog = { warn() {}, error() {}, log() {} };

// ---- 假 fetch：按 URL 前缀匹配预置响应，同时记录调用参数供断言 ----
function fakeFetch(routes) {
  const calls = [];
  return Object.assign(async (url, init) => {
    calls.push({ url: String(url), init });
    const hit = routes.find((r) => String(url).includes(r.match));
    if (!hit) throw new Error('未预置的 URL：' + url);
    if (hit.throw) throw hit.throw;
    return hit.res;
  }, { calls });
}
function jsonRes(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body,
    headers: { get: () => null } };
}
// 可异步迭代的响应体（Node undici 形态），content-length 走 headers.get
function streamRes(chunks, contentLength, status = 200) {
  return {
    ok: status >= 200 && status < 300, status,
    headers: { get: (k) => (String(k).toLowerCase() === 'content-length' ? String(contentLength) : null) },
    body: (async function* () { for (const c of chunks) yield Buffer.from(c); })(),
  };
}
const release = (tag, assets) => ({ tag_name: tag, html_url: 'https://github.com/x/y/releases/tag/' + tag, assets });
const asset = (name, url, size) => ({ name, browser_download_url: url, size });

const GH = 'https://github.com/wangnan05563/EmoReply/releases/download/';
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'upd-smoke-'));
const mk = (currentVersion, extra = {}) => createUpdater({ currentVersion, dir: path.join(TMP, currentVersion), log: silentLog, ...extra });

async function main() {
  console.log('1. 版本号比较（仓库口径 YYYYMMDD-NNN / 打包 0.1.0-YYYYMMDD-NNN / 标签 v 前缀 / 纯 semver）');
  eq(compareVersions('20261012-001', '20261101-001'), -1, '旧日期 → 有新版本');
  eq(compareVersions('0.1.0-20261012-001', 'v20261012-001'), 0, '打包临时形态与 v 前缀标签同日期视为同版本');
  eq(compareVersions('20261101-009', 'v20261101-002'), 1, '同日期比序号，本机 009 已比仓库 002 新');
  eq(compareVersions('0.1.0-20261101-001', '0.1.0-20261102-001'), -1, '跨日期不等长数字按数值比（不是字符串字典序）');
  eq(compareVersions('1.2.3', '1.10.0'), -1, '纯 semver 走点分比较，1.2.3 < 1.10.0');
  eq(compareVersions('20261101-001', '1.2.3'), 0, '口径不同不硬比（宁可不提醒，也不把 0.1.0 说成比日期版新）');
  eq(versionKey('garbage'), null, '读不懂的版本号返回 null');
  eq(compareVersions('garbage', '20261101-001'), 1, '本机版本读不懂时保守当作「有新版本」，交给用户判断');
  eq(compareVersions('20261101-001', 'garbage'), null, '仓库版本读不懂返回 null，由 check() 给友好文案而不是误报最新');

  console.log('2. 安装包挑选与文件名清洗');
  eq(pickAsset(release('t', [asset('EmoReply-20261101-002-x64.nsis.7z', GH + 'a.7z', 1)])), null, '非 exe 资产一律不选（7z/zip 没有「提示安装」语义）');
  eq(pickAsset(release('t', [
    asset('a.exe.blockmap', GH + 'a.blockmap', 1),
    asset('EmoReply-portable-x64.exe', GH + 'p.exe', 1),
    asset('EmoReply-Setup-x64.exe', GH + 's.exe', 1),
  ])).name, 'EmoReply-Setup-x64.exe', '优先 NSIS 安装器，忽略 blockmap');
  eq(pickAsset(release('t', [asset('EmoReply-portable-x64.exe', GH + 'p.exe', 1)])).name,
    'EmoReply-portable-x64.exe', '只有 portable 时兜底选它');
  eq(sanitizeName('../../evil.exe'), 'evil.exe', '目录成分被剥掉（路径穿越只留最后一段）');
  eq(sanitizeName('..\\..\\evil.exe'), 'evil.exe', '反斜杠同样按分隔符处理（Windows 形态）');
  eq(sanitizeName('....evil.exe'), 'evil.exe', '前导点剥掉，不留隐藏名/幽灵名');
  eq(sanitizeName('..'), '', '纯点段不是文件名，直接拒绝');
  eq(sanitizeName('a/b\\c.exe'), 'c.exe', '多级路径只取最后一段');
  eq(sanitizeName('bad;name.exe'), '', '含 shell 元字符的文件名直接拒绝');
  eq(sanitizeName('x'.repeat(200) + '.exe'), '', '超长文件名拒绝');
  eq(sanitizeName('EmoReply Setup (x64).exe'), 'EmoReply Setup (x64).exe', '空格/括号/连字符属合法集，原样保留');

  console.log('3. check()：每条远端形态都要给出可读结论');
  {
    const u = mk('20261012-001', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      res: jsonRes(release('v20261101-002', [asset('EmoReply-Setup-x64.exe', GH + 'v20261101-002/EmoReply-Setup-x64.exe', 4096)])) }]) });
    const r = await u.check();
    eq([r.state, r.latest, r.name, r.size], ['newer', 'v20261101-002', 'EmoReply-Setup-x64.exe', 4096], '有新版：状态/版本/资产名/字节数齐全');
    ok(/^发现版本|发现新版本/.test(r.message), '有新版文案是「发现新版本」口径');
    ok(r.url.startsWith('https://github.com/'), '下载地址取 browser_download_url 且是 https');
    ok(u.apiUrl.includes(REPO) && u.apiUrl.startsWith('https://api.github.com/repos/'), '查询走内置仓库地址（硬编码，无用户输入）');
  }
  {
    const r = await mk('20261101-002', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      res: jsonRes(release('v20261101-002', [])) }]) }).check();
    eq([r.state, /已是最新/.test(r.message)], ['latest', true], '同版本 → 已是最新');
  }
  {
    const r = await mk('20261101-002', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      res: jsonRes({ message: 'Not Found', status: '404' }, 404) }]) }).check();
    eq([r.state, r.reason, /还没有发布/.test(r.message)], ['no_release', undefined, true],
      '仓库尚无 Release → 明确说「还没有发布任何版本」，不混进失败分支');
  }
  {
    const r = await mk('20261101-002', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      res: jsonRes({}, 403) }]) }).check();
    eq([r.state, r.reason], ['error', 'rate_limit'], '403 归因到限流（未登录 60 次/小时）');
  }
  {
    const r = await mk('20261101-002', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      throw: Object.assign(new Error('socket hang up'), { name: 'TypeError' }) }]) }).check();
  eq([r.state, r.reason, /连不上 GitHub/.test(r.message)], ['error', 'network', true], '网络异常有友好文案');
  }
  {
    // 资产指向非 GitHub 域名：响应里写什么都不许下（否则等于服务器指哪打哪）
    const r = await mk('20261012-001', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      res: jsonRes(release('v20261101-002', [asset('Setup.exe', 'https://evil.example.com/Setup.exe', 10)])) }]) }).check();
    eq([r.state, r.reason], ['error', 'unsafe_asset'], '非官方域名下载地址被拒绝');
  }
  {
    const r = await mk('20261012-001', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      res: jsonRes(release('v20261101-002', [])) }]) }).check();
    eq([r.state, r.reason], ['error', 'no_asset'], '有新版但没安装包 → 说清楚，不假装成功');
  }
  {
    const r = await mk('20261012-001', { fetchImpl: fakeFetch([{ match: 'releases/latest',
      res: jsonRes(release('weird-tag', [])) }]) }).check();
    eq([r.state, r.reason], ['error', 'unknown_version'], '仓库版本号读不懂不误报「已最新」');
  }

  console.log('4. download()：进度、完整性、域名与残留文件清理');
  {
    const info = { state: 'newer', latest: 'v20261101-002', name: 'EmoReply-Setup-x64.exe',
      size: 6, url: GH + 'EmoReply-Setup-x64.exe' };
    const u = mk('20261012-001', { fetchImpl: fakeFetch([{ match: 'releases/download',
      res: streamRes(['abc', 'def'], 6) }]) });
    const seen = [];
    const r = await u.download(info, (p) => seen.push(p.pct));
    eq(r.state, 'ready', '下载完成 → ready');
    eq(fs.readFileSync(r.file).length, 6, '落盘字节数正确');
    eq(path.basename(r.file), 'EmoReply-Setup-x64.exe', '落盘文件名 = 清洗后的资产名');
    ok(path.dirname(r.file) === path.join(TMP, '20261012-001'), '落盘在指定 updates 目录内');
    ok(seen.length >= 1 && seen[seen.length - 1] === 99, '进度回调有回报且完成前不超过 99%（100% 留给 ready 态）');
  }
  {
    // 声称 6 字节只收到 3 字节：必须删残留文件并报错，不能把半截安装包交给用户点安装
    const info = { state: 'newer', name: 'Setup.exe', size: 6, url: GH + 'Setup.exe' };
    const u = mk('short', { fetchImpl: fakeFetch([{ match: 'releases/download', res: streamRes(['abc'], 6) }]) });
    const r = await u.download(info);
    eq([r.state, r.reason], ['error', 'size_mismatch'], '字节数与声明不符判失败');
    ok(!fs.existsSync(path.join(TMP, 'short', 'Setup.exe')), '失败即删除残留文件');
  }
  {
    const u = mk('host');
    const r = await u.download({ state: 'newer', name: 'Setup.exe', size: 1, url: 'https://evil.example.com/Setup.exe' });
    eq([r.state, /GitHub 官方域名/.test(r.message)], ['error', true], '绕过 check() 直接调 download 也拦非官方域名');
  }
  {
    // 名字里带穿越也不许跑出下载目录：清洗成最后一段后仍落在 dir 内
    const u = mk('trav', { fetchImpl: fakeFetch([{ match: 'releases/download', res: streamRes(['ab'], 2) }]) });
    const r = await u.download({ state: 'newer', name: '..\\..\\evil.exe', size: 2, url: GH + 'x' });
    eq([r.state, path.basename(r.file), path.dirname(r.file) === path.join(TMP, 'trav')],
      ['ready', 'evil.exe', true], '穿越名清洗后仍在下载目录内落盘（不写到目录外面去）');
  }
  {
    const u = mk('http500', { fetchImpl: fakeFetch([{ match: 'releases/download', res: streamRes([], 0, 500) }]) });
    const r = await u.download({ state: 'newer', name: 'Setup.exe', size: 5, url: GH + 'x' });
    eq([r.state, r.reason], ['error', 'download'], 'HTTP 500 归为下载失败');
  }

  console.log('5. install()：不经过 shell 的数组参数，且拒绝不存在的文件');
  {
    const spawned = [];
    const file = path.join(TMP, '5', 'EmoReply-Setup-x64.exe');
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, 'MZ');
    const u = createUpdater({ currentVersion: '1', dir: path.join(TMP, '5'), log: silentLog,
      fetchImpl: async () => { throw new Error('不该被调用'); },
      spawnImpl: (cmd, args, opts) => { spawned.push({ cmd, args, opts }); return { unref() {} }; } });
    const r = u.install(file);
    eq([r.state, spawned[0].cmd, spawned[0].args], ['launched', file, []], '启动安装包走数组参数（无 shell 解析）');
    eq([spawned[0].opts.detached, spawned[0].opts.shell], [true, undefined], 'detached 启动、绝不带 shell:true');
    eq(u.install(path.join(TMP, 'nope.exe')).state, 'error', '安装包不存在时报错而不是拉起一个空进程');
  }
  // 真拉起一个进程（Windows 上的 tasklist，跑完自退、不留孤儿）：
  // 上面那条只证明了参数形状，这条证明 detached 启动在真系统调用里也不抛、能拿到子进程。
  if (process.platform === 'win32' && fs.existsSync('C:/Windows/System32/tasklist.exe')) {
    const u = createUpdater({ currentVersion: '1', dir: path.join(TMP, '5'), log: silentLog,
      fetchImpl: async () => { throw new Error('不该被调用'); } });
    const r = u.install('C:/Windows/System32/tasklist.exe');
    eq([r.state, typeof r.file], ['launched', 'string'], '真实 spawn：detached 启动系统小程序成功（拿到子进程）');
  }

  fs.rmSync(TMP, { recursive: true, force: true });
  console.log(`\n===== UPDATER SMOKE: ${pass} PASS =====`);
}
main().catch((e) => { console.error('UPDATER SMOKE FAILED:', e.message || e); process.exit(1); });
