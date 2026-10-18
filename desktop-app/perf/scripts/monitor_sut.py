#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monitor_sut.py —— 被测系统（SUT）资源监控 sidecar（配置驱动，零业务硬编码）

为什么需要它：
  容量型优化（流式 IO、对象池、缓存、线程模型）的收益无法从「延迟 / 错误率」看出，
  必须采样 SUT 进程的实际资源占用（内存峰值、CPU）。回放 / 压测引擎本身不采集这些，
  因此另起一个独立 sidecar，随压测一起采样，结束后停止，CSV 供报告引用。

定位 SUT 进程的方式（全部来自配置，不写死任何 pid / 映像名 / 路径）：
  resource_monitor.match.mode = pid_file | name | cmd_substr
    - pid_file   ：启动脚本把 SUT pid 写入该文件（最精确）
    - name       ：按进程映像名匹配（如 java.exe）—— 注意：只采样「第一个匹配」并告警多匹配，
                   绝不因此执行全局杀进程
    - cmd_substr ：按命令行子串匹配（如 Harness / MyServer）

指标（跨平台子集，按需启用）：
  rss_mb       常驻内存（working set）
  private_mb   私有内存（Windows PrivateBytes / Linux Pss 近似）—— 容量优化最关心的峰值
  cpu_percent  进程 CPU 占用（双采样 delta）
  handles      (Windows) 句柄数
  threads      线程数

用法：
  python scripts/monitor_sut.py --config config/jmeter-config.yml
  （建议与压测同时启动；Ctrl+C 或 duration_samples 耗尽后停止并落盘 CSV）

依赖：PyYAML（同引擎：pip install pyyaml）；Windows 内存/句柄走 ctypes，Linux 走 /proc，
      不强制 psutil。
