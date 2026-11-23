"""3D scene extraction for an F2F 3D signoff state (Open3DBench nangate45_3D).

Inputs are the evaluator's own outputs for one final state:
  6_final.def        placement (cells tagged _bottom/_upper) and detailed routing on metal1..metal20 + HBT vias
  violated_paths     setup paths reported by the evaluator STA (worst first)
  5_route_drc.rpt    detailed-route DRC markers (type, bbox, layer)
  hbt_net_merge.tsv  hybrid-bond terminals (x, y, merged net)
  route.guide(.gz)   global-route guides actually submitted (ECO edits live here)

Stack (tech LEF order): metal1..metal10 = bottom die, hbt = bond layer, metal11..metal20 = upper die (face-to-face, metal20 is
the upper die's device side). Coordinates are emitted in 0.01 um integers.
"""
import array, base64, glob, gzip, hashlib, json, os, re, time
from collections import defaultdict
from pathlib import Path
from . import config as C

PLATFORM = C.PLATFORM_DIR
LEF_DIRS = [PLATFORM / "lef_bottom", PLATFORM / "lef_upper", PLATFORM / "lef"]   # tier-specific masters first
LAYERS = [f"metal{i}" for i in range(1, 11)] + ["hbt"] + [f"metal{i}" for i in range(11, 21)]
LIDX = {n: i for i, n in enumerate(LAYERS)}
GRID = (60, 40)
DRC_TYPES = ["Short", "Metal Spacing", "Cut Spacing", "NS Metal", "Cut Short", "Other"]


def opn(p):
    p = str(p)
    if not os.path.exists(p) and os.path.exists(p + ".gz"):
        p += ".gz"
    return gzip.open(p, "rt", errors="replace") if p.endswith(".gz") else open(p, errors="replace")


def lef_sizes():
    sizes = {}
    for f in [f for d in reversed(LEF_DIRS) for f in glob.glob(str(d / "*.lef"))]:
        cur = None
        for line in open(f, errors="replace"):
            t = line.split()
            if not t:
                continue
            if t[0] == "MACRO":
                cur = t[1]
            elif t[0] == "SIZE" and cur:
                sizes[cur] = (float(t[1]), float(t[3]))
    return sizes


def tech_stack():
    """Layer geometry from the technology LEF (HEIGHT = bottom of the layer above its die's substrate, THICKNESS, default WIDTH).
    Face-to-face: the upper die uses the same heights mirrored about the bond layer."""
    info, cur = {}, None
    for d in LEF_DIRS:
        for f in glob.glob(str(d / "*tech*.lef")):
            for line in open(f, errors="replace"):
                t = line.split()
                if not t:
                    continue
                if t[0] == "LAYER" and len(t) > 1:
                    cur = t[1]; info.setdefault(cur, {})
                elif t[0] == "END" and len(t) > 1 and t[1] == cur:
                    cur = None
                elif cur and t[0] in ("HEIGHT", "THICKNESS", "WIDTH") and len(t) == 3 and t[2] == ";":   # skip spacing-table rows
                    info[cur].setdefault(t[0].lower(), float(t[1]))
        if info:
            break
    bot = [info.get(f"metal{i}", {}) for i in range(1, 11)]
    top_bottom_die = max((b.get("height", 0) + b.get("thickness", 0)) for b in bot) if bot and bot[0] else 12.09
    hb = info.get("hbt", {}); hb_t = hb.get("thickness", 2.0)
    upper_dev = 2 * top_bottom_die + hb_t
    stack = []
    for i, name in enumerate(LAYERS):
        g = info.get(name, {})
        h, th, w = g.get("height", i * 0.6), g.get("thickness", 0.3), g.get("width", 0.1)
        if name == "hbt":
            z = top_bottom_die
        elif i > LIDX["hbt"]:
            z = upper_dev - h - th
        else:
            z = h
        stack.append({"name": name, "z": round(z, 4), "t": th, "w": w})
    cut_w = {k: v.get("width", 0.07) for k, v in info.items() if k.startswith("via")}
    return stack, round(upper_dev, 4), cut_w


def tier_of(master):
    return 1 if master.endswith("_upper") or "_upper" in master else 0


def cls_of(inst, master):
    m = master.split("_bottom")[0].split("_upper")[0]
    if m.startswith("fakeram"): return 5
    if m.startswith(("FILLCELL", "TAPCELL")): return 4
    if m.startswith(("DFF", "SDFF")): return 1
    if inst.startswith(("clkbuf", "clkload")) or m.startswith("CLKBUF"): return 3
    if inst.startswith(("rebuffer", "split", "max_cap", "max_length", "hold", "input", "output", "wire")) or m.startswith("BUF"): return 2
    return 0


