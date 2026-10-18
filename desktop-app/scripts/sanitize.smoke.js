// T01392 冒烟测试：node scripts/sanitize.smoke.js
// 覆盖顺序铁律回归锁（markdown 未剥时 bullet 正则不误剥）、元话语、悬空管道、标签行、表情收敛。
'use strict';
const assert = require('node:assert');
const {
  stripMarkdown, cleanCandidate, splitRationale, expandCandidates,
  isMetaCandidate, normalizeEmoji, sanitizeCandidates,
} = require('../sanitize');

let pass = 0;
function eq(actual, expected, name) {
  assert.deepStrictEqual(actual, expected, `${name}\n  actual:   ${JSON.stringify(actual)}\n  expected: ${JSON.stringify(expected)}`);
  pass++;
  console.log(`  ok ${name}`);
}

// ---- 顺序铁律回归锁（T01126 实测教训）----
eq(stripMarkdown('**好呀**'), '好呀', '铁律：markdown 星号先剥干净');
eq(cleanCandidate('**好呀**'), '好呀', '铁律：cleanCandidate 后无星号残留');
eq(splitRationale('选项1：**好呀**|理由一句话'), ['好呀', '理由一句话'], '铁律：先 markdown 后前缀，混合输入不误剥');

// ---- splitRationale ----
eq(splitRationale('内容|这是理由'), ['内容', '这是理由'], '管道拆分');
eq(splitRationale('内容含|竖线|理由'), ['内容含|竖线', '理由'], '多个管道取最后一个');
eq(splitRationale('内容|'), ['内容', null], '尾部悬空管道剥除（T01131）');
eq(splitRationale('正文 理由：这样更稳妥'), ['正文', '这样更稳妥'], '内联理由兜底（T01126）');
eq(splitRationale(''), ['', null], '空输入');

// ---- expandCandidates ----
eq(expandCandidates('1. 好呀\n2. 收到啦'), ['好呀', '收到啦'], '多行 bullet 拆分');
eq(expandCandidates('选项1：好呀|理由 选项2：收到|理由2'), ['好呀|理由', '收到|理由2'], '内联编号拆分');
eq(expandCandidates('## 候选回复\n好呀，周末一起去'), ['好呀，周末一起去'], '标题行丢弃');
eq(expandCandidates('## 候选回复'), [], '纯标题无候选 → 空');
eq(expandCandidates('内容|\n理由在下一行'), ['内容|理由在下一行'], '悬空管道接回（T01131）');

// ---- 元话语 ----
eq(isMetaCandidate('请提供具体的聊天记录，这样我才能帮你起草回复。'), true, '元话语命中');
eq(isMetaCandidate('作为一个AI助手，我无法生成回复'), true, '能力自述命中');
eq(isMetaCandidate('请告诉我地址，我明天过去'), false, '普通请求不误杀');
eq(sanitizeCandidates('请提供聊天记录'), [], '元话语候选被管线剔除');

// ---- 表情收敛（与 py _EMOJI_REPEAT 同语义：连续重复先压成 1 个，再按总数截断）----
eq(normalizeEmoji('好🎉🎉🎉'), '好🎉', '连续重复压成 1 个');
eq(normalizeEmoji('好🎉🎊🎉'), '好🎉🎊', '非连续超限截断靠后');
eq(normalizeEmoji('好🎉🎊🎈🧨'), '好🎉🎊', '总数限 2 个');
eq(normalizeEmoji('好呀😊，一起？'), '好呀😊，一起？', '正常表情不动');

// ---- 端到端管线 ----
eq(
  sanitizeCandidates('1. **周末好呀，一起爬山？**|表达热情邀约\n2. 收到，谢谢告知～'),
  [
    { text: '周末好呀，一起爬山？', rationale: '表达热情邀约' },
    { text: '收到，谢谢告知～', rationale: null },
  ],
  '端到端：markdown+编号+理由混合清洗',
);
eq(
  sanitizeCandidates('<p>好的，没问题</p>'),
  [{ text: '好的，没问题', rationale: null }],
  'HTML 标记剥离（C25）',
);

console.log(`\n${pass} checks passed`);

