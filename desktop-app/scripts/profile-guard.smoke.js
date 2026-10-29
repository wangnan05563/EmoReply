// T03639 冒烟测试：node scripts/profile-guard.smoke.js
// 护栏是「脚本会不会清掉真实用户配置」的唯一防线，判据必须锁死：
// OS 默认配置目录（Win %APPDATA%/%LOCALAPPDATA%、mac Application Support、linux ~/.config）
// 一律拒绝，取不到路径同样拒绝（无法证明安全就不清键）；显式 e2e 目录放行。
'use strict';
const assert = require('node:assert');
const os = require('node:os');
const path = require('node:path');
const { isDefaultProfileFile, RESIDUE_KEYS, RESET_KEYS } = require('./profile-guard');

let pass = 0;
function refuses(file, name) {
  assert.strictEqual(isDefaultProfileFile(file), true, name + ' 应判为生产档案');
  pass++; console.log(`  ok ${name} → 拒绝`);
}
function allows(file, name) {
  assert.strictEqual(isDefaultProfileFile(file), false, name + ' 应放行');
  pass++; console.log(`  ok ${name} → 放行`);
}

refuses('', '取不到路径');
refuses(path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'EmoReply', 'settings.json'), 'Windows Roaming');
refuses(path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'EmoReply', 'settings.json'), 'Windows Local');
refuses(path.join(os.homedir(), 'Library', 'Application Support', 'EmoReply', 'settings.json'), 'macOS Application Support');
refuses(path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'EmoReply', 'settings.json'), 'Linux ~/.config');
allows(path.join(process.cwd(), 'tmp', 'e2e-profile', 'settings.json'), '仓库内 e2e 目录');
allows(path.join(os.tmpdir(), 'e2e-fresh', 'settings.json'), '系统临时目录下的独立档案');

// 内置指令播种键不参与残留判定：清空它们正是为了触发首启播种（产品会立刻写回）
assert.ok(RESIDUE_KEYS.length === RESET_KEYS.length - 2, '残留判定剔除播种键');
assert.ok(!RESIDUE_KEYS.includes('emoreply_commands') && !RESIDUE_KEYS.includes('emoreply_commands_seeded'), '播种键不入残留清单');
assert.ok(RESIDUE_KEYS.includes('emoreply_wizard_done') && RESIDUE_KEYS.includes('emoreply_config'),
  '向导标志与配置必须参与残留判定（A1 的两个门槛）');
pass += 3;
console.log(`  ok 残留键集与 A1 门槛对齐（3 条）`);
console.log(`\n===== PROFILE-GUARD SMOKE: ${pass} PASS =====`);
