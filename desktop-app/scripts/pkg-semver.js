// T01164：electron-builder 强制要求 package.json 的 version 为合法 semver，
// 而本项目 patch 号约定是 YYYYMMDD-NNN（T01160）。本脚本在打包期间临时改写为
// 0.1.0-YYYYMMDD-NNN，打包结束（无论成败）还原原文件，保证两条约定都成立。
// T05736：如设置了 EMO_REPLY_EB_OUTPUT 环境变量，则把 build.directories.output
// 临时改写为该唯一路径，使 electron-builder 写入全新空目录，绕开 release\desktop-installer
// 内被防病毒/索引服务持锁的 app.asar（该锁会让 EnsureEmptyDir 抛错中断流水线）。
// T05736 补强：未指定该环境变量时，默认 ALSO 生成一个唯一输出目录（不再固定写
// release\desktop-installer）——一键发布链路 build-publish.ps1 直接 `npm run dist`，
// 不会经 setup-and-build-desktop.ps1 设置该变量，若回退到固定目录仍会撞锁。构建
// 成功后把产物安装包回写到规范目录 release\desktop-installer，供 config.json 的
// asset.dir 收集；调用方显式指定输出目录时则由调用方自行搬运，此处不回写。
// 工程目录不落任何可重复构建物；删除 release\ 即等于清空全部产物，重跑脚本可再生。
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');

const pkgPath = path.join(__dirname, '..', 'package.json');
// 中断保护：改写前把原文落盘成备份文件；进程被强杀（finally 不执行）时，
// 下一次运行开头会先用备份还原，避免 package.json 永久停留在临时版本/临时输出目录。
const backupPath = pkgPath + '.pkg-semver-backup';
if (fs.existsSync(backupPath)) {
  fs.copyFileSync(backupPath, pkgPath);
  fs.unlinkSync(backupPath);
  console.log('[pkg-semver] 检测到上次未完成的改写，已恢复 package.json');
}
const orig = fs.readFileSync(pkgPath, 'utf8');
const pkg = JSON.parse(orig);
let modified = false;

const m = /^(\d{8}-\d{3})$/.exec(pkg.version || '');
if (m) {
  pkg.version = `0.1.0-${pkg.version}`;
  modified = true;
  console.log(`[pkg-semver] 临时版本 -> ${pkg.version}`);
}

const repoRoot = path.join(__dirname, '..', '..');
const canonicalOutput = path.join(repoRoot, 'release', 'desktop-installer');
const envOutput = process.env.EMO_REPLY_EB_OUTPUT;
// 规范目录可能被 Defender 永久持锁（win-unpacked\resources\app.asar），任何针对它的
// EnsureEmptyDir 都会失败。无显式输出目录时改用「时间戳+PID」的唯一空目录构建，
// 成功后再把安装包回写规范目录；显式指定则由调用方负责搬运，此处不回写。
let outputOverride = envOutput;
let copyBack = false;
if (!outputOverride) {
  const d = new Date();
  const stamp = [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
    String(d.getHours()).padStart(2, '0'),
    String(d.getMinutes()).padStart(2, '0'),
    String(d.getSeconds()).padStart(2, '0'),
  ].join('');
  outputOverride = path.join(repoRoot, 'release', `desktop-installer-${stamp}-${process.pid}`);
  copyBack = true;
}
pkg.build = pkg.build || {};
pkg.build.directories = pkg.build.directories || {};
pkg.build.directories.output = outputOverride;
modified = true;
console.log(`[pkg-semver] 输出目录 -> ${outputOverride}`);
if (copyBack) console.log(`[pkg-semver] 构建后回写安装包 -> ${canonicalOutput}`);

if (modified) {
  fs.writeFileSync(backupPath, orig);
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n');
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
  if (modified) {
    fs.writeFileSync(pkgPath, orig);
    if (fs.existsSync(backupPath)) { fs.unlinkSync(backupPath); }
    console.log('[pkg-semver] 已还原 package.json');
  }
}

// 构建成功后把唯一目录里的安装包回写到规范目录，供 build-publish.ps1 按
// config.json 的 asset.dir=release/desktop-installer 收集。规范目录顶层新建文件
// 不受 app.asar 句柄锁影响；对同名旧文件做有限次重试以容忍防病毒短暂持锁。
if (code === 0 && copyBack) {
  try {
    fs.mkdirSync(canonicalOutput, { recursive: true });
    const exes = fs.readdirSync(outputOverride)
      .filter((f) => /^EmoReply-.*\.exe$/i.test(f))
      .filter((f) => fs.statSync(path.join(outputOverride, f)).isFile());
    let written = 0;
    for (const f of exes) {
      const src = path.join(outputOverride, f);
      const dst = path.join(canonicalOutput, f);
      let ok = false;
      for (let attempt = 1; attempt <= 5 && !ok; attempt++) {
        try {
          fs.copyFileSync(src, dst);
          ok = true;
          written++;
        } catch (e) {
          if (attempt === 5) {
            console.warn(`[pkg-semver] 回写失败（已重试 5 次）：${f} -> ${dst} :: ${e.message}`);
          } else {
            Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 400);
          }
        }
      }
    }
    console.log(`[pkg-semver] 已回写 ${written}/${exes.length} 个安装包到 ${canonicalOutput}`);
  } catch (e) {
    console.warn(`[pkg-semver] 回写安装包到规范目录失败（发布阶段可能找不到产物）：${e.message}`);
  }
}
process.exit(code);