// ---- T01395：换一批去重（LCS 0.8）----
const { filterDuplicatePairs, similarity, isDuplicate } = require('../sanitize');
eq(similarity('好呀😊', '好呀~'), 1, '归一化后表情标点不计差异');
eq(isDuplicate('收到，谢谢告知～', ['收到谢谢告知']), true, 'LCS 高相似判重');
eq(isDuplicate('周末一起爬山吗？', ['收到谢谢告知']), false, '不同内容不判重');
eq(
  filterDuplicatePairs(
    [{ text: '收到，谢谢告知', rationale: null }, { text: '好嘞，知道了', rationale: null }],
    ['收到谢谢告知'],
  ).map((p) => p.text),
  ['好嘞，知道了'],
  '换一批过滤已给候选',
);
console.log(`(含 T01395 去重用例)`);

// ---- T01401：要不要回判定（本地启发式，压误判）----
const { heuristicNoReply } = require('../sanitize');
eq(heuristicNoReply('👍'), { noReply: true, reason: '对方只发了一个表情，无需回复' }, '纯表情拦截');
eq(heuristicNoReply('好的，收到啦'), { noReply: true, reason: '对方只是简短确认（嗯/好的/收到类），无需回复' }, '极短确认拦截');
eq(heuristicNoReply('嗯'), { noReply: true, reason: '对方只是简短确认（嗯/好的/收到类），无需回复' }, '单字确认拦截');
eq(heuristicNoReply('好的，那明天见面聊？'), { noReply: false, reason: null }, '确认+提问 → 放行（压误判）');
eq(heuristicNoReply('明天下午3点开会，请准时参加。'), { noReply: false, reason: null }, '正式通知 → 放行');
eq(heuristicNoReply(''), { noReply: false, reason: null }, '空消息放行');

// ---- T01554：管道符错位 + 启发式拆分（与 m1-poc/py、Android/kt 三端同构）----
eq(splitRationale('好|今天天气不错，我们去爬山吧'), ['今天天气不错，我们去爬山吧', '好'], 'T01554 错位检测：短文本在前→交换');
eq(splitRationale('今天天气不错，我们去爬山吧|语气轻松，回应邀请'), ['今天天气不错，我们去爬山吧', '语气轻松，回应邀请'], '正常管道不交换');
eq(splitRationale('好的我看看|短'), ['好的我看看', '短'], 'text>3 字符不满足错位条件');
eq(splitRationale('好的 rationale: 语气轻松'), ['好的', '语气轻松'], 'T01554 英文关键词启发式拆分');
eq(splitRationale('好的理由：这个建议很合理'), ['好的', '这个建议很合理'], 'T01554 中文关键词拆分（理由不带标签）');
// 误伤回归锁：kt 原实现的「说明」弱词会把这些真实候选拦腰截断
eq(splitRationale('好的，我说明一下情况'), ['好的，我说明一下情况', null], '误伤锁：含「说明」的完整句不拆分');
eq(splitRationale('请把说明书递给我'), ['请把说明书递给我', null], '误伤锁：「说明书」不拆分');
eq(splitRationale('你这个理由我接受不了'), ['你这个理由我接受不了', null], '误伤锁：「理由」后无冒号视为正文');

// ---- TRAIL_PIPE 正则修复回归锁（py \Z → JS $ 移植陷阱）----
eq(cleanCandidate('厦门旅行时光。|'), '厦门旅行时光。', '悬空管道符被剥除（此前因 \Z 退化为字面 Z 而失效）');
eq(cleanCandidate('请把材料送到 Z 区前台'), '请把材料送到 Z 区前台', '含 Z 正文不得被误删（此前会吞掉「 Z」）');
eq(cleanCandidate('走 Z 通道'), '走 Z 通道', '单个 Z 前空白不被 stripping');

