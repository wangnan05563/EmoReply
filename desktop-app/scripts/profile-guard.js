// T03639：CDP 脚本的档案前置（ui-e2e.js / ui-shots.js 共用）
//
// 为什么要前置：这两个脚本都要「强制重跑向导」，即清掉 emoreply_wizard_done；
// 而 T01541 后 localStorage 只是主进程 settings.json 的镜像，删除会写穿磁盘。
// 于是两件事必须同时成立：
//   ① 护栏——只允许作用于显式传入的 e2e profile，落在 OS 默认配置目录（真实用户档案）
//      时停手，绝不能把用户的 Key/主题/指令清掉；
//   ② 复位——清的不只是向导标志：A1 的门槛是「wizard_done==='1' 或已配 apiKey」，
//      B/C/H/I 段本就假设未配置云端 Key 的降级链路。旧 profile 里这两个状态都由磁盘决定，
//      所以同一目录第二轮起稳定假失败。全套 SYNC 键一起清，判据才与「跑过几轮」无关。
'use strict';
const os = require('node:os');
const path = require('node:path');

// 与 renderer.js 的 SYNC_KEYS 一致（写穿层的键集）
const RESET_KEYS = ['emoreply_wizard_done', 'emoreply_config', 'emoreply_models', 'emoreply_commands',
  'emoreply_commands_seeded', 'emoreply_history', 'emoreply_ui_scale', 'emoreply_theme', 'emoreply_accent'];

// 复位后回读时不检这两条：清空它们正是为了触发内置指令播种，产品启动即写回——
// 属预期行为，不是残留（C4/C5 要求内置包在场）。
const RESIDUE_KEYS = RESET_KEYS.filter((k) => k !== 'emoreply_commands' && k !== 'emoreply_commands_seeded');

// 取不到路径也判为「默认档案」：无法证明安全就不清键
function isDefaultProfileFile(file) {
  if (!file) return true;
  const roots = [process.env.APPDATA, process.env.LOCALAPPDATA,
    process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'),
    path.join(os.homedir(), 'Library', 'Application Support')].filter(Boolean).map((d) => path.resolve(d));
  const abs = path.resolve(file);
  return roots.some((d) => abs === d || abs.startsWith(d + path.sep));
}

// ev 由各脚本注入（签名：(expression) => Promise<value>，需 returnByValue + awaitPromise）
// 取值表达式必须自己收成字符串：CDP 对「直接把 IPC 对象交给 awaitPromise」会返回
// type=function / value={}（实测 Electron 33 + Chrome130），拿不到 file 就被护栏误判为
// 生产档案。typeof 检查 + 三次重试覆盖页面刚导航完桥未就绪的窗口。
const STATS_EXPR = `(async () => {
  const s = globalThis.emoReply && globalThis.emoReply.settings;
  if (!s || typeof s.stats !== 'function') return '';
  const r = await s.stats();
  return r && r.file ? String(r.file) : '';
})()`;

async function readCfgFile(ev, retry = () => new Promise((r) => setTimeout(r, 500))) {
  for (let i = 0; i < 3; i++) {
    let file = '';
    try {
      file = String(await ev(STATS_EXPR) || '');
    } catch (e) {
      // 可忽略：CDP 求值失败（桥未就绪/页面导航中）本就由下面的 retry 再取一轮，
      // 最终取不到时由 isDefaultProfileFile('')「无法证明安全就不清键」兜底拒绝。
      console.warn('[profile-guard] 读取 settings.file 失败，将重试：', e?.message || e);
    }
    if (file) return file;
    await retry();
  }
  return ''; // 取不到路径 = 无法证明安全，交由护栏拒绝
}

function guardHint(file) {
  return [
    '前置检查失败（exit 3）：设置事实源不在 e2e 专用目录，继续跑会清掉真实用户配置。',
    '  当前文件：' + (file || '(未取到——preload 缺 settings.stats，或主进程 settings 未就绪)'),
    '  处理：换全新独立 profile 启动，例如',
    '    rm -rf tmp/e2e-profile && env -u ELECTRON_RUN_AS_NODE \\',
    '      ./node_modules/electron/dist/electron.exe . --remote-debugging-port=9222 --user-data-dir=tmp/e2e-profile',
  ].join('\n');
}

// 清全套 SYNC 键 → 等写穿 IPC → reload 后回读残留；返回 '' 表示复位干净
async function resetProfileState(ev, sleep, reload) {
  await ev(`(${JSON.stringify(RESET_KEYS)}.forEach((k) => localStorage.removeItem(k)))`);
  await sleep(400);
  await reload();
  // 同上：跨 CDP 取值收成字符串，避免对象被序列化成 type=function
  const raw = await ev(`JSON.stringify({
    left: ${JSON.stringify(RESIDUE_KEYS)}.filter((k) => localStorage.getItem(k) !== null),
    apiKey: (() => { try { return !!JSON.parse(localStorage.getItem('emoreply_config') || '{}').apiKey; } catch (e) { return false; } })(),
  })`);
  let r = null;
  try {
    r = JSON.parse(String(raw));
  } catch (e) {
    // 可忽略：解析失败即按「有残留」保守处理（下面 left 回落 RESIDUE_KEYS → 拒绝继续跑），
    // 不需要向上抛出打断前置检查；这里只留痕便于排查 CDP 取值异常。
    console.warn('[profile-guard] 回读状态解析失败，按残留处理：', e?.message || e);
  }
  const left = r?.left || RESIDUE_KEYS;
  const apiKey = !!r?.apiKey;
  if (!left.length && !apiKey) return '';
  return '状态复位未生效（残留 ' + (left.join(',') || '-') + (apiKey ? ' +已配置apiKey' : '') + '）：'
    + '疑似复用了生产 profile 或写穿层未生效，请 `rm -rf` 后换全新 --user-data-dir 重跑';
}

module.exports = { RESET_KEYS, RESIDUE_KEYS, isDefaultProfileFile, readCfgFile, guardHint, resetProfileState };
