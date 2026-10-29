// EmoReply 桌面端 POC 主进程（T01054 / FR-4.1 桌面形态：悬浮窗 + 剪贴板交付）
//
// 职责边界：
// - 窗口：小尺寸、置顶、无边框悬浮窗，可拖动，不抢焦点（blurable=false）
// - 剪贴板：读（取对方消息原文）/ 写（回帖候选，用户自行粘贴发送——绝不自动发送，FR-4.2）
// - LLM：主进程内 HTTPS 直连 OpenAI 兼容 /chat/completions（密钥仅存渲染进程 localStorage，
//   随配置传参调用，不落盘、不入仓库）；未配置密钥时降级本地模板（附录 C24 同款约束）
const { app, BrowserWindow, ipcMain, clipboard, globalShortcut, Tray, Menu, nativeImage, safeStorage } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const https = require('node:https');
const http = require('node:http');
const { URL } = require('node:url');
const { KnowledgeBase, LexicalRetriever, EmbeddingRetriever, deriveKey, encrypt, decrypt } = require('./kb');
const { createSettingsStore } = require('./settings-store'); // T01541：设置持久化（userData/settings.json）
const embedRetriever = new EmbeddingRetriever(); // T01402：向量档（配置 embeddingModel 才启用）
// T01392：候选清洗管线（与 m1-poc sanitize.py 同构；顺序铁律先 markdown 后标签前缀）
const { sanitizeCandidates, cleanCandidate, heuristicNoReply } = require('./sanitize');
const { extractToneCorpus } = require('./tone');
const ocr = require('./ocr');
// T01393：LLM 流式调用（独立模块，纯 Node 可冒烟）
const { callLlmStream, fanoutGenerate, generateCore, screenCandidate } = require('./llm');

let win = null;
let kb = null; // RAG 知识库（SRS 决策③；懒初始化，依赖 userData 路径）
const retriever = new LexicalRetriever();
let tray = null; // T01530：系统托盘（初始化失败保持 null，走降级口径）
let isQuitting = false; // T01530：仅托盘菜单「退出应用」/before-quit 置真，close→隐藏 不误杀进程
let settings = null; // T01541：设置存储（whenReady 内初始化）
// T01566：置顶开关状态（悬浮窗默认置顶语义不变；用户可经标题栏按钮取消。winPin 持久化）
let pinned = true;

// T01541：单实例锁——实测根因：托盘常驻使旧进程存活，双击图标"重启"会起第二实例，
// 而 localStorage(leveldb) 被旧实例独占锁定，新实例读到空存储 → 用户看到"设置全部重置"。
// 二次启动改为唤起既有窗口；设置本身另有文件层兜底（见 settings-store.js）。
if (!app.requestSingleInstanceLock()) {
  console.log('[single-instance] 锁未获得：唤起既有实例窗口后退出');
  app.quit();
} else {
  app.on('second-instance', () => { console.log('[single-instance] 收到 second-instance → 唤回窗口'); showWindowFromTray(); });
}

function createWindow() {
  win = new BrowserWindow({
    width: 380,
    height: 560,
    // 品牌图标（T01163）：标题栏 + 任务栏；打包安装包时在 build 配置中再指向同一文件
    icon: path.join(__dirname, 'assets', 'emoreply.ico'),
    alwaysOnTop: pinned, // T01566：置顶状态可切换（默认置顶，winPin='0' 时普通层级）
    focusable: true,
    frame: false,
    resizable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: true, // T01415：隐藏时 Chromium 自动节流（timer/动画 1fps）
    },
  });
  win.loadFile('index.html');
  // T01415：隐藏/显示 → renderer 暂停/恢复动画（常驻低开销）
  win.on('hide', () => { if (!win.isDestroyed()) win.webContents.send('app:visibility', false); });
  win.on('show', () => { if (!win.isDestroyed()) win.webContents.send('app:visibility', true); });
  // T01530：托盘常驻时窗口关闭 = 隐藏到托盘（进程不终止）；真退出由 isQuitting 放行
  win.on('close', (e) => {
    if (!isQuitting && tray) {
      e.preventDefault();
      win.hide();
    }
  });
}

