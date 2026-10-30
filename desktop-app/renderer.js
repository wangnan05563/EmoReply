// 渲染进程：配置（localStorage，密钥不出本机）+ 交互流（读剪贴板 → 生成 → 点选复制）
//
// T01541：设置持久化改造——localStorage 降级为"只读镜像 + 写穿缓存"，
// 唯一事实源是主进程 userData/settings.json（原子写/版本兼容/密钥 safeStorage 加密）。
// 本块必须位于一切 localStorage 读取之前（顶层同步水合）：
//   ① sendSync 拉全量 → 覆写镜像；② 文件没有但镜像有的键（老用户首启）→ 回写迁移；
//   ③ 拦载 SYNC_KEYS 的 setItem → 写穿主进程；④（T03639）removeItem 同样写穿——
//      只拦 set 会让删除停留在镜像里，事实源仍是旧值，下次水合把已删的键灌回来。
(() => {
  const SYNC_KEYS = new Set([
    'emoreply_config', 'emoreply_models', 'emoreply_commands', 'emoreply_commands_seeded',
    'emoreply_history', 'emoreply_wizard_done', 'emoreply_ui_scale', 'emoreply_theme', 'emoreply_accent',
  ]);
  const er = globalThis.emoReply;
  if (!er?.settingsSync || !er?.settings) return;
  const _set = Storage.prototype.setItem;
  const _remove = Storage.prototype.removeItem;
  let snap = {};
  try {
    snap = er.settingsSync.get() || {};
  } catch (e) {
    // 水合失败维持旧行为（继续用镜像里的值），但留痕：桥异常时这层静默降级最容易被误判成「设置丢失」
    console.warn('[renderer] 设置水合失败，沿用本地镜像：', e?.message || e);
  }
  const all = snap.values || {};
  for (const [k, v] of Object.entries(all)) _set.call(localStorage, k, v);
  for (const k of SYNC_KEYS) {
    if (k in all) continue;
    const legacy = localStorage.getItem(k);
    if (legacy === null) continue;
    // 磁盘文件已存在 = 它是事实源，镜像里的多余值是「删了但 leveldb 快照未落」的幽灵，剪掉；
    // 只有文件缺失/损坏（首启或自愈）才把镜像当老数据迁进去。不剪这一刀，删除会在下次启动复活。
    if (snap.existed) _remove.call(localStorage, k);
    else er.settings.set(k, legacy);
  }
  Storage.prototype.setItem = function (k, v) {
    _set.call(this, k, v);
    if (SYNC_KEYS.has(k)) er.settings.set(k, String(v));
  };
  Storage.prototype.removeItem = function (k) {
    _remove.call(this, k);
    if (SYNC_KEYS.has(k)) er.settings.remove(k);
  };
})();
const $ = (id) => document.getElementById(id);
const CFG_KEY = 'emoreply_config';
const MODELS_KEY = 'emoreply_models'; // T01398：多模型套件 [{name,baseUrl,model,apiKey}]
// T01399 快捷指令（常量须在 fillCommandsTextarea 首个调用点之前声明——曾因 TDZ 在顶层初始化中断）
const CMDS_KEY = 'emoreply_commands';
const CMDS_SEEDED = 'emoreply_commands_seeded';

const BUILTIN_COMMANDS = [
  { name: '委婉拒绝', prompt: '针对对方消息写一个委婉但立场清晰的拒绝回复，先共情再说明难处，最后给出替代建议。{message}', order: 1 },
  { name: '高情商道歉', prompt: '针对对方消息写一条真诚不卑微的道歉，承认具体问题并给出改进动作，避免过度自责。{message}', order: 2 },
  { name: '温和催促', prompt: '针对对方消息写一条温和的催促回复，给对方台阶下，同时明确时间点。{message}', order: 3 },
  { name: '安慰共情', prompt: '针对对方消息写一条安慰回复，先共情情绪再给一句支持，不说教不灌鸡汤。{message}', order: 4 },
  { name: '得体感谢', prompt: '针对对方消息写一条得体的感谢回复，具体指出感谢的点，不客套空洞。{message}', order: 5 },
];

function loadModels() {
  try {
    const arr = JSON.parse(localStorage.getItem(MODELS_KEY) || '[]');
    return Array.isArray(arr) ? arr.filter((m) => m?.baseUrl && m?.apiKey && m?.model) : [];
  } catch (e) {
    console.warn('[renderer] 模型套件解析失败，使用空列表：', e?.message);
    return [];
  }
}

function saveModels(list) {
  localStorage.setItem(MODELS_KEY, JSON.stringify(list));
}

let currentMessage = '';

function loadCfg() {
  try {
    return JSON.parse(localStorage.getItem(CFG_KEY) || '{}');
  } catch (e) {
    console.warn('[cfg] 配置解析失败，使用默认：', e?.message);
    return {};
  }
}

function fillCfg() {
  const cfg = loadCfg();
  $('cfgBaseUrl').value = cfg.baseUrl || 'https://api.deepseek.com/v1';
  $('cfgModel').value = cfg.model || 'deepseek-chat';
  $('cfgApiKey').value = '';
  $('cfgApiKey').placeholder = cfg.apiKey ? 'API Key（已设置，留空沿用）' : 'API Key（仅保存在本机）';
  $('cfgMe').value = cfg.me || '';
  $('cfgEmbedModel').value = cfg.embeddingModel || '';
  $('cfgHistory').checked = localStorage.getItem('emoreply_history') === '1';
  $('cfgAutoHide').checked = cfg.autoHide === '1'; // T01409
}

