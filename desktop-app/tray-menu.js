// T03996：托盘菜单模板（纯数据 + 注入的回调）。
//
// 为什么不就地写在 main.js 里：托盘是原生控件，CDP 驱动不了，「菜单里有哪几项、点了做什么」
// 这段逻辑本来只能靠代码评审保。抽成不依赖 Electron 的模板后，scripts/tray-menu.smoke.js
// 就能在纯 Node 里断言项序与每个 click 的落点（main.js 只负责把它交给 Menu.buildFromTemplate）。
//
// 约束：菜单里不出现任何「可选更新源 / 手动填仓库」的项——更新地址只在 updater.js 里是代码常量。
function buildTrayMenuTemplate(h) {
  return [
    { label: '显示/隐藏窗口', click: () => h.toggleWindow() },
    {
      label: '窗口置顶',
      type: 'checkbox',
      checked: !!h.pinned,
      click: (item) => h.applyPin(item.checked),
    },
    { type: 'separator' },
    // T03996：不在这儿查版本、不在这儿下载——只把用户送到设置中心同一条入口，避免两套实现
    { label: '检查更新…', click: () => h.openUpdatePanel() },
    { type: 'separator' },
    { label: '退出应用', click: () => h.quitApp() },
  ];
}

module.exports = { buildTrayMenuTemplate };
