#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用 GitHub Release 发布脚本（零依赖，仅 Python 标准库）【模板，去硬编码】

把「创建 Release + 附带说明（读 Markdown 文件）+ 上传资产」三步原子化做完。
所有项目相关参数（仓库坐标 / 标签 / 资产路径 / 说明文件 / token 来源）全部来自
配置文件或命令行参数，脚本本身不硬编码任何项目名 / 路径 / 版本 / token。

两种用法：
  1) 直接传资产与说明（最通用）：
     python publish-release.py <资产路径> <说明md路径> [显示名] [repo] [tag]

  2) 读配置（推荐，去硬编码）：
     python publish-release.py --config config.json [--key github_release]
     脚本从 config 的 github_release 块解析 repo / tag / 资产(glob) / 说明(<VER>模板)，
     并自动定位最新资产、按版本推导说明文件路径、缺失时从 git log 生成。

token 读取优先级（见 github_release.token 配置）：
  环境变量 GH_TOKEN  >  本地 .env（gitignored，绝不入库）  >  git credential fill 兜底
说明文件读取：utf-8 -> gbk -> 系统默认 编码回退（兼容中文 Windows 记事本 GBK 保存）。

幂等：Release 已存在则复用；资产已存在则跳过上传。网络抖动自动重试。

完整规则：SKILL.md 规则 16 + references/retrospective.md 维度 16 + config/traps.md 陷阱 62-66。
"""
import argparse
import fnmatch
import json
import locale
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("GH_API_BASE", "https://api.github.com").rstrip("/")
# 项目根取脚本目录的上一级；项目内路径一律相对它解析，不依赖当前工作目录。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPO = (os.environ.get("GH_REPO") or "").strip()
TAG = (os.environ.get("GH_TAG") or "").strip()
TOKEN = (os.environ.get("GH_TOKEN") or "").strip()
TARGET = (os.environ.get("GH_TARGET") or "").strip()
RELEASE_NAME = (os.environ.get("GH_RELEASE_NAME") or "").strip()
DRAFT = os.environ.get("GH_DRAFT", "0") == "1"
PRERELEASE = os.environ.get("GH_PRERELEASE", "0") == "1"
DRY_RUN = os.environ.get("GH_DRY_RUN", "0") == "1"

VER_PLACEHOLDER = "<VER>"
API_RETRIES = 5
API_RETRY_SLEEP = 10
API_TIMEOUT = 90
RETRY_HTTP_STATUS = (403, 429, 500, 502, 503)
FINAL_HTTP_STATUS = (409, 422)


def _abs(path):
    """把项目内相对路径按项目根解析，绝对路径原样返回。"""
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path))


def _dotenv_candidates():
    """.env 查找顺序去重：当前工作目录 -> 项目根 -> 脚本所在目录。"""
    candidates = []
    for d in (os.getcwd(), PROJECT_ROOT, SCRIPT_DIR):
        p = os.path.join(d, ".env")
        if p not in candidates:
            candidates.append(p)
    return candidates


def _merge_env_line(raw):
    """解析一行 .env 并写入环境；注释、空行、无键名一律跳过，已存在的环境变量不覆盖。"""
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        return
    k, v = line.split("=", 1)
    k, v = k.strip(), v.strip()
    if len(v) >= 2 and v[0] in ("'", '"') and v[-1] == v[0]:
        v = v[1:-1]
    if k and k not in os.environ:
        os.environ[k] = v


def _apply_dotenv_file(p):
    try:
        with open(p, encoding="utf-8") as f:
            for raw in f:
                _merge_env_line(raw)
    except Exception:
        pass


def _load_dotenv():
    """从本地 .env 读取配置（GitHub Token 等），不覆盖已存在的环境变量。

    查找顺序：当前工作目录 -> 项目根 -> 脚本所在目录。.env 必须被 .gitignore 忽略，
    不会入库；入库模板请用 .env.example（占位符）。配套陷阱 66。
    """
    for p in _dotenv_candidates():
        if os.path.isfile(p):
            _apply_dotenv_file(p)


def _read_text(path):
    """读取文本文件，编码回退：utf-8 -> gbk -> 系统默认。

    兼容中文 Windows 下记事本以 GBK 保存的 .md / .env 等文件（陷阱 65）。
    """
    for enc in ("utf-8", "gbk", None):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except UnicodeError:  # UnicodeDecodeError 即其子类
            continue
    with open(path, encoding=locale.getpreferredencoding(False)) as f:
        return f.read()


_load_dotenv()


def sh(cmd):
    """跑一条 shell 命令，返回 stdout（失败返回空串，不抛）。"""
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def detect_repo():
    url = sh(f'git -C "{PROJECT_ROOT}" remote get-url origin')
    url = url.replace("git@github.com:", "https://github.com/").replace(".git", "")
    if "github.com/" in url:
        return url.split("github.com/", 1)[1].strip("/")
    return ""


def detect_tag():
    return sh(f'git -C "{PROJECT_ROOT}" describe --tags --abbrev=0')


def detect_branch():
    b = sh(f'git -C "{PROJECT_ROOT}" rev-parse --abbrev-ref HEAD')
    return b or "master"


def fetch_token():
    """复用 git 凭据库里的 github.com 密码（即 PAT/凭证）。

    必须用 Python 子进程直接喂 stdin，不能依赖 shell 的 `printf | git credential
    fill`（Windows 原生 CMD 没有 printf，.bat 环境下会失败，陷阱 64）。
    """
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=30,
        )
        for line in (proc.stdout or "").splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    return ""


def _api_headers(content_type):
    """按当前 TOKEN 组装请求头（main 里补齐的凭证要对后续请求生效）。"""
    return {
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/vnd.github+json",
        "User-Agent": "publish-release",
        "Content-Type": content_type,
    }


def _api_once(url, body, method):
    req = urllib.request.Request(
        url, data=body, method=method,
        headers=_api_headers("application/json"))
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
        txt = r.read().decode("utf-8")
        return r.status, (json.loads(txt) if txt else None)


def _http_error_result(e):
    """HTTPError 转 (状态码, 解析后的 JSON 或 None, 原始文本)。"""
    payload = e.read().decode("utf-8", "replace")
    try:
        parsed = json.loads(payload) if payload else None
    except Exception:
        parsed = None
    return e.code, parsed, payload


def _api_failure(e):
    """HTTPError 分支归类：返回 (是否值得重试, 立即返回的结果)。"""
    code, parsed, payload = _http_error_result(e)
    if code in FINAL_HTTP_STATUS:
        return False, (code, parsed)
    if code in RETRY_HTTP_STATUS:
        return True, (code, parsed or payload)
    return False, (code, parsed or payload)


def api(path, data=None, method=None, base=None):
    base = base or API_BASE
    body = json.dumps(data).encode("utf-8") if data is not None else None
    method = method or ("POST" if body else "GET")
    last = None
    for _ in range(API_RETRIES):
        try:
            return _api_once(base + path, body, method)
        except urllib.error.HTTPError as e:
            retry, last = _api_failure(e)
            if not retry:
                return last
            time.sleep(API_RETRY_SLEEP)
        except Exception as e:
            last = ("EXC", str(e))
            time.sleep(API_RETRY_SLEEP)
    return last


def get_existing_release_by_tag(tag):
    st, resp = api(f"/repos/{REPO}/releases/tags/{tag}")
    if st == 200 and isinstance(resp, dict):
        return resp
    return None


def _tag_from_package_json(repo_root, tag_value, version_file_path):
    pj = os.path.join(repo_root, version_file_path or "package.json")
    if not os.path.isfile(pj):
        return tag_value
    try:
        return json.load(open(pj, encoding="utf-8")).get("version", tag_value)
    except Exception:
        return tag_value


def _resolve_tag(gh, repo_root):
    """按 tag_source 解析版本号（auto_git_describe / package_json / config_field / custom）。"""
    tag_source = gh.get("tag_source", "auto_git_describe")
    tag_value = gh.get("tag_value") or TAG
    if tag_source == "package_json":
        return _tag_from_package_json(repo_root, tag_value, gh.get("version_file_path"))
    if tag_source in ("config_field", "custom"):
        return tag_value
    return tag_value or detect_tag()


def _newest_matching_asset(full, glob_pat, exclude_patterns):
    """目录内按 glob 匹配、且不在排除名单里的文件，按修改时间倒序。"""
    try:
        files = [os.path.join(full, f) for f in os.listdir(full)
                 if os.path.isfile(os.path.join(full, f))]
    except Exception:
        files = []
    matches = []
    for fp in files:
        name = os.path.basename(fp)
        if not fnmatch.fnmatch(name, glob_pat):
            continue
        if any(x in name for x in exclude_patterns):
            continue
        matches.append(fp)
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches


def _resolve_asset(adir, repo_root, tag):
    """解析待上传资产：dir/glob 里的版本占位符用 tag 填充，取最新一个。"""
    if not adir.get("dir") or not adir.get("glob"):
        return None
    ver = tag or ""
    full = os.path.normpath(os.path.join(repo_root, str(adir["dir"]).replace(VER_PLACEHOLDER, ver)))
    glob_pat = str(adir["glob"]).replace(VER_PLACEHOLDER, ver)
    matches = _newest_matching_asset(full, glob_pat, adir.get("exclude_patterns") or [])
    if matches:
        return matches[0]
    print(f"[warn] 未匹配到资产：{full} \\ {glob_pat}", file=sys.stderr)
    return None


def _auto_generate_notes(ncfg, repo_root, tag, rel):
    """正式说明缺失时，用 git log 兜底生成 .auto.md，返回生成文件路径。"""
    gen = os.path.join(repo_root, rel.replace(".md", ".auto.md"))
    log_args = ncfg.get("auto_generate_git_log", "--no-merges --oneline -20")
    try:
        with open(gen, "w", encoding="utf-8") as f:
            f.write(f"# Release {tag}\n\n")
            f.write("（自动生成的发布说明，来自 git 提交记录；正式发布前请替换为正式说明。）\n\n")
            f.write(sh(f'git -C "{repo_root}" log {log_args}'))
        return gen
    except Exception:
        return None


def _resolve_notes(gh, repo_root, tag):
    """按 notes.path_template 定位说明文件，缺失且允许自动生成时落到 .auto.md。"""
    ncfg = gh.get("notes") or {}
    pt = ncfg.get("path_template")
    if not pt or not tag:
        return None
    rel = pt.replace(VER_PLACEHOLDER, tag)
    notes = os.path.join(repo_root, rel)
    if os.path.isfile(notes) or not ncfg.get("auto_generate", True):
        return notes
    return _auto_generate_notes(ncfg, repo_root, tag, rel) or notes


def _resolve_display(adir, gh, asset, tag):
    """资产显示名优先级：display_name 模板、资产文件名、publish_script。"""
    named = str(adir.get("display_name") or "").replace(VER_PLACEHOLDER, tag or "")
    if named:
        return named
    if asset:
        return os.path.basename(asset)
    return gh.get("publish_script") or "asset"


def resolve_from_config(cfg, key, repo_root):
    """从 config 的 github_release 块解析 repo/tag/asset/notes（去硬编码核心）。"""
    gh = cfg.get(key) or {}
    adir = gh.get("asset", {})
    repo = gh.get("repo") or REPO or detect_repo()
    tag = _resolve_tag(gh, repo_root)
    asset = _resolve_asset(adir, repo_root, tag)
    notes = _resolve_notes(gh, repo_root, tag)
    display = _resolve_display(adir, gh, asset, tag)
    return repo, tag, asset, notes, display


def _build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("asset", nargs="?")
    ap.add_argument("notes", nargs="?")
    ap.add_argument("display_name", nargs="?")
    ap.add_argument("repo", nargs="?")
    ap.add_argument("tag", nargs="?")
    ap.add_argument("--config", help="配置文件路径（JSON）")
    ap.add_argument("--key", default="github_release", help="config 中发布配置所在 key")
    return ap


def _release_root(cfg_path, cfg):
    """配置所在目录 + config.project.root 得到项目根。"""
    base = os.path.dirname(os.path.abspath(cfg_path)) or PROJECT_ROOT
    root_rel = (cfg.get("project") or {}).get("root") or "."
    return os.path.normpath(os.path.join(base, root_rel))


def _from_config(args):
    """--config 分支：解析配置写回全局，返回 (asset, notes, display)。"""
    global REPO, TAG, RELEASE_NAME
    cfg_path = args.config if os.path.isfile(args.config) else _abs(args.config)
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    repo_root = _release_root(cfg_path, cfg)
    r, t, asset, notes, display = resolve_from_config(cfg, args.key, repo_root)
    REPO = r
    TAG = t
    name_tmpl = (cfg.get(args.key) or {}).get("name_template")
    if name_tmpl and not RELEASE_NAME:
        RELEASE_NAME = str(name_tmpl).replace(VER_PLACEHOLDER, t or "")
    if args.asset:
        asset = _abs(args.asset)
    if args.notes:
        notes = _abs(args.notes)
    if args.display_name:
        display = args.display_name
    return asset, notes, display


def _from_args(args):
    """位置参数分支：直接采用命令行传入的资产与说明。"""
    if not args.asset or not args.notes:
        sys.exit("用法: python publish-release.py <资产路径> <说明md路径> [显示名] [repo] [tag]  |  或 --config <config.json>")
    asset = _abs(args.asset)
    notes = _abs(args.notes)
    return asset, notes, args.display_name or os.path.basename(asset)


def _fill_defaults(args):
    """仓库/标签/凭证/分支：环境变量与配置优先，缺失时按 git 兜底探测。"""
    global REPO, TAG, TOKEN, TARGET
    if not REPO:
        REPO = args.repo or detect_repo()
    if not TAG:
        TAG = args.tag or detect_tag()
    if not TOKEN:
        TOKEN = fetch_token()
    if not TARGET:
        TARGET = detect_branch()


def _require_inputs(asset, notes):
    if not REPO:
        sys.exit("无法确定仓库（GH_REPO 未设且 git remote 无 github.com origin）")
    if not TAG:
        sys.exit("无法确定标签（GH_TAG 未设且无 git tag）")
    if not asset or not os.path.isfile(asset):
        sys.exit(f"资产不存在: {asset}")
    if not notes or not os.path.isfile(notes):
        sys.exit(f"说明文件不存在: {notes}")


def _print_dry_run(asset, body, release_name, display):
    size = os.path.getsize(asset)
    print("\n[DRY-RUN] 以下动作【不会】真正执行：")
    print(f"  POST /repos/{REPO}/releases")
    print(f"    tag_name   = {TAG}")
    print(f"    target     = {TARGET}")
    print(f"    name       = {release_name}")
    print(f"    draft      = {DRAFT}  prerelease = {PRERELEASE}")
    print(f"    body 长度  = {len(body)} 字符")
    print(f"  UPLOAD 资产  = {display}  ({size} bytes)")
    print("  （设置 GH_DRY_RUN=0 或清除该变量后重跑即可真正发布）")


def _create_release(body, release_name):
    """创建或复用 Release，返回带 upload_url 的 Release 对象（失败即退出）。"""
    st, resp = api(f"/repos/{REPO}/releases", {
        "tag_name": TAG,
        "target_commitish": TARGET,
        "name": release_name,
        "body": body,
        "draft": DRAFT,
        "prerelease": PRERELEASE,
    })
    if st not in (200, 201) and st not in (409, 422):
        print("create release 失败:", st, json.dumps(resp, ensure_ascii=False)[:800])
        sys.exit(1)

    if isinstance(resp, dict) and "upload_url" in resp:
        return resp
    release = get_existing_release_by_tag(TAG)
    if not release:
        print("无法获取已有 Release，退出。", json.dumps(resp, ensure_ascii=False)[:400])
        sys.exit(1)
    return release


def _upload_asset(release, display, asset):
    """上传资产；同名已存在则跳过，网络抖动重试 4 次。"""
    already = {a.get("name") for a in (release.get("assets") or [])}
    if display in already:
        print("资产已存在，跳过上传:", display)
        return

    upload_url = release["upload_url"].split("{")[0] + "?name=" + urllib.parse.quote(display)
    size = os.path.getsize(asset)
    data = open(asset, "rb").read()
    print("上传中", display, f"{size} bytes ...")
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                upload_url, data=data, method="POST",
                headers={
                    "Authorization": "Bearer " + TOKEN,
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "publish-release",
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(size),
                })
            with urllib.request.urlopen(req, timeout=1800) as r:
                j = json.loads(r.read().decode("utf-8"))
                print("资产上传完成:", r.status, j.get("name"), j.get("size"), j.get("browser_download_url"))
            return
        except Exception as e:
            last = e
            print(f"  上传第 {attempt + 1} 次失败: {str(e)[:200]}")
            time.sleep(15)
    sys.exit(f"资产上传失败: {last}")


def main():
    args = _build_parser().parse_args()

    if args.config:
        asset, notes, display = _from_config(args)
    else:
        asset, notes, display = _from_args(args)

    _fill_defaults(args)
    _require_inputs(asset, notes)

    print(f"仓库={REPO}  标签={TAG}  目标={TARGET}  draft={DRAFT} prerelease={PRERELEASE}")
    print(f"资产={asset}\n说明={notes}")

    body = _read_text(notes)
    release_name = RELEASE_NAME or display

    if DRY_RUN:
        _print_dry_run(asset, body, release_name, display)
        return

    if len(TOKEN) < int(os.environ.get("GH_TOKEN_MIN_LEN", "20")):
        sys.exit("GH_TOKEN 缺失（环境变量 / .env / git 凭据库均无 github.com 凭证）")

    release = _create_release(body, release_name)
    print("release id:", release.get("id"), "html:", release.get("html_url"))
    _upload_asset(release, display, asset)


if __name__ == "__main__":
    main()
