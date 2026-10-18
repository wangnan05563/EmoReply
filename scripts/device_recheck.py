#!/usr/bin/env python3
"""T01339 device recheck helper.

Phase 1: automated environment prep (install / grants / accessibility / IME).
Phase 2: guided walkthrough - prints what to verify per item, waits for
         Enter, then captures a screenshot as evidence into tmp/recheck/.

Usage (device must be online, not in use by a human):
    python scripts/device_recheck.py            # full run
    python scripts/device_recheck.py --prep     # Phase 1 only
    python scripts/device_recheck.py --list     # show checklist items only

Windows: PS 5.1 friendly output kept ASCII-prefixed; Chinese text is fine
because this is Python, not a .ps1 file.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ADB_CANDIDATES = [
    r"D:\android\Sdk\platform-tools\adb.exe",
    r"D:\android\platform-tools\adb.exe",
]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APK = PROJECT_ROOT / "release" / "build" / "app" / "outputs" / "apk" / "debug" / "app-debug.apk"
SHOT_DIR = PROJECT_ROOT / "tmp" / "recheck"
REMOTE_SHOT = "/sdcard/emoreply_recheck.png"
PKG = "com.emoreply.poc"
READER_SVC = f"{PKG}/com.emoreply.poc.capture.EmoReplyReaderService"
IME_ID = f"{PKG}/.ime.EmoReplyImeService"
EXPECTED_VERSION = "246301"

# (id, title, what-to-verify)  -- full details in docs/t01339-device-recheck-checklist.md
ITEMS = [
    ("A1-t01164-icon", "启动器/设置/最近任务图标", "圆形遮罩下气泡尾完整、爱心/星光可见"),
    ("A2-t01292-keymask", "设置页 API Key", "保存后显示掩码；眼睛图标可明文核对"),
    ("A3-t01145-readscreen", "群聊点👁", "汇总读屏内容；聊天回到底部；无翻动(1屏)"),
    ("A4-t01147-compact", "精简模式弹键盘", "图标行下直接是候选区；开关持久化"),
    ("B1-t01343-side", "右侧我方发言", "侧别标注为「我」，不误判对方"),
    ("B2-t01343-range", "读屏范围滑杆 2/3", "上滑次数=范围-1；进度提示出现"),
    ("B3-t01343-time", "含时间分隔的记录", "时间行转「时间：」标记；旧话题不混入"),
    ("B4-t01334-imagetext", "微信图片消息(图内文字)", "图内文字不入上下文(节点树+OCR两路)"),
    ("B5-t01312-progress", "多屏读屏过程", "IME 显示「读屏 i/N」进度"),
    ("B6-t01135-collapse", "读屏触发瞬间", "IME 先收起再读屏再恢复"),
    ("B7-t01152-recovery", "kill 进程后点👁", "服务自动恢复并完成读屏"),
    ("B8-t01138-dualcol", "多条候选输入区", "展示态双栏均分、编辑态整宽"),
    ("C1-t01348-tone", "AUTO 开+领导/朋友 persona", "领导克制(35)、朋友活泼(75)"),
    ("C2-t01348-toggle", "AUTO 开关联动", "开=滑杆禁用「自动」；拖滑杆退出且手动值不被覆盖"),
    ("C3-t01348-migrate", "老装升级 persona.db", "v1→v2 迁移无损，旧关系标注还在"),
    ("C4-t01349-multi", "多风格开关开", "rationale 出现克制/中性/活泼前缀"),
    ("C5-t01349-latency", "延迟实测(开关各≥10次)", "记录 P50/P95 回填 T01349"),
    ("C6-t01350-adapt", "同维度忽略≥10次(<30%)", "后续生成降一档；第9次不生效；开关关不降"),
    ("C7-t01350-notif", "通知链路反馈", "仅全局统计，不报错"),
    ("D1-t01336-notificon", "系统通知小图标", "纯白单色剪影，无灰边"),
    ("D2-t01344-presets", "端侧模型预设页", "列表/路径自动填/下载页跳转"),
    ("D3-t01335-vercode", "versionCode 链路", "本机 246301；远程更高可检出(需服务端)"),
]


def find_adb() -> str:
    import shutil
    found = shutil.which("adb")
    if found:
        return found
    for p in ADB_CANDIDATES:
        if Path(p).exists():
            return p
    sys.exit("[FAIL] adb.exe not found; edit ADB_CANDIDATES")


def sh(adb: str, *args: str, capture: bool = False) -> str:
    cmd = [adb, *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if capture:
        return out.strip()
    print(f"  $ adb {' '.join(args)}")
    if out.strip():
        print("    " + out.strip().replace("\n", "\n    ")[:500])
    return out


def phase1(adb: str) -> bool:
    print("== Phase 1: environment prep ==")
    devs = sh(adb, "devices", capture=True).splitlines()[1:]
    online = [d for d in devs if d.strip() and "device" in d and "offline" not in d]
    if len(online) != 1:
        print(f"[FAIL] expected exactly 1 device online, got: {online}")
        return False

    if not APK.exists():
        print(f"[FAIL] APK missing: {APK}")
        return False
    print(f"[OK] APK found (mtime={time.strftime('%m-%d %H:%M', time.localtime(APK.stat().st_mtime))})")

    sh(adb, "install", "-r", str(APK))
    sh(adb, "shell", "pm", "grant", PKG, "android.permission.WRITE_SECURE_SETTINGS")
    sh(adb, "shell", "settings", "put", "secure", "enabled_accessibility_services", READER_SVC)
    sh(adb, "shell", "ime", "enable", IME_ID)

    dump = sh(adb, "shell", "dumpsys", "package", PKG, capture=True)
    vc = [ln for ln in dump.splitlines() if "versionCode" in ln]
    print(f"  versionCode lines: {vc[:1]}")
    ok = EXPECTED_VERSION in " ".join(vc)
    print(f"[{'OK' if ok else 'WARN'}] versionCode expected {EXPECTED_VERSION}")

    cur = sh(adb, "shell", "ime", "set", IME_ID, capture=True)
    print(f"[OK] IME set -> {IME_ID} {cur}")
    print("[DONE] Phase 1 complete. Now unlock device, then run Phase 2 walkthrough.")
    return True


def phase2(adb: str) -> None:
    print("== Phase 2: guided verification ==")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for i, (iid, title, expect) in enumerate(ITEMS, 1):
        print(f"\n[{i}/{len(ITEMS)}] {title}  (id={iid})")
        print(f"  预期: {expect}")
        input("  操作真机完成验证后按回车截图存证（n=跳过截图, f=标记失败）: ")
        ans = input("  结果 (p=通过 / f=失败 / s=跳过): ").strip().lower()
        results[iid] = ans
        if ans != "s":
            name = SHOT_DIR / f"{i:02d}-{iid}.png"
            # file-based capture + pull (exec-out binary is fragile on Windows)
            sh(adb, "shell", "screencap", "-p", REMOTE_SHOT)
            sh(adb, "pull", REMOTE_SHOT, str(name))
            sh(adb, "shell", "rm", REMOTE_SHOT)

    print("\n== Summary ==")
    for iid, r in results.items():
        mark = {"p": "PASS", "f": "FAIL", "s": "SKIP"}.get(r, "?")
        print(f"  [{mark}] {iid}")
    fails = [k for k, v in results.items() if v == "f"]
    print(f"\n{len(results) - len(fails)}/{len(results)} passed; fails: {fails or 'none'}")
    print("Evidence screenshots in", SHOT_DIR)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", action="store_true", help="Phase 1 only")
    ap.add_argument("--list", action="store_true", help="print checklist and exit")
    args = ap.parse_args()

    if args.list:
        for i, (iid, title, expect) in enumerate(ITEMS, 1):
            print(f"{i:2d}. [{iid}] {title} - {expect}")
        return

    adb = find_adb()
    print(f"adb: {adb}")
    if not phase1(adb):
        sys.exit(1)
    if not args.prep:
        phase2(adb)


if __name__ == "__main__":
    main()
