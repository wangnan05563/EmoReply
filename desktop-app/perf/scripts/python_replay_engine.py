#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 JMX -> HTTP 回放 + 回归对比引擎（**完全由配置驱动，零业务硬编码**）

本引擎作为 JMeter 不可用时的等价回退（或轻量回归验证的主力引擎），
也可在 JMeter 可用时并行使用。所有参数来自 `config/jmeter-config.yml`，
脚本本身不内嵌任何业务常量（端口、令牌、端点名、缺陷码等均从配置读取）。

依赖：仅标准库（urllib / xml.etree / csv / json / threading）。
      PyYAML 用于读取配置（`pip install pyyaml`）。

用法：
  # 解析自检（不发请求，确认 JMX 被正确解析）
  python python_replay_engine.py --config jmeter-config.yml --selftest --phase reads

  # 跑单个阶段（写入 <output_dir>/<phase>.jsonl）
  python python_replay_engine.py --config jmeter-config.yml --phase reads

  # 跑全部启用阶段 + 生成报告
  python python_replay_engine.py --config jmeter-config.yml --all --report

  # 仅生成报告（基于已产出的 jsonl + 可选基线对比）
  python python_replay_engine.py --config jmeter-config.yml --report
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from xml.etree import ElementTree as ET


# ===========================================================================
# 配置
# ===========================================================================
def load_config(path: str) -> dict:
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 PyYAML：请先 `pip install pyyaml`")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(p: str, base: str) -> str:
    """jmx 等相对路径按配置文件所在目录解析，否则按当前工作目录。"""
    if not p:
        return p
    if os.path.isabs(p):
        return p
    return os.path.join(base, p)


# ===========================================================================
# JMX 解析（通用契约）
# ===========================================================================
def parse_jmx(path: str) -> dict:
    """解析 TestPlan 的 UDV / ThreadGroup / 每个 HTTPSamplerProxy(方法,path,body,紧随的断言)。

    契约（与 JMeter 行为一致）：
      - UDV：TestPlan 下 Argument（name/value）
      - ThreadGroup：num_threads / ramp_time / scheduler? / duration / loops
      - Sampler body：HTTPArgument 中 Argument.name 为空且 use_equals=false => 原始请求体
      - 断言：仅取 Assertion.test_field == 'Assertion.response_code' 的 ResponseAssertion
              test_type==2 => 正则 fullmatch；否则子串包含
    """
    tree = ET.parse(path)
    root = tree.getroot()
    pm = {c: p for p in root.iter() for c in p}

    udv = {}
    for arg in root.findall(".//TestPlan//elementProp[@elementType='Argument']"):
        nm = arg.find("stringProp[@name='Argument.name']")
        vl = arg.find("stringProp[@name='Argument.value']")
        if nm is not None and vl is not None:
            udv[nm.text] = vl.text or ""

    tg = root.find(".//ThreadGroup")
    threads = int(tg.find("stringProp[@name='ThreadGroup.num_threads']").text)
    ramp = int(tg.find("stringProp[@name='ThreadGroup.ramp_time']").text)
    loops_el = tg.find(".//elementProp[@name='ThreadGroup.main_controller']"
                       "/stringProp[@name='LoopController.loops']")
    loops = int(loops_el.text) if loops_el is not None and loops_el.text else -1
    sched_el = tg.find("boolProp[@name='ThreadGroup.scheduler']")
    sched = sched_el is not None and sched_el.text == "true"
    dur_el = tg.find("stringProp[@name='ThreadGroup.duration']")
    duration = int(dur_el.text) if (dur_el is not None and dur_el.text) else 0
    if not sched:
        duration = max(duration, 30)  # 循环模式下给一个下限，避免瞬间跑完

    samplers = []
    for s in root.iter("HTTPSamplerProxy"):
        method = (s.find("stringProp[@name='HTTPSampler.method']").text or "GET").upper()
        raw_path = s.find("stringProp[@name='HTTPSampler.path']").text or ""
        body = _extract_body(s)
        assertions = _extract_assertions(s, pm)
        name = s.get("testname") or raw_path
        samplers.append({"method": method, "path": raw_path, "body": body,
                         "assertions": assertions, "name": name})
    return {"udv": udv, "threads": threads, "ramp": ramp, "loops": loops,
            "scheduler": sched, "duration": duration, "samplers": samplers}


def _extract_body(s) -> "str | None":
    args = s.find("elementProp[@elementType='Arguments']/collectionProp[@name='Arguments.arguments']")
    if args is None:
        return None
    # 原始请求体：Argument.name 为空（解析为 None 或 ""）且 use_equals=false
    for el in args.findall("elementProp[@elementType='HTTPArgument']"):
        nm = el.find("stringProp[@name='Argument.name']")
        vl = el.find("stringProp[@name='Argument.value']")
        if nm is not None and (nm.text is None or nm.text == "") and vl is not None:
            return vl.text
    # 具名参数 => 表单体（罕见）
    parts = []
    for el in args.findall("elementProp[@elementType='HTTPArgument']"):
        nm = el.find("stringProp[@name='Argument.name']")
        vl = el.find("stringProp[@name='Argument.value']")
        if nm is not None and nm.text:
            parts.append(f"{nm.text}={vl.text or ''}")
    return "&".join(parts) if parts else None


