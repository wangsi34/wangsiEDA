"""DEF -> compact layout JSON for the browser canvas (cells, routed wires per layer, PG stripes,
critical-path instances). Pure parsing; no EDA binary needed."""
import json, re
from pathlib import Path


def lef_sizes(lef_path):
    sizes, cur = {}, None
    for line in open(lef_path, encoding="utf-8", errors="replace"):
        t = line.split()
        if not t:
            continue
        if t[0] == "MACRO":
            cur = t[1]
        elif t[0] == "SIZE" and cur:
            sizes[cur] = (float(t[1]), float(t[3]))
        elif t[0] == "END" and len(t) > 1 and t[1] == cur:
            cur = None
    return sizes


def cell_class(inst, macro):
    if macro.startswith("FILLCELL"): return 0
    if macro.startswith("TAPCELL"): return 1
    if inst.startswith(("clkbuf", "clkload")) or macro.startswith("CLKBUF"): return 2
    if macro.startswith(("DFF", "SDFF")): return 3
    if inst.startswith(("rebuffer", "split", "buffer", "hold", "input", "output", "clone", "wire")): return 4
    return 5


CLASSES = ["filler", "tap", "clock", "flop", "repair_buf", "logic"]


def section(text, name):
    m = re.search(r"^%s\s+\d+\s*;(.*?)^END %s" % (name, name), text, re.S | re.M)
    return m.group(1) if m else ""


def parse_paths(body, wires, scale):
    for m in re.finditer(r"(?:ROUTED|NEW)\s+(metal\d+)\s*(\d+)?\s*(?:\+\s*SHAPE\s+\S+\s*)?((?:\(\s*[-\d*]+\s+[-\d*]+(?:\s+[-\d]+)?\s*\)\s*(?:[A-Za-z]\w*\s*)?)+)", body):
        layer, pts = m.group(1), re.findall(r"\(\s*([-\d*]+)\s+([-\d*]+)(?:\s+[-\d]+)?\s*\)", m.group(3))
        px = py = None
        arr = wires.setdefault(layer, [])
        prev = None
        for xs, ys in pts:
            x = px if xs == "*" else int(xs); y = py if ys == "*" else int(ys)
            if prev is not None and (x, y) != prev:
                arr.extend([prev[0] // scale, prev[1] // scale, x // scale, y // scale])
            prev, px, py = (x, y), x, y


def def_to_layout(def_path, lef_path, timing_rpt, out_path):
    text = Path(def_path).read_text(encoding="utf-8", errors="replace")
    dbu = int(re.search(r"UNITS DISTANCE MICRONS (\d+)", text).group(1))
    scale = max(1, dbu // 100)          # store in 0.01 um units
    die = [int(v) // scale for v in re.search(r"DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)", text).groups()]
    sizes = lef_sizes(lef_path)
    cells, names = [], {}
    for ent in section(text, "COMPONENTS").split(";"):
        m = re.search(r"-\s+(\S+)\s+(\S+).*?\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)", ent, re.S)
        if not m:
            continue
        inst, macro, x, y, ori = m.groups()
        w, h = sizes.get(macro, (0.19, 1.4))
        if ori in ("E", "W", "FE", "FW"): w, h = h, w
        names[inst.replace("\\", "")] = len(cells)
        cells.append([int(x) // scale, int(y) // scale, int(w * 100), int(h * 100), cell_class(inst, macro)])
    wires, pg = {}, {}
    for ent in section(text, "NETS").split(";"):
        if "ROUTED" in ent:
            parse_paths(ent, wires, scale)
    for ent in section(text, "SPECIALNETS").split(";"):
        if "ROUTED" in ent:
            parse_paths(ent, pg, scale)
    pins = []
    for ent in section(text, "PINS").split(";"):
        m = re.search(r"-\s+(\S+).*?\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)", ent, re.S)
        if m:
            pins.append([m.group(1), int(m.group(2)) // scale, int(m.group(3)) // scale])
    crit = []
    if timing_rpt and Path(timing_rpt).exists():
        rpt = Path(timing_rpt).read_text(errors="replace")
        grp = rpt.split("Path Group: core_clock")
        if len(grp) > 1:
            for inst in re.findall(r"[\^v]\s+(\S+?)/\w+\s+\(", grp[1]):
                if inst in names and names[inst] not in crit:
                    crit.append(names[inst])
    stats = {"cells": len(cells), "wire_segments": sum(len(v) // 4 for v in wires.values()),
             "layers": sorted(wires, key=lambda s: int(s[5:]))}
    out = {"unit": "0.01um", "die": die, "classes": CLASSES, "cells": cells, "wires": wires, "pg": pg,
           "pins": pins, "crit": crit, "stats": stats}
    Path(out_path).write_text(json.dumps(out, separators=(",", ":")))
    return stats