// ---- T01530 系统托盘 + 窗口最小化（FR-PC：最小化按钮 + 托盘常驻）----
// 降级口径：托盘初始化失败仅告警日志，最小化（任务栏）仍可用，不崩溃。
function showWindowFromTray() {
  if (!win || win.isDestroyed()) return;
  if (win.isMinimized()) win.restore();
  if (!win.isVisible()) win.show();
  win.focus();
  win.moveTop(); // 置顶悬浮窗语义：托盘唤回即恢复交互态
}

function createTray() {
  try {
    const iconPath = path.join(__dirname, 'assets', 'emoreply.ico');
    const img = nativeImage.createFromPath(iconPath);
    if (img.isEmpty()) throw new Error(`托盘图标加载失败：${iconPath}`);
    tray = new Tray(img);
    tray.setToolTip('EmoReply 悬浮窗（Alt+Shift+E 呼出）');
    refreshTrayMenu();
    // 单击托盘 = 恢复窗口并置顶（右键走系统菜单，Windows 下需显式弹出）
    tray.on('click', showWindowFromTray);
    tray.on('double-click', showWindowFromTray);
    return true;
  } catch (e) {
    console.warn('[tray] 系统托盘初始化失败，降级为仅任务栏最小化：', e?.message);
    tray = null;
    return false;
  }
}

// T01566：托盘菜单随置顶状态重建（checkbox 状态在构建时快照，切换后须重设）
function refreshTrayMenu() {
  if (!tray) return;
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '显示/隐藏窗口', click: () => toggleWindow() },
      {
        label: '窗口置顶',
        type: 'checkbox',
        checked: pinned,
        click: (item) => applyPin(item.checked),
      },
      { type: 'separator' },
      { label: '退出应用', click: () => { isQuitting = true; app.quit(); } },
    ]),
  );
}

// T01566：置顶切换的事实源函数（IPC 与托盘菜单共用）——落盘 + 同步托盘菜单勾选
function applyPin(on) {
  pinned = !!on;
  if (win && !win.isDestroyed()) win.setAlwaysOnTop(pinned);
  if (settings) settings.set('winPin', pinned ? '1' : '0');
  refreshTrayMenu();
  return pinned;
}

ipcMain.on('win:minimize', () => {
  if (win && !win.isDestroyed()) win.minimize();
});

// ---- T01566 关闭到托盘 / 置顶切换 ----
// 关闭 = 隐藏到托盘（进程不退出，托盘图标可唤回）；托盘不可用时降级最小化到任务栏，
// 返回 'degraded' 供渲染层提示用户——绝不静默退出（验收：程序进程正常运行）。
ipcMain.handle('win:close', () => {
  if (!win || win.isDestroyed()) return 'none';
  if (tray) {
    win.hide();
    return 'tray';
  }
  win.minimize();
  return 'degraded';
});

ipcMain.handle('win:pin', (_e, on) => applyPin(on));
ipcMain.handle('win:get-pin', () => pinned);

// ---- T01394 全局快捷键（FR-PC3-1，方案见 docs/t01391-m1-architecture-review.md §4）----
const HOTKEY = 'Alt+Shift+E';
let lastHotkeyClip = ''; // 复制后热键直达生成：与上次触发时的剪贴板比对，相同则不重复生成

function toggleWindow() {
  if (!win) return;
  if (win.isVisible()) {
    win.hide(); // 二次触发 = 隐藏（恢复语义：再次呼出回到交互态）
    return;
  }
  win.show();
  win.focus();
  // 复制后热键直达生成：呼出时剪贴板有新内容（≠上次已生成的）→ 自动填入并生成（同 Android ✨ 一体化口径）
  const clip = clipboard.readText().trim();
  if (clip && clip !== lastHotkeyClip) {
    lastHotkeyClip = clip;
    win.webContents.send('hotkey:generate', clip);
  }
}

