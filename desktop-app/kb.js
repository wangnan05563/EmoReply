// RAG 人设知识库（SRS 决策③）——与 m1-poc rag.py / Android KnowledgeBase.kt 同构的 JS 移植。
//
// 存储：AES-256-GCM 加密单文件 JSON（密钥 = SHA256(机器指纹 || 可选口令)，端侧绑定）；
// 条目三类 persona / relation / platform；检索 bigram 词面重叠 + 同联系人加权 0.15。
// 纯 Node 模块（不依赖 electron），由 main.js 在主进程内使用。
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const CATEGORY_PERSONA = 'persona';
const CATEGORY_RELATION = 'relation';
const CATEGORY_PLATFORM = 'platform';
const CATEGORIES = [CATEGORY_PERSONA, CATEGORY_RELATION, CATEGORY_PLATFORM];

const IV_LEN = 12;
const AUTH_TAG_LEN = 16;

// ---- 加解密原语（与 m1-poc crypto.py 密文格式互通：`iv.ciphertext` hex 两段）----

function deriveKey(passphrase) {
  const seed = passphrase || `${os.platform()}-${os.hostname()}`;
  return crypto.createHash('sha256').update(seed, 'utf8').digest();
}

function encrypt(key, plain) {
  const iv = crypto.randomBytes(IV_LEN);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const ct = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final(), cipher.getAuthTag()]);
  return iv.toString('hex') + '.' + ct.toString('hex');
}

function decrypt(key, payload) {
  const [ivHex, ctHex] = String(payload).split('.', 2);
  if (!ivHex || !ctHex) throw new Error('解密失败：口令不匹配或数据损坏');
  const buf = Buffer.from(ctHex, 'hex');
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, Buffer.from(ivHex, 'hex'));
  decipher.setAuthTag(buf.subarray(buf.length - AUTH_TAG_LEN));
  try {
    return Buffer.concat([decipher.update(buf.subarray(0, buf.length - AUTH_TAG_LEN)), decipher.final()]).toString('utf8');
  } catch (e) {
    throw new Error('解密失败：口令不匹配或数据损坏', { cause: e });
  }
}

// ---- KnowledgeBase：加密知识库（单文件 JSON，条目增删查）----

function newId() {
  return crypto.randomBytes(6).toString('hex'); // 12 位，与 uuid4().hex[:12] 同长
}

class KnowledgeBase {
  constructor(dbPath, passphrase) {
    this.dbPath = dbPath;
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
    this.key = deriveKey(passphrase);
    this.entries = [];
    if (fs.existsSync(dbPath)) {
      try {
        const payload = decrypt(this.key, fs.readFileSync(dbPath, 'utf8'));
        this.entries = (JSON.parse(payload).entries || []).map((e) => ({
          category: e.category,
          text: e.text,
          contact: e.contact || null, // relation 类可绑定联系人；其余为 null
          id: e.id,
          created_at: e.created_at,
        }));
      } catch (e) {
        console.warn('[kb] 知识库加载失败，使用初始态：', e?.message); // 口令不匹配/损坏 → 不崩溃（与 PersonaStore 口径一致）
        this.entries = [];
      }
    }
  }

  persist() {
    const payload = JSON.stringify({ entries: this.entries });
    fs.writeFileSync(this.dbPath, encrypt(this.key, payload), 'utf8');
  }

  add(category, text, contact) {
    if (!CATEGORIES.includes(category)) {
      throw new Error(`非法知识分类：${category}（允许：${CATEGORIES.join('/')}）`);
    }
    text = String(text || '').trim();
    if (!text) throw new Error('知识条目内容不能为空');
    const entry = {
      category,
      text,
      contact: category === CATEGORY_RELATION ? (contact || null) : null,
      id: newId(),
      created_at: Date.now() / 1000,
    };
    this.entries.push(entry);
    this.persist();
    return entry.id;
  }

  remove(id) {
    const before = this.entries.length;
    this.entries = this.entries.filter((e) => e.id !== id);
    const changed = this.entries.length !== before;
    if (changed) this.persist();
    return changed;
  }

  list() {
    return this.entries.slice();
  }

  get count() {
    return this.entries.length;
  }

  wipe() {
    this.entries = []; // FR-6.3 联动：一键清除
    this.persist();
  }
}

// ---- 词面检索（默认）：bigram 重叠率 + 同联系人加权，零依赖可离线 ----

function bigrams(text) {
  // CJK 友好：过滤标点/空白，单字也保留（短查询兜底）；与 Python bigrams 同口径
  const cleaned = [...String(text || '')].filter(
    (ch) => /[a-zA-Z0-9]/.test(ch) || (ch >= '\u3400' && ch <= '\u9fff'),
  );
  if (cleaned.length < 2) return new Set(cleaned);
  const set = new Set(cleaned.map((ch) => ch.toString()));
  for (let i = 0; i < cleaned.length - 1; i++) set.add(cleaned[i] + cleaned[i + 1]);
  return set;
}

