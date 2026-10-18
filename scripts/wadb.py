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
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADB_CANDIDATES = [
    r"D:\android\platform-tools\adb.exe",
    r"D:\android\Sdk\platform-tools\adb.exe",
]
SERIAL_FILE = ROOT / "tmp" / ".adbserial"
OUT_DIR = ROOT / "tmp" / "wadb"
REMOTE_SHOT = "/sdcard/wadb_shot.png"
REMOTE_DUMP = "/sdcard/wadb_window.xml"


def adb_bin() -> str:
    for p in ADB_CANDIDATES:
        if Path(p).exists():
            return p
    sys.exit("[FAIL] adb.exe not found")


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
        if len(parts) >= 3 and "_adb-tls-connect._tcp" in parts[1]:
            rows.append((parts[0], parts[-1]))  # (instance, host:port)
    return rows


def discover_and_connect(attempts: int = 3) -> str:
    """Find the phone's current wireless-debug endpoint and connect.

    Preference order:
      1. the mDNS *service name* serial -> adb auto-follows port rotation, so
         this survives the random port changes Android does every session
      2. host:port from mDNS (retry a few times, the list can be stale right
         after the phone rotates the port)
    """
    for i in range(attempts):
        rows = _mdns_rows()
        if not rows:
            time.sleep(2)
            continue
        instance = rows[0][0]
        name_serial = f"{instance}._adb-tls-connect._tcp"
        if serial_alive(name_serial):
            SERIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            SERIAL_FILE.write_text(name_serial, encoding="utf-8")
            print(f"  [reuse] {name_serial}")
            return name_serial
        host_port = rows[0][1]
        for serial, _state in list_devices():
            if host_port.split(":")[0] in serial or serial.endswith("_tcp"):
                _run(["disconnect", serial])
        r = _run(["connect", host_port], timeout=40)
        print(f"  [connect] {host_port} {(r.stdout or '').strip()}".rstrip())
        time.sleep(1.0)
        for cand in (host_port, name_serial):
            if serial_alive(cand):
                SERIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
                SERIAL_FILE.write_text(cand, encoding="utf-8")
                return cand
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
    for attempt in range(retries + 1):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["ensure", "shell", "raw", "install", "pull",
                                       "dump", "shot", "tap", "text", "key",
                                       "swipe", "logcat", "devices"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("-o", "--out")
    ap.add_argument("--lines", type=int, default=0)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.action == "ensure":
        print(ensure(force=True))
        return
    if args.action == "devices":
        print("\n".join(f"{s}\t{st}" for s, st in list_devices()))
        return

    if args.action == "shell":
        print(adb(["shell", *args.rest], timeout=90))
    elif args.action == "raw":
        # pass through verbatim; use `raw -- logcat -c` for dash-prefixed args.
        rest = list(args.rest)
        if rest and rest[0] == "--":
            rest = rest[1:]
        print(adb([*rest], timeout=180))
    elif args.action == "install":
        apk = args.rest[0]
        for i in range(3):
            out = adb(["install", "-r", "-t", apk], timeout=600)
            print(out)
            if "Success" in out:
                return
            print(f"  [retry {i + 1}] reconnecting...")
            ensure(force=True)
        sys.exit("[FAIL] install retries exhausted")
    elif args.action == "pull":
        print(adb(["pull", *args.rest], timeout=180))
    elif args.action == "dump":
        adb(["shell", "uiautomator", "dump", REMOTE_DUMP], timeout=60)
        local = args.out or str(OUT_DIR / "window.xml")
        print(adb(["pull", REMOTE_DUMP, local], timeout=60))
    elif args.action == "shot":
        adb(["shell", "screencap", "-p", REMOTE_SHOT], timeout=60)
        local = args.out or str(OUT_DIR / f"{args.rest[0] if args.rest else int(time.time())}.png")
        print(adb(["pull", REMOTE_SHOT, local], timeout=120))
        adb(["shell", "rm", REMOTE_SHOT])
    elif args.action == "tap":
        x, y = args.rest[0], args.rest[1]
        print(adb(["shell", "input", "tap", x, y], timeout=30))
    elif args.action == "text":
        # adb input text cannot carry non-ASCII; caller should use clipboard path instead
        print(adb(["shell", "input", "text", args.rest[0]], timeout=30))
    elif args.action == "key":
        print(adb(["shell", "input", "keyevent", args.rest[0]], timeout=30))
    elif args.action == "swipe":
        print(adb(["shell", "input", "swipe", *args.rest], timeout=30))
    elif args.action == "logcat":
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


if __name__ == "__main__":
    main()