$('btnSaveCfg').onclick = () => {
  const cfg = loadCfg();
  cfg.baseUrl = $('cfgBaseUrl').value.trim() || 'https://api.deepseek.com/v1';
  cfg.model = $('cfgModel').value.trim() || 'deepseek-chat';
  const typed = $('cfgApiKey').value.trim();
  if (typed) cfg.apiKey = typed; // 留空沿用
  cfg.me = $('cfgMe').value.trim() || '我';
  cfg.embeddingModel = $('cfgEmbedModel').value.trim() || ''; // T01402：向量档开关（空=词面检索）
  localStorage.setItem('emoreply_history', $('cfgHistory').checked ? '1' : '0'); // T01404
  cfg.autoHide = $('cfgAutoHide').checked ? '1' : '0'; // T01409
  localStorage.setItem(CFG_KEY, JSON.stringify(cfg));
  saveModels(parseModelsTextarea()); // T01398：多模型套件随主配置一并保存
  saveCommands(parseCommandsTextarea()); // T01399：快捷指令一并保存
  syncCfgSnapshot(); // T01407：推送配置快照给主进程（HTTP 层用）
  fillCfg();
  renderCommandBar();
  $('cfgStatus').textContent = '已保存。密钥经系统加密存储于本机（userData/settings.json）。';
};

// T01398：多模型套件（textarea 每行 名称|接口地址|模型名|Key）
function parseModelsTextarea() {
  const lines = $('cfgModels').value.split('\n').map((l) => l.trim()).filter(Boolean);
  const out = [];
  for (const line of lines) {
    const [name, baseUrl, model, apiKey] = line.split('|').map((p) => (p || '').trim());
    if (!baseUrl || !model || !apiKey) continue; // 缺字段整行忽略（保存前预检）
    out.push({ name: name || model, baseUrl, model, apiKey });
  }
  return out;
}

function syncCfgSnapshot() {
  const cfg = loadCfg();
  globalThis.emoReply.syncCfg({ config: cfg, models: loadModels() }).catch(() => {});
}

function fillModelsTextarea() {
  const list = loadModels();
  $('cfgModels').value = list
    .map((m) => [m.name, m.baseUrl, m.model, m.apiKey].join('|'))
    .join('\n');
  $('cfgModelsStatus').textContent = list.length >= 2
    ? `已配 ${list.length} 套：生成时并行对比`
    : '≥2 套时启用并行对比（1 套时按上方主配置单发）';
}

$('btnCheck').onclick = () => {
  const cfg = loadCfg();
  $('cfgStatus').textContent = cfg.apiKey
    ? `已配置：${cfg.baseUrl} / ${cfg.model}`
    : '未配置 Key：当前使用本地模板兜底';
};

$('btnRead').onclick = async () => {
  const text = await globalThis.emoReply.readClipboard();
  currentMessage = text || '';
  excludeTexts = []; // T01395：新消息 = 新一轮，已给候选清空
  const el = $('msg');
  if (currentMessage) {
    el.textContent = currentMessage;
    el.classList.remove('muted');
  } else {
    el.textContent = '剪贴板为空';
    el.classList.add('muted');
  }
};

$('btnGen').onclick = async () => {
  await runGenerate([]);
};

// T01395：生成流程抽出（普通生成 exclude=[]；「换一条」带已给候选重跑）
async function runGenerate(exclude) {
  if (cmdRunning) return; // T01399：与快捷指令互斥，防流式卡交叉
  // 空态守卫：剪贴板为空时不生成（否则会凭空产出降级模板候选，用户看到无来源的回复）
  if (!currentMessage) {
    $('notice').textContent = '请先「读剪贴板」获取对方消息，再生成回复';
    $('notice').classList.remove('ok');
    $('candidates').innerHTML = ''; // 与正常生成口径一致：新一轮清空旧候选，避免残留误导
    return;
  }
  const btn = $('btnGen');
  btn.disabled = true;
  btn.classList.add('busy'); // T01531：图标按钮忙碌态=转圈（不再改文案）
  cmdRunning = true;
  $('notice').textContent = '';
  $('candidates').innerHTML = '';
  // T01393/T01398：流式渲染——单模型单卡；多模型按 model 分组多卡并排对比
  let streamEls = new Map(); // model -> {wrap, el}
  // T01410：骨架行（流式开始前的等待反馈；首个 chunk 到达即移除）
  const skeletons = [];
  for (let i = 0; i < 2; i++) {
    const sk = document.createElement('div');
    sk.className = 'skeleton';
    $('candidates').appendChild(sk);
    skeletons.push(sk);
  }
  const clearSkeletons = () => skeletons.forEach((s) => s.remove());
  const offChunk = globalThis.emoReply.onLlmChunk(({ text, model }) => {
    clearSkeletons();
    if (!text) return;
    let entry = streamEls.get(model || '__single__');
    if (!entry) {
      const wrap = document.createElement('div');
      if (model) {
        const label = document.createElement('div');
        label.className = 'stream-model';
        label.textContent = model;
        wrap.appendChild(label);
      }
      const el = document.createElement('div');
      el.className = 'candidate streaming';
      wrap.appendChild(el);
      $('candidates').appendChild(wrap);
      entry = { wrap, el };
      streamEls.set(model || '__single__', entry);
    }
    entry.el.textContent = text;
  });
  try {
    const cfg = loadCfg();
    // T01401：生成前要不要回判定（无需回复 → 明示原因不生成）。
    // 压误判=只有明显无需才拦；被拦后用户再点一次即旁路强制生成（judgeBypass 一次性生效）
    if (!judgeBypass) {
      const judge = await globalThis.emoReply.shouldReply({ config: cfg, message: currentMessage });
      if (!judge.needReply) {
        judgeBypass = true; // 下一次点击旁路判定
        $('notice').textContent = `未生成——${judge.reason || '判定无需回复'}。确认要回？再点一次「生成回复」`;
        $('notice').classList.remove('ok');
        return;
      }
    }
    judgeBypass = false;
    // T01398：多模型套件 ≥2 → 并行对比；否则只传主配置（旧行为）
    const extras = loadModels();
    const mainModel = cfg.apiKey ? [{ name: cfg.model, baseUrl: cfg.baseUrl, model: cfg.model, apiKey: cfg.apiKey }] : [];
    const models = [...mainModel, ...extras].filter(
      (m, i, arr) => arr.findIndex((x) => x.baseUrl === m.baseUrl && x.model === m.model) === i, // 地址+模型去重
    );
    const res = await globalThis.emoReply.generate({
      message: currentMessage,
      style: $('style').value,
      me: cfg.me || '我',
      contact: $('curContact').value.trim() || null, // 关系话术加权用
      config: cfg,
      exclude, // T01395：换一批（已给候选 LCS 去重）
      historyEnabled: localStorage.getItem('emoreply_history') === '1', // T01404
      models: models.length >= 2 ? models : undefined,
    });
    for (const [, entry] of streamEls) entry.wrap.remove(); // 流式区整体替换为清洗后候选
    streamEls = null;
    renderNotice(res);
    // T01392：清洗管线移至主进程（sanitize.js），此处候选已是 {text, rationale, source?} 干净对
    res.candidates.forEach(({ text, rationale, source }, idx) => {
      if (!text) return;
      const card = renderCandidateCard(text, rationale, source);
      card.classList.add('pop');
      card.style.animationDelay = Math.min(idx * 60, 300) + 'ms'; // T01410：逐条弹入
      card.dataset.idx = String(idx + 1); // T01412：数字键直选
      card.setAttribute('aria-label', '候选' + (idx + 1) + '：' + text);
      $('candidates').appendChild(card);
    });
  } finally {
    offChunk(); // 生成结束/异常都要摘掉监听，防重复挂载
    if (streamEls) for (const [, entry] of streamEls) entry.wrap.remove();
    btn.disabled = false;
    btn.classList.remove('busy');
    cmdRunning = false;
  }
}