// ---- T01720：理由换行续行接回上一条候选（与 m1-poc/py、Android/kt 三端同构）----
const { mergeRationaleLines } = require('../sanitize');
eq(
  expandCandidates('1. 哈哈被你发现了\n理由：以自嘲化解调侃\n2. 那咱定在10点半\n理由：直接给行动方案'),
  ['哈哈被你发现了|以自嘲化解调侃', '那咱定在10点半|直接给行动方案'],
  'T01720：理由续行接回上一条候选（不产出独立候选）',
);
eq(mergeRationaleLines(['好呀周末一起去', '理由：答应得爽快']), ['好呀周末一起去|答应得爽快'], 'T01720：常规续接补管道符');
eq(mergeRationaleLines(['好呀周末一起去|', '理由：答应得爽快']), ['好呀周末一起去|答应得爽快'], 'T01720：悬空管道后直接续接');
eq(mergeRationaleLines(['好呀', '理由：其一', '原因：其二']), ['好呀|其一；其二'], 'T01720：两条理由续行以「；」续接');
eq(mergeRationaleLines(['理由：无上一行']), [], 'T01720：理由行在最前 → 丢弃');
eq(mergeRationaleLines(['- 理由：bullet 修饰的续行', '真候选']), ['真候选'], 'T01720：bullet 修饰的理由行丢弃（无上一行）');
eq(
  expandCandidates('1. 原因很简单，我睡过头了\n2. 下次一定早起'),
  ['原因很简单，我睡过头了', '下次一定早起'],
  'T01720 误伤锁：「原因」后非冒号的正文不合并',
);
eq(
  sanitizeCandidates('1. 好呀周末一起去\n理由：答应得爽快\n2. 下次吧\n理由：这周约满了'),
  [
    { text: '好呀周末一起去', rationale: '答应得爽快' },
    { text: '下次吧', rationale: '这周约满了' },
  ],
  'T01720 端到端：理由归位到 rationale，候选数正确',
);

// ---- T02224：数量契约配对（无标记理由行不再成为候选；与 kt/py 三端同构）----
const { pairUnmarkedReasons } = require('../sanitize');
const RAW_ALT4 = [
  '收到，麻烦直接把提示词发我，我看看具体场景再帮你优化 🙏',
  '语气简洁明了，直接推进下一步，避免无效寒暄。',
  '你发我一份提示词吧，我看看怎么调整更合适 🙏',
  '侧重行动建议，以"我"为主语提出具体动作。',
].join('\n');
eq(
  sanitizeCandidates(RAW_ALT4, { expected: 2 }),
  [
    { text: '收到，麻烦直接把提示词发我，我看看具体场景再帮你优化 🙏', rationale: '语气简洁明了，直接推进下一步，避免无效寒暄。' },
    { text: '你发我一份提示词吧，我看看怎么调整更合适 🙏', rationale: '侧重行动建议，以"我"为主语提出具体动作。' },
  ],
  'T02224：分隔符被整个丢掉时按数量契约配对，理由不再漏成候选',
);
eq(
  sanitizeCandidates('Sure, send me the prompt\nExplains the next step clearly', { expected: 1 }),
  [{ text: 'Sure, send me the prompt', rationale: 'Explains the next step clearly' }],
  'T02224：请求 1 条而展开 2 行 → 第二行必非候选',
);
eq(pairUnmarkedReasons(['好呀', '收到'], 2), ['好呀', '收到'], 'T02224 误伤锁：行数未超 N 时不配对（两条真候选）');
eq(pairUnmarkedReasons(['甲', '乙', '丙'], 2), ['甲', '乙', '丙'], 'T02224：行数为奇无法对齐 → 不配对');
eq(pairUnmarkedReasons(['好呀|爽快', '下次吧'], 2), ['好呀|爽快', '下次吧'], 'T02224：已有理由标记 → 走词表路径');
eq(pairUnmarkedReasons(['a', 'b', 'c', 'd', 'e', 'f'], 2), ['a|b', 'c|d'], 'T02224：超发时配对并按 N 截断');
eq(sanitizeCandidates(RAW_ALT4), expandCandidates(RAW_ALT4).map((l) => { const [t, r] = splitRationale(l); return { text: t, rationale: r }; }), 'T02224：expected 缺省（0）时行为与修复前一致');
eq(
  splitRationale('内容：好的，我按这版先改改看|边界保持：改动成本最低'),
  ['好的，我按这版先改改看', '边界保持：改动成本最低'],
  'T02224：契约字段名「内容：」当行首标签时被剥除',
);
