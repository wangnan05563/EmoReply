// 打包产物 OCR 回归（Electron 侧）：对 dist/win-unpacked/resources/app.asar 内的 ocr.js
// 做端到端中文识别，验证 build.files 裁掉 tesseract.js-core 的 *.wasm.js 后打包环境仍可用。
// 纯 node 冒烟（scripts/ocr.smoke.js）跑的是 node_modules，不覆盖 asar 内模块解析，
// 而 asar 只有 Electron 的 fs/require 补丁能穿透，故此脚本必须用 electron 跑。
// 用法：env -u ELECTRON_RUN_AS_NODE npx electron scripts/ocr-asar-check.js
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { app } = require('electron');

app.disableHardwareAcceleration();

const root = path.join(__dirname, '..');
const asarDir = path.join(root, 'dist', 'win-unpacked', 'resources');
const asarPath = path.join(asarDir, 'app.asar');
const imgPath = path.join(root, 'tmp', 'ocr-asar-test.png');
const ocr = require(path.join(asarPath, 'ocr.js'));

async function main() {
  if (!fs.existsSync(asarPath)) throw new Error(`asar 不存在: ${asarPath} —— 先跑 npm run dist`);
  if (!fs.existsSync(imgPath)) throw new Error(`测试图不存在: ${imgPath}`);
  const langDir = path.join(asarPath, 'assets', 'ocr');
  console.log('asar:', asarPath);
  console.log('model:', fs.existsSync(path.join(langDir, 'chi_sim.traineddata.gz')) ? 'gz' : 'plain');

  ocr.initOcr(langDir);
  const t0 = Date.now();
  const { lines, text, confidence } = await ocr.recognizeImage(imgPath);
  console.log(`elapsed: ${Date.now() - t0}ms  confidence: ${confidence}`);
  console.log('lines:', JSON.stringify(lines, null, 1));

  // 断言：两句测试文本都应被识别。tesseract 中文 PSM=6 会按字输出并用空格分隔
  //（"周 未 一 起 爬 山 好 吗"），故断言前先剥所有空白，否则关键词匹配必然失败。
  const flat = text.replaceAll(/\s+/g, '');
  const expect = ['爬山', '开会'];
  const miss = expect.filter((k) => !flat.includes(k));
  if (miss.length) throw new Error(`未识别到关键词 ${JSON.stringify(miss)}；实际: ${JSON.stringify(flat)}`);
  console.log('ASAR OCR CHECK PASSED');
}

// CJS 没有顶层 await：就绪逻辑走 ready 事件，失败在函数内兜底
async function check() {
  try {
    await main();
    process.exit(0);
  } catch (e) {
    console.error('ASAR OCR CHECK FAILED:', e?.message || e);
    process.exit(1);
  }
}

app.on('ready', check);
