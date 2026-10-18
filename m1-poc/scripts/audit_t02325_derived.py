"""T02325 清点：aimsg三期 全部任务的派生/归并/归档对账（紧凑表）。

REST 旁路 GET /api/tasks?projectId= ：完整未截断 JSON。⚠ `derived_from` 存的是**父单
编号字符串**（"T02222"）而不是父单 UUID——按编号回查。
"""
import json
import urllib.request
from pathlib import Path

PROJECT = "3689e7c0-f3a2-404a-bdbf-41262fd0fc89"


def rest_get(path, token, base):
    req = urllib.request.Request(base + path,
                                 headers={"X-Access-Token": token,
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body["tasks"] if isinstance(body, dict) else body


def main():
    cfg = json.loads((Path.home() / ".workbuddy" / "mcp.json").read_text(encoding="utf-8"))
    mtask = cfg["mcpServers"]["mtask"]
    base, token = mtask["url"].replace("/api/mcp", ""), next(iter(mtask["headers"].values()))
    rows = rest_get(f"/api/tasks?projectId={PROJECT}&limit=500", token, base)
    by_no = {t["task_no"]: t for t in rows}
    out = []
    for t in sorted(rows, key=lambda x: x["task_no"]):
        d = t.get("derived_from")
        rec = {"task_no": t["task_no"], "status": t["status"],
               "verified": bool(t.get("verified")), "archived": bool(t.get("archived")),
               "result_len": len(t.get("handle_result") or ""),
               "desc_len": len(t.get("description") or ""),
               "derived_from": d, "title": t["title"][:52]}
        if d:
            p = by_no.get(d)
            rec["parent_found"] = bool(p)
            rec["parent_archived"] = bool(p and p.get("archived"))
            rec["merged_sections"] = ((p or {}).get("handle_result") or "").count(
                f"【派生单 {t['task_no']}")
            rec["parent_refs_title"] = any(t["task_no"] in ((p or {}).get("handle_result") or "")
                                           for _ in [0])
        out.append(rec)
    Path("../tmp/t02325_inventory.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(rows)} 条")
    for r in out:
        if r["derived_from"]:
            print(f"{r['task_no']} st={r['status']} ver={int(r['verified'])} res={r['result_len']}"
                  f" 父={r['derived_from']}(在库={int(r['parent_found'])},"
                  f"arch={int(r['parent_archived'])}) 归并节={r['merged_sections']} | {r['title']}")
    print("\n父单 handle_result 里的派生节标记：")
    for no, t in by_no.items():
        hr = t.get("handle_result") or ""
        if "派生单" in hr:
            print(f"  {no} ({len(hr)}字): " + ", ".join(
                s for s in sorted({hr[i + 1:][:6] for i, _ in enumerate(hr)
                                   if hr.startswith("派生单 ", i)})))


if __name__ == "__main__":
    main()