// T01395：候选卡三动作（FR-PC4-1）——复制 | 粘贴回宿主 | 换一条（重生成）
function renderCandidateCard(text, rationale, source) {
  const div = document.createElement('div');
  div.className = 'candidate';
  div.tabIndex = 0; // T01412：纯键盘可达（Tab/方向键聚焦，Enter 复制）
  div.setAttribute('role', 'button');
  div.title = rationale ? `为什么这样回：${rationale}` : '';
  const head = document.createElement('div');
  head.className = 'cand-head';
  if (source) {
    const badge = document.createElement('span');
    badge.className = 'src';
    badge.textContent = source;
    head.appendChild(badge);
  }
  // T01411：rationale 升级为可点击语气标签——点击即按该语气重生成
  if (rationale) {
    const tone = document.createElement('span');
    tone.className = 'tone-tag';
    tone.textContent = rationale;
    tone.title = '点击按此语气重生成';
    tone.onclick = (ev) => {
      ev.stopPropagation();
      regenerateWithTone(rationale);
    };
    head.appendChild(tone);
  }
  div.appendChild(head);
  const body = document.createElement('div');
  body.className = 'cand-text';
  body.textContent = text;
  div.appendChild(body);
  const actions = document.createElement('div');
  actions.className = 'cand-actions';
  const mkBtn = (iconId, tip, fn) => {
    const b = document.createElement('button');
    b.className = 'act ibtn sm';
    setIcon(b, iconId);
    b.dataset.tip = tip;
    b.onclick = (ev) => {
      ev.stopPropagation();
      fn();
    };
    return b;
  };
  actions.appendChild(mkBtn('i-copy', '复制｜复制候选文本到剪贴板（自行粘贴发送）', async () => {
    await globalThis.emoReply.writeClipboard(text);
    body.textContent = '✓ 已复制到剪贴板：' + text;
    setTimeout(() => (body.textContent = text), 1200);
  }));
  actions.appendChild(mkBtn('i-paste-in', '回填｜隐藏本窗并粘贴到原窗口（自行发送）', async () => {
    await globalThis.emoReply.pasteBack(text);
  }));
  actions.appendChild(mkBtn('i-refresh', '换一条｜重新生成，避开已给候选', async () => {
    excludeTexts.push(text);
    await runGenerate(excludeTexts.slice());
  }));
  div.appendChild(actions);
  return div;
}
let excludeTexts = []; // 本次消息的「已给候选」；重新读剪贴板时重置
let judgeBypass = false; // T01401：判定拦截后的一次性旁路（再点一次强制生成）

// T01395：美化回填——剪贴板原文润色后写回剪贴板
$('btnBeautify').onclick = async () => {
  const btn = $('btnBeautify');
  btn.disabled = true;
  const original = await globalThis.emoReply.readClipboard();
  $('notice').textContent = '美化中…';
  try {
    const cfg = loadCfg();
    const res = await globalThis.emoReply.polish({ config: cfg, text: original });
    if (res.text) {
      await globalThis.emoReply.writeClipboard(res.text);
      $('notice').textContent = '已美化并写回剪贴板：' + res.text;
      $('notice').classList.add('ok');
    } else {
      $('notice').textContent = res.notice || '美化未产出结果';
      $('notice').classList.remove('ok');
    }
  } finally {
    btn.disabled = false;
  }
};

