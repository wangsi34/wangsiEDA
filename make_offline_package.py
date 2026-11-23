#!/usr/bin/env python3
"""把脱敏快照（wangsieda.export_snapshot 的输出）打成可直接双击打开的本地数据包。
用法：python make_offline_package.py <快照目录> <界面目录 ui/> <输出目录>
输出目录中的“打开念思.html”在任何系统上双击即可打开，无需安装任何软件。"""
import base64, json, shutil, sys
from pathlib import Path


def main(snap, ui, out):
    snap, ui, out = Path(snap), Path(ui), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "ui").exists():
        shutil.rmtree(out / "ui")
    shutil.copytree(ui, out / "ui")
    rd = lambda p: json.loads(p.read_text(encoding="utf-8"))
    data = {"routes": {p.stem: rd(p) for p in snap.glob("*.json")},
            "scenes": {p.name[len("scene_"):-len(".json.gz")]: base64.b64encode(p.read_bytes()).decode() for p in snap.glob("scene_*.json.gz")},
            "jobs": {p.stem: rd(p) for p in (snap / "job").glob("*.json")},
            "events": {p.stem: rd(p) for p in (snap / "events").glob("*.json")}}
    (out / "ui" / "offline-data.js").write_text("window.WANGSI_OFFLINE = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
                                                encoding="utf-8")
    html = (ui / "index.html").read_text(encoding="utf-8").replace('"/ui/', '"ui/')
    html = html.replace('<script src="ui/viewer3d.js">', '<script src="ui/offline-data.js"></script>\n<script src="ui/viewer3d.js">')
    (out / "打开念思.html").write_text(html, encoding="utf-8")
    print("package written to", out)


if __name__ == "__main__":
    main(*sys.argv[1:4])
