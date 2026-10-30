// T01164：electron-builder 强制要求 package.json 的 version 为合法 semver，
// 而本项目 patch 号约定是 YYYYMMDD-NNN（T01160）。本脚本在打包期间临时改写为
// 0.1.0-YYYYMMDD-NNN，打包结束（无论成败）还原原文件，保证两条约定都成立。
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');

const pkgPath = path.join(__dirname, '..', 'package.json');
const orig = fs.readFileSync(pkgPath, 'utf8');
const pkg = JSON.parse(orig);
const m = /^(\d{8}-\d{3})$/.exec(pkg.version || '');

if (m) {
  pkg.version = `0.1.0-${pkg.version}`;
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
  console.log(`[pkg-semver] 临时版本 -> ${pkg.version}`);
}

// 直接以当前 Node 解释器跑 electron-builder 自带的 CLI，参数走数组不过 shell：
// 拼字符串给 execSync 需要 shell 解析（Sonar S4721 的注入面），数组参数没有这个问题。
const builderCli = path.join(__dirname, '..', 'node_modules', 'electron-builder', 'out', 'cli', 'cli.js');
const args = process.argv.slice(2);
let code = 0;
try {
  const r = cp.spawnSync(process.execPath, [builderCli, ...args], { stdio: 'inherit', cwd: path.join(__dirname, '..') });
  if (r.error) {
    console.error('[pkg-semver] 启动 electron-builder 失败：', r.error.message);
    code = 1;
  } else {
    code = r.status ?? 1; // 0 成功原样保留；null（被信号终止）按失败算
  }
} catch (e) {
  code = e.status || 1;
} finally {
  fs.writeFileSync(pkgPath, orig);
  console.log(`[pkg-semver] 已还原版本 -> ${JSON.parse(orig).version}`);
}
process.exit(code);