function renderNotice(res) {
  const el = $('notice');
  el.innerHTML = '';
  if (res.notice) {
    el.classList.remove('ok');
    el.appendChild(document.createTextNode(res.notice));
    // T01410：失败/降级态给可操作建议——直连设置区
    if (res.degraded) {
      const fix = document.createElement('span');
      fix.className = 'fix-link';
      fix.textContent = ' → 打开设置检查';
      fix.onclick = () => openSettingsPage('settingsBox'); // T01532：设置已收进设置中心，链接改为开覆盖层并展开设置区
      el.appendChild(fix);
    }
  } else {
    el.textContent = '云端生成完成';
    el.classList.add('ok');
  }
}

// ---- RAG 知识库管理（与 Android MainActivity 同构）----
const CAT_LABELS = { persona: '个人事实', relation: '关系话术', platform: '平台礼仪' };

$('kbCategory').onchange = () => {
  const isRelation = $('kbCategory').value === 'relation';
  $('kbContact').style.display = isRelation ? '' : 'none';
  if (!isRelation) $('kbContact').value = '';
};

async function loadKb() {
  const entries = await globalThis.emoReply.kbList();
  $('kbCount').textContent = `共 ${entries.length} 条`;
  const list = $('kbList');
  list.innerHTML = '';
  entries.forEach((e) => {
    const div = document.createElement('div');
    div.className = 'kb-item';
    const span = document.createElement('span');
    const tag = document.createElement('span');
    tag.className = 'cat';
    tag.textContent = `[${CAT_LABELS[e.category] || e.category}] `;
    span.appendChild(tag);
    span.appendChild(document.createTextNode(e.text));
    div.appendChild(span);
    const del = document.createElement('button');
    del.className = 'del ibtn sm';
    setIcon(del, 'i-trash');
    del.dataset.tip = '删除条目｜从知识库移除这条知识（不可恢复）';
    del.onclick = async () => {
      await globalThis.emoReply.kbRemove(e.id);
      refreshKb();
    };
    div.appendChild(del);
    list.appendChild(div);
  });
}

// 经典脚本没有顶层 await：入口设为同步函数，内部挂失败日志，避免未处理拒绝静默消失。
function refreshKb() {
  loadKb().catch(kbRefreshFailed);
}

$('btnKbSave').onclick = async () => {
  try {
    await globalThis.emoReply.kbAdd(
      $('kbCategory').value,
      $('kbText').value,
      $('kbContact').value.trim() || null,
    );
    $('kbText').value = '';
    $('kbStatus').textContent = '';
    $('kbStatus').classList.add('ok');
    refreshKb();
  } catch (e) {
    $('kbStatus').textContent = e.message || '保存失败';
    $('kbStatus').classList.remove('ok');
  }
};

$('btnKbWipe').onclick = async () => {
  if (!confirm('确定清空全部知识条目？（不可恢复）')) return;
  await globalThis.emoReply.kbWipe();
  refreshKb();
};

// 顶层调用一律挂上失败日志：本文件是经典脚本（非 module），没有顶层 await，
// 未处理的 Promise 拒绝会静默消失，查问题时无从下手。
function kbRefreshFailed(e) {
  console.warn('[renderer] 知识库刷新失败：', e?.message || e);
}

fillCfg();
fillModelsTextarea(); // T01398：多模型套件回填
syncCfgSnapshot(); // T01407：启动即同步快照
fillCommandsTextarea(); // T01399：快捷指令回填
$('btnCheck').click();
refreshKb();

// T01394：复制后热键直达生成——主进程判定「剪贴板有新内容」后推文本过来，
// 渲染层填入消息区并自动触发生成（等价于人工点「读剪贴板」+「生成回复」两步）
globalThis.emoReply.onHotkeyGenerate((text) => {
  currentMessage = text || '';
  excludeTexts = []; // 新消息 = 新一轮
  const el = $('msg');
  if (currentMessage) {
    el.textContent = currentMessage;
    el.classList.remove('muted');
  }
  const btn = $('btnGen');
  if (!btn.disabled) btn.click(); // 生成中防重入
});

// ---- T01396：首次运行向导（Key → 模型 → 试一句，完成后不再弹出）----
const WZ_DONE = 'emoreply_wizard_done';

function maybeShowWizard() {
  const cfg = loadCfg();
  if (localStorage.getItem(WZ_DONE) === '1' || cfg.apiKey) return; // 配置过即不弹
  $('wizard').style.display = 'block';
}

$('wzPreset').onchange = () => {
  const [model, baseUrl] = $('wzPreset').value.split('|');
  const custom = !model || model === 'custom';
  $('wzModel').style.display = custom ? '' : 'none';
  $('wzBaseUrl').style.display = custom ? '' : 'none';
  if (!custom) {
    $('wzModel').value = model;
    $('wzBaseUrl').value = baseUrl;
  }
};

$('wzSkip').onclick = () => {
  localStorage.setItem(WZ_DONE, '1');
  $('wizard').style.display = 'none';
};

$('wzTest').onclick = async () => {
  const key = $('wzKey').value.trim();
  let model = $('wzModel').value.trim();
  let baseUrl = $('wzBaseUrl').value.trim();
  if (!$('wzModel').value.trim()) {
    [model, baseUrl] = $('wzPreset').value.split('|');
  }
  const status = $('wzStatus');
  if (!key) {
    status.textContent = '请先粘贴 API Key';
    status.className = 'status bad';
    return;
  }
  if (!baseUrl) {
    status.textContent = '自定义模型需填接口地址';
    status.className = 'status bad';
    return;
  }
  // 落盘配置（与设置页同口径：localStorage，密钥不出本机），并同步主设置区
  const cfg = { ...loadCfg(), apiKey: key, model, baseUrl };
  localStorage.setItem(CFG_KEY, JSON.stringify(cfg));
  fillCfg();
  status.textContent = '连接测试中…';
  status.className = 'status';
  const res = await globalThis.emoReply.generate({
    message: '你好，请回复一句简短问候',
    style: '简短',
    me: cfg.me || '我',
    contact: null,
    config: cfg,
  });
  if (!res.degraded && res.candidates.length) {
    status.textContent = '✓ 连接成功，示例回复：' + res.candidates[0].text;
    status.className = 'status ok';
    localStorage.setItem(WZ_DONE, '1');
    setTimeout(() => ($('wizard').style.display = 'none'), 1500);
  } else {
    status.textContent = res.notice || '连接失败，请检查 Key 与接口地址';
    status.className = 'status bad';
  }
};

