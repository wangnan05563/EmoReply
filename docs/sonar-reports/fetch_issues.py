import base64
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("SONAR_HOST_URL", "http://localhost:9000")
TOKEN = os.environ.get("SONAR_TOKEN", "")
PROJECT = os.environ.get("SONAR_PROJECT_KEY", "EmoReply")


def api(path, **params):
    if not TOKEN:
        raise SystemExit("缺少环境变量 SONAR_TOKEN（不要写进代码或配置文件）")
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    auth = base64.b64encode((TOKEN + ":").encode("ascii")).decode("ascii")
    req.add_header("Authorization", "Basic " + auth)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print("HTTP %s for %s\n%s" % (exc.code, url, exc.read().decode("utf-8", "replace")))
        raise


def fetch_all_issues():
    issues = []
    page = 1
    while True:
        data = api(
            "/api/issues/search",
            componentKeys=PROJECT,
            resolved="false",
            ps=500,
            p=page,
        )
        issues.extend(data.get("issues", []))
        total = data.get("total", 0)
        if len(issues) >= total or not data.get("issues"):
            break
        page += 1
    return issues


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "issues-baseline.json"
    issues = fetch_all_issues()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(issues, fh, ensure_ascii=False, indent=2)

    print("total_open=%d" % len(issues))
    for key in ("severity", "type"):
        counts = {}
        for i in issues:
            counts[i.get(key, "?")] = counts.get(i.get(key, "?"), 0) + 1
        print(key, json.dumps(counts, ensure_ascii=False))
    rules = {}
    for i in issues:
        rules[i["rule"]] = rules.get(i["rule"], 0) + 1
    print("rules:")
    for rule, cnt in sorted(rules.items(), key=lambda kv: -kv[1]):
        print("  %-18s %d" % (rule, cnt))
    comps = {}
    for i in issues:
        comps[i["component"]] = comps.get(i["component"], 0) + 1
    print("components:")
    for comp, cnt in sorted(comps.items(), key=lambda kv: -kv[1]):
        print("  %-60s %d" % (comp, cnt))


if __name__ == "__main__":
    main()
