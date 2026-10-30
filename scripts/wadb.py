#!/usr/bin/env python3
"""Wireless ADB helper for EmoReply on-device verification.

Problem it solves
-----------------
adb over Wi-Fi drops silently (the TCP transport goes `offline` whenever the
phone restarts its debugging listener, screen state changes, or Wi-Fi roams).
When that happens every adb call fails with `device offline` and testing stops.

This wrapper keeps ONE serial alive:
  * reuses the serial cached in tmp/.adbserial while it answers `device`
  * otherwise re-discovers via `adb mdns services` and reconnects

Usage
-----
    python scripts/wadb.py ensure              # print the live serial (reconnect if needed)
    python scripts/wadb.py shell dumpsys window
    python scripts/wadb.py install path.apk    # long transfer, auto retry x3
    python scripts/wadb.py dump                # save UI hierarchy to tmp/wadb/window.xml
    python scripts/wadb.py shot NAME           # screencap -> tmp/wadb/NAME.png + pull
    python scripts/wadb.py tap X Y
    python scripts/wadb.py text 'hello'        # via input text (ASCII only)
    python scripts/wadb.py logcat TAG -o tmp/wadb/log.txt -n 800

Keystore rule from project memory: before tapping anything, dump the layout and
read the CURRENT bounds; never reuse historical coordinates.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERIAL_FILE = ROOT / "tmp" / ".adbserial"
OUT_DIR = ROOT / "tmp" / "wadb"
REMOTE_SHOT = "/sdcard/wadb_shot.png"
REMOTE_DUMP = "/sdcard/wadb_window.xml"
ADB_EXE = "adb.exe"
MDNS_SERVICE = "_adb-tls-connect._tcp"


def sdk_dir_from_local_properties():
    """工程内唯一事实源：android-app/local.properties 的 sdk.dir（已 gitignore，随机器走）。"""
    lp = ROOT / "android-app" / "local.properties"
    if not lp.is_file():
        return None
    for line in lp.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("sdk.dir="):
            raw = line.split("=", 1)[1].strip()
            return Path(raw.replace("\\\\", "\\").replace("\\:", ":"))
    return None


def adb_candidates():
    """adb 依次取：$ADB -> $ANDROID_HOME/$ANDROID_SDK_ROOT -> 工程 local.properties -> 用户默认 SDK -> PATH。"""
    if os.environ.get("ADB"):
        yield Path(os.environ["ADB"])
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(key):
            yield Path(os.environ[key]) / "platform-tools" / ADB_EXE
    sdk = sdk_dir_from_local_properties()
    if sdk:
        yield sdk / "platform-tools" / ADB_EXE
    if os.environ.get("LOCALAPPDATA"):
        yield Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk" / "platform-tools" / ADB_EXE
    found = shutil.which("adb")
    if found:
        yield Path(found)


def adb_bin() -> str:
    for p in adb_candidates():
        if Path(p).exists():
            return str(p)
    sys.exit("[FAIL] adb.exe not found (set ANDROID_HOME or sdk.dir in android-app/local.properties)")


def _run(args, timeout=30):
    return subprocess.run([adb_bin(), *args], capture_output=True, text=True,
                          timeout=timeout, errors="replace")


def list_devices():
    r = _run(["devices"])
    out = []
    for ln in r.stdout.splitlines()[1:]:
        if not ln.strip():
            continue
        serial, _, state = ln.partition("\t")
        out.append((serial.strip(), state.strip()))
    return out


def serial_alive(serial: str) -> bool:
    try:
        r = subprocess.run([adb_bin(), "-s", serial, "get-state"],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout or "").strip() == "device"
    except Exception:
        return False


def _mdns_rows():
    r = _run(["mdns", "services"], timeout=40)
    rows = []
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and MDNS_SERVICE in parts[1]:
            rows.append((parts[0], parts[-1]))  # (instance, host:port)
    return rows


def _save_serial(serial: str) -> None:
    SERIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERIAL_FILE.write_text(serial, encoding="utf-8")


def _name_serial(instance: str) -> str:
    return f"{instance}.{MDNS_SERVICE}"


def _drop_stale_for(host_port: str) -> None:
    """Remove leftover connections pointing at the previous port of this host."""
    host = host_port.split(":")[0]
    for serial, _state in list_devices():
        if host in serial or serial.endswith("_tcp"):
            _run(["disconnect", serial])


def _try_reuse_name_serial(instance: str) -> str:
    """Prefer the mDNS service-name serial: it survives port rotation."""
    name_serial = _name_serial(instance)
    if serial_alive(name_serial):
        _save_serial(name_serial)
        print(f"  [reuse] {name_serial}")
        return name_serial
    return ""


def _try_connect_host_port(host_port: str, instance: str) -> str:
    _drop_stale_for(host_port)
    r = _run(["connect", host_port], timeout=40)
    print(f"  [connect] {host_port} {(r.stdout or '').strip()}".rstrip())
    time.sleep(1.0)
    for cand in (host_port, _name_serial(instance)):
        if serial_alive(cand):
            _save_serial(cand)
            return cand
    return ""


def discover_and_connect(attempts: int = 3) -> str:
    """Find the phone's current wireless-debug endpoint and connect.

    Preference order:
      1. the mDNS *service name* serial -> adb auto-follows port rotation, so
         this survives the random port changes Android does every session
      2. host:port from mDNS (retry a few times, the list can be stale right
         after the phone rotates the port)
    """
    for _ in range(attempts):
        rows = _mdns_rows()
        if not rows:
            time.sleep(2)
            continue
        instance, host_port = rows[0]
        reused = _try_reuse_name_serial(instance)
        if reused:
            return reused
        connected = _try_connect_host_port(host_port, instance)
        if connected:
            return connected
        time.sleep(3)
    sys.exit("[FAIL] no wireless debugging service reachable - check it is enabled on the phone")


def ensure(force: bool = False) -> str:
    if not force and SERIAL_FILE.exists():
        s = SERIAL_FILE.read_text(encoding="utf-8").strip()
        if s and serial_alive(s):
            return s
    return discover_and_connect()


def adb(args, timeout=60, retries=2):
    serial = ensure()
    for _ in range(retries + 1):
        try:
            r = subprocess.run([adb_bin(), "-s", serial, *args],
                               capture_output=True, text=True, timeout=timeout,
                               errors="replace")
        except subprocess.TimeoutExpired:
            print(f"  [timeout] {' '.join(args)[:80]}")
            return ""
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if "device offline" in out or "device not found" in out or "no device" in out:
            serial = ensure(force=True)
            continue
        return out
    return out


def _build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["ensure", "shell", "raw", "install", "pull",
                                       "dump", "shot", "tap", "text", "key",
                                       "swipe", "logcat", "devices"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("-o", "--out")
    ap.add_argument("--lines", type=int, default=0)
    return ap


def _do_shell(args):
    print(adb(["shell", *args.rest], timeout=90))


def _do_raw(args):
    # pass through verbatim; use `raw -- logcat -c` for dash-prefixed args.
    rest = list(args.rest)
    if rest and rest[0] == "--":
        rest = rest[1:]
    print(adb([*rest], timeout=180))


def _do_install(args):
    apk = args.rest[0]
    for i in range(3):
        out = adb(["install", "-r", "-t", apk], timeout=600)
        print(out)
        if "Success" in out:
            return
        print(f"  [retry {i + 1}] reconnecting...")
        ensure(force=True)
    sys.exit("[FAIL] install retries exhausted")


def _do_pull(args):
    print(adb(["pull", *args.rest], timeout=180))


def _do_dump(args):
    adb(["shell", "uiautomator", "dump", REMOTE_DUMP], timeout=60)
    local = args.out or str(OUT_DIR / "window.xml")
    print(adb(["pull", REMOTE_DUMP, local], timeout=60))


def _do_shot(args):
    adb(["shell", "screencap", "-p", REMOTE_SHOT], timeout=60)
    name = args.rest[0] if args.rest else int(time.time())
    local = args.out or str(OUT_DIR / f"{name}.png")
    print(adb(["pull", REMOTE_SHOT, local], timeout=120))
    adb(["shell", "rm", REMOTE_SHOT])


def _do_tap(args):
    x, y = args.rest[0], args.rest[1]
    print(adb(["shell", "input", "tap", x, y], timeout=30))


def _do_text(args):
    # adb input text cannot carry non-ASCII; caller should use clipboard path instead
    print(adb(["shell", "input", "text", args.rest[0]], timeout=30))


def _do_key(args):
    print(adb(["shell", "input", "keyevent", args.rest[0]], timeout=30))


def _do_swipe(args):
    print(adb(["shell", "input", "swipe", *args.rest], timeout=30))


def _do_logcat(args):
    # args.rest: [tagspec...] ; -o writes to file after collecting -n lines
    serial = ensure()
    cmd = [adb_bin(), "-s", serial, "logcat", "-d", "-v", "time", *args.rest]
    if args.lines:
        cmd += ["-t", str(args.lines)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"written {len(out)} chars -> {args.out}")
    else:
        print(out)


ACTION_HANDLERS = {
    "shell": _do_shell,
    "raw": _do_raw,
    "install": _do_install,
    "pull": _do_pull,
    "dump": _do_dump,
    "shot": _do_shot,
    "tap": _do_tap,
    "text": _do_text,
    "key": _do_key,
    "swipe": _do_swipe,
    "logcat": _do_logcat,
}


def main():
    args = _build_parser().parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.action == "ensure":
        print(ensure(force=True))
        return
    if args.action == "devices":
        print("\n".join(f"{s}\t{st}" for s, st in list_devices()))
        return

    handler = ACTION_HANDLERS.get(args.action)
    if handler:
        handler(args)


if __name__ == "__main__":
    main()