maybeShowWizard();

// ---- T01399：自定义快捷指令（名称+提示词模板+排序+绑定模型；内置高情商场景包）----
function loadCommands() {
  if (localStorage.getItem(CMDS_SEEDED) !== '1') {
    // 内置场景包首次播种（用户可自行修改覆盖）
    if (!localStorage.getItem(CMDS_KEY)) {
      localStorage.setItem(CMDS_KEY, JSON.stringify(BUILTIN_COMMANDS));
    }
    localStorage.setItem(CMDS_SEEDED, '1');
  }
  try {
    const arr = JSON.parse(localStorage.getItem(CMDS_KEY) || '[]');
    return (Array.isArray(arr) ? arr : [])
      .filter((c) => c?.name && c?.prompt)
      .sort((a, b) => (a.order ?? 99) - (b.order ?? 99));
  } catch (e) {
    console.warn('[renderer] 快捷指令解析失败，使用空列表：', e?.message);
    return [];
  }
}

function saveCommands(list) {
  localStorage.setItem(CMDS_KEY, JSON.stringify(list));
}

function parseCommandsTextarea() {
  const lines = $('cfgCommands').value.split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.map((line, i) => {
    const [name, prompt, model] = line.split('|').map((p) => (p || '').trim());
    return { name, prompt, model: model || null, order: i + 1 };
  }).filter((c) => c.name && c.prompt);
}

function fillCommandsTextarea() {
  $('cfgCommands').value = loadCommands()
    .map((c) => [c.name, c.prompt, c.model || ''].join('|'))
    .join('\n');
}

// 指令栏：主界面按钮（点击即以当前消息运行指令）
let cmdRunning = false;
async function runCommand(cmd) {
  if (cmdRunning) return; // 指令/生成互斥，防流式卡交叉
  if (!currentMessage) {
    $('notice').textContent = '请先「读剪贴板」获取对方消息，再运行指令';
    $('notice').classList.remove('ok');
    return;
  }
  cmdRunning = true;
  $('notice').textContent = '';
  $('candidates').innerHTML = '';
  let streamEl = null;
  const skeletons = [];
  for (let i = 0; i < 2; i++) {
    const sk = document.createElement('div');
    sk.className = 'skeleton';
    $('candidates').appendChild(sk);
    skeletons.push(sk);
  }
  const offChunk = globalThis.emoReply.onLlmChunk(({ text, model }) => {
    if (text) skeletons.forEach((s) => s.remove());
    if (model !== cmd.name || !text) return; // 只渲染本指令的流
    if (!streamEl) {
      streamEl = document.createElement('div');
      streamEl.className = 'candidate streaming';
      $('candidates').appendChild(streamEl);
    }
    streamEl.textContent = text;
  });
  try {
    const cfg = loadCfg();
    const res = await globalThis.emoReply.runCommand({
      config: cfg,
      models: loadModels(),
      message: currentMessage,
      command: cmd,
    });
    if (streamEl) streamEl.remove();
    streamEl = null;
    $('notice').textContent = res.notice || `「${cmd.name}」完成`;
    $('notice').classList.toggle('ok', !res.notice);
    res.candidates.forEach(({ text, rationale }) => {
      if (!text) return;
      $('candidates').appendChild(renderCandidateCard(text, rationale, cmd.name));
    });
  } finally {
    offChunk();
    if (streamEl) streamEl.remove();
    skeletons.forEach((s) => s.remove());
    cmdRunning = false;
  }
}

function renderCommandBar() {
  const bar = $('cmdBar');
  bar.innerHTML = '';
  const cmds = loadCommands();
  if (!cmds.length) return;
  cmds.forEach((cmd) => {
    const b = document.createElement('button');
    b.className = 'cmd';
    b.textContent = cmd.name; // T01531 口径：指令名即数据，保留文字，统一接入倾斜+提示
    b.dataset.tip = cmd.name + '｜快捷指令：' + cmd.prompt.slice(0, 60) + (cmd.prompt.length > 60 ? '…' : '');
    b.onclick = () => runCommand(cmd);
    bar.appendChild(b);
  });
}
renderCommandBar();

// ---- T01405：撤回——恢复被覆盖的剪贴板内容（多层栈）----
$('btnUndo').onclick = async () => {
  const r = await globalThis.emoReply.undoClipboard();
  if (r.restored) {
    $('notice').textContent = '已撤回，剪贴板恢复为：' + (r.text || '（空）');
    $('notice').classList.remove('ok');
  } else {
    $('notice').textContent = '没有可撤回的覆盖记录';
    $('notice').classList.remove('ok');
  }
};

// ---- T01403：Ollama 离线档（一键预设 + 连通检查）----
const OLLAMA_LINE = 'Ollama|http://localhost:11434/v1|llama3.2|ollama';

$('btnAddOllama').onclick = () => {
  const cur = $('cfgModels').value;
  if (cur.split('\n').some((l) => l.includes('localhost:11434'))) {
    $('cfgModelsStatus').textContent = 'Ollama 套件已存在';
    return;
  }
  $('cfgModels').value = (cur.trim() ? cur.trimEnd() + '\n' : '') + OLLAMA_LINE;
  $('cfgModelsStatus').textContent = '已添加 Ollama（记得点「保存设置」）；模型名可按本机已装模型修改';
};

