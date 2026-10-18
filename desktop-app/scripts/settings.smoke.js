// T01541 冒烟测试：node scripts/settings.smoke.js
// 覆盖：原子写与落盘、损坏自愈、版本兼容（未知键保留/缺失回落）、密钥加密与降级、
// 解密失败剔除、跨实例持久化复读。safeStorage 用可注入假件，纯 Node 无需 Electron。
'use strict';
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createSettingsStore, SCHEMA, ENC_PREFIX } = require('../settings-store');

let pass = 0;
function ok(cond, name) { assert.ok(cond, name); pass++; console.log(`  ok ${name}`); }
function eq(a, b, name) { assert.deepStrictEqual(a, b, name); pass++; console.log(`  ok ${name}`); }

const silentLog = { warn() {}, error() {} };
// 假 safeStorage：可逆变换 + 前缀识别，接口与 Electron 对齐
function fakeSafe() {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (s) => Buffer.from('X:' + s, 'utf8'),
    decryptString: (buf) => { const s = buf.toString('utf8'); if (!s.startsWith('X:')) throw new Error('bad key'); return s.slice(2); },
  };
}
function tmpdir(tag) { return fs.mkdtempSync(path.join(os.tmpdir(), 'set-' + tag + '-')); }

// ---- 1. 基本读写 + 原子落盘 ----
{
  const dir = tmpdir('basic');
  const s = createSettingsStore({ dir, safeStorage: fakeSafe(), debounceMs: 10, log: silentLog });
  eq(s.getAll(), {}, '新目录空存储');
  s.set('emoreply_theme', 'dark');
  s.set('emoreply_config', JSON.stringify({ model: 'gpt-x', apiKey: 'sk-secret' }));
  fs.writeFileSync(dir + '/settings.json.tmp', 'CRASH-LEFTOVER'); // 模拟上次崩溃残留 tmp
  s.flushSync();
  ok(!fs.existsSync(dir + '/settings.json.tmp'), '原子写：崩溃残留 tmp 被覆盖替换');
  const raw = fs.readFileSync(dir + '/settings.json', 'utf8');
  const j = JSON.parse(raw);
  eq(j.schema, SCHEMA, '文件含 schema 版本字段');
  ok(raw.includes('enc:'), '敏感键落盘为密文');
  ok(!raw.includes('sk-secret'), '明文密钥不落盘');
  const s2 = createSettingsStore({ dir, safeStorage: fakeSafe(), log: silentLog }); // 新实例=重启
  eq(s2.getAll().emoreply_theme, 'dark', '跨实例复读：偏好持久');
  eq(JSON.parse(s2.getAll().emoreply_config).apiKey, 'sk-secret', '跨实例复读：密钥解密还原');
}

// ---- 2. 防抖：set 不阻塞、到期自动落盘 ----
{
  const dir = tmpdir('debounce');
  const s = createSettingsStore({ dir, safeStorage: null, debounceMs: 30, log: silentLog });
  s.set('emoreply_history', '1');
  ok(!fs.existsSync(dir + '/settings.json'), 'set 立即返回未同步写盘（防抖）');
  setTimeout(() => {
    ok(fs.existsSync(dir + '/settings.json'), '防抖到期自动落盘');
    run3();
  }, 80);
}
function run3() {
  // ---- 3. 损坏自愈 ----
  {
    const dir = tmpdir('corrupt');
    fs.writeFileSync(path.join(dir, 'settings.json'), '{"schema":1,"values":{BROKEN');
    const s = createSettingsStore({ dir, safeStorage: fakeSafe(), log: silentLog });
    eq(s.getAll(), {}, '损坏文件回落空存储不崩溃');
    const backups = fs.readdirSync(dir).filter((f) => f.startsWith('settings.json.corrupt-'));
    eq(backups.length, 1, '损坏原件已备份（不静默吞数据）');
    ok(s.stats().lastErr, '损坏错误留痕可排查');
  }
  // ---- 4. 版本兼容：未知键保留、高 schema 不误降 ----
  {
    const dir = tmpdir('version');
    fs.writeFileSync(path.join(dir, 'settings.json'), JSON.stringify({
      schema: 99, values: { emoreply_theme: 'dark', future_key: 'from-newer-build' },
    }));
    const s = createSettingsStore({ dir, safeStorage: fakeSafe(), log: silentLog });
    eq(s.getAll().emoreply_theme, 'dark', '高 schema 文件按已知字段读取');
    s.set('emoreply_accent', '#123456');
    s.flushSync();
    const j = JSON.parse(fs.readFileSync(path.join(dir, 'settings.json'), 'utf8'));
    eq(j.values.future_key, 'from-newer-build', '未知新键原样保留（向前兼容）');
  }
  // ---- 5. safeStorage 不可用：明文降级 + 显式标记 ----
  {
    const dir = tmpdir('insecure');
    const s = createSettingsStore({ dir, safeStorage: { isEncryptionAvailable: () => false }, log: silentLog });
    s.set('emoreply_config', '{"apiKey":"sk-plain"}');
    s.flushSync();
    const raw = fs.readFileSync(path.join(dir, 'settings.json'), 'utf8');
    ok(raw.includes('sk-plain'), '降级路径功能可用（明文存储）');
    ok(s.stats().meta.insecureSecrets === true, '降级不静默：meta 留标记');
  }
  // ---- 6. 解密失败（换机/换用户 DPAPI）：坏键剔除不炸 ----
  {
    const dir = tmpdir('decfail');
    fs.writeFileSync(path.join(dir, 'settings.json'), JSON.stringify({
      schema: 1, values: { emoreply_config: ENC_PREFIX + Buffer.from('not-for-us', 'utf8').toString('base64'), emoreply_theme: 'dark' },
    }));
    const s = createSettingsStore({ dir, safeStorage: fakeSafe(), log: silentLog });
    const all = s.getAll();
    ok(!('emoreply_config' in all), '解不开的敏感键剔除（宁缺勿错）');
    eq(all.emoreply_theme, 'dark', '其余键不受影响');
  }
  console.log(`\n===== SETTINGS SMOKE: ${pass} PASS =====`);
}