app.whenReady().then(() => {
  // T01541：设置存储先于窗口就绪——渲染层首帧同步水合（settings:get-all）依赖它
  settings = createSettingsStore({ dir: app.getPath('userData'), safeStorage });
  // T01566：置顶状态须在 createWindow 前恢复（alwaysOnTop 是构造项）
  pinned = settings.getAll()['winPin'] !== '0';
  kb = new KnowledgeBase(path.join(app.getPath('userData'), 'kb.json.enc'));
  // T01404：加密历史（文件路径就绪；renderer 显式开启后才会调用 history:record）
  historyPath = path.join(app.getPath('userData'), 'history.json.enc');
  historyKey = deriveKey();
  createWindow();
  createTray(); // T01530：托盘常驻（失败仅告警，不影响窗口功能）
  // 注册失败（被其他应用占用）不崩溃：悬浮窗仍可手动操作，控制台留痕
  const ok = globalShortcut.register(HOTKEY, toggleWindow);
  if (!ok) console.warn(`[hotkey] ${HOTKEY} 注册失败（可能被其他应用占用），快捷键不可用`);
  startHttpApi(); // T01407：本地 HTTP 接口（127.0.0.1）
  installWindowAnchoring(); // T01409：吸附+位置记忆+失焦收纳
  ocr.initOcr(path.join(__dirname, 'assets', 'ocr')); // T01400：离线 OCR（chi_sim best）
});
// T01530：托盘常驻后窗口关闭走「隐藏」，此事件仅在真退出（isQuitting）或托盘降级时触发；
// 托盘初始化失败（降级）保持原「关窗即退」语义，避免无托盘时进程幽灵常驻。
app.on('window-all-closed', () => {
  if (isQuitting || !tray) app.quit();
});
app.on('before-quit', () => { isQuitting = true; if (settings) settings.flushSync(); }); // 兜底：任何 app.quit() 路径不再被 close→hide 拦截；T01541 退出前强制落盘

// ---- T01541 设置持久化 IPC ----
// get-all 走 sendSync：渲染层顶层代码（主题/向导/指令种子）在首个 await 前就要读值，
// 异步注入会赶不上；payload 为个位数 KB 的一次性水合，阻塞可忽略。
// T03639：existed 告诉渲染层这次读盘是否可信——文件在 = 它是唯一事实源，镜像里有而文件里
// 没有的键是被删过的，不能再当「老用户待迁移」回灌；文件缺失/损坏才走首启迁移。
ipcMain.on('settings:get-all', (e) => {
  e.returnValue = settings ? { values: settings.getAll(), existed: settings.existed() } : { values: {}, existed: false };
});
ipcMain.handle('settings:set', (_e, k, v) => { if (settings) settings.set(String(k), String(v)); });
// T03639：删除也走 IPC——渲染层 localStorage.removeItem 需同步清掉事实源，否则下次水合灌回
ipcMain.handle('settings:remove', (_e, k) => (settings ? settings.remove(String(k)) : false));
ipcMain.handle('settings:stats', () => (settings ? settings.stats() : null));
app.on('will-quit', () => globalShortcut.unregisterAll());

// ---- 剪贴板 IPC ----

ipcMain.handle('clipboard:read', () => clipboard.readText().trim());

// T01407：配置快照（renderer 保存设置时推送；HTTP 层与 IPC 层共用同一份）
let cfgSnapshot = { config: {}, models: [] };
ipcMain.handle('cfg:sync', (_e, snap) => {
  cfgSnapshot = { config: snap?.config || {}, models: Array.isArray(snap?.models) ? snap.models : [] };
  return true;
});
function loadCfgSnapshot() {
  return cfgSnapshot;
}

// ---- T01405 撤回：本应用覆盖剪贴板前，把旧内容入栈（最多 10 层），撤回即弹栈恢复 ----
const clipboardStack = [];

function writeClipboardTracked(text) {
  const prev = clipboard.readText();
  clipboardStack.push(prev);
  if (clipboardStack.length > 10) clipboardStack.shift();
  clipboard.writeText(String(text || ''));
}

ipcMain.handle('clipboard:write', (_e, text) => {
  writeClipboardTracked(text);
  return true;
});

ipcMain.handle('clipboard:undo', () => {
  if (!clipboardStack.length) return { restored: false, text: null };
  const prev = clipboardStack.pop();
  clipboard.writeText(prev);
  return { restored: true, text: prev };
});

ipcMain.handle('clipboard:undoDepth', () => clipboardStack.length);

// ---- T01395 一键回填：粘贴回宿主（FR-PC4-1）----
// 流程：写剪贴板 → 隐藏悬浮窗（让出焦点）→ 200ms 后对宿主窗口合成 Ctrl+V。
// Windows 零依赖路径：WScript.Shell SendKeys '^v'（子进程，独立于 Electron）。
// ⚠ 绝不自动发送（FR-4.2）——只粘贴，发送永远由用户完成。
const { exec } = require('node:child_process');