$('btnCheckOllama').onclick = async () => {
  $('cfgModelsStatus').textContent = '检查 Ollama（localhost:11434）…';
  const r = await globalThis.emoReply.checkOllama();
  if (r.ok) {
    const names = r.models.join('、') || '（未安装任何模型）';
    $('cfgModelsStatus').textContent = `✓ Ollama 在线，已装模型：${names}`;
  } else {
    $('cfgModelsStatus').textContent = '✗ ' + (r.error || 'Ollama 不可达') + '（安装：ollama.com，装完 ollama pull llama3.2）';
  }
};

// ---- T01404：本地历史列表（加密读取；复制/回填即用）----
function historyCopyButton(text, index) {
  const b = document.createElement('button');
  b.className = 'del ibtn sm';
  b.style.background = '#e0e7ff';
  b.style.color = '#3730a3';
  setIcon(b, 'i-copy');
  b.dataset.tip = '复制候选' + (index + 1) + '｜' + String(text).slice(0, 40);
  b.onclick = async () => {
    await globalThis.emoReply.writeClipboard(text);
    setIcon(b, 'i-check'); // 成功反馈：图标短暂换勾
    setTimeout(() => setIcon(b, 'i-copy'), 1000);
  };
  return b;
}

function historyPasteButton(candidates) {
  const pb = document.createElement('button');
  pb.className = 'del ibtn sm';
  pb.style.background = '#dcfce7';
  pb.style.color = '#166534';
  setIcon(pb, 'i-paste-in');
  pb.dataset.tip = '回填候选1｜隐藏本窗并粘贴到原窗口（自行发送）';
  pb.onclick = async () => {
    if (candidates[0]) await globalThis.emoReply.pasteBack(candidates[0]);
  };
  return pb;
}

function historyItem(entry) {
  const div = document.createElement('div');
  div.className = 'kb-item';
  const time = new Date(entry.ts).toLocaleString('zh-CN', { hour12: false });
  const span = document.createElement('span');
  span.textContent = time + ' · ' + (entry.message || '').slice(0, 20) + ' · ' + entry.candidates.length + '条候选';
  span.title = entry.message || '';
  div.appendChild(span);
  const actions = document.createElement('span');
  entry.candidates.forEach((c, i) => actions.appendChild(historyCopyButton(c, i)));
  actions.appendChild(historyPasteButton(entry.candidates));
  div.appendChild(actions);
  return div;
}

async function loadHistory() {
  if (localStorage.getItem('emoreply_history') !== '1') {
    $('historyList').textContent = '未开启（在设置里勾选「启用本地历史」后开始记录）';
    return;
  }
  const entries = await globalThis.emoReply.historyList();
  const list = $('historyList');
  list.innerHTML = '';
  if (!entries.length) {
    list.textContent = '暂无记录（开启后新生成的回复会记录在此）';
    return;
  }
  entries.forEach((h) => list.appendChild(historyItem(h)));
}

function refreshHistory() {
  loadHistory().catch(historyRefreshFailed);
}
$('btnHistoryRefresh').onclick = refreshHistory;
$('btnHistoryClear').onclick = async () => {
  if (!confirm('确定清空全部本地历史？（加密文件将删除，不可恢复）')) return;
  await globalThis.emoReply.historyClear();
  refreshHistory();
};

function historyRefreshFailed(e) {
  console.warn('[renderer] 历史列表刷新失败：', e?.message || e);
}

refreshHistory();

// ---- T01408：语气学习 UI ----
$('btnToneLearn').onclick = async () => {
  const text = $('toneText').value.trim();
  const status = $('toneStatus');
  if (!text) {
    status.textContent = '请先粘贴聊天记录';
    status.classList.remove('ok');
    return;
  }
  const res = await globalThis.emoReply.toneLearn({ text, contact: $('toneContact').value.trim() || null });
  if (res.added > 0) {
    status.textContent = `已提取并注入 ${res.added} 条语气语料（可在知识库中查看/删除）`;
    status.classList.add('ok');
    $('toneText').value = '';
    refreshKb();
  } else {
    status.textContent = res.notice || '未能提取到语料';
    status.classList.remove('ok');
  }
};

// T01411：按语气标签重生成（复用 llm:command 管线：护栏+清洗+流式）
function regenerateWithTone(rationale) {
  if (!currentMessage) {
    $('notice').textContent = '请先「读剪贴板」获取对方消息';
    $('notice').classList.remove('ok');
    return;
  }
  runCommand({
    name: '语气·' + String(rationale).slice(0, 12),
    prompt: '按「' + rationale + '」的语气重写一条回复，保持原意与信息点不变，只调整语气与措辞。{message}',
    model: null,
  });
}

// ---- T01412：无障碍——数字键直选候选 / 方向键导航 / Enter 复制 / 界面缩放 ----
function selectCandidateByNumber(cards, key) {
  const card = cards[Number(key) - 1];
  if (card) {
    card.querySelector('.act')?.click(); // 首个动作 = 复制
    card.focus();
  }
}

function focusAdjacentCandidate(cards, key) {
  const cur = cards.indexOf(document.activeElement);
  const down = key === 'ArrowDown';
  let next;
  if (cur === -1) {
    next = down ? 0 : cards.length - 1;
  } else {
    next = Math.min(Math.max(cur + (down ? 1 : -1), 0), cards.length - 1);
  }
  cards[next]?.focus();
}

