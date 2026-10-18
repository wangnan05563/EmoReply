// T01400 冒烟：tesseract.js 离线中文 OCR（langPath=本地 assets/ocr，零 CDN 依赖）
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert');
const { createWorker } = require('tesseract.js');
const { isGarbledText, isNoiseLine } = require('../ocr');

// ---- T01554：乱码行判据（与 Android isGarbledText、m1-poc is_garbled_text 三端同构）----
// ① 控制字符
assert.equal(isGarbledText('你好\u0007世界'), true, '控制字符判为乱码');
assert.equal(isGarbledText('你好\t可以的'), false, '\\t 属正常空白不判乱码');
// ② 有意义字符占比
assert.equal(isGarbledText('■□▲△■□▲△'), true, '低意义字符占比判为乱码');
assert.equal(isGarbledText('周末一起去爬山好吗'), false, '正常中文不判乱码');
assert.equal(isGarbledText('好的\u{1F44C}收到'), false, '含 emoji 正常句不判乱码');
// ③ 连续重复符号（汉字叠字受保护）
assert.equal(isGarbledText('???'), true, '连续问号判为乱码');
assert.equal(isGarbledText('■■■'), true, '连续方块判为乱码');
assert.equal(isGarbledText('好好好'), false, '中文叠字是合法表达，不判乱码');
// ④ 纯符号行
assert.equal(isGarbledText('※◎※◎'), true, '纯符号行判为乱码');
assert.equal(isGarbledText('好'), false, '长度 ≤2 不触发纯符号判据');
// 接入 isNoiseLine
assert.equal(isNoiseLine('???'), true, '噪声行：乱码被最终过滤');
assert.equal(isNoiseLine('[图片]'), true, '噪声行：媒体占位');
assert.equal(isNoiseLine('明天下午三点开会'), false, '正常行保留');
assert.equal(isNoiseLine('好的，我说明一下'), false, '含「说明」的正常句不得被过滤');
console.log('  ok T01554 乱码过滤 12 项（三端同构判据）');

async function main() {
  const langPath = path.join(__dirname, '..', 'assets', 'ocr');
  // 与 ocr.js 同口径：优先 .gz 压缩模型
  const useGzip = fs.existsSync(path.join(langPath, 'chi_sim.traineddata.gz'));
  console.log('model:', useGzip ? 'chi_sim.traineddata.gz (gzip)' : 'chi_sim.traineddata');
  console.time('worker-init');
  const worker = await createWorker('chi_sim', 1, {
    oem: 1,
    langPath,
    gzip: useGzip,
    cacheMethod: 'none', // 与 ocr.js 同口径：不回写解压模型（否则污染 assets/ocr）
    logger: () => {}, // 静默
  });
  console.timeEnd('worker-init');

  // 生成一张含中文的测试图（纯 Node 无 canvas：用 tesseract 支持的简单方案——
  // 直接用 SVG? tesseract.js recognize 接受图片路径/buffer。用最小 PNG 需要编码器。
  // 方案：让 tesseract 识别一张由 makeImageGuard 生成的位图——简化：用现有聊天截图路径参数化，
  // 无参时用内置 1x1 白图验证管线贯通（识别为空但流程完整）。
  const arg = process.argv[2];
  if (arg) {
    console.time('recognize');
    const { data } = await worker.setParameters({ tessedit_pageseg_mode: '7' }).then(() => worker.recognize(arg));
    console.timeEnd('recognize');
    console.log('OCR result >>>');
    console.log(data.text.trim());
    console.log('confidence:', data.confidence);
  } else {
    // 管线冒烟：白图应输出空文本且不抛错
    const pngB64 =
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
    const buf = Buffer.from(pngB64, 'base64');
    console.time('recognize');
    const { data } = await worker.recognize(buf);
    console.timeEnd('recognize');
    console.log('blank-image text:', JSON.stringify(data.text.trim()), '(expect empty)');
  }
  await worker.terminate();
  console.log('OCR OFFLINE SMOKE PASSED');
  process.exit(0);
}

main().catch((e) => {
  console.error('OCR SMOKE FAILED:', e.message || e);
  process.exit(1);
});