ipcMain.handle('paste:back', async (_e, text) => {
  const s = String(text || '');
  if (!s) return false;
  writeClipboardTracked(s); // T01405：回填覆盖也入撤回栈
  if (win?.isVisible()) win.hide();
  if (process.platform !== 'win32') return false; // macOS/Linux 后续单
  setTimeout(() => {
    exec(
      "powershell -NoProfile -Command \"(New-Object -ComObject WScript.Shell).SendKeys('^v')\"",
      { windowsHide: true },
      (err) => {
        if (err) console.warn('[paste-back] 合成 Ctrl+V 失败：', err.message);
      },
    );
  }, 200); // 评审文档 §4：隐藏后留焦点切换窗口，实测定值
  return true;
});

// ---- LLM 生成（OpenAI 兼容）----

ipcMain.handle('llm:generate', async (_e, payload) => {
  generating = true; // T01409：生成中豁免失焦收纳
  try {
    return await doGenerate(payload);
  } finally {
    generating = false;
  }
});

async function doGenerate(payload) {
  const { message, style, me, contact, config, models, exclude, historyEnabled } = payload || {};
  const res = await generateCore(
    { kb, retriever, embedRetriever },
    { config, models, message, style, me, contact, exclude },
    (model, full) => {
      if (win && !win.isDestroyed()) {
        win.webContents.send('llm:chunk', model ? { model, text: full } : { text: full });
      }
    },
  );
  // T01404：显式开启本地历史时记录（加密落盘；降级模板不记）
  if (!res.degraded && historyEnabled) {
    try {
      recordHistory({ message: String(message || ''), candidates: res.candidates.map((c) => c.text) });
    } catch (e) {
      console.warn('[history] 记录失败（不阻断生成）：', e?.message);
    }
  }
  return res;
}

ipcMain.handle('llm:polish', async (_e, payload) => {
  const { config, text: original } = payload || {};
  const source = String(original || '').trim();
  if (!source) return { text: '', notice: '剪贴板为空，无可美化的原文' };
  if (!config?.baseUrl || !config?.apiKey || !config?.model) {
    return { text: '', notice: '未配置云端生成，美化不可用（设置里填 Key 后启用）' };
  }
  const system =
    '你是文字润色助手。把用户给出的原文润色得更通顺、得体、有情商，' +
    '保持原意与称呼不变，不编造新事实，输出润色后的正文，不要 markdown 格式符，不要解释。';
  try {
    const { text } = await callLlmStream(config, system, source, null);
    if (screenCandidate(text)) return { text: '', notice: '润色结果未通过内容安全检查' };
    const cleaned = sanitizeCandidates(text);
    const out = cleaned[0]?.text || cleanCandidate(text);
    return { text: out, notice: null };
  } catch (err) {
    return { text: '', notice: err.userMessage };
  }
});

// ---- T01404 本地加密历史（FR 隐私口径：默认不存，显式开启后才落盘，AES-256-GCM 端侧加密）----
// 密钥 = 机器指纹（与 kb.json.enc 同 deriveKey 口径）；文件不存在 = 从未开启，零残留。
const HISTORY_MAX = 50;
let historyPath = null;
let historyKey = null;

function loadHistory() {
  if (!historyPath) return [];
  if (!fs.existsSync(historyPath)) return []; // 从未开启过 → 零残留
  try {
    return JSON.parse(decrypt(historyKey, fs.readFileSync(historyPath, 'utf8'))).entries || [];
  } catch (e) {
    console.warn('[history] 历史解密失败，重置：', e?.message);
    return [];
  }
}

function persistHistory(entries) {
  if (!historyPath) return;
  fs.writeFileSync(historyPath, encrypt(historyKey, JSON.stringify({ entries })), 'utf8');
}

function recordHistory(entry) {
  if (!historyPath) return false; // 未开启（renderer 传 historyEnabled 才会调）
  const entries = loadHistory();
  entries.push({ id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), ts: Date.now(), ...entry });
  persistHistory(entries.slice(-HISTORY_MAX));
  return true;
}