document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target.isContentEditable) return;
  const cards = [...document.querySelectorAll('#candidates .candidate:not(.streaming)')];
  // 数字键 1-9：直选第 N 条候选（触发其复制动作）
  if (/^[1-9]$/.test(e.key) && cards.length) {
    selectCandidateByNumber(cards, e.key);
    return;
  }
  // Enter：聚焦卡 → 复制
  if (e.key === 'Enter' && e.target.classList?.contains('candidate')) {
    e.target.querySelector('.act')?.click();
    return;
  }
  // 方向键：候选间移动焦点
  if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && cards.length) {
    e.preventDefault();
    focusAdjacentCandidate(cards, e.key);
  }
});

// 界面缩放（多 DPI/视力适配）：小 0.9 / 标准 1.0 / 大 1.15，body zoom 联动全局
function applyUiScale() {
  const s = localStorage.getItem('emoreply_ui_scale') || '1';
  document.body.style.zoom = s;
  const sel = $('cfgUiScale');
  if (sel) sel.value = s;
}
$('cfgUiScale').onchange = () => {
  localStorage.setItem('emoreply_ui_scale', $('cfgUiScale').value);
  applyUiScale();
};
applyUiScale();

// ---- FR-9.1 暗色模式（T01413 令牌已就绪，此处补应用/持久化/跟随系统）----
const THEME_KEY = 'emoreply_theme';

function applyTheme() {
  const mode = localStorage.getItem(THEME_KEY) || 'light';
  const dark = mode === 'dark' || (mode === 'auto' && globalThis.matchMedia('(prefers-color-scheme: dark)').matches);
  if (dark) document.documentElement.dataset.theme = 'dark';
  else delete document.documentElement.dataset.theme;
  const sel = $('cfgTheme');
  if (sel) sel.value = mode;
}
$('cfgTheme').onchange = () => {
  localStorage.setItem(THEME_KEY, $('cfgTheme').value);
  applyTheme();
};
applyTheme();
// 跟随系统：系统主题切换时即时生效（仅 auto 档需要）
if (globalThis.matchMedia) {
  globalThis.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
    if ((localStorage.getItem(THEME_KEY) || 'light') === 'auto') applyTheme();
  });
}

// ---- T01414：自定义强调色（品牌衍生 6 档，color-mix 派生全族联动）----
const ACCENTS = [
  { name: '品牌蓝', hex: '#2563eb' },
  { name: '品牌青', hex: '#21AFAF' },
  { name: '深海蓝', hex: '#08275E' },
  { name: '紫', hex: '#7c3aed' },
  { name: '玫瑰', hex: '#e11d48' },
  { name: '琥珀', hex: '#d97706' },
];

function applyAccent(hex) {
  document.documentElement.style.setProperty('--c-accent', hex);
  document.querySelectorAll('#accentSwatches .sw').forEach((s) => {
    s.classList.toggle('on', s.dataset.hex === hex);
  });
}

(function initAccent() {
  const bar = $('accentSwatches');
  const saved = localStorage.getItem('emoreply_accent') || ACCENTS[0].hex;
  ACCENTS.forEach(({ name, hex }) => {
    const b = document.createElement('button');
    b.className = 'sw';
    b.style.background = hex;
    b.dataset.tip = name + '｜切换界面强调色'; // T01531：色板接入统一文字提示
    b.dataset.hex = hex;
    b.onclick = () => {
      localStorage.setItem('emoreply_accent', hex);
      applyAccent(hex);
    };
    bar.appendChild(b);
  });
  applyAccent(saved); // 启动恢复（含默认）
})();

// ---- T01415：常驻低开销——隐藏时暂停全部动画（shimmer/光标/弹入）----
globalThis.emoReply.onVisibility((visible) => {
  document.body.classList.toggle('win-hidden', !visible);
});

// ---- T01400：截图取文（全屏 OCR → 文本填入消息区，可编辑后生成）----
$('btnOcr').onclick = async () => {
  const btn = $('btnOcr');
  btn.disabled = true;
  $('notice').textContent = '截屏识别中…（首次约需数秒）';
  $('notice').classList.remove('ok');
  try {
    const res = await globalThis.emoReply.ocrScreenshot();
    if (!res.ok) {
      $('notice').textContent = res.notice || '未识别到文本';
      return;
    }
    currentMessage = res.text;
    excludeTexts = []; // 新消息 = 新一轮
    const el = $('msg');
    el.textContent = res.text;
    el.classList.remove('muted');
    $('notice').textContent = `识别 ${res.lines.length} 行（置信度 ${Math.round(res.confidence)}）——可直接生成，或先在上方手动修正`;
    $('notice').classList.remove('ok');
  } finally {
    btn.disabled = false;
  }
};

// ---- T01530：窗口控制栏——最小化（主进程 win.minimize；任务栏/Alt+Tab/托盘单击可唤回）----
$('winMin').onclick = () => globalThis.emoReply.minimizeWindow();

// ---- T01566：关闭按钮＝隐藏到托盘（进程不退出）；托盘不可用时主进程降级最小化并提示 ----
$('winClose').onclick = async () => {
  const mode = await globalThis.emoReply.closeWindow();
  if (mode === 'degraded') {
    $('notice').textContent = '系统托盘不可用：窗口已收进任务栏（程序未退出）';
    $('notice').classList.remove('ok');
  }
};