"""

import argparse
import csv
import ctypes
import os
import sys
import time


# --------------------------------------------------------------------------
# 配置加载（与引擎一致：PyYAML）
# --------------------------------------------------------------------------
def load_config(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 PyYAML：请先 `pip install pyyaml`")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_match(cfg: dict) -> dict:
    """合并 resource_monitor.match 与 sandbox.process_match，返回有效的匹配规则。"""
    rm = (cfg.get("resource_monitor") or {}).get("match") or {}
    sb = (cfg.get("sandbox") or {}).get("process_match") or {}
    mode = rm.get("mode") or sb.get("mode") or ""
    if not mode:
        return {"mode": ""}
    return {
        "mode": mode,
        "pid_file": rm.get("pid_file") or sb.get("pid_file") or "",
        "name": rm.get("name") or sb.get("name") or "",
        "cmd_substr": rm.get("cmd_substr") or sb.get("cmd_substr") or "",
    }


# --------------------------------------------------------------------------
# 进程枚举（跨平台，优先 psutil，否则平台原生）
# --------------------------------------------------------------------------
def enumerate_processes():
    """返回 [(pid:int, name:str, cmdline:str), ...]"""
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
    # 平台原生兜底
    if sys.platform.startswith("win"):
        return _enum_windows()
    return _enum_linux()


def _enum_windows():
    import subprocess
    out = []
    try:
        raw = subprocess.run(
            ["wmic", "process", "get", "name,processid,commandline", "/format:csv"],
            capture_output=True, text=True, timeout=30, errors="replace").stdout
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 3)
            if len(parts) < 4:
                continue
            node, name, pid, cmd = parts[0], parts[1], parts[2], parts[3]
            if not pid.isdigit():
                continue
            out.append((int(pid), name, cmd))
    except Exception:
        pass
    return out


def _enum_linux():
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


def resolve_pids(mm: dict):
    """根据匹配规则返回 SUT pid 列表（通常 1 个；多匹配仅采样首个并告警）。"""
    mode = mm.get("mode")
    if mode == "pid_file":
        p = mm.get("pid_file")
        if p and os.path.isfile(p):
            try:
                return [int(open(p).read().strip())]
            except Exception:
                return []
        return []
    if mode in ("name", "cmd_substr"):
        key = mm.get("name") if mode == "name" else mm.get("cmd_substr")
        if not key:
            return []
        hits = []
        for pid, name, cmd in enumerate_processes():
            hay = cmd if mode == "cmd_substr" else name
            if key and key.lower() in (hay or "").lower():
                hits.append(pid)
        return hits
    return []


# --------------------------------------------------------------------------
# 指标采样（跨平台）
# --------------------------------------------------------------------------
class Probe:
    """对单个 pid 采样内存 / CPU / 句柄 / 线程。单指标失败不影响其他指标。"""

    def __init__(self, pid: int):
        self.pid = pid
        self._prev_cpu = None
        self._prev_wall = None
        self._win = None
        if sys.platform.startswith("win"):
            self._init_windows()

    def _init_windows(self):
        import ctypes
        from ctypes import wintypes
        self._ct = ctypes
        self._k = ctypes.windll.kernel32
        self._p = ctypes.windll.psapi
        self._QUERY = 0x0400 | 0x0010  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]
        self._PMC = PMC
        self._h = self._k.OpenProcess(self._QUERY, False, self.pid)
        if not self._h:
            raise RuntimeError(f"OpenProcess 失败 pid={self.pid}（进程可能已退出或无权限）")

    def sample(self) -> dict:
        if sys.platform.startswith("win"):
            return self._sample_windows()
        return self._sample_linux()

    def _sample_windows(self):
        d = {"rss_mb": None, "private_mb": None, "cpu_percent": None,
             "handles": None, "threads": None}
        mc = self._PMC()
        mc.cb = ctypes.sizeof(mc)
        if self._p.GetProcessMemoryInfo(self._h, ctypes.byref(mc), ctypes.sizeof(mc)):
            d["rss_mb"] = mc.WorkingSetSize / 1048576.0
            d["private_mb"] = mc.PrivateUsage / 1048576.0
        # CPU（双采样）
        ctime = ctypes.c_ulonglong(); ktime = ctypes.c_ulonglong()
        etime = ctypes.c_ulonglong(); utime = ctypes.c_ulonglong()
        if self._k.GetProcessTimes(self._h, ctypes.byref(etime), ctypes.byref(ctime),
                                   ctypes.byref(ktime), ctypes.byref(utime)):
            cpu_total = (ktime.value + utime.value) / 1e7  # 100ns → s
            now = time.time()
            if self._prev_cpu is not None:
                dt = now - self._prev_wall
                if dt > 0:
                    d["cpu_percent"] = max(0.0, (cpu_total - self._prev_cpu) / dt * 100.0)
            self._prev_cpu = cpu_total
            self._prev_wall = now
        # 句柄数
        hc = ctypes.c_ulong()
        if self._k.GetProcessHandleCount(self._h, ctypes.byref(hc)):
            d["handles"] = hc.value
        return d

    def _sample_linux(self):
        d = {"rss_mb": None, "private_mb": None, "cpu_percent": None,
             "handles": None, "threads": None}
        pid = self.pid
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        d["rss_mb"] = float(line.split()[1]) / 1024.0
                    elif line.startswith("Threads:"):
                        d["threads"] = int(line.split()[1])
            # 私有内存近似：smaps 的 Pss 求和；读不到则 None
            pss = 0.0
            try:
                with open(f"/proc/{pid}/smaps") as f:
                    for line in f:
                        if line.startswith("Pss:"):
                            pss += float(line.split()[1])
                if pss:
                    d["private_mb"] = pss / 1024.0
            except Exception:
                pass
            # CPU
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().split()
            utime = int(fields[13]); stime = int(fields[14])
            clk = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
            cpu_total = (utime + stime) / clk
            now = time.time()
            if self._prev_cpu is not None:
                dt = now - self._prev_wall
                if dt > 0:
                    d["cpu_percent"] = max(0.0, (cpu_total - self._prev_cpu) / dt * 100.0)
            self._prev_cpu = cpu_total
            self._prev_wall = now
        except FileNotFoundError:
            raise RuntimeError(f"进程 {pid} 已退出")
        except Exception:
            pass
        return d

    def close(self):
        if getattr(self, "_win", None) and self._h:
            try:
                self._ct.windll.kernel32.CloseHandle(self._h)
            except Exception:
                pass


# --------------------------------------------------------------------------
# 主循环
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="SUT 资源监控 sidecar（配置驱动）")
    ap.add_argument("--config", default="config/jmeter-config.yml",
                    help="jmeter-config.yml 路径（相对 CWD 或绝对）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rm = cfg.get("resource_monitor") or {}
    if not rm.get("enabled", False):
        print("[monitor] resource_monitor.enabled=false，退出（无操作）")
        return
    mm = _resolve_match(cfg)
    if not mm.get("mode"):
        print("[monitor] 未配置 match.mode（且 sandbox.process_match 也未配置），无法定位 SUT，退出")
        return

    interval = float(rm.get("sample_interval_sec", 2) or 2)
    max_samples = int(rm.get("duration_samples", 0) or 0)
    metrics = rm.get("metrics") or ["rss_mb", "private_mb", "cpu_percent"]
    out_csv = rm.get("output_csv") or "logs/sut_resource.csv"
    alert = rm.get("alert") or {}
    alert_on = bool(alert.get("enabled", False))
    max_rss = float(alert.get("max_rss_mb", 0) or 0)
    on_exceed = alert.get("on_exceed", "warn")

    pids = resolve_pids(mm)
    if not pids:
        print(f"[monitor] 按 {mm} 未找到 SUT 进程，退出")
        return
    if len(pids) > 1:
        print(f"[monitor] 警告：匹配到多个 pid {pids}，仅采样首个 {pids[0]}（绝不因此全局杀进程）")
    pid = pids[0]

    try:
        probe = Probe(pid)
    except Exception as e:
        print(f"[monitor] 无法打开 SUT 进程：{e}")
        return

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    base_cols = ["ts_s", "pid"]
    extra_cols = [m for m in ["rss_mb", "private_mb", "cpu_percent", "handles", "threads"] if m in metrics]
    peak = {c: 0.0 for c in ("rss_mb", "private_mb")}
    peak_exceeded = False

    print(f"[monitor] 开始采样 pid={pid} interval={interval}s metrics={metrics} -> {out_csv}")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(base_cols + extra_cols + ["peak_rss_mb", "peak_private_mb"])
        t0 = time.time()
        n = 0
        try:
            while True:
                try:
                    d = probe.sample()
                except RuntimeError as e:
                    print(f"[monitor] 采样终止：{e}")
                    break
                ts = round(time.time() - t0, 1)
                row = [ts, pid]
                for c in extra_cols:
                    v = d.get(c)
                    row.append(round(v, 1) if isinstance(v, float) else v)
                for c in ("rss_mb", "private_mb"):
                    if d.get(c) is not None:
                        peak[c] = max(peak[c], d[c])
                        if alert_on and max_rss and d[c] > max_rss:
                            peak_exceeded = True
                            print(f"[monitor] 告警：{c}={d[c]:.1f}MB 超过阈值 {max_rss:.0f}MB "
                                  f"（逼近内存上限，如 -Xmx）")
                row += [round(peak["rss_mb"], 1), round(peak["private_mb"], 1)]
                w.writerow(row)
                fh.flush()
                n += 1
                if max_samples and n >= max_samples:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[monitor] 收到中断，停止采样")
        finally:
            probe.close()
    print(f"[monitor] 完成：samples={n} peak_rss_mb={peak['rss_mb']:.1f} "
          f"peak_private_mb={peak['private_mb']:.1f}")
    if peak_exceeded and on_exceed == "fail":
        print(f"[monitor] on_exceed=fail：峰值超过 {max_rss:.0f}MB，以非 0 退出")
        sys.exit(2)


if __name__ == "__main__":
    main()
