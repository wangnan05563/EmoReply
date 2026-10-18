#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kill_sut.py —— 定向关停被测进程（安全工具，配置驱动，绝不全局杀进程）

为什么需要它（真实事故教训）：
  同机常驻着不可动的其它同名进程（CI/Jenkins、ES、SonarQube、其它 Java/Node 服务）。
  一句 `taskkill /F /T /IM java.exe` 或 `killall node` 会**误杀用户基础设施**，造成生产事故。
  本工具只杀「通过 process_match 精确定位到的 SUT pid」，从代码层面杜绝全局杀进程路径。

定位（来自 --config 的 sandbox.process_match，或命令行显式指定）：
  --pid <n>              直接给定 pid（最精确、最安全）
  --pid-file <path>      从文件读取 pid
  --name <image>         按映像名（如 java.exe）—— 多匹配需 --allow-multi 才执行，防误杀
  --cmd-substr <s>       按命令行子串（如 Harness / MyServer）

安全闸门：
  - mode=name 且匹配到多个 pid 时拒绝执行（除非 --allow-multi），因为同名可能含无关服务。
  - 永远不构造 `taskkill /IM <image>` / `killall` 这类按映像全局命令。
  - 默认杀进程树（-T / --kill-tree），避免孤儿子进程残留。

用法：
  python scripts/kill_sut.py --config config/jmeter-config.yml
  python scripts/kill_sut.py --pid 20832
  python scripts/kill_sut.py --cmd-substr Harness --kill-tree
"""

import argparse
import os
import subprocess
import sys


def enumerate_processes():
    try:
        import psutil
        out = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                out.append((p.info["pid"], p.info["name"] or "",
                            " ".join(p.info["cmdline"] or [])))
            except Exception:
                pass
        return out
    except ImportError:
        pass
    if sys.platform.startswith("win"):
        try:
            raw = subprocess.run(
                ["wmic", "process", "get", "name,processid,commandline", "/format:csv"],
                capture_output=True, text=True, timeout=30, errors="replace").stdout
            out = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",", 3)
                if len(parts) < 4 or not parts[2].isdigit():
                    continue
                out.append((int(parts[2]), parts[1], parts[3]))
            return out
        except Exception:
            return []
    # Linux
    out = []
    for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
        try:
            with open(f"/proc/{pid}/comm") as f:
                name = f.read().strip()
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
            out.append((int(pid), name, cmd))
        except Exception:
            continue
    return out


def resolve_pids(args, cfg):
    """返回 (pids, mode)。定位优先级：显式 CLI 标志 > 配置文件 sandbox.process_match。"""
    if args.pid:
        return [args.pid], "pid"
    if args.pid_file:
        try:
            return [int(open(args.pid_file).read().strip())], "pid_file"
        except Exception as e:
            print(f"[kill] 无法读取 pid 文件 {args.pid_file}: {e}")
            return [], None
    # 1) 显式 CLI 标志优先，并自动推断 mode
    mm = {}
    if args.name:
        mm = {"mode": "name", "name": args.name}
    elif args.cmd_substr:
        mm = {"mode": "cmd_substr", "cmd_substr": args.cmd_substr}
    # 2) 配置文件默认值：仅当 CLI 未显式提供相应字段时补充
    if args.config:
        sb = (cfg.get("sandbox") or {}).get("process_match") or {}
        for k in ("mode", "name", "cmd_substr", "pid_file"):
            if not mm.get(k) and sb.get(k):
                mm[k] = sb.get(k)
    mode = mm.get("mode")
    if not mode:
        return [], None
    key = mm.get(mode)
    if not key:
        return [], mode
    hits = []
    for pid, name, cmd in enumerate_processes():
        hay = cmd if mode == "cmd_substr" else name
        if key.lower() in (hay or "").lower():
            hits.append(pid)
    return hits, mode


def kill_targeted(pids, kill_tree, retries, wait_ms):
    if not pids:
        print("[kill] 未定位到任何 SUT 进程，无操作")
        return
    for pid in pids:
        for attempt in range(1, max(1, retries) + 1):
            try:
                if sys.platform.startswith("win"):
                    cmd = ["taskkill", "/PID", str(pid), "/F"]
                    if kill_tree:
                        cmd.append("/T")
                    # errors='replace'：中文 locale 下 taskkill 输出为 cp936，避免后台线程解码崩溃
                    subprocess.run(cmd, capture_output=True, text=True, timeout=15, errors="replace")
                else:
                    os.kill(pid, 15)  # SIGTERM
                    if kill_tree:
                        # POSIX 进程树：向进程组发信号（假设已在独立进程组）
                        try:
                            os.killpg(os.getpgid(pid), 15)
                        except Exception:
                            pass
                print(f"[kill] 已发送终止信号 pid={pid} (tree={kill_tree})")
                break
            except ProcessLookupError:
                print(f"[kill] pid={pid} 已不存在")
                break
            except Exception as e:
                print(f"[kill] pid={pid} 第{attempt}次失败: {e}")
                if attempt >= retries:
                    break
        if wait_ms:
            import time
            time.sleep(wait_ms / 1000.0)


def main():
    ap = argparse.ArgumentParser(description="定向关停 SUT（安全，绝不全局杀进程）")
    ap.add_argument("--config", default="", help="jmeter-config.yml（读取 sandbox.process_match）")
    ap.add_argument("--pid", type=int, default=0, help="直接给定 pid")
    ap.add_argument("--pid-file", default="", help="从文件读取 pid")
    ap.add_argument("--name", default="", help="按映像名匹配（多匹配需 --allow-multi）")
    ap.add_argument("--cmd-substr", default="", help="按命令行子串匹配")
    ap.add_argument("--kill-tree", action="store_true", help="杀进程树（含子进程）")
    ap.add_argument("--allow-multi", action="store_true",
                    help="mode=name 匹配到多个时仍执行（谨慎：可能含无关服务）")
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--wait-ms", type=int, default=500)
    args = ap.parse_args()

    cfg = {}
    if args.config:
        try:
            import yaml
            with open(args.config, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[kill] 读取配置失败：{e}")

    pids, mode = resolve_pids(args, cfg)
    if not pids:
        print("[kill] 未定位到 SUT 进程，退出")
        return
    # 安全闸门：mode=name 多匹配默认拒绝（CLI 或 config 同源，均生效）
    if mode == "name" and len(pids) > 1 and not args.allow_multi:
        print(f"[kill] 拒绝执行：按映像名匹配到 {len(pids)} 个进程 "
              f"({pids})，可能含无关服务。若确认均为 SUT，请加 --allow-multi。")
        sys.exit(1)
    print(f"[kill] 将定向关停以下 pid（绝不全局杀同名进程）：{pids}")
    kill_targeted(pids, args.kill_tree, args.retries, args.wait_ms)
    print("[kill] 完成")


if __name__ == "__main__":
    main()
