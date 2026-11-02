// T03996：桌面端「在线更新」——内置开源仓库地址，检测最新Release、下载安装包、交回用户安装。
//
// 设计边界（按单里约束逐条对齐）：
// - 仓库地址是**代码常量**（REPO），不读配置、不开放输入，也不给测试留后门：
//   更新包等于「以用户身份执行的程序」，能把源指向别处的开关就是攻击面。
//   因此本模块把 fetch/spawn/dir 做成注入参数供冒烟测试替换，运行时由 main.js 传真值。
// - 只认 GitHub 官方域名的下载地址：Release 资产 URL 来自远端响应，
//   不校验主机就等于「服务器说下哪就下哪」，域名白名单 + 文件名清洗缺一不可。
// - 失败一律返回结构化原因（state/message），由界面给友好文案，绝不静默。
// -  authenticity：GitHub 不提供资产校验和，完整性只能靠 HTTPS + 内置仓库；
//   能额外核对的只有响应里的 size（下载字节数），不一致即判失败并删除残留文件。
const path = require('node:path');
const fs = require('node:fs');

// 内置仓库（唯一事实源）：https://github.com/wangnan05563/EmoReply
const REPO = 'wangnan05563/EmoReply';
const ALLOW_HOSTS = new Set([
  'api.github.com', 'github.com', 'raw.githubusercontent.com',
  'objects.githubusercontent.com', 'release-assets.githubusercontent.com',
]);
// 版本号约定（T01160/T01164）：仓库里是 YYYYMMDD-NNN，打包时临时写成 0.1.0-YYYYMMDD-NNN，
// 标签习惯再加个 v 前缀。三者取到的都是同一段日期序号，所以先按日期序号比，取不到才回落普通 semver。
const DATE_SEQ_RE = /(\d{8})-(\d{3})/;

/** 归一化版本号：优先日期序号（本项目权威口径），其次点分 semver，都不像返回 null。 */
function versionKey(raw) {
  const s = String(raw == null ? '' : raw).trim();
  const m = DATE_SEQ_RE.exec(s);
  if (m) return { kind: 'date', a: Number(m[1]), b: Number(m[2]) };
  const p = /^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(s);
  if (p) return { kind: 'semver', a: Number(p[1]), b: Number(p[2]), c: Number(p[3]) };
  return null;
}

/** -1 = older 小于 newer，0 = 同版本，1 = older 已是最新（后一个参数是仓库里的最新版）。 */
function compareVersions(older, newer) {
  const x = versionKey(older), y = versionKey(newer);
  if (!y) return null;          // 仓库版本号读不懂：不能断定有更新
  if (!x) return 1;             // 本机版本号读不懂（异常构建）：保守起见当作「有新版本」提示用户
  if (x.kind !== y.kind) return 0; // 口径不同不硬比，避免把 0.1.0 当成比 20261101 更新的版本
  const d = (x.a - y.a) || (x.b - y.b) || ((x.c || 0) - (y.c || 0));
  return d < 0 ? -1 : d > 0 ? 1 : 0;
}

/**
 * 资产名来自远端响应：先取路径最后一段（两种分隔符都认），再清洗字符。
 * 顺序不能反——先剥斜杠会让 `../../evil.exe` 变成 `....evil.exe`，
 * 看着像被拒了，其实只是把穿越痕迹换成前导点，落盘名仍然不对。
 * 前导点一律剥掉（隐藏文件与 `.`/`..` 这类幽灵名都不该出现在下载目录）。
 */
function sanitizeName(name) {
  const s = String(name || '').replace(/\\/g, '/');
  const base = s.slice(s.lastIndexOf('/') + 1).replace(/^\.+/, '');
  return /^[\w][\w.\- ()+]*$/.test(base) && base.length <= 120 ? base : '';
}

