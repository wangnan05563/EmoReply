#!/usr/bin/env python3
"""T01122: Remix Icon SVG -> Android VectorDrawable XML 转换器。

用法: python svg2vd.py <name> <svg_file> <out_dir>
从 Remix Icon SVG 提取 path 数据生成 24dp VectorDrawable（fill 白色，运行时 tint 着色）。
"""
import re
import sys
from pathlib import Path

TEMPLATE = """<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp"
    android:height="24dp"
    android:viewportWidth="24"
    android:viewportHeight="24"
    android:tint="#FFFFFFFF">
{paths}
</vector>
"""

PATH_TPL = '    <path android:fillColor="#FFFFFFFF"{extra} android:pathData="{d}" />'


def convert(svg_path: Path) -> str:
    svg = svg_path.read_text(encoding="utf-8")
    # 提取所有 <path d="..."/>，保留 fill-rule
    paths_out = []
    for m in re.finditer(r"<path\b[^>]*/?>", svg):
        tag = m.group(0)
        d = re.search(r'\bd="([^"]+)"', tag)
        if not d:
            continue
        extra = ""
        if 'fill-rule="evenodd"' in tag:
            extra = '\n        android:fillType="evenOdd"'
        paths_out.append(PATH_TPL.format(extra=extra, d=d.group(1).strip()))
    return TEMPLATE.format(paths="\n".join(paths_out))


def main() -> None:
    name, svg_file, out_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    xml = convert(svg_file)
    out = out_dir / f"ic_{name}.xml"
    out.write_text(xml, encoding="utf-8")
    n = xml.count("<path")
    print(f"ic_{name}.xml  ({n} path)")


if __name__ == "__main__":
    main()