ipcMain.handle('history:list', () => loadHistory().slice().reverse()); // 新在前

ipcMain.handle('history:clear', () => {
  if (historyPath && fs.existsSync(historyPath)) fs.rmSync(historyPath, { force: true });
  return true;
});

ipcMain.handle('history:record', (_e, entry) => recordHistory(entry));

// ---- T01403 端侧/本地模型（Ollama 离线档）：本机连通检查 ----
function resolveOllamaModels(data, statusCode, resolve) {
  try {
    const models = (JSON.parse(data).models || []).map((m) => m.name);
    resolve({ ok: statusCode === 200, models });
  } catch (e) {
    console.warn('[main] ollama 响应解析失败：', e?.message);
    resolve({ ok: false, error: 'Ollama 返回格式异常' });
  }
}

function checkOllamaTags() {
  return new Promise((resolve) => {
    const req = http.get('http://localhost:11434/api/tags', { timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', (c) => (data += c));
      res.on('end', () => resolveOllamaModels(data, res.statusCode, resolve));
    });
    req.on('timeout', () => {
      req.destroy();
      resolve({ ok: false, error: '连接超时（Ollama 未运行？）' });
    });
    req.on('error', (e) => resolve({ ok: false, error: `连接失败（${e.message}）` }));
  });
}
ipcMain.handle('ollama:check', () => checkOllamaTags());

// ---- T01399 自定义快捷指令（FR-PC 扩展）：名称+提示词模板+绑定模型 ----
// 模板占位符 {message} → 对方消息原文；未绑定模型用主配置；流式推流带指令名标签。
ipcMain.handle('llm:command', async (_e, payload) => {
  const { config, models, message, command } = payload || {};
  const promptTemplate = String(command?.prompt || '').trim();
  if (!promptTemplate) return { candidates: [], notice: '指令提示词为空', source: command?.name };
  const msg = String(message || '').trim();
  const user = promptTemplate.includes('{message}')
    ? promptTemplate.replaceAll('{message}', msg)
    : `${promptTemplate}\n\n对方消息：\n${msg}`;
  // 绑定模型：在多模型套件里按 name 匹配；未绑定/未命中 → 主配置
  const suite = (models || []).find((m) => m.name === command?.model);
  const cfg = suite
    ? { baseUrl: suite.baseUrl, apiKey: suite.apiKey, model: suite.model }
    : config;
  if (!cfg?.baseUrl || !cfg?.apiKey || !cfg?.model) {
    return { candidates: [], notice: '未配置云端生成，指令不可用', source: command?.name };
  }
  const system =
    `你是聊天回复助手，为用户按指定指令加工回复。指令：${promptTemplate}\n` +
    '约束：直接输出正文（可给出 1~2 条候选，每条按「内容|理由」格式），不要 markdown 格式符。';
  try {
    const { text } = await callLlmStream(cfg, system, user, (_d, full) => {
      if (win && !win.isDestroyed()) win.webContents.send('llm:chunk', { model: command?.name, text: full });
    });
    if (screenCandidate(text)) return { candidates: [], notice: '结果未通过内容安全检查', source: command?.name };
    const candidates = sanitizeCandidates(text).map((c) => ({ ...c, source: command?.name }));
    if (!candidates.length) return { candidates: [], notice: '结果清洗后为空', source: command?.name };
    return { candidates, notice: null, streamed: true, source: command?.name };
  } catch (err) {
    return { candidates: [], notice: err.userMessage, source: command?.name };
  }
});

