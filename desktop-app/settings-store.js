// T01541：设置持久化存储（主进程侧唯一事实源）
//
// 背景（实测根因）：设置原先只存渲染进程 localStorage（leveldb 独占锁）。
// 托盘常驻使旧进程存活时，用户双击图标"重启"会起第二实例——读不到锁定的
// localStorage，界面即呈现"设置全部重置"；进程被强杀时未落盘写入同样丢失。
//
// 设计约束（对应任务单）：
// - 存储位置：OS 用户级配置目录（app.getPath('userData')/settings.json），重装不清零
// - 原子性：写临时文件 → fsync → rename 替换，崩溃不产生半截文件
// - 版本兼容：schema 字段 + 迁移钩子；未知键保留（向前兼容），缺失键回落默认（向后兼容）
// - 安全性：敏感键（含 apiKey 的 config/models）经 Electron safeStorage（Windows=DPAPI）
//   加密为 enc:<base64>；不可用时降级明文并置 meta.insecureSecrets 标记（不静默）
// - 损坏自愈：JSON 解析失败 → 原文件改名 settings.json.corrupt-<ts> 备份后重建
// 纯 Node 可测：safeStorage / dir 均注入，冒烟见 scripts/settings.smoke.js
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const SCHEMA = 1;
const SECRET_KEYS = new Set(['emoreply_config', 'emoreply_models']); // 值内含 apiKey
const ENC_PREFIX = 'enc:';

function createSettingsStore({ dir, safeStorage = null, debounceMs = 150, log = console }) {
  const file = path.join(dir, 'settings.json');
  let data = { schema: SCHEMA, values: {}, meta: {} };
  let loadedFromDisk = false; // T03639：渲染层据此区分「首启迁移」与「文件已确立事实源」
  let dirtyTimer = 0;
  let lastErr = null;

  // ---- 加解密（safeStorage 缺失/不可用时明文降级 + 标记，读取端两种形态都认）----
  function encryptVal(k, v) {
    if (!SECRET_KEYS.has(k)) return v;
    if (safeStorage?.isEncryptionAvailable()) {
      return ENC_PREFIX + safeStorage.encryptString(v).toString('base64');
    }
    data.meta.insecureSecrets = true;
    return v;
  }
  function decryptVal(k, v) {
    if (typeof v !== 'string' || !v.startsWith(ENC_PREFIX)) return v;
    try {
      return safeStorage.decryptString(Buffer.from(v.slice(ENC_PREFIX.length), 'base64'));
    } catch (e) {
      // 换机/换用户导致 DPAPI 解不开：该键回落丢失而非带毒返回，留日志便于排查
      log.warn(`[settings] 解密失败，键 ${k} 已丢弃：${e.message}`);
      return null;
    }
  }

  // ---- 版本迁移钩子：低版本→SCHEMA 逐版升级；当前仅 v1，未知更高 schema 只警告不炸 ----
  function migrate(raw) {
    if (typeof raw.schema === 'number' && raw.schema > SCHEMA) {
      log.warn(`[settings] 文件 schema=${raw.schema} 高于当前支持 ${SCHEMA}，按已知字段读取`);
    }
    return { schema: SCHEMA, values: raw.values && typeof raw.values === 'object' ? raw.values : {}, meta: raw.meta || {} };
  }

  function load() {
    try {
      const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
      data = migrate(raw);
      loadedFromDisk = true;
    } catch (e) {
      if (e.code !== 'ENOENT') {
        // 损坏自愈：备份原件再重建，绝不静默吞掉用户数据
        lastErr = e.message;
        const bak = file + '.corrupt-' + Date.now();
        try { fs.renameSync(file, bak); log.warn(`[settings] 文件损坏，已备份到 ${bak}`); } catch (err) { log.warn('[settings] 损坏备份失败：' + err.message); }
      }
      data = { schema: SCHEMA, values: {}, meta: {} };
    }
  }

  // 解密视图（渲染层水合用）：密文解不开或无值的键直接剔除
  function getAll() {
    const out = {};
    for (const [k, v] of Object.entries(data.values)) {
      const pv = decryptVal(k, v);
      if (pv !== null && pv !== undefined) out[k] = String(pv);
    }
    return out;
  }

  // ---- 原子写：tmp → fsync → rename（NTFS 同目录 rename 原子替换）----
  function flushSync() {
    if (dirtyTimer) { clearTimeout(dirtyTimer); dirtyTimer = 0; }
    try {
      fs.mkdirSync(dir, { recursive: true });
      const tmp = file + '.tmp';
      const fd = fs.openSync(tmp, 'w');
      try { fs.writeSync(fd, JSON.stringify(data, null, 2)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
      fs.renameSync(tmp, file);
      lastErr = null;
    } catch (e) {
      lastErr = e.message;
      log.error('[settings] 落盘失败：' + e.message);
    }
  }

  function set(k, v) {
    data.values[k] = encryptVal(k, String(v));
    if (!dirtyTimer) dirtyTimer = setTimeout(() => { dirtyTimer = 0; flushSync(); }, debounceMs); // 防抖不阻塞主线程
  }

  // T03639：删除与 set 不对称——立即落盘、不防抖。删除若落在防抖窗口内被硬杀，
  // 文件里还留着这个键，下次启动水合就把旧值灌回镜像，用户看到「删了又回来了」。
  // 删除代价低（个位数 KB 原子写），宁慢勿丢。
  function remove(k) {
    if (!(k in data.values)) return false;
    delete data.values[k];
    flushSync();
    return true;
  }

  load();
  return {
    getAll, set, remove, flushSync,
    existed: () => loadedFromDisk,
    stats: () => ({ file, schema: data.schema, keys: Object.keys(data.values).length, lastErr, meta: data.meta }),
  };
}

module.exports = { createSettingsStore, SCHEMA, SECRET_KEYS, ENC_PREFIX };