def _extract_assertions(s, pm) -> list:
    parent = pm.get(s)
    if parent is None:
        return []
    kids = list(parent)
    idx = kids.index(s)
    if idx + 1 >= len(kids) or kids[idx + 1].tag != "hashTree":
        return []
    out = []
    for ra in kids[idx + 1].findall("ResponseAssertion"):
        field = ra.find("stringProp[@name='Assertion.test_field']")
        if field is None or field.text != "Assertion.response_code":
            continue
        tt = ra.find("intProp[@name='Assertion.test_type']")
        test_type = int(tt.text) if tt is not None and tt.text else 2
        pats = [sp.text for sp in ra.findall(
            "collectionProp[@name='Asserion.test_strings']/stringProp") if sp.text]
        out.append({"test_type": test_type, "patterns": pats})
    return out


def substitute(text, udv, overrides) -> "str | None":
    """变量替换：${VAR} 取 overrides 再取 UDV；${__P(prop,default)} 取 override 或 default。"""
    if not text:
        return text

    def repl_p(m):
        inner = m.group(1)
        parts = inner.split(",", 1)
        prop = parts[0].strip()
        default = parts[1] if len(parts) > 1 else ""
        if prop in overrides:
            return str(overrides[prop])
        return substitute(default, udv, overrides)

    t = re.sub(r"\$\{__P\(([^)]*)\)\}", repl_p, text)

    def repl_v(m):
        name = m.group(1)
        if name in overrides:
            return str(overrides[name])
        return udv.get(name, "")

    return re.sub(r"\$\{([A-Za-z0-9_]+)\}", repl_v, t)


def expected_ok(code: str, assertions: list) -> bool:
    """响应码是否满足所有 response_code 断言（与 JMeter 一致）。无断言则 2xx/3xx 通过。"""
    if not assertions:
        return code.startswith(("2", "3"))
    for a in assertions:
        pats = a["patterns"]
        if a["test_type"] == 2:  # Matches（正则 fullmatch）
            ok = any(re.fullmatch(p, code) for p in pats)
        else:                    # Substring / Contains / Equals
            ok = any(p in code for p in pats)
        if not ok:
            return False
    return True


# ===========================================================================
# 鉴权 / 请求头
# ===========================================================================
def resolve_token(auth: dict) -> str:
    src = auth.get("token_source", "literal")
    if src == "env":
        return os.environ.get(auth.get("token_env", ""), "")
    if src == "file":
        p = auth.get("token_file", "")
        return open(p, encoding="utf-8").read().strip() if p and os.path.exists(p) else ""
    return auth.get("token_literal", "")


def build_auth_headers(target: dict) -> dict:
    auth = target.get("auth", {})
    token = resolve_token(auth)
    headers = dict(target.get("fixed_headers", {}))
    t = auth.get("type", "none")
    if t == "bearer" and token:
        headers[auth.get("header_name", "Authorization")] = f"Bearer {token}"
    elif t == "header" and token:
        headers[auth.get("header_name", "Authorization")] = token
    elif t == "cookie" and token:
        headers["Cookie"] = f"{auth.get('cookie_name', 'token')}={token}"
    return headers


def build_overrides(target: dict) -> dict:
    """用令牌覆盖 JMX 中声明的用户变量（如 ${WEB_TOKEN}）。"""
    auth = target.get("auth", {})
    token = resolve_token(auth)
    return {name: token for name in auth.get("udv_names", []) if token}


# ===========================================================================
# HTTP 回放（正确性契约 —— 见 references/lessons_learned.md）
# ===========================================================================
def send_once(method: str, url: str, body, headers: dict, timeout: float) -> "tuple[str, float]":
    """发送单请求。返回 (状态码字符串, 请求耗时毫秒)；连接失败返回 ('000', 耗时)。

    正确性要点（务必保留）：
      1) JSON 请求体必须设置 Content-Type: application/json，否则服务端把
         原始 bytes 当请求体解析而 422/500。
      2) GET/DELETE 的 k=v 体在 JMeter 中实为 query string，由调用方拼接。
      3) 耗时必须是「本次请求」本地计时，不能用全局阶段起点，否则高并发下
         后发请求会被记成接近 duration 的伪大值。
    """
    req_headers = dict(headers)
    data = None
    if body:
        if isinstance(body, str) and body.strip()[:1] in ("{", "["):
            req_headers.setdefault("Content-Type", "application/json")
            data = body.encode("utf-8")
        else:
            data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in req_headers.items():
        req.add_header(k, v)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = str(resp.status)
    except urllib.error.HTTPError as e:
        code = str(e.code)
    except Exception:
        code = "000"
    el = (time.perf_counter() - t0) * 1000.0
    return code, round(el, 2)