def run_dirs(workroot):
    """Accept an evaluator work root (one run below it) or a single evaluator run directory."""
    res = glob.glob(f"{workroot}/results/nangate45_3D/*/*/6_final.def") or glob.glob(f"{workroot}/*/results/nangate45_3D/*/*/6_final.def")
    if len(res) != 1:
        raise RuntimeError(f"final state not unique under {workroot}: {res}")
    r = Path(res[0]).parent
    return r, Path(str(r).replace("/results/", "/reports/")), Path(str(r).replace("/results/", "/logs/"))


# ------------------------------------------------------------------ guides
def guide_blocks(path):
    """net -> sha1 of its guide block (streaming)."""
    out, net, h = {}, None, None
    with opn(path) as f:
        for line in f:
            s = line.strip()
            if net is None:
                if s and s != "(":
                    net = s
                continue
            if s == "(":
                h = hashlib.sha1()
            elif s == ")":
                out[net] = h.hexdigest()
                net = None
            elif h is not None:
                h.update(s.encode())
    return out


def guide_boxes(path, nets):
    out, net, keep = defaultdict(list), None, False
    with opn(path) as f:
        for line in f:
            s = line.strip()
            if net is None:
                if s and s != "(":
                    net, keep = s, s in nets
                continue
            if s == ")":
                net = None
            elif keep and s != "(":
                t = s.split()
                if len(t) == 5 and t[4] in LIDX:
                    out[net].append((int(t[0]) // 20, int(t[1]) // 20, int(t[2]) // 20, int(t[3]) // 20, LIDX[t[4]]))
    return out


def guide_diff(base_guide, new_guide):
    a, b = guide_blocks(base_guide), guide_blocks(new_guide)
    changed = sorted(n for n in b if a.get(n) != b[n])
    ga, gb = guide_boxes(base_guide, set(changed)), guide_boxes(new_guide, set(changed))
    diff = {}
    for n in changed:
        sa, sb = set(ga.get(n, [])), set(gb.get(n, []))
        diff[n] = {"added": sorted(sb - sa), "removed": sorted(sa - sb), "kept": len(sa & sb)}
    return {"changed_nets": changed, "nets": diff}


# ------------------------------------------------------------------ violated paths
PIN_RE = re.compile(r"^\s*(-?[\d.]+)\s+(-?[\d.]+)\s+([\^v])\s+(\S+)\s+\((\S+)\)")


def parse_paths(path, limit=400):
    paths, cur = [], None
    with opn(path) as f:
        for line in f:
            if line.startswith("Startpoint:"):
                if cur and cur.get("slack") is not None:
                    paths.append(cur)
                cur = {"start": line.split(":", 1)[1].split("(")[0].strip(), "pins": [], "arrival_done": False}
            elif cur is None:
                continue
            elif line.startswith("Endpoint:"):
                cur["end"] = line.split(":", 1)[1].split("(")[0].strip()
            elif "data arrival time" in line:
                cur["arrival_done"] = True
            elif "slack (" in line:
                cur["slack"] = float(line.split()[0])
            elif not cur["arrival_done"]:
                m = PIN_RE.match(line)
                if m:
                    cur["pins"].append((m.group(4), m.group(5), float(m.group(1))))
    if cur and cur.get("slack") is not None:
        paths.append(cur)
    paths.sort(key=lambda p: p["slack"])
    return paths, paths[:limit]


# ------------------------------------------------------------------ DEF
def parse_def(path, focus_nets, want_pins):
    sizes = lef_sizes()
    comps, cells, pins, nets_out = {}, [], {}, {}
    pin2net = {}
    dens = [[0.0] * (GRID[0] * GRID[1]) for _ in LAYERS]
    routes = [array.array('H') for _ in LAYERS]   # every routed segment, 0.1 um grid, per layer
    vias = [array.array('H') for _ in LAYERS]     # via instances, indexed by the lower routing layer
    n_comp = n_nets = 0
    layer_len = [0.0] * len(LAYERS)
    die = None
    sect, buf = None, ""
    cur_net, cur_conn, cur_wires, cur_layer = None, [], [], None
    gx = gy = 1

    def flush_net():
        if cur_net is not None and cur_net in focus_nets:
            nets_out[cur_net] = {"conn": cur_conn, "wires": cur_wires}

    with opn(path) as f:
        for line in f:
            if sect is None:
                if line.startswith("DIEAREA"):
                    v = [int(x) for x in re.findall(r"-?\d+", line)]
                    die = (v[2], v[3]); gx, gy = die[0] / GRID[0], die[1] / GRID[1]
                elif line.startswith("COMPONENTS"):
                    sect = "C"
                elif line.startswith("PINS"):
                    sect = "P"
                elif line.startswith("NETS"):
                    sect = "N"
                continue
            if line.startswith("END "):
                if sect == "N":
                    flush_net()
                sect, buf = None, ""
                continue
            if sect in ("C", "P"):
                buf += line
                if ";" not in line:
                    continue
                ent, buf = buf, ""
                if sect == "C":
                    m = re.search(r"-\s+(\S+)\s+(\S+).*?\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)", ent, re.S)
                    if not m:
                        continue
                    inst, master, x, y, ori = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), m.group(5)
                    n_comp += 1
                    w, h = sizes.get(master) or sizes.get(master.replace("_bottom", "").replace("_upper", ""), (0.38, 1.4))
                    if ori in ("E", "W", "FE", "FW"):
                        w, h = h, w
                    t, c = tier_of(master), cls_of(inst, master)
                    comps[inst] = (x // 20, y // 20, int(w * 100), int(h * 100), t, master)
                    if c != 4:
                        cells.append([x // 20, y // 20, int(w * 100), int(h * 100), t, c])
                else:
                    m = re.search(r"-\s+(\S+).*?\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)", ent, re.S)
                    if m:
                        pins[m.group(1)] = (int(m.group(2)) // 20, int(m.group(3)) // 20)
                continue
            # NETS: streaming, only focus nets keep geometry; every net feeds the density grid
            s = line.strip()
            if line.startswith("    - ") or s.startswith("- "):
                flush_net()
                n_nets += 1
                cur_net, cur_conn, cur_wires, cur_layer = s.split()[1], [], [], None
            if cur_net is None:
                continue
            for inst, pin in re.findall(r"\(\s*(\S+)\s+(\S+)\s*\)", s):
                if inst in comps or inst == "PIN":
                    if (inst, pin) in want_pins:
                        pin2net[(inst, pin)] = cur_net
                    if cur_net in focus_nets:
                        cur_conn.append((inst, pin))
            for vx, vy, vname in re.findall(r"\(\s*(-?\d+)\s+(-?\d+)(?:\s+-?\d+)?\s*\)\s+([A-Za-z]\w*)\s*$", s):
                mv = re.match(r"via(\d+)_", vname)
                li = LIDX.get(f"metal{mv.group(1)}") if mv else (LIDX["hbt"] if vname.startswith("hb") else None)
                if li is not None:
                    vias[li].extend((min(65535, int(vx) // 200), min(65535, int(vy) // 200)))
            m = re.search(r"(?:ROUTED|NEW)\s+(metal\d+|hbt)\s+(.*)$", s)
            if m and m.group(1) in LIDX:
                li = LIDX[m.group(1)]
                pts = re.findall(r"\(\s*([-\d*]+)\s+([-\d*]+)(?:\s+-?\d+)?\s*\)", m.group(2))
                if len(pts) >= 2:
                    (x1, y1), (x2, y2) = pts[0], pts[1]
                    x1, y1 = int(x1), int(y1)
                    x2 = x1 if x2 == "*" else int(x2)
                    y2 = y1 if y2 == "*" else int(y2)
                    L = (abs(x2 - x1) + abs(y2 - y1)) / 2000.0
                    layer_len[li] += L
                    cx, cy = min(GRID[0] - 1, int((x1 + x2) / 2 / gx)), min(GRID[1] - 1, int((y1 + y2) / 2 / gy))
                    dens[li][cy * GRID[0] + cx] += L
                    routes[li].extend((min(65535, x1 // 200), min(65535, y1 // 200), min(65535, x2 // 200), min(65535, y2 // 200)))
                    if cur_net in focus_nets:
                        cur_wires.append((x1 // 20, y1 // 20, x2 // 20, y2 // 20, li))
    return dict(die=die, comps=comps, cells=cells, pins=pins, nets=nets_out, pin2net=pin2net, dens=dens, layer_len=layer_len, routes=routes, vias=vias, n_comp=n_comp, n_nets=n_nets)


def parse_drc(path):
    out, cur = [], None
    with opn(path) as fh:
        lines = fh.readlines()
    for line in lines:
        if line.startswith("violation type:"):
            cur = line.split(":", 1)[1].strip()
        elif "bbox" in line and cur:
            v = re.findall(r"-?[\d.]+", line.split("=", 1)[1])
            lay = line.rsplit("Layer", 1)[1].strip()
            m = re.match(r"via(\d+)$", lay)
            if m:
                lay = f"metal{m.group(1)}"          # a cut-layer marker is drawn on the metal below it
            elif lay in ("via_10hbt", "via_hbt11"):
                lay = "hbt"
            if len(v) >= 4 and lay in LIDX:
                ti = DRC_TYPES.index(cur) if cur in DRC_TYPES else len(DRC_TYPES) - 1
                out.append([int(float(v[0]) * 100), int(float(v[1]) * 100), int(float(v[2]) * 100), int(float(v[3]) * 100), LIDX[lay], ti])
    return out


def parse_hbt(path):
    out = []
    for i, line in enumerate(opn(path)):
        if i == 0:
            continue
        t = line.rstrip("\n").split("\t")
        if len(t) >= 6:
            out.append([int(t[4]) // 20, int(t[5]) // 20, t[0]])
    return out


def drt_trace(logs):
    """Detailed-route violations per iteration for each die (evaluator logs)."""
    out = {}
    for tier in ("bottom", "upper"):
        p = logs / f"drt_pass_{tier}.log"
        if p.exists():
            out[tier] = [int(v) for v in re.findall(r"Number of violations = (\d+)", p.read_text(errors="replace"))]
    return out


# ------------------------------------------------------------------ scene
def build(label, workroot, focus_extra=(), base_guide=None, meta=None):
    t0 = time.time()
    res, rep, logs = run_dirs(workroot)
    allp, top = parse_paths(res / "violated_paths")
    want = set()
    for p in top[:60]:
        for pin, master, d in p["pins"]:
            if "/" in pin:
                want.add(tuple(pin.rsplit("/", 1)))
    gd = None
    if base_guide:
        gd = guide_diff(base_guide, res / "route.guide")
    focus = set(focus_extra) | set((gd or {}).get("changed_nets", []))
    d = parse_def(res / "6_final.def", focus | {"__placeholder__"}, want)
    # second pass for nets on the worst paths (needs pin -> net from the first pass)
    path_nets = {n for n in d["pin2net"].values()} - focus
    if path_nets:
        d2 = parse_def(res / "6_final.def", path_nets, set())
        d["nets"].update(d2["nets"])
    comps, pins = d["comps"], d["pins"]

    def loc(name):
        if name in comps:
            c = comps[name]
            return [c[0] + c[2] // 2, c[1] + c[3] // 2, c[4]]
        if name in pins:
            return [pins[name][0], pins[name][1], 0]
        return None

    paths = []
    for p in top:
        pts, prev = [], None
        for pin, master, dly in p["pins"]:
            inst = pin.rsplit("/", 1)[0] if "/" in pin else pin
            l = loc(inst)
            if l and l != prev:
                pts.append(l + [round(dly, 3)])
                prev = l
        paths.append({"start": p["start"], "end": p["end"], "slack": p["slack"], "pts": pts,
                      "worst_stage": max(p["pins"], key=lambda x: x[2])[0:3] if p["pins"] else None})
    endpoints = {}
    for p in allp:
        e = p.get("end")
        l = loc(e)
        if l and (e not in endpoints or p["slack"] < endpoints[e][3]):
            endpoints[e] = l + [p["slack"]]
    nets = {}
    for n, v in d["nets"].items():
        conn = [[i, pn] + (loc(i) or [0, 0, 0]) for i, pn in v["conn"]]
        nets[n] = {"wires": v["wires"], "conn": conn, "fanout": max(0, len(conn) - 1),
                   "length_um": round(sum(abs(w[2] - w[0]) + abs(w[3] - w[1]) for w in v["wires"]) / 100.0, 1)}
    metrics = {}
    for f in (logs / "6_report.json",):
        if f.exists():
            m = json.loads(f.read_text())
            metrics = {k: m.get(k) for k in ("finish__timing__setup__tns", "finish__timing__setup__ws", "finish__timing__drv__setup_violation_count",
                                             "finish__timing__drv__hold_violation_count", "finish__power__total", "finish__design__instance__area")}
    drc = parse_drc(rep / "5_route_drc.rpt")
    stk, upz, cutw = tech_stack()
    scene = {"label": label, "unit": "0.01um", "die": [d["die"][0] // 20, d["die"][1] // 20], "layers": LAYERS, "drc_types": DRC_TYPES,
             "cells": d["cells"], "cell_classes": ["logic", "flop", "buffer", "clock", "filler", "macro"],
             "macros": [[c[0], c[1], c[2], c[3], c[4], n] for n, c in comps.items() if c[5].startswith("fakeram")],
             "hbt": parse_hbt(res / "hbt_net_merge.tsv"), "drc": drc, "paths": paths,
             "endpoints": [[k] + v for k, v in endpoints.items()], "nets": nets, "guide_diff": gd,
             "stack": stk, "upper_device_z": upz,
             "vias": {"unit": "0.1um", "encoding": "base64 uint16 LE x,y; index = lower routing layer", "cut_width": cutw,
                      "layers": [base64.b64encode(v.tobytes()).decode() for v in d["vias"]]},
             "verify": verify_block(d, rep, logs),
             "routes": {"unit": "0.1um", "encoding": "base64 uint16 LE x1,y1,x2,y2",
                        "layers": [base64.b64encode(r.tobytes()).decode() for r in d["routes"]]},
             "density": {"grid": GRID, "layers": [[round(x, 1) for x in g] for g in d["dens"]]},
             "layer_length_um": [round(x) for x in d["layer_len"]], "drt_trace": drt_trace(logs), "metrics": metrics,
             "meta": dict(meta or {}, workroot=str(workroot), extracted_at=time.strftime("%F %T"), seconds=round(time.time() - t0, 1),
                          n_violated_paths_reported=len(allp))}
    return scene


def verify_block(d, rep, logs):
    """Independent reference numbers for the consistency check: counts straight from the DEF and the evaluator's own reports."""
    ev = {}
    for f in (logs / "6_report.json", logs / "5_2_route.json"):
        if f.exists():
            try:
                for k, v in json.loads(f.read_text()).items():
                    if re.search(r"wirelength|route__drc|hbt|instance__count|net__count", k, re.I) and isinstance(v, (int, float)):
                        ev[k] = v
            except ValueError:
                pass
    res = Path(str(rep).replace("/reports/", "/results/"))
    for up in list(res.parents)[3:8]:   # the evaluator's own metrics.json (detailed-route wirelength, DRC, HBT)
        mj = up / "reports" / "metrics.json"
        if mj.exists():
            m = json.loads(mj.read_text())
            ev.update({f"metrics.{k}": m[k] for k in ("drt_wirelength_um", "drc", "hbt_count") if isinstance(m.get(k), (int, float))})
            break
    rpt = rep / "5_route_drc.rpt"
    drc_report = None
    if rpt.exists():
        with opn(rpt) as fh:
            drc_report = sum(1 for l in fh if l.startswith("violation type:"))
    return {"def_components": d["n_comp"], "def_nets": d["n_nets"],
            "def_segments": [len(r) // 4 for r in d["routes"]], "def_vias": [len(v) // 2 for v in d["vias"]],
            "def_length_um": [round(x, 1) for x in d["layer_len"]], "drc_report_count": drc_report, "evaluator": ev}


def diff_against(scene, base):
    """Mark DRC markers and HBTs as new/fixed relative to a baseline scene (e.g. X0)."""
    key = lambda r: (r[5], r[4], r[0] // 50, r[1] // 50)
    bk = {key(r) for r in base["drc"]}
    sk = {key(r) for r in scene["drc"]}
    scene["drc_new"] = [i for i, r in enumerate(scene["drc"]) if key(r) not in bk]
    scene["drc_fixed"] = [r for r in base["drc"] if key(r) not in sk]
    bh = {(h[0], h[1]) for h in base["hbt"]}
    scene["hbt_new"] = [i for i, h in enumerate(scene["hbt"]) if (h[0], h[1]) not in bh]
    be = {e[0]: e[4] for e in base["endpoints"]}
    scene["endpoint_delta"] = {e[0]: round(e[4] - be[e[0]], 4) for e in scene["endpoints"] if e[0] in be}
    scene["fixed_endpoints"] = [[k, *base_e[1:4], base_e[4]] for k, base_e in ((e[0], e) for e in base["endpoints"])
                                if k not in {x[0] for x in scene["endpoints"]}][:4000]
    return scene


def save(scene, name):
    out = C.VAR / "scenes"
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{name}.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(scene, f, separators=(",", ":"))
    return p
