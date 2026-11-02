// T03996 冒烟测试：node scripts/tray-menu.smoke.js
// 托盘是原生控件、CDP 点不到，菜单项序与「点了落到哪个动作」只能在这里锁死。
// 判据重点：检查更新那一项**只**把用户送到设置中心入口，不自己查版本、不自己下载、更不退出程序。
'use strict';
const assert = require('node:assert');
const { buildTrayMenuTemplate } = require('../tray-menu');

let pass = 0;
const calls = [];
const h = {
  toggleWindow: () => calls.push('toggleWindow'),
  applyPin: (on) => calls.push('applyPin:' + on),
  openUpdatePanel: () => calls.push('openUpdatePanel'),
  quitApp: () => calls.push('quitApp'),
  pinned: true,
};
const tpl = buildTrayMenuTemplate(h);

// 1. 项序与文案（改菜单必须同时改这里，防止「在线更新」被悄悄挪走或重名）
assert.deepStrictEqual(tpl.map((i) => i.label || i.type),
  ['显示/隐藏窗口', '窗口置顶', 'separator', '检查更新…', 'separator', '退出应用']);
pass++; console.log('  ok 菜单项序：显示/隐藏 → 置顶 → ─ → 检查更新… → ─ → 退出应用');

// 2. 每个 click 只落到自己那一个动作，不多不少
function only(label, expected) {
  const item = tpl.find((i) => i.label === label);
  assert.ok(item, '菜单项缺失：' + label);
  calls.length = 0;
  item.click({ checked: true });
  assert.deepStrictEqual(calls, expected, label + ' 的落点不符');
  pass++; console.log(`  ok ${label} → ${expected.join(',')}`);
}
only('显示/隐藏窗口', ['toggleWindow']);
only('窗口置顶', ['applyPin:true']);
only('检查更新…', ['openUpdatePanel']);
only('退出应用', ['quitApp']);

// 3. 「检查更新」不得越权：不触发退出、不触发置顶切换（更新动作全部走设置中心那一条路径）
calls.length = 0;
tpl.find((i) => i.label === '检查更新…').click({});
assert.deepStrictEqual(calls, ['openUpdatePanel'], '检查更新越权调用了别的动作');
assert.ok(!calls.some((c) => c.startsWith('quitApp')), '点检查更新绝不能退出应用');
pass++; console.log('  ok 检查更新只唤起设置中心入口，无副作用（不退出/不改置顶）');

// 4. 置顶勾选态来自构建时的快照（切换后须重建菜单这条既有约定）
assert.strictEqual(tpl[1].type, 'checkbox');
assert.strictEqual(tpl[1].checked, true, 'pinned=true 应勾上');
assert.strictEqual(buildTrayMenuTemplate({ ...h, pinned: false })[1].checked, false, 'pinned=false 应取消勾选');
pass++; console.log('  ok 置顶 checkbox 跟随 pinned 快照（true/false 两侧）');

// 5. 菜单里没有「手填更新源/仓库地址」这类项：更新源只能是 updater.js 里的代码常量
assert.ok(!tpl.some((i) => /仓库|地址|源|URL|自定义/i.test(String(i.label || ''))),
  '菜单里出现了可改更新源的项');
pass++; console.log('  ok 菜单不暴露更新源入口（地址内置，不可由用户/界面改写）');

console.log(`\n===== TRAY-MENU SMOKE: ${pass} PASS =====`);