/** 选要下载的安装包：只要 Windows 的 .exe，优先 NSIS 安装器，其次 portable；忽略 blockmap/元数据。 */
function pickAsset(release) {
  const list = Array.isArray(release && release.assets) ? release.assets : [];
  const usable = list.filter((a) => a && typeof a.browser_download_url === 'string'
    && !/\.(blockmap|yml|yaml|json)$/i.test(String(a.name || '')));
  const win = usable.filter((a) => /\.exe$/i.test(String(a.name || '')));
  const byName = (re) => win.find((a) => re.test(String(a.name || '')));
  // electron-builder 的 nsis 产物名带 Setup/Installer，portable 带 portable——优先前者（装上即升级旧版）
  return byName(/setup|installer/i) || byName(/portable/i) || win[0] || null;
}

/**
 * @param {object} o { currentVersion, dir, fetchImpl, spawnImpl, log, repo(仅测试可覆写) }
 */
function createUpdater(o = {}) {
  const repo = o.repo || REPO;
  const fetchImpl = o.fetchImpl || globalThis.fetch;
  const spawnImpl = o.spawnImpl || require('node:child_process').spawn;
  const log = o.log || console;
  const dir = o.dir;
  const currentVersion = o.currentVersion || '0';
  if (typeof fetchImpl !== 'function') throw new Error('createUpdater 需要 fetch 实现');
  if (!dir) throw new Error('createUpdater 需要下载目录 dir');
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Error('内置仓库地址不合法：' + repo);

  const apiUrl = `https://api.github.com/repos/${repo}/releases/latest`;

  /** 查最新版本。返回 { state, ... }，state ∈ newer | latest | no_release | error */
  async function check() {
    let res;
    try {
      res = await fetchImpl(apiUrl, {
        headers: { 'User-Agent': 'emoreply-desktop-updater', Accept: 'application/vnd.github+json' },
        signal: AbortSignal.timeout(15000),
      });
    } catch (e) {
      const msg = String((e && e.message) || e || '');
      return { state: 'error', message: /timeout|abort/i.test(msg)
        ? '检查更新超时：网络不通或 GitHub 无响应，请稍后再试。'
        : `连不上 GitHub（${repo}）：${msg}`, reason: 'network' };
    }
    if (res.status === 404) {
      // 仓库还没有任何 Release：这是「没有可检测的线上版本」，不是失败，也不该吓唬用户
      return { state: 'no_release', current: currentVersion, repo,
        message: `内置仓库 ${repo} 还没有发布任何版本，暂时没有可检测的线上更新。` };
    }
    if (res.status === 403 || res.status === 429) {
      return { state: 'error', reason: 'rate_limit',
        message: 'GitHub 接口调用过于频繁（未登录限流），请过一小时再试。' };
    }
    if (!res.ok) return { state: 'error', reason: 'http_' + res.status,
      message: `GitHub 返回异常状态 ${res.status}，请稍后再试。` };

    let data;
    try { data = await res.json(); } catch (e) {
      return { state: 'error', reason: 'bad_json', message: 'GitHub 返回内容解析失败，请稍后再试。' };
    }
    const latest = data && (data.tag_name || data.name);
    const cmp = compareVersions(currentVersion, latest);
    const base = { current: currentVersion, latest: latest || '', repo,
      pageUrl: typeof data.html_url === 'string' ? data.html_url : `https://github.com/${repo}/releases` };
    if (cmp === null) return { ...base, state: 'error', reason: 'unknown_version',
      message: `读不懂仓库版本号「${latest}」，无法判断是否需要更新。` };
    if (cmp >= 0) return { ...base, state: 'latest', message: `已是最新版本（${currentVersion}）。` };

    const asset = pickAsset(data);
    if (!asset) return { ...base, state: 'error', reason: 'no_asset',
      message: `发现新版本 ${latest}，但发布里没有可下载的 Windows 安装包。` };
    const name = sanitizeName(asset.name);
    let url;
    try { url = new URL(asset.browser_download_url); } catch (e) { url = null; }
    if (!name || !url || !ALLOW_HOSTS.has(url.hostname)) {
      return { ...base, state: 'error', reason: 'unsafe_asset',
        message: `发现新版本 ${latest}，但下载地址不在 GitHub 官方域名内，已拒绝。` };
    }
    return { ...base, state: 'newer', name, size: Number(asset.size) || 0, url: url.href,
      message: `发现新版本 ${latest}（当前 ${currentVersion}）。` };
  }

  /** 下载到 dir/<name>；onProgress({received,total,pct})。返回 { state, file|message } */
  async function download(info, onProgress) {
    if (!info || info.state !== 'newer') return { state: 'error', message: '没有待下载的更新。' };
    let url;
    try { url = new URL(info.url); } catch (e) { return { state: 'error', message: '下载地址不合法。' }; }
    if (!ALLOW_HOSTS.has(url.hostname)) return { state: 'error', message: '下载地址不在 GitHub 官方域名内，已拒绝。' };
    const name = sanitizeName(info.name);
    if (!name) return { state: 'error', message: '安装包文件名不合法，已拒绝。' };
    const file = path.join(dir, name);
    fs.mkdirSync(dir, { recursive: true });
    try {
      const res = await fetchImpl(info.url, { headers: { 'User-Agent': 'emoreply-desktop-updater' } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const total = Number(info.size) || Number(res.headers && res.headers.get && res.headers.get('content-length')) || 0;
      const fd = fs.openSync(file, 'w');
      let received = 0, lastTick = 0;
      try {
        for await (const chunk of streamOf(res)) {
          const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
          fs.writeSync(fd, buf);
          received += buf.length;
          const now = Date.now();
          if (now - lastTick >= 200 || received === total) {
            lastTick = now;
            if (onProgress) onProgress({ received, total, pct: total ? Math.min(99, Math.round(received / total * 100)) : 0 });
          }
        }
      } finally {
        fs.closeSync(fd);
      }
      if (total && received !== total) {
        fs.rmSync(file, { force: true });
        return { state: 'error', reason: 'size_mismatch',
          message: `下载不完整（${received}/${total} 字节），已删除残留文件，请重试。` };
      }
      return { state: 'ready', file, bytes: received, name, latest: info.latest,
        message: `新版安装包已下载完成（${(received / 1048576).toFixed(1)} MB）。` };
    } catch (e) {
      fs.rmSync(file, { force: true });
      return { state: 'error', reason: 'download', message: '下载失败：' + (String((e && e.message) || e)) + '，请稍后再试。' };
    }
  }

  /** 启动安装包（数组参数不过 shell）；调用方负责随后退出本程序。 */
  function install(file) {
    const name = sanitizeName(path.basename(String(file || '')));
    if (!name || !fs.existsSync(file)) return { state: 'error', message: '安装包不存在，请重新下载。' };
    try {
      const child = spawnImpl(file, [], { detached: true, stdio: 'ignore' });
      if (child && typeof child.unref === 'function') child.unref();
      return { state: 'launched', file, message: '已启动安装程序，当前窗口将退出。' };
    } catch (e) {
      return { state: 'error', message: '安装程序启动失败：' + (String((e && e.message) || e)) };
    }
  }

  return { check, download, install, repo, currentVersion, apiUrl };
}

/** 兼容 Node undici（可异步迭代）与 Web stream（getReader）两种响应体。 */
async function* streamOf(res) {
  const b = res && res.body;
  if (b && typeof b[Symbol.asyncIterator] === 'function') { yield* b; return; }
  if (b && typeof b.getReader === 'function') {
    const reader = b.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      yield value;
    }
  }
  yield await res.arrayBuffer();
}

module.exports = { createUpdater, compareVersions, versionKey, pickAsset, sanitizeName, REPO, ALLOW_HOSTS, streamOf };
