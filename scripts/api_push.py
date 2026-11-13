# -*- coding: utf-8 -*-
"""Push the 11 local commits via GitHub Git Data API (git:// transport is blocked).

Every blob and tree SHA is verified against the local object database, so the
uploaded chain is content-identical to the local one.
"""
import base64
import json
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(r"D:\code\otherProjects\EmoReply")
REPO = "wangnan05563/EmoReply"
API = "https://api.github.com/repos/" + REPO


def g(args, text=True, timeout=90):
    p = subprocess.run(["git"] + args, cwd=str(ROOT), capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError("git %s: %s" % (args, p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace").strip() if text else p.stdout


def get_token():
    for line in (Path.home() / ".git-credentials").read_text(encoding="utf-8").splitlines():
        if "github.com" in line:
            u = urlparse(line.strip())
            if u.password:
                return unquote(u.password)
    raise RuntimeError("github token not found")


def api(path, token, method="GET", payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "emoreply-push",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:600]
        raise RuntimeError("API %s %s -> %s: %s" % (method, path, e.code, body)) from None


def iso(dt_line):
    m = re.match(r"^(.*) <(.*)> (\d+) ([+-]\d{4})$", dt_line)
    if not m:
        raise RuntimeError("bad identity line: " + dt_line)
    name, email, ts, tz = m.groups()
    sign = 1 if tz[0] == "+" else -1
    off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
    dt = datetime.fromtimestamp(int(ts), timezone(off)).isoformat(timespec="seconds")
    return {"name": name, "email": email, "date": dt}


def main():
    token = get_token()
    local_base = g(["rev-parse", "origin/main"])
    ref = api("/git/ref/heads/main", token)
    remote_sha = ref["object"]["sha"]
    if remote_sha != local_base:
        raise RuntimeError("remote moved: %s != %s" % (remote_sha, local_base))
    shas = g(["rev-list", "--reverse", "origin/main..HEAD"]).split()
    if not shas:
        print("nothing to push")
        return
    prev_commit = remote_sha
    prev_tree = g(["rev-parse", "origin/main^{tree}"])
    api_tree = api("/git/commits/" + remote_sha, token)["tree"]["sha"]
    if api_tree != prev_tree:
        raise RuntimeError("remote tree mismatch: %s != %s" % (api_tree, prev_tree))
    print("base=%s  commits=%d" % (remote_sha[:8], len(shas)), flush=True)

    for idx, s in enumerate(shas):
        raw = g(["cat-file", "commit", s], text=False)
        hdr, _, msg = raw.partition(b"\n\n")
        hl = hdr.decode("utf-8", "replace").splitlines()
        author = next(l[7:] for l in hl if l.startswith("author "))
        committer = next(l[10:] for l in hl if l.startswith("committer "))
        message = msg.decode("utf-8", "replace")
        parent = g(["rev-parse", s + "^"])
        expected_parent = local_base if idx == 0 else shas[idx - 1]
        if parent != expected_parent:
            raise RuntimeError("local parent chain broken at " + s)

        out = g(["diff-tree", "-r", "--no-commit-id", "--name-status", "-z", parent, s], text=False)
        items = [x.decode("utf-8", "replace") for x in out.split(b"\0") if x]
        pairs = []
        i = 0
        while i < len(items):
            st = items[i]
            if st[0] not in "AMTD":
                raise RuntimeError("unsupported status %r in %s" % (st, s))
            pairs.append((st, items[i + 1]))
            i += 2

        entries = []
        for st, path in pairs:
            if st.startswith("D"):
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            blob = g(["cat-file", "blob", s + ":" + path], text=False)
            local_blob = g(["rev-parse", s + ":" + path])
            res = api("/git/blobs", token, "POST", {
                "content": base64.b64encode(blob).decode("ascii"),
                "encoding": "base64",
            })
            if res["sha"] != local_blob:
                raise RuntimeError("blob sha mismatch for %s: %s != %s" % (path, res["sha"], local_blob))
            ls = g(["ls-tree", s, "--", path])
            mode = ls.split()[0] if ls else "100644"
            entries.append({"path": path, "mode": mode, "type": "blob", "sha": res["sha"]})

        tree = api("/git/trees", token, "POST", {"base_tree": prev_tree, "tree": entries})
        local_tree = g(["rev-parse", s + "^{tree}"])
        if tree["sha"] != local_tree:
            raise RuntimeError("tree sha mismatch at %s: %s != %s" % (s[:8], tree["sha"], local_tree))

        new = api("/git/commits", token, "POST", {
            "message": message,
            "tree": tree["sha"],
            "parents": [prev_commit],
            "author": iso(author),
            "committer": iso(committer),
        })
        same = new["sha"] == s
        print("  %s -> %s  tree=OK  sha=%s  %s"
              % (s[:8], new["sha"][:8], "SAME" if same else "REWRITTEN",
                 message.splitlines()[0][:52]), flush=True)
        prev_commit, prev_tree = new["sha"], tree["sha"]

    # optimistic-lock check, then move the remote branch
    now = api("/git/ref/heads/main", token)["object"]["sha"]
    if now != remote_sha:
        raise RuntimeError("remote changed during upload: %s -> %s" % (remote_sha, now))
    upd = api("/git/refs/heads/main", token, "PATCH", {"sha": prev_commit, "force": False})
    print("remote main -> %s" % upd["object"]["sha"], flush=True)

    # keep local branch + tracking ref aligned with the uploaded chain
    g(["update-ref", "refs/heads/main", prev_commit])
    g(["update-ref", "refs/remotes/origin/main", prev_commit])
    print("local HEAD  -> %s" % g(["rev-parse", "HEAD"]), flush=True)
    print("status: %s" % (g(["status", "--porcelain=v1"]) or "(clean)"), flush=True)


if __name__ == "__main__":
    main()