// ---- T01566：置顶切换（图钉态=已置顶：窗口恒在最前、失焦不降级；再点恢复普通层级）----
const pinBtn = $('winPin');
function renderPinState(on) {
  pinBtn.classList.toggle('on', on);
  setIcon(pinBtn, on ? 'i-pin' : 'i-pin-off');
  pinBtn.dataset.tip = on
    ? '已置顶｜点击取消置顶（窗口将恢复普通层级）'
    : '未置顶｜点击置顶（窗口始终显示在其他窗口之上）';
  pinBtn.setAttribute('aria-label', on ? '取消置顶' : '置顶');
}
// 首帧水合主进程事实源（含 winPin 持久化值）
function hydratePin() {
  globalThis.emoReply.getPin().then(renderPinState, (e) =>
    console.warn('[renderer] 置顶态水合失败：', e?.message || e));
}
hydratePin();
pinBtn.onclick = async () => renderPinState(await globalThis.emoReply.setPin(!pinBtn.classList.contains('on')));

// ---- T01532：设置中心覆盖层（RAG/设置/历史统一入口；右上角齿轮开，返回键/Esc 关）----
function openSettingsPage(sectionSummary) {
  const page = $('settingsPage');
  page.classList.add('on');
  // 原生 <dialog>：open 属性与 .on 同步维护，避免「看得见但状态是关的」。
  // 用 show() 而非 showModal()：本页是整屏覆盖层，不要遮罩与焦点陷阱（与改造前行为一致）。
  if (!page.open) page.show();
  if (sectionSummary) { // 降级引导等场景：直达并展开指定区块
    const det = $(sectionSummary)?.closest('details');
    if (det) det.open = true;
    setTimeout(() => det?.scrollIntoView({ behavior: 'smooth' }), 60);
  }
}
function closeSettingsPage() {
  const page = $('settingsPage');
  page.classList.remove('on');
  if (page.open) page.close();
}
$('openSettings').onclick = () => openSettingsPage();
$('closeSettings').onclick = closeSettingsPage;
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSettingsPage(); });

// ================= T01531：全局图标按钮体系（无文字·无边框·大图标 + 倾斜 + 提示）=================
// 图标写入：SVG symbol 引用（描边随 currentColor，主题/禁用态自动适配）
function setIcon(btn, id) {
  btn.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#' + id + '"/></svg>';
}

// ---- 悬浮倾斜（Tilt）：鼠标在元素内位置 → 3D 倾角；rAF 合帧防卡顿，离开弹性回正 ----
const TILT_MAX = 9; // 最大倾角（度）——轻微立体，不干扰阅读
let tiltEl = null;
let tiltX = 0;
let tiltY = 0;
let tiltRaf = 0;
let tiltDown = false;

function applyTilt(b, x, y) {
  const r = b.getBoundingClientRect();
  const px = (x - r.left) / Math.max(r.width, 1) - 0.5; // -0.5..0.5
  const py = (y - r.top) / Math.max(r.height, 1) - 0.5;
  const rx = (-py * TILT_MAX).toFixed(2);
  const ry = (px * TILT_MAX).toFixed(2);
  b.style.transform =
    `perspective(420px) rotateX(${rx}deg) rotateY(${ry}deg) scale(${tiltDown ? 0.94 : 1.05})`;
}

function resetTilt() {
  if (!tiltEl) return;
  tiltEl.style.transform = '';
  tiltEl.classList.remove('tilt');
  tiltEl = null;
}

document.addEventListener('mousemove', (e) => {
  const b = e.target instanceof Element ? e.target.closest('button:not(:disabled)') : null;
  if (tiltEl && tiltEl !== b) resetTilt();
  if (!b) return;
  tiltEl = b;
  b.classList.add('tilt');
  tiltX = e.clientX;
  tiltY = e.clientY;
  if (!tiltRaf) {
    tiltRaf = requestAnimationFrame(() => {
      tiltRaf = 0;
      if (tiltEl) applyTilt(tiltEl, tiltX, tiltY);
    });
  }
});
document.addEventListener('mousedown', (e) => {
  const b = e.target instanceof Element ? e.target.closest('button') : null;
  if (b && b === tiltEl) {
    tiltDown = true;
    applyTilt(b, tiltX, tiltY);
  }
});
document.addEventListener('mouseup', () => {
  tiltDown = false;
  if (tiltEl) applyTilt(tiltEl, tiltX, tiltY);
});
window.addEventListener('blur', resetTilt);

// ---- 文字提示（Tooltip）：data-tip=「名称｜功能说明」，300ms 延迟，离开平滑消失 ----
const tipBox = document.createElement('div');
tipBox.id = 'tip';
document.body.appendChild(tipBox);
let tipTimer = 0;
let tipFor = null;

function hideTip() {
  clearTimeout(tipTimer);
  tipBox.classList.remove('on');
  tipFor = null;
}

function placeTip(b) {
  tipBox.textContent = b.dataset.tip || '';
  tipBox.classList.add('on'); // 先显示才能量出宽高
  const r = b.getBoundingClientRect();
  const w = tipBox.offsetWidth;
  const h = tipBox.offsetHeight;
  let x = r.left + r.width / 2 - w / 2;
  x = Math.max(4, Math.min(x, window.innerWidth - w - 4));
  let y = r.top - h - 6; // 默认在按钮上方，放不下翻到下方
  if (y < 4) y = r.bottom + 6;
  tipBox.style.left = x + 'px';
  tipBox.style.top = y + 'px';
}

document.addEventListener('mouseover', (e) => {
  const b = e.target instanceof Element ? e.target.closest('button') : null;
  if (!b || b === tipFor) return;
  hideTip();
  if (!b.dataset.tip) return;
  tipFor = b;
  tipTimer = setTimeout(() => {
    if (tipFor === b) placeTip(b);
  }, 300);
});
document.addEventListener('mouseout', (e) => {
  const to = e.relatedTarget;
  if (tipFor && !(to instanceof Node && tipFor.contains(to))) hideTip();
});
document.addEventListener('click', hideTip);
window.addEventListener('blur', hideTip);