def finalize_url(method: str, url: str, body) -> "tuple[str, object]":
    """GET/DELETE 的 k=v 体 => 拼到 query string；JSON 体保持为请求体。"""
    if method in ("GET", "DELETE") and body and isinstance(body, str) \
            and body.strip() and not body.strip().startswith(("{", "[")):
        sep = "&" if "?" in url else "?"
        return url + sep + body, None
    return url, body


# ===========================================================================
# 阶段执行
# ===========================================================================
def run_phase(cfg: dict, phase: dict, headers: dict, overrides: dict) -> dict:
    cfg_base = os.path.dirname(os.path.abspath(cfg["__config_path__"]))
    info = parse_jmx(resolve_path(phase["jmx"], cfg_base))
    udv = info["udv"]
    threads = phase.get("threads_override") or info["threads"]
    duration = phase.get("duration_override") or info["duration"]
    ramp = info["ramp"]
    base = cfg["target"]["base_url"].rstrip("/")
    timeout = cfg["target"].get("default_timeout_sec", 30)
    stagger = cfg["target"].get("ramp_stagger", True)

    prepared = []
    for sm in info["samplers"]:
        path = substitute(sm["path"], udv, overrides)
        body = substitute(sm["body"], udv, overrides)
        url = base + path
        url, body = finalize_url(sm["method"], url, body)
        prepared.append((sm["method"], url, body, sm["assertions"], sm["name"]))

    records, lock = [], threading.Lock()
    stop = threading.Event()
    deadline = time.perf_counter() + duration

    def worker(wid):
        # 每线程独立 session 在此简化为独立连接；错峰模拟爬坡
        if stagger and ramp > 0:
            time.sleep(min(ramp, threads) * wid / max(threads, 1))
        while not stop.is_set() and time.perf_counter() < deadline:
            for method, url, body, assertions, label in prepared:
                if stop.is_set() or time.perf_counter() >= deadline:
                    break
                code, el = send_once(method, url, body, headers, timeout)
                ok = expected_ok(code, assertions)
                with lock:
                    records.append((label, code, ok, el))

    t0 = time.perf_counter()
    ths = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in ths:
        t.start()
    try:
        while time.perf_counter() < deadline:
            time.sleep(2)
    finally:
        stop.set()
        for t in ths:
            t.join(timeout=5)
    wall = time.perf_counter() - t0

    out_dir = cfg["report"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{phase['name']}.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for label, code, ok, el in records:
            f.write(json.dumps({"label": label, "code": code, "ok": ok, "el": el}) + "\n")

    total = len(records)
    errs = sum(1 for (_, _, ok, _) in records if not ok)
    lats = sorted(r[3] for r in records)
    meta = {
        "phase": phase["name"], "jmx": phase["jmx"], "threads": threads,
        "duration": duration, "wall_sec": round(wall, 1), "samplers": len(prepared),
        "total": total, "errors": errs,
        "error_rate": round(100.0 * errs / total, 3) if total else 0,
        "p50_ms": _pct(lats, 50), "p95_ms": _pct(lats, 95), "p99_ms": _pct(lats, 99),
        "out": out_path,
    }
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


# ===========================================================================
# 沙箱编排（可选）：配置驱动地启动 / 探针 / 回收独立目标实例
# ---------------------------------------------------------------------------
# 仅当 config.sandbox.enabled=true 时生效；否则为 no-op。用于压测独立实例，
# 避免触碰生产。所有参数来自配置，引擎不内嵌任何路径 / 命令 / 业务常量。
#
# 生命周期硬化（应对压测机常见陷阱，详见 references/lessons_learned.md §2 #15~#21）：
#   - 启动前反复清空端口占用进程，避免「孤儿后端串味」（请求打到旧配置的残留实例）
#   - 注入 env_overrides，便于压测期按目标服务需要临时调整运行时开关
#     （如真正关闭单源限流 / 跳过响应压缩钩子——很多框架的 enable:false 会被忽略）
#   - 校验「监听端口的进程是本次新拉起的实例」（兼容启动器双重 fork：spawn pid
#     与真正监听 pid 不同，按进程树归属判定，不严格相等）
#   - 压测前高频突发探测限流是否真关闭（低频是假阴性），可选登录预热吸收冷启动瞬态
# ===========================================================================
def _substitute_tmpl(tmpl: str, cfg: dict) -> "tuple[str, list]":
    """替换启动命令模板中的占位符，返回 (命令字符串, shlex 拆分后的参数列表)。

    支持占位符：
      {python} -> 当前 Python 解释器（带引号以兼容含空格路径）
      {port}   -> sandbox.port
      {pkg}    -> sandbox.pkg
    """
    sb = cfg.get("sandbox", {}) or {}
    cmd = (tmpl
           .replace("{python}", '"' + sys.executable + '"')
           .replace("{port}", str(sb.get("port", 0)))
           .replace("{pkg}", str(sb.get("pkg", ""))))
    return cmd, shlex.split(cmd)


def _http_code(url: str, timeout: float = 3) -> str:
    """发 GET，返回状态码字符串（连接失败 => '000'）。"""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return str(resp.status)
    except urllib.error.HTTPError as e:
        return str(e.code)
    except Exception:
        return "000"


def _http_code_body(url: str, method: str, body: "str | None", timeout: float = 5) -> str:
    """发带 body 的请求（用于登录预热），返回状态码字符串。"""
    data = body.encode("utf-8") if isinstance(body, str) and body else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return str(resp.status)
    except urllib.error.HTTPError as e:
        return str(e.code)
    except Exception:
        return "000"


# ---------- 端口占用查询 / 清空（Windows 优先，通用降级）----------
def _listening_pids(port: int) -> "list[int]":
    """返回当前监听指定 TCP 端口的进程 PID 列表（Windows: netstat -ano）。

    注意：netstat 在非英文 Windows 输出含非 UTF-8 字节，故按字节读取并以
    errors="ignore" 解码，避免 reader 线程解码崩溃导致 stdout 为 None。
    """
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        out = r.stdout.decode("utf-8", "ignore") if r.stdout else ""
    except Exception:
        return []
    pids = []
    needle = ":" + str(port)
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        parts = line.split()
        # 本地地址通常是 parts[1]，PID 是末列
        local = parts[1] if len(parts) >= 2 else ""
        if needle in local:
            try:
                pids.append(int(parts[-1]))
            except ValueError:
                pass
    return pids


def _parent_of(pid: int) -> "int | None":
    """Windows: 取进程父 PID（WMI）。按字节读取避免非 UTF-8 解码崩溃。"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").ParentProcessId"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        v = (r.stdout.decode("utf-8", "ignore") if r.stdout else "").strip()
        return int(v) if v.isdigit() else None
    except Exception:
        return None


def _descends(pid: "int | None", ancestor: int, depth: int = 10) -> bool:
    """pid 的进程树是否向上回溯到 ancestor（兼容启动器双重 fork）。"""
    seen = set()
    cur = pid
    for _ in range(depth):
        if cur is None or cur in seen:
            break
        seen.add(cur)
        if cur == ancestor:
            return True
        cur = _parent_of(cur)
    return False


def _kill_port(port: int, retries: int, wait_ms: int) -> bool:
    """反复杀掉占用端口的进程，直到达成空闲；返回最终是否空闲。"""
    for _ in range(max(1, int(retries))):
        pids = _listening_pids(port)
        if not pids:
            return True
        for pid in set(pids):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except Exception:
                pass
        time.sleep(max(0, wait_ms) / 1000.0)
    return not _listening_pids(port)


def _wait_health(base_url: str, health_path: str, retries: int) -> bool:
    """轮询健康检查路径，非 5xx（含 401 鉴权拒绝）即视为存活。"""
    if not health_path:
        return True
    url = base_url.rstrip("/") + health_path
    for _ in range(max(1, int(retries))):
        code = _http_code(url, timeout=3)
        if code != "000" and not code.startswith("5"):
            return True
        time.sleep(1)
    return False


def _burst_check(base_url: str, path: str, n: int, timeout: float = 3) -> "tuple[int, int, int]":
    """高频突发探测：返回 (ok, err429, other)。

    ★ 关键：用紧挨着的突发而非低频探测来判定限流是否真关闭。
    低频请求（< 阈值/分钟）无论限流开还是关都不触发 429，是假阴性；
    只有突发超阈值才能暴露限流仍生效。
    """
    url = base_url.rstrip("/") + (path or "/health")
    ok = err429 = other = 0
    for _ in range(max(1, int(n))):
        code = _http_code(url, timeout)
        if code == "200":
            ok += 1
        elif code == "429":
            err429 += 1
        else:
            other += 1
    return ok, err429, other


def _login_warmup(base_url: str, url: str, method: str, body_tmpl: str,
                  port: int, max_retries: int) -> "tuple[bool, str]":
    """登录预热：反复重试登录直至 200，吸收冷启动瞬态挂起（如沙箱写盘拦截干扰）。

    返回 (ok, reason)。reason 为 'rate_limited' 表示限流未关（应中止），
    'timeout' 表示始终未成功。
    """
    body = (body_tmpl or "").replace("{port}", str(port))
    target = base_url.rstrip("/") + url
    for i in range(max(1, int(max_retries))):
        code = _http_code_body(target, method, body, timeout=5)
        if code == "200":
            return True, f"ok@{i}"
        if code == "429":
            return False, "rate_limited"
        time.sleep(0.5)
    return False, "timeout"


# ---------------------------------------------------------------------------
# 外部 SUT 进程定位与定向关停（绝不全局杀同名进程）
# ---------------------------------------------------------------------------
def _enum_procs():
    """返回 [(pid, name, cmdline), ...]，跨平台（优先 psutil，否则原生）。"""
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


def _resolve_match_pids(mm: dict):
    """按 process_match 规则返回 SUT pid 列表（通常 1 个）。"""
    mode = (mm or {}).get("mode")
    if mode == "pid_file":
        pf = (mm or {}).get("pid_file")
        if pf and os.path.isfile(pf):
            try:
                return [int(open(pf).read().strip())]
            except Exception:
                return []
        return []
    if mode in ("name", "cmd_substr"):
        key = (mm or {}).get("name" if mode == "name" else "cmd_substr")
        if not key:
            return []
        return [pid for pid, name, cmd in _enum_procs()
                if key.lower() in ((cmd if mode == "cmd_substr" else name) or "").lower()]
    return []


def _kill_targeted(pids, lifecycle: dict):
    """只杀匹配到的 pid（进程树），绝不构造 taskkill /IM <image> 这类全局命令。"""
    if not pids:
        return
    tree = bool((lifecycle or {}).get("kill_tree", True))
    retries = int((lifecycle or {}).get("kill_retries", 5) or 5)
    wait_ms = int((lifecycle or {}).get("kill_wait_ms", 500) or 500)
    for pid in pids:
        for _ in range(max(1, retries)):
            try:
                if sys.platform.startswith("win"):
                    cmd = ["taskkill", "/PID", str(pid), "/F"]
                    if tree:
                        cmd.append("/T")
                    subprocess.run(cmd, capture_output=True, text=True, timeout=15, errors="replace")
                else:
                    os.kill(pid, 15)
                print(f"[sandbox] 已定向终止 SUT pid={pid} (tree={tree})")
                break
            except ProcessLookupError:
                print(f"[sandbox] SUT pid={pid} 已退出")
                break
            except Exception as e:
                print(f"[sandbox] 终止 pid={pid} 失败: {e}")
        if wait_ms:
            time.sleep(wait_ms / 1000.0)


class SandboxRunner:
    """上下文管理器：enabled 时启动独立实例，退出（正常 / 异常）时回收。

    启动前：按 port_kill_retries 清空端口占用（防孤儿后端串味）。
    启动后：env_overrides 注入、健康检查、新实例校验（require_new_instance）、
           压测前高频突发限流探测（preflight）+ 可选登录预热。
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sb = cfg.get("sandbox", {}) or {}
        self.enabled = bool(self.sb.get("enabled"))
        self.proc = None
        self.log = None
        self.proc_match = self.sb.get("process_match") or {}
        self.lifecycle = self.sb.get("lifecycle") or {}

    def _harden_before_spawn(self) -> None:
        port = int(self.sb.get("port", 0) or 0)
        retries = int(self.sb.get("port_kill_retries", 0) or 0)
        wait_ms = int(self.sb.get("port_kill_wait_ms", 500) or 500)
        if port and retries:
            free = _kill_port(port, retries, wait_ms)
            print(f"[sandbox] 端口 {port} 清空{'成功' if free else '未达成（继续，可能冲突）'}")

    def _verify_new_instance(self) -> None:
        if not self.sb.get("require_new_instance", False):
            return
        port = int(self.sb.get("port", 0) or 0)
        if not port or self.proc is None:
            return
        pids = _listening_pids(port)
        if not pids:
            print("[sandbox] 警告：未检测到监听进程，实例可能启动失败")
            return
        if any(_descends(pid, self.proc.pid) for pid in pids):
            print(f"[sandbox] 新实例校验通过：监听 pid {pids} 属于本次拉起（spawn pid {self.proc.pid}，"
                  f"已兼容启动器双重 fork）")
        else:
            print(f"[sandbox] 警告：端口 {port} 的监听进程 {pids} 不属于本次拉起 "
                  f"(spawn pid {self.proc.pid})，可能命中孤儿后端——数据可能失真")

    def _preflight(self, base_url: str) -> None:
        pf = self.sb.get("preflight", {}) or {}
        if not pf.get("enabled", False):
            return
        # 高频突发限流探测
        burst_n = int(pf.get("rate_limit_burst", 0) or 0)
        if burst_n:
            burst_path = pf.get("burst_path") or self.sb.get("health_path", "") or "/health"
            ok, e429, other = _burst_check(base_url, burst_path, burst_n, timeout=3)
            print(f"[preflight] 突发探测 {burst_path} x{burst_n}: ok={ok} 429={e429} other={other}")
            if pf.get("abort_on_rate_limit", False) and e429 > 0:
                raise SystemExit(
                    f"[preflight] FATAL 限流仍未真正关闭（突发出现 {e429} 个 429）——"
                    f"请在 env_overrides 中按目标服务关闭限流后重试，避免产出污染数据")
        # 登录预热
        if pf.get("login_warmup", False):
            ok, reason = _login_warmup(
                base_url, pf.get("login_url", ""), pf.get("login_method", "POST"),
                pf.get("login_body", ""), int(self.sb.get("port", 0) or 0),
                int(pf.get("login_max_retries", 30) or 30))
            print(f"[preflight] 登录预热: ok={ok} reason={reason}")
            if not ok and reason == "rate_limited":
                raise SystemExit("[preflight] FATAL 登录被限流（429）——限流未真正关闭，中止")

    def __enter__(self):
        if not self.enabled:
            return self
        cmd = self.sb.get("startup_cmd", "")
        cwd = self.sb.get("cwd", "")
        if not cmd:
            # 外部 SUT 模式：进程由操作员自行启动（原生 JMeter CLI 驱动场景），
            # 引擎不直接 spawn，但退出时按 process_match 定向关停（绝不全局杀同名进程）。
            if self.proc_match.get("mode"):
                print("[sandbox] 外部 SUT 模式：不启动进程，仅按 process_match 定向关停")
                return self
            print("[sandbox] 未配置 startup_cmd，跳过启动")
            return self
        self._harden_before_spawn()
        cmd_str, args = _substitute_tmpl(cmd, self.cfg)
        os.makedirs(cwd, exist_ok=True) if cwd else None
        log_path = os.path.join(cwd or ".", "sandbox.stdout.log")
        self.log = open(log_path, "wb")
        # env_overrides：通用环境变量注入（零业务硬编码）
        env = {**os.environ, **(self.sb.get("env_overrides") or {})}
        print(f"[sandbox] 启动命令: {cmd_str}")
        if self.sb.get("env_overrides"):
            print(f"[sandbox] 注入环境变量: {list((self.sb.get('env_overrides') or {}).keys())}")
        print(f"[sandbox] 工作目录: {cwd or '(当前)'}")
        print(f"[sandbox] 日志: {log_path}")
        self.proc = subprocess.Popen(args, cwd=cwd or None, env=env,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        base = self.cfg["target"]["base_url"]
        ok = _wait_health(base, self.sb.get("health_path", ""),
                          int(self.sb.get("health_retries", 30)))
        if ok:
            print("[sandbox] 健康检查通过")
        else:
            print("[sandbox] 警告：健康检查未在重试次数内通过，仍继续执行")
        self._verify_new_instance()
        self._preflight(base)
        print("[sandbox] 开始压测")
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except Exception as e:
                print(f"[sandbox] 回收进程出错: {e}")
            finally:
                if self.log:
                    self.log.close()
                print("[sandbox] 已回收独立实例")
        else:
            # 外部 SUT：按 process_match 定向关停（绝不全局杀同名进程；误杀用户基础设施的真实事故教训）
            mm = self.proc_match
            if mm.get("mode"):
                pids = _resolve_match_pids(mm)
                if pids:
                    print(f"[sandbox] 定向关停外部 SUT pid={pids}")
                    _kill_targeted(pids, self.lifecycle)
                else:
                    print("[sandbox] 未定位到外部 SUT 进程，跳过关停")
        return False  # 不吞掉异常


# ===========================================================================
# 缺陷分类 & 聚合
# ===========================================================================
def is_defect(code: str, rules: dict) -> bool:
    """真实服务缺陷：明确码 + 前缀（默认 000/405/5xx）。与断言口径错误区分。"""
    if code in rules.get("defect_codes", []):
        return True
    for p in rules.get("defect_prefixes", []):
        if code.startswith(p):
            return True
    return False


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return round(sorted_vals[k], 2)


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rows.append((d["label"], str(d["code"]), bool(d["ok"]), float(d["el"])))
    return rows


def read_jtl(path):
    """读取 JMeter JTL（带表头或不带）。返回 (label, code, elapsed)。"""
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        sample = f.read(4096)
        f.seek(0)
        has_header = "label" in (sample.splitlines()[0] if sample else "")
        rdr = csv.DictReader(f) if has_header else csv.reader(f)
        for r in rdr:
            if has_header:
                rows.append((r.get("label", ""), str(r.get("responseCode", "")),
                             float(r.get("elapsed", 0) or 0)))
            elif len(r) >= 4:
                rows.append((r[2], str(r[3]), float(r[1] or 0)))
    return rows


def aggregate(rows, jmx_map, rules, percentiles):
    """按 label 聚合：错误率（断言口径）+ 缺陷率（真实缺陷）+ 百分位。"""
    by_label = defaultdict(list)
    for label, code, _ok, el in rows:
        by_label[label].append((code, el))
    agg = {}
    for label, lst in by_label.items():
        asr = jmx_map.get(label, {}).get("assertions", [])
        errs = sum(1 for code, _ in lst if not expected_ok(code, asr))
        defects = sum(1 for code, _ in lst if is_defect(code, rules))
        lats = sorted(e for _, e in lst)
        agg[label] = {
            "total": len(lst), "errors": errs, "defects": defects,
            "error_rate": round(100.0 * errs / len(lst), 3) if lst else 0,
            "defect_rate": round(100.0 * defects / len(lst), 3) if lst else 0,
            "p": {p: _pct(lats, p) for p in percentiles},
            "path": jmx_map.get(label, {}).get("path", ""),
            "method": jmx_map.get(label, {}).get("method", ""),
        }
    return agg


def load_jmx_map(jmx, cfg_base):
    info = parse_jmx(resolve_path(jmx, cfg_base))
    return {sm["name"]: {"assertions": sm["assertions"], "path": sm["path"],
                         "method": sm["method"]} for sm in info["samplers"]}


# ===========================================================================
# 报告
# ===========================================================================
def gen_report(cfg: dict, phases_meta: list) -> str:
    cfg_base = os.path.dirname(os.path.abspath(cfg["__config_path__"]))
    rules = cfg.get("defect_rules", {})
    pcts = cfg["report"].get("percentiles", [50, 95, 99])
    pcol = "/".join(f"p{p}" for p in pcts)
    lines = ["# 性能回归压测报告\n"]
    lines.append(f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')} ｜ 模式：{cfg.get('mode')}\n")

    # ---- 总览 ----
    lines.append("## 1. 总览\n")
    head = "| 阶段 | 请求数 | 断言错误数 | 错误率 | 真实缺陷(按规则) | " + \
           " | ".join(f"p{p}" for p in pcts) + " |"
    lines.append(head)
    lines.append("|------|------:|----------:|------:|----------------:" +
                 "|----:" * len(pcts) + "|")
    for m in phases_meta:
        rows = read_jsonl(m["out"])
        total = len(rows)
        errs = sum(1 for (_, code, ok, _) in rows if not ok)
        defects = sum(1 for (_, code, _, _) in rows if is_defect(code, rules))
        drate = round(100.0 * defects / total, 3) if total else 0
        erate = round(100.0 * errs / total, 3) if total else 0
        lats = sorted(el for (_, _, _, el) in rows)
        pvals = " | ".join(str(_pct(lats, p)) for p in pcts)
        lines.append(f"| {m['phase']} | {total} | {errs} | "
                     f"{erate}% | {defects} ({drate}%) | {pvals} |")
    lines.append("")

    # ---- 关键端点（来自 comparison.key_endpoints）----
    comp = cfg.get("comparison", {})
    if comp.get("key_endpoints"):
        # 用第一个启用阶段的 jmx 构建端点映射（关键端点通常跨阶段一致）
        first = next((p for p in cfg["phases"] if p.get("enabled")), None)
        jmx_map = load_jmx_map(first["jmx"], cfg_base) if first else {}
        lines.append("## 2. 关键端点（修复前后，可选）\n")
        lines.append("| 端点 | 方法 | 当前错误率 | 当前缺陷率 | " +
                     " | ".join(f"p{p}" for p in pcts) + " |")
        lines.append("|------|------|-----------|-----------" +
                     "|----:" * len(pcts) + "|")
        new_rows_by_label = defaultdict(list)
        for m in phases_meta:
            for label, code, ok, el in read_jsonl(m["out"]):
                new_rows_by_label[label].append((code, el))
        for ke in comp["key_endpoints"]:
            name = _find_key(jmx_map, ke.get("method"), ke.get("path_substr"))
            if not name:
                lines.append(f"| {ke.get('title')} | - | (计划中未找到) | - | " +
                             " | ".join("-" for _ in pcts) + " |")
                continue
            lst = new_rows_by_label.get(name, [])
            asr = jmx_map.get(name, {}).get("assertions", [])
            errs = sum(1 for code, _ in lst if not expected_ok(code, asr))
            defects = sum(1 for code, _ in lst if is_defect(code, rules))
            lats = sorted(e for _, e in lst)
            pvals = " | ".join(str(_pct(lats, p)) for p in pcts)
            lines.append(f"| {ke.get('title')} | {ke.get('method')} | "
                         f"{round(100.0*errs/len(lst),3) if lst else 0}% | "
                         f"{round(100.0*defects/len(lst),3) if lst else 0}% | {pvals} |")
        lines.append("")

    # ---- 全端点错误率表 ----
    if cfg["report"].get("endpoint_table", True):
        lines.append("## 3. 全端点错误率 / P95\n")
        lines.append("| 端点 | 方法 | path | 请求数 | 错误率 | 缺陷率 | P95(ms) |")
        lines.append("|------|------|------|------:|------:|------:|------:|")
        all_labels = set()
        for m in phases_meta:
            for label, _, _, _ in read_jsonl(m["out"]):
                all_labels.add(label)
        # 取第一个阶段的 jmx_map 提供 path/method（同名端点通常一致）
        first = next((p for p in cfg["phases"] if p.get("enabled")), None)
        jmx_map = load_jmx_map(first["jmx"], cfg_base) if first else {}
        for label in sorted(all_labels):
            rows = []
            for m in phases_meta:
                rows.extend([(c, e) for lab, c, _, e in read_jsonl(m["out"]) if lab == label])
            if not rows:
                continue
            asr = jmx_map.get(label, {}).get("assertions", [])
            errs = sum(1 for c, _ in rows if not expected_ok(c, asr))
            defects = sum(1 for c, _ in rows if is_defect(c, rules))
            lats = sorted(e for _, e in rows)
            meta = jmx_map.get(label, {})
            lines.append(f"| {label} | {meta.get('method','')} | {meta.get('path','')} | "
                         f"{len(rows)} | {round(100.0*errs/len(rows),3)}% | "
                         f"{round(100.0*defects/len(rows),3)}% | {_pct(lats,95)} |")
        lines.append("")

    # ---- 基线完整性注记 ----
    if comp.get("enabled") and comp.get("baseline_jtl"):
        lines.append("## 4. 基线对比完整性说明\n")
        baseline_rows = read_jtl(comp["baseline_jtl"])
        base_labels = {r[0] for r in baseline_rows}
        cur_labels = all_labels
        missing = cur_labels - base_labels
        if comp.get("baseline_integrity", {}).get("enforce_same_endpoints") and missing:
            lines.append(f"⚠️ 基线 JTL 缺以下当前端点，**与本轮非 apple-to-apple**，"
                         f"逐端点「修复前」数字不可直接由该 JTL 得出：")
            lines.append("`" + "`, `".join(sorted(missing)[:12]) +
                         ("` …" if len(missing) > 12 else "`"))
            if comp.get("baseline_integrity", {}).get("doc_fallback_allowed"):
                lines.append("已改用文档 / 修复验证证据作为「修复前」基线（请在报告中明确标注来源）。")
        else:
            lines.append("基线端点集合与当前一致，可直接做前后对比。")
        lines.append("")

    report = "\n".join(lines) + "\n"
    out_dir = cfg["report"]["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "regression_report.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    return out_path


def _find_key(jmx_map, method, substr):
    for name, v in jmx_map.items():
        if v.get("method") == method and substr in v.get("path", ""):
            return name
    return None


# ===========================================================================
# 自检
# ===========================================================================
def selftest(cfg: dict, phase_name: str) -> None:
    cfg_base = os.path.dirname(os.path.abspath(cfg["__config_path__"]))
    phase = next((p for p in cfg["phases"] if p["name"] == phase_name), None)
    if not phase:
        raise SystemExit(f"未找到阶段 {phase_name}")
    info = parse_jmx(resolve_path(phase["jmx"], cfg_base))
    print(f"[selftest] {phase_name}: threads={info['threads']} ramp={info['ramp']} "
          f"duration={info['duration']} samplers={len(info['samplers'])}")
    udv = info["udv"]
    overrides = build_overrides(cfg["target"])
    for sm in info["samplers"][:3]:
        path = substitute(sm["path"], udv, overrides)
        body = substitute(sm["body"], udv, overrides)
        url = cfg["target"]["base_url"].rstrip("/") + path
        url, body = finalize_url(sm["method"], url, body)
        print(f"  - {sm['method']} {url[:90]} | body={repr((body or '')[:40])} "
              f"| assertions={[(a['test_type'], a['patterns']) for a in sm['assertions']]}")


# ===========================================================================
# 入口
# ===========================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--phase", help="只跑指定阶段（按 name）")
    ap.add_argument("--all", action="store_true", help="跑全部启用阶段")
    ap.add_argument("--report", action="store_true", help="生成报告")
    ap.add_argument("--selftest", action="store_true", help="仅解析自检，不发请求")
    a = ap.parse_args()

    cfg = load_config(a.config)
    cfg["__config_path__"] = os.path.abspath(a.config)

    if a.selftest:
        target = a.phase or (next(p for p in cfg["phases"] if p.get("enabled"))["name"])
        selftest(cfg, target)
        return

    headers = build_auth_headers(cfg["target"])
    overrides = build_overrides(cfg["target"])

    # 确定要运行的阶段（仅当显式 --phase/--all，或无标志默认跑全部启用阶段）
    if a.phase:
        run_phases = [p for p in cfg["phases"] if p["name"] == a.phase and p.get("enabled", True)]
    elif a.all:
        run_phases = [p for p in cfg["phases"] if p.get("enabled", True)]
    elif a.report:
        run_phases = []  # 仅 --report：基于已产出 jsonl，不启动服务、不重跑
    else:
        run_phases = [p for p in cfg["phases"] if p.get("enabled", True)]  # 默认跑全部

    phases_meta = []
    if run_phases:
        # 仅在真正运行阶段时才拉起沙箱（config.sandbox.enabled 时）
        min_lines = int((cfg.get("sandbox") or {}).get("min_output_lines", 0) or 0)
        with SandboxRunner(cfg):
            for ph in run_phases:
                print(f"\n=== running phase: {ph['name']} ===")
                meta = run_phase(cfg, ph, headers, overrides)
                # 数据有效性保护：单阶段产出样本数不足（测试被中止 / 登录瞬态失败），
                # 自动重跑一次，避免污染数据进入聚合。
                if min_lines and meta.get("total", 0) < min_lines:
                    print(f"[sandbox] 警告：{ph['name']} 仅产出 {meta['total']} 样本（< {min_lines}），"
                          f"疑似测试被中止/登录瞬态失败，自动重跑一次")
                    meta = run_phase(cfg, ph, headers, overrides)
                phases_meta.append(meta)
    elif a.report:
        # 仅报告：汇总已有 jsonl（不启动服务）
        for ph in cfg["phases"]:
            if not ph.get("enabled", True):
                continue
            out = os.path.join(cfg["report"]["output_dir"], f"{ph['name']}.jsonl")
            if os.path.exists(out):
                phases_meta.append({"phase": ph["name"], "out": out})

    if a.report and phases_meta:
        out_path = gen_report(cfg, phases_meta)
        print(f"\n[written] {out_path}")
    elif not a.report and not run_phases and not a.phase and not a.all:
        # 无标志且无启用阶段：提示用法
        print("无启用阶段可运行。使用 --all 跑全部，或 --phase <name> 跑单阶段，"
              "或 --report 仅汇总已有 jsonl。")


if __name__ == "__main__":
    main()
