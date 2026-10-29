// 预加载：以 contextBridge 暴露最小 IPC 面（contextIsolation 开启，渲染进程无 Node 权限）
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('emoReply', {
  // T01541：设置持久化（主进程 userData/settings.json 为唯一事实源；get 同步供首帧水合）
  settingsSync: { get: () => ipcRenderer.sendSync('settings:get-all') },
  settings: {
    set: (k, v) => ipcRenderer.invoke('settings:set', k, v),
    remove: (k) => ipcRenderer.invoke('settings:remove', k),
    stats: () => ipcRenderer.invoke('settings:stats'),
  },
  readClipboard: () => ipcRenderer.invoke('clipboard:read'),
  writeClipboard: (text) => ipcRenderer.invoke('clipboard:write', text),
  generate: (payload) => ipcRenderer.invoke('llm:generate', payload),
  // T01395：粘贴回宿主（写剪贴板 + 隐藏悬浮窗 + 合成 Ctrl+V；绝不自动发送）
  pasteBack: (text) => ipcRenderer.invoke('paste:back', text),
  // T01395：美化剪贴板原文（结果写回剪贴板）
  polish: (payload) => ipcRenderer.invoke('llm:polish', payload),
  // T01399：自定义快捷指令生成
  runCommand: (payload) => ipcRenderer.invoke('llm:command', payload),
  // T01405：撤回——恢复被本应用覆盖的剪贴板内容
  undoClipboard: () => ipcRenderer.invoke('clipboard:undo'),
  // T01403：Ollama 本机连通检查
  checkOllama: () => ipcRenderer.invoke('ollama:check'),
  // T01401：要不要回判定
  shouldReply: (payload) => ipcRenderer.invoke('llm:shouldReply', payload),
  // T01404：本地加密历史
  historyList: () => ipcRenderer.invoke('history:list'),
  historyClear: () => ipcRenderer.invoke('history:clear'),
  // T01407：配置快照同步（HTTP 层与 IPC 层共用主进程内存快照）
  syncCfg: (snapshot) => ipcRenderer.invoke('cfg:sync', snapshot),
  // T01408：个人语气学习
  toneLearn: (payload) => ipcRenderer.invoke('tone:learn', payload),
  // T01400：截图 OCR 取文
  ocrScreenshot: () => ipcRenderer.invoke('ocr:screenshot'),
  // T01530：窗口最小化（控制栏按钮；托盘/任务栏可唤回）
  minimizeWindow: () => ipcRenderer.send('win:minimize'),
  // T01566：关闭到托盘（托盘不可用时降级最小化并回 'degraded'）+ 置顶切换
  closeWindow: () => ipcRenderer.invoke('win:close'),
  setPin: (on) => ipcRenderer.invoke('win:pin', on),
  getPin: () => ipcRenderer.invoke('win:get-pin'),
  // T01415：窗口可见性（隐藏时暂停动画）
  onVisibility: (cb) => {
    const listener = (_e, visible) => cb(visible);
    ipcRenderer.on('app:visibility', listener);
    return () => ipcRenderer.removeListener('app:visibility', listener);
  },
  // T01393：流式 chunk 订阅——返回取消函数（生成结束/异常时必须调用，防重复挂监听）
  onLlmChunk: (cb) => {
    const listener = (_e, data) => cb(data);
    ipcRenderer.on('llm:chunk', listener);
    return () => ipcRenderer.removeListener('llm:chunk', listener);
  },
  // T01394：全局快捷键直达生成（剪贴板新内容已由主进程读好推过来）
  onHotkeyGenerate: (cb) => {
    const listener = (_e, text) => cb(text);
    ipcRenderer.on('hotkey:generate', listener);
    return () => ipcRenderer.removeListener('hotkey:generate', listener);
  },
  kbAdd: (category, text, contact) => ipcRenderer.invoke('kb:add', { category, text, contact }),
  kbRemove: (id) => ipcRenderer.invoke('kb:remove', id),
  kbList: () => ipcRenderer.invoke('kb:list'),
  kbCount: () => ipcRenderer.invoke('kb:count'),
  kbWipe: () => ipcRenderer.invoke('kb:wipe'),
});