// 单条得分：category 过滤 + bigram 重叠率 + 同联系人加权；无重叠且非当前联系人 → null（不注入，避免噪声）
function entryScore(e, q, contact, category) {
  if (category && e.category !== category) return null;
  const sameContact = Boolean(e.contact && contact && e.contact === contact);
  const eGrams = bigrams(e.text);
  let overlap = 0;
  if (q.size) {
    let hit = 0;
    for (const g of q) if (eGrams.has(g)) hit++;
    overlap = hit / q.size;
  }
  const score = overlap + (sameContact ? LexicalRetriever.CONTACT_BOOST : 0);
  return score > 0 ? score : null;
}

class LexicalRetriever {
  static CONTACT_BOOST = 0.15;

  search(kb, query, { contact = null, category = null, topK = 3 } = {}) {
    const q = bigrams(query || '');
    const scored = [];
    for (const e of kb.list()) {
      const score = entryScore(e, q, contact, category);
      if (score !== null) scored.push([score, e]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    return scored.slice(0, topK).map(([, e]) => e);
  }
}

module.exports = { KnowledgeBase, LexicalRetriever, CATEGORIES, bigrams, deriveKey, encrypt, decrypt };

// ---- T01402：EmbeddingRetriever（可选档，配置了 embedding 模型才启用）----
// 向量来源：OpenAI 兼容 /embeddings 端点；条目向量按 id+文本哈希内存缓存（条目多时省调用）。
// 排序：余弦相似度 + 同联系人加权 0.15（与 LexicalRetriever 同一次级信号口径）。
// 检索失败/未配置 → 调用方回落 LexicalRetriever（永不因向量档失败丢知识）。
function cosine(a, b) {
  let dot = 0, na = 0, nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (!na || !nb) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

function entryCacheKey(e) {
  // id + 文本摘要哈希：编辑条目（文本变）自动失效。仅内存缓存键，无跨端互通，取 sha256（S4790：sha1 属弱摘要）
  const h = crypto.createHash('sha256').update(e.text, 'utf8').digest('hex').slice(0, 12);
  return `${e.id}:${h}`;
}

// 与安卓侧 baseUrl.trimEnd('/') 同口径；不用 /\/+$/（S5852 判回溯超线性）
function trimTrailingSlashes(url) {
  let s = url;
  while (s.endsWith('/')) s = s.slice(0, -1);
  return s;
}

function postJson(url, apiKey, body, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    let u;
    try {
      u = new URL(url);
    } catch (e) {
      return reject(e);
    }
    const mod = u.protocol === 'http:' ? require('node:http') : https;
    const req = mod.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'http:' ? 80 : 443),
        path: u.pathname + u.search,
        method: 'POST',
        timeout: timeoutMs,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiKey}`,
          'Content-Length': Buffer.byteLength(JSON.stringify(body)),
        },
      },
      (res) => {
        let data = '';
        res.on('data', (c) => (data += c));
        res.on('end', () => {
          if (res.statusCode < 200 || res.statusCode > 299) {
            return reject(new Error(`embeddings ${res.statusCode}`));
          }
          try {
            resolve(JSON.parse(data));
          } catch (e) {
            reject(e);
          }
        });
      },
    );
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    req.write(JSON.stringify(body));
    req.end();
  });
}

class EmbeddingRetriever {
  static CONTACT_BOOST = LexicalRetriever.CONTACT_BOOST;

  constructor() {
    this.cache = new Map(); // cacheKey -> Float32Array/number[]
  }

  async embedQueries(config, embeddingModel, texts) {
    const endpoint = trimTrailingSlashes(config.baseUrl) + '/embeddings';
    const res = await postJson(endpoint, config.apiKey, {
      model: embeddingModel,
      input: texts,
    });
    return (res.data || []).map((d) => d.embedding);
  }

  /**
   * 向量检索。返回 [{entry, score}] 或 null（配置缺失/调用失败 → 调用方回落词面档）。
   */
  async search(kb, query, { contact = null, config = null, embeddingModel = null, topK = 3 } = {}) {
    if (!config?.baseUrl || !config?.apiKey || !embeddingModel) return null;
    const entries = kb.list();
    if (!entries.length) return [];
    try {
      const pending = entries.filter((e) => !this.cache.has(entryCacheKey(e)));
      const qVecs = await this.embedQueries(config, embeddingModel, [query, ...pending.map((e) => e.text)]);
      const qVec = qVecs[0];
      if (!qVec) return null;
      pending.forEach((e, i) => this.cache.set(entryCacheKey(e), qVecs[i + 1]));
      const scored = [];
      for (const e of entries) {
        const eVec = this.cache.get(entryCacheKey(e));
        if (!eVec) continue;
        const sameContact = Boolean(e.contact && contact && e.contact === contact);
        const score = cosine(qVec, eVec) + (sameContact ? EmbeddingRetriever.CONTACT_BOOST : 0);
        if (score > 0.05) scored.push({ entry: e, score }); // 微小阈值滤噪声
      }
      scored.sort((a, b) => b.score - a.score);
      return scored.slice(0, topK);
    } catch (e) {
      console.warn('[kb] 向量检索失败，回落词面档：', e?.message);
      return null;
    }
  }
}

module.exports.EmbeddingRetriever = EmbeddingRetriever;
module.exports.cosine = cosine;