// ---- T01401 要不要回判定（生成前轻量分类，压误判优先）----
// 两级：①本地启发式（明显无需回复才拦，零延迟）；②LLM 轻量分类（云端配置时）。
// 保守原则：LLM 失败/超时/输出不可解析 → 一律放行（宁误生成不误拦）。
ipcMain.handle('llm:shouldReply', async (_e, payload) => {
  const { config, message } = payload || {};
  const local = heuristicNoReply(message);
  if (local.noReply) return { needReply: false, reason: local.reason, via: 'local' };
  if (!config?.baseUrl || !config?.apiKey || !config?.model) {
    return { needReply: true, reason: null, via: 'local' }; // 无云端 → 只用启发式
  }
  const system =
    '判断对方这条消息是否需要用户回复。只需要回复、问候式寒暄、纯寒暄结尾不需要回复；' +
    '提出问题、请求、邀约、告知需要确认的事项需要回复。' +
    '只输出一行：需要回复输出 NEED_REPLY；不需要输出 NO_REPLY|原因（原因不超过 15 字）。';
  try {
    const { text } = await callLlmStream(config, system, String(message || ''), null);
    const m = /NO_REPLY\|(.+)/.exec(text.trim());
    if (m) return { needReply: false, reason: m[1].trim().slice(0, 30), via: 'llm' };
    return { needReply: true, reason: null, via: 'llm' }; // NEED_REPLY 或不可解析 → 放行
  } catch (err) {
    console.warn('[main] 回复判定 LLM 调用失败，默认放行：', err?.message);
    return { needReply: true, reason: null, via: 'error' }; // 判定失败宁放过
  }
});

// ---- T01408 个人语气学习：聊天记录 → 高频句式 → 注入 RAG 语料（relation 绑联系人）----
ipcMain.handle('tone:learn', (_e, { text, contact }) => {
  const corpus = extractToneCorpus(text);
  if (!corpus.length) {
    return { added: 0, notice: '可用语料不足（至少需要 3 条有效口语消息，4~60 字）' };
  }
  let added = 0;
  for (const c of corpus) {
    try {
      kb.add('relation', `语气参考（本人惯用）：${c.text}`, contact || null);
      added++;
    } catch (e) {
      console.warn('[tone] 语料入库失败：', e?.message);
    }
  }
  return { added, notice: null };
});

// ---- RAG 知识库 IPC（SRS 决策③）----

ipcMain.handle('kb:add', (_e, { category, text, contact }) => kb.add(category, text, contact));
ipcMain.handle('kb:remove', (_e, id) => kb.remove(id));
ipcMain.handle('kb:list', () => kb.list());
ipcMain.handle('kb:count', () => kb.count);
ipcMain.handle('kb:wipe', () => {
  kb.wipe();
  return true;
});

// ---- T01400 截图 OCR 取文：desktopCapturer 全屏 → tesseract.js 离线识别 → 行列表 ----
// 简化说明：M 版实现为全屏识别 + 渲染层文本编辑（框选覆盖层 UI 归后续单）。
const { desktopCapturer } = require('electron');

ipcMain.handle('ocr:screenshot', async () => {
  try {
    const sources = await desktopCapturer.getSources({
      types: ['screen'],
      thumbnailSize: { width: 1920, height: 1080 }, // 主屏 1080p 级识别分辨率
    });
    if (!sources.length) return { ok: false, notice: '未找到屏幕源' };
    const png = sources[0].thumbnail.toPNG();
    const res = await ocr.recognizeImage(png);
    if (!res.lines.length) return { ok: false, notice: '未识别到有效文本（可重截或改用复制粘贴）' };
    return { ok: true, lines: res.lines, text: res.text, confidence: res.confidence };
  } catch (e) {
    return { ok: false, notice: 'OCR 失败：' + (e.message || e) };
  }
});

// ---- T01407 外部 HTTP 调用接口（localhost，供脚本/第三方触发）----
// POST /generate {text, style?, me?} → {candidates:[{text,rationale}], degraded, notice}
// 复用 generateCore 全管线（检索/清洗/护栏/降级）；仅绑定 127.0.0.1，不监听外网。
const HTTP_PORT = 17777;

function startHttpApi() {
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    if (req.method === 'GET' && req.url === '/health') {
      res.end(JSON.stringify({ ok: true, version: '20260930-004' }));
      return;
    }
    if (req.method !== 'POST' || req.url !== '/generate') {
      res.statusCode = 404;
      res.end(JSON.stringify({ error: 'not found（POST /generate）' }));
      return;
    }
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', async () => {
      let body = {};
      try {
        body = JSON.parse(raw || '{}');
      } catch (e) {
        console.warn('[main] /generate 请求体 JSON 解析失败：', e?.message);
        res.statusCode = 400;
        res.end(JSON.stringify({ error: '请求体不是合法 JSON' }));
        return;
      }
      const text = String(body.text || '').trim();
      if (!text) {
        res.statusCode = 400;
        res.end(JSON.stringify({ error: '缺少 text 字段' }));
        return;
      }
      const cfg = loadCfgSnapshot();
      try {
        const result = await generateCore(
          { kb, retriever, embedRetriever },
          {
            config: cfg.config,
            models: cfg.models,
            message: text,
            style: body.style,
            me: body.me,
            contact: body.contact || null,
          },
        );
        res.end(JSON.stringify(result));
      } catch (e) {
        res.statusCode = 500;
        res.end(JSON.stringify({ error: e.message }));
      }
    });
  });
  server.on('error', (e) => console.warn('[http-api] 启动失败（端口占用？）：', e.message));
  server.listen(HTTP_PORT, '127.0.0.1', () => {
    console.log(`[http-api] listening on http://127.0.0.1:${HTTP_PORT} (POST /generate)`);
  });
}

// ---- T01409 悬浮窗锚点吸附 + 位置记忆 + 失焦收纳（FR-PC P2 体验）----
// 吸附：move 结束（debounce 250ms）后距工作区边缘 <24px → 吸附贴边（双轴=四角）。
// 位置记忆：吸附后持久化 userData/pos.json，启动恢复（clamp 在可视区内防显示器变更飞出）。
// 失焦收纳：cfg.autoHide='1'（设置开关，默认关——避免配置过程被打断）时 blur→hide；
//   生成中不收纳。⚠ 简化：PRD 的「按宿主应用记忆位置」需枚举前台应用（Electron 无此 API），
//   本版为全局位置记忆，宿主级记忆待后续 UIA/原生方案。
const { screen } = require('electron');
let posSaveTimer = null;
let posPath = null;
let generating = false; // 生成进行中标志（llm:generate 开始/结束维护，收纳豁免）

function loadSavedPos() {
  try {
    if (posPath && fs.existsSync(posPath)) return JSON.parse(fs.readFileSync(posPath, 'utf8'));
  } catch (e) {
    console.warn('[main] 位置文件读取失败，忽略：', e?.message);
  }
  return null;
}

function saveWindowPos() {
  if (!win || win.isDestroyed() || !posPath) return;
  try {
    const [x, y] = win.getPosition();
    fs.writeFileSync(posPath, JSON.stringify({ x, y }), 'utf8');
  } catch (e) {
    console.warn('[main] 位置文件写入失败（不致命）：', e?.message);
  }
}

function clampIntoWorkArea(x, y, w, h) {
  const disp = screen.getDisplayNearestPoint({ x: x || 0, y: y || 0 }).workArea;
  return {
    x: Math.min(Math.max(x, disp.x), disp.x + disp.width - w),
    y: Math.min(Math.max(y, disp.y), disp.y + disp.height - h),
  };
}

function snapToEdge() {
  if (!win || win.isDestroyed()) return;
  const [x, y] = win.getPosition();
  const [w, h] = win.getSize();
  const disp = screen.getDisplayNearestPoint({ x, y }).workArea;
  const T = 24; // 吸附阈值 px
  let nx = x, ny = y;
  if (Math.abs(x - disp.x) < T) nx = disp.x;
  else if (Math.abs(disp.x + disp.width - (x + w)) < T) nx = disp.x + disp.width - w;
  if (Math.abs(y - disp.y) < T) ny = disp.y;
  else if (Math.abs(disp.y + disp.height - (y + h)) < T) ny = disp.y + disp.height - h;
  if (nx !== x || ny !== y) {
    win.setPosition(nx, ny);
    saveWindowPos();
  }
}

function installWindowAnchoring() {
  if (!posPath) posPath = path.join(app.getPath('userData'), 'pos.json');
  // 启动恢复（clamp 防显示器变更后飞出屏外）
  const saved = loadSavedPos();
  if (saved) {
    const [w, h] = win.getSize();
    const c = clampIntoWorkArea(saved.x, saved.y, w, h);
    win.setPosition(c.x, c.y);
  }
  win.on('move', () => {
    clearTimeout(posSaveTimer);
    posSaveTimer = setTimeout(() => {
      snapToEdge();
      saveWindowPos();
    }, 250);
  });
  // 失焦收纳（开关默认关；生成中豁免）
  win.on('blur', () => {
    if (cfgSnapshot.config?.autoHide === '1' && !generating && win.isVisible()) win.hide();
  });
}
