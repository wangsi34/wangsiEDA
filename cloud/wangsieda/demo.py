"""Bundled demo: a small SYNTHETIC face-to-face design, generated on the fly, so the console can be tried without any private data.

  python3 -m wangsieda.demo                  # generate (first time) and serve on http://127.0.0.1:18766 (prints the URL with token)
  python3 -m wangsieda.demo --open           # same, and open the browser
  python3 -m wangsieda.demo --rebuild        # regenerate the demo data
  python3 -m wangsieda.demo --package OUT    # write a double-click offline copy (OUT/打开念思.html), no Python needed to view it

Everything lives in <install>/demo_root (its own database, scenes and jobs), so a real installation is never touched.
The generator writes evaluator-style outputs (DEF, route guides, violated paths, DRC report, HBT table, reports) and then runs
the real scene extraction (tools.viz_tools.scene_build), so the 3D review exercises the same code path as real runs.
The numbers are invented and labelled as such; they are not results of any real design.
"""
import argparse, json, os, random, shutil, sys, time
from pathlib import Path

PKG = Path(__file__).resolve().parent
BASE = PKG.parent
DEMO = BASE / "demo_root"
CASE = "demo_f2f"
DBU = 2000                      # DEF units per micron
DIE = (600.0, 480.0)            # um
ROW_H, SITE = 1.4, 0.19

THICK = [0.13, 0.14, 0.14, 0.28, 0.28, 0.28, 0.8, 0.8, 2.0, 2.0]
WIDTH = [0.07, 0.07, 0.07, 0.14, 0.14, 0.14, 0.4, 0.4, 0.8, 0.8]
CELLS = {"INV_X1": (0.38, 1.4), "NAND2_X1": (0.57, 1.4), "NOR2_X1": (0.57, 1.4), "BUF_X2": (0.76, 1.4), "BUF_X8": (1.9, 1.4),
         "DFF_X1": (3.8, 1.4), "CLKBUF_X2": (0.95, 1.4), "AOI21_X1": (0.76, 1.4)}
SRAM = ("fakeram45_64x32", 64.0, 48.0)


# ---------------------------------------------------------------------------------------------- platform (LEF)
def write_platform(p):
    (p / "lef").mkdir(parents=True, exist_ok=True)
    for d in ("lef_bottom", "lef_upper"):
        (p / d).mkdir(exist_ok=True)
    t, h = [], 0.3
    heights = []
    for i in range(10):
        heights.append(h)
        h += THICK[i] + 0.25
    for i in range(10):
        t.append(f"LAYER metal{i + 1}\n  TYPE ROUTING ;\n  WIDTH {WIDTH[i]} ;\n  HEIGHT {heights[i]:.3f} ;\n  THICKNESS {THICK[i]} ;\nEND metal{i + 1}\n")
        if i < 9:
            t.append(f"LAYER via{i + 1}\n  TYPE CUT ;\n  WIDTH {WIDTH[i]} ;\nEND via{i + 1}\n")
    t.append("LAYER hbt\n  TYPE ROUTING ;\n  WIDTH 1.0 ;\n  THICKNESS 2.0 ;\nEND hbt\n")
    for k in range(10):                     # upper die: metal11 faces the bond (mirror of metal10), metal20 is its device side
        i = 9 - k
        t.append(f"LAYER metal{11 + k}\n  TYPE ROUTING ;\n  WIDTH {WIDTH[i]} ;\n  HEIGHT {heights[i]:.3f} ;\n  THICKNESS {THICK[i]} ;\nEND metal{11 + k}\n")
    (p / "lef" / "demo_tech.lef").write_text("".join(t))
    for tier in ("bottom", "upper"):
        cells = "".join(f"MACRO {n}_{tier}\n  CLASS CORE ;\n  SIZE {w} BY {hh} ;\nEND {n}_{tier}\n" for n, (w, hh) in CELLS.items())
        cells += f"MACRO {SRAM[0]}_{tier}\n  CLASS BLOCK ;\n  SIZE {SRAM[1]} BY {SRAM[2]} ;\nEND {SRAM[0]}_{tier}\n"
        (p / f"lef_{tier}" / f"demo_{tier}.lef").write_text(cells)


# ---------------------------------------------------------------------------------------------- design
def d(v):
    return int(round(v * DBU))


class Design:
    """One synthetic placement shared by all states; states differ in routing of the critical net, HBTs, DRC and timing."""

    def __init__(self, seed=7):
        r = self.r = random.Random(seed)
        self.comps, self.cells_by_row = {}, {}
        self.srams = []
        for k in range(4):                                       # SRAMs on the upper die
            x, y = 40 + k * 135, DIE[1] - 70
            self.srams.append((f"sram{k}", x, y))
            self.comps[f"sram{k}"] = (f"{SRAM[0]}_upper", x, y)
        n = 0
        rows = int((DIE[1] - 20) / ROW_H)
        for row in range(rows):
            y = 10 + row * ROW_H
            x = 10.0
            while x < DIE[0] - 15:
                if r.random() < 0.25:
                    m = r.choices(list(CELLS), weights=[6, 6, 4, 3, 1, 3, 1, 3])[0]
                    w = CELLS[m][0]
                    name = f"u{n}"
                    if m == "DFF_X1": name = f"ff{n}"
                    if m == "CLKBUF_X2": name = f"clkbuf{n}"
                    if m.startswith("BUF"): name = f"buf{n}"
                    self.comps[name] = (f"{m}_bottom", round(x, 2), round(y, 2))
                    self.cells_by_row.setdefault(row, []).append(name)
                    n += 1
                    x += w
                x += SITE * r.randint(1, 8)
        self.names = [k for k in self.comps if not k.startswith("sram")]
        self.ffs = [k for k in self.names if k.startswith("ff")]
        # the critical net: an input buffer near the left edge drives a long shared tree to many sinks
        self.drv = min(self.names, key=lambda k: abs(self.comps[k][1] - 15) + abs(self.comps[k][2] - DIE[1] / 2))
        self.comps[self.drv] = ("BUF_X8_bottom",) + self.comps[self.drv][1:]
        far = sorted(self.names, key=lambda k: -self.comps[k][1])
        self.crit_sinks = [k for k in far if not k.startswith(("ff", "clk"))][:6]
        self.sinks = self.crit_sinks + r.sample([k for k in self.names if k not in self.crit_sinks and k != self.drv], 110)
        self.nets = self.make_nets()

    def pos(self, inst):
        m, x, y = self.comps[inst]
        w, h = (SRAM[1], SRAM[2]) if m.startswith("fakeram") else CELLS[m.rsplit("_", 1)[0]]
        return x + w / 2, y + h / 2

    def make_nets(self):
        r, nets = self.r, []
        pool = self.names
        for i in range(1400):                                    # local nets on the bottom die
            a = r.choice(pool)
            ax, ay = self.pos(a)
            near = [b for b in r.sample(pool, 40) if abs(self.pos(b)[0] - ax) + abs(self.pos(b)[1] - ay) < 60][:r.randint(1, 3)]
            if near:
                nets.append((f"n{i}", a, near, "local"))
        for k, (s, sx, sy) in enumerate(self.srams):             # cross-tier nets to the SRAMs
            for j in range(14):
                a = r.choice(pool)
                nets.append((f"sram{k}_d{j}", a, [s], "cross"))
        return nets


def route_local(a, b):
    (x1, y1), (x2, y2) = a, b
    lv = random.Random(int(x1 * 7 + y2 * 13)).choice([(2, 3), (2, 3), (4, 5), (6, 7)])
    return [(f"metal{lv[0]}", (x1, y1), (x2, y1)), (f"metal{lv[1]}", (x2, y1), (x2, y2))]


def def_text(D, state):
    """state: x0 | cert | miss."""
    out = ["VERSION 5.8 ;", "DESIGN demo_f2f ;", f"UNITS DISTANCE MICRONS {DBU} ;", f"DIEAREA ( 0 0 ) ( {d(DIE[0])} {d(DIE[1])} ) ;"]
    out.append(f"COMPONENTS {len(D.comps)} ;")
    for n, (m, x, y) in D.comps.items():
        out.append(f"- {n} {m} + PLACED ( {d(x)} {d(y)} ) N ;")
    out.append("END COMPONENTS")
    out += ["PINS 2 ;", f"- in_req + NET in_req + DIRECTION INPUT + PLACED ( {d(2)} {d(DIE[1] / 2)} ) N ;",
            f"- clk + NET clk + DIRECTION INPUT + PLACED ( {d(2)} {d(20)} ) N ;", "END PINS"]
    body, hbts = [], []

    def net(name, conns, segs, vias=()):
        body.append(f"- {name} " + " ".join(f"( {i} {p} )" for i, p in conns))
        first = True
        for layer, (x1, y1), (x2, y2) in segs:
            kw = "+ ROUTED" if first else "NEW"
            first = False
            p2 = f"( {d(x2)} * )" if y1 == y2 else f"( * {d(y2)} )" if x1 == x2 else f"( {d(x2)} {d(y2)} )"
            body.append(f"  {kw} {layer} ( {d(x1)} {d(y1)} ) {p2}")
        for layer, (x, y), vname in vias:
            body.append(f"  NEW {layer} ( {d(x)} {d(y)} ) {vname}")
        body.append(" ;")

    for name, a, sinks, kind in D.nets:
        ax, ay = D.pos(a)
        segs, vias = [], []
        if kind == "local":
            for b in sinks:
                segs += route_local((ax, ay), D.pos(b))
        else:                                              # bottom metal9/10 -> HBT -> upper metal11/12 -> SRAM pin
            sx, sy = D.pos(sinks[0])
            hx, hy = round(ax + (sx - ax) * 0.3, 1), round(ay + (sy - ay) * 0.3, 1)
            segs += [("metal9", (ax, ay), (hx, ay)), ("metal10", (hx, ay), (hx, hy)), ("metal11", (hx, hy), (sx, hy)), ("metal12", (sx, hy), (sx, sy))]
            vias += [("metal10", (hx, hy), "hbt_via")]
            hbts.append((name, hx, hy))
        net(name, [(a, "Z")] + [(b, "A") for b in sinks], segs, vias)
    # the critical net: shared trunk along y=mid, branches up/down to every sink (long, thin metal)
    dx, dy = D.pos(D.drv)
    segs = [("metal2", (dx, dy), (DIE[0] - 20, dy))]
    for s in D.sinks:
        sx, sy = D.pos(s)
        segs += [("metal3", (sx, dy), (sx, sy))]
    vias = []
    if state in ("cert", "miss"):                          # HCSI: dedicated cross-tier branch to the critical sinks
        k = 2 if state == "cert" else 4
        for i, s in enumerate(D.crit_sinks[:k]):
            sx, sy = D.pos(s)
            ax_, ay_ = round(dx + 6 + 3 * i, 1), round(dy + 4 + 2 * i, 1)
            bx_, by_ = round(sx - 6, 1), round(sy + 3, 1)
            segs += [("metal10", (dx, dy), (ax_, dy)), ("metal10", (ax_, dy), (ax_, ay_)), ("metal11", (ax_, ay_), (bx_, ay_)),
                     ("metal11", (bx_, ay_), (bx_, by_)), ("metal10", (bx_, by_), (sx, by_)), ("metal10", (sx, by_), (sx, sy))]
            vias += [("metal10", (ax_, ay_), "hbt_via"), ("metal10", (bx_, by_), "hbt_via")]
            hbts += [(f"net_crit_hcsi{i}_a", ax_, ay_), (f"net_crit_hcsi{i}_b", bx_, by_)]
    net("net_crit", [(D.drv, "Z")] + [(s, "A") for s in D.sinks], segs, vias)
    out.append(f"NETS {len(D.nets) + 1} ;")
    out += body
    out.append("END NETS")
    out.append("END DESIGN")
    return "\n".join(out) + "\n", hbts


def guide_text(D, state):
    lines = []
    for name, a, sinks, kind in D.nets[:80]:
        ax, ay = D.pos(a)
        lines += [name, "(", f"{d(ax - 2)} {d(ay - 2)} {d(ax + 2)} {d(ay + 2)} metal2", ")"]
    dx, dy = D.pos(D.drv)
    boxes = [f"{d(dx)} {d(dy - 3)} {d(DIE[0] - 20)} {d(dy + 3)} metal2"]
    if state in ("cert", "miss"):
        k = 2 if state == "cert" else 4
        for i, s in enumerate(D.crit_sinks[:k]):
            sx, sy = D.pos(s)
            boxes += [f"{d(dx)} {d(dy + 2 * i)} {d(sx)} {d(dy + 10 + 2 * i)} metal11", f"{d(sx - 8)} {d(sy - 2)} {d(sx + 2)} {d(sy + 6)} metal10"]
    lines += ["net_crit", "("] + boxes + [")"]
    return "\n".join(lines) + "\n"


def paths_text(D, state, worst):
    r = random.Random({"x0": 1, "cert": 2, "miss": 3}[state])
    out = []
    x0_slack = [-3.212, -3.170, -2.054, -2.031, -1.987, -1.951]     # worst sinks of net_crit at X0
    isolated = {"x0": 0, "cert": 2, "miss": 4}[state]
    for k, s in enumerate(D.crit_sinks):
        ff = min(D.ffs, key=lambda f: abs(D.pos(f)[0] - D.pos(s)[0]) + abs(D.pos(f)[1] - D.pos(s)[1]))
        st0 = round(3.42 - (x0_slack[k] - x0_slack[0]), 2)             # stage delay on net_crit at X0
        st = 0.24 if k < isolated else st0
        slack = round(x0_slack[k] + (st0 - st), 3)
        if slack >= 0:
            continue
        out += ["Startpoint: in_req (input port clocked by core_clock)", f"Endpoint: {ff} (rising edge-triggered flip-flop clocked by core_clock)",
                "", "  Delay    Time   Description", "---------------------------------------------------------",
                "   0.20    0.20 ^ in_req (in)", f"   0.12    0.32 ^ {D.drv}/Z (BUF_X8_bottom)", f"   {st:.2f}    {0.32 + st:.2f} ^ {s}/A (INV_X1_bottom)",
                f"   0.09    {0.41 + st:.2f} v {s}/ZN (INV_X1_bottom)", f"   0.05    {0.46 + st:.2f} v {ff}/D (DFF_X1_bottom)",
                f"           {0.46 + st:.2f}   data arrival time", f"          {slack:.3f}   slack (VIOLATED)", ""]
    for k in range(160):
        a, ff = r.choice(D.names), r.choice(D.ffs)
        sl = -round(r.uniform(0.02, 1.6), 3)
        out += [f"Startpoint: {a} (rising edge-triggered flip-flop clocked by core_clock)", f"Endpoint: {ff} (rising edge-triggered flip-flop clocked by core_clock)",
                f"   0.30    0.30 ^ {a}/Z (BUF_X2_bottom)", f"   0.40    0.70 v {ff}/D (DFF_X1_bottom)", "           0.70   data arrival time",
                f"          {sl:.3f}   slack (VIOLATED)", ""]
    return "\n".join(out)


def drc_text(n_short, n_other, seed):
    r = random.Random(seed)
    out = []
    for i in range(n_short + n_other):
        t = "Short" if i < n_short else r.choice(["Metal Spacing", "Cut Spacing", "NS Metal"])
        x, y = r.uniform(20, DIE[0] - 20), r.uniform(20, DIE[1] - 20)
        lay = r.choice(["metal2", "metal3", "metal2", "metal4", "via2", "metal11"])
        out += [f"violation type: {t}", f"\tsrcs: net:n{r.randint(0, 1399)} net:n{r.randint(0, 1399)}",
                f"\tbbox = ({x:.3f}, {y:.3f}) - ({x + 0.2:.3f}, {y + 0.14:.3f}) on Layer {lay}"]
    return "\n".join(out) + "\n"


STATES = {   # synthetic signoff numbers (Ge bounds below); NEAR_MISS has more raw gain but adds shorts
    "x0":   dict(label="X0", tns=-2412.6, wns=-3.212, hold_tns=-0.512, hold_wns=-0.031, short=157, other=57, viol=1834),
    "cert": dict(label="CERTIFIED", tag="F0310", tns=-2118.4, wns=-2.054, hold_tns=-0.498, hold_wns=-0.029, short=151, other=54, viol=1702),
    "miss": dict(label="NEAR_MISS", tag="F0312", tns=-2050.7, wns=-1.987, hold_tns=-0.507, hold_wns=-0.030, short=183, other=60, viol=1679),
}


def write_run(D, root, state):
    s = STATES[state]
    res = root / "results/nangate45_3D" / CASE / "base"
    rep = root / "reports/nangate45_3D" / CASE / "base"
    logs = root / "logs/nangate45_3D" / CASE / "base"
    for p in (res, rep, logs):
        p.mkdir(parents=True, exist_ok=True)
    text, hbts = def_text(D, state)
    (res / "6_final.def").write_text(text)
    (res / "route.guide").write_text(guide_text(D, state))
    (res / "violated_paths").write_text(paths_text(D, state, s["wns"]))
    (res / "hbt_net_merge.tsv").write_text("net\ttop_pin\tbottom_pin\tsite\tx\ty\n" +
                                           "".join(f"{n}\t-\t-\t{i}\t{d(x)}\t{d(y)}\n" for i, (n, x, y) in enumerate(hbts)))
    (rep / "5_route_drc.rpt").write_text(drc_text(s["short"], s["other"], {"x0": 11, "cert": 12, "miss": 13}[state]))
    (logs / "6_report.json").write_text(json.dumps({"finish__timing__setup__tns": s["tns"], "finish__timing__setup__ws": s["wns"],
                                                    "finish__timing__drv__setup_violation_count": s["viol"],
                                                    "finish__timing__drv__hold_violation_count": 12, "finish__power__total": 0.0412,
                                                    "finish__design__instance__area": 61234.5}))
    for tier, base in (("bottom", 900), ("upper", 140)):
        seq = [int(base * 0.55 ** i) + (s["short"] // (6 if tier == "bottom" else 20)) for i in range(8)]
        (logs / f"drt_pass_{tier}.log").write_text("".join(f"Number of violations = {v}\n" for v in seq))
    return len(hbts)


def def_wirelength(D, state):
    text, _ = def_text(D, state)
    import re
    total = 0.0
    for m in re.finditer(r"(?:ROUTED|NEW) (?:metal\d+) \( (\d+) (\d+) \) \( (\S+) (\S+) \)", text):
        x1, y1 = int(m.group(1)), int(m.group(2))
        x2 = x1 if m.group(3) == "*" else int(m.group(3))
        y2 = y1 if m.group(4) == "*" else int(m.group(4))
        total += (abs(x2 - x1) + abs(y2 - y1)) / DBU
    return round(total, 1)


# ---------------------------------------------------------------------------------------------- generate
def generate():
    if DEMO.exists():
        shutil.rmtree(DEMO)
    (DEMO / "evaluator").mkdir(parents=True)
    for sub in ("blocks", "prompts"):
        (DEMO / sub).symlink_to(BASE / sub, target_is_directory=True)
    write_platform(DEMO / "platform")
    D = Design()
    runs, n_hbt, wl = {}, {}, {}
    for st in STATES:
        runs[st] = DEMO / "evaluator" / st
        n_hbt[st] = write_run(D, runs[st], st)
        wl[st] = def_wirelength(D, st)
    cfg = {"_note": "wangsiEDA demo (synthetic data). Generated by python3 -m wangsieda.demo",
           "cases": [CASE], "scene_cases": [CASE], "platform_dir": str(DEMO / "platform"), "x0_runs": {CASE: str(runs["x0"])},
           "clock_period_ns": {CASE: 1.0}, "use_default_prompts": True}
    real = BASE / "wangsi_config.json"
    if real.exists():                                      # keep the person's model settings so the chat works in the demo
        rc = json.loads(real.read_text())
        cfg.update({k: rc[k] for k in ("llm_env_file", "llm_model", "llm_extra", "llm_max_tokens", "llm_timeout_s") if k in rc})
    (DEMO / "wangsi_config.json").write_text(json.dumps(cfg, indent=1))
    os.environ["WANGSI_ROOT"], os.environ["WANGSI_CONFIG"] = str(DEMO), str(DEMO / "wangsi_config.json")

    from . import store
    from .tools import load_all, call, StepContext
    load_all()
    seed_catalog(store, n_hbt, wl)
    ctx = StepContext()
    for st, uid in (("cert", "demo:F0310"), ("miss", "demo:F0312")):
        s = STATES[st]
        call("scene_build", {"case": CASE, "label": s["label"], "run": str(runs[st]), "title": f"{s['tag']} (synthetic demo)",
                             "programs": [s["tag"]], "eval_uid": uid}, ctx)
    seed_job(store)
    store.meta_set("catalog_refresh", {"at": time.time(), "designs": 1, "demo": True, "errors": {}})
    return DEMO


def seed_catalog(store, n_hbt, wl):
    x0 = STATES["x0"]
    x0m = {"official_tns_ns": x0["tns"], "official_wns_ns": x0["wns"], "hold_tns_ns": x0["hold_tns"], "worst_hold_slack_ns": x0["hold_wns"],
           "drc_total": x0["short"] + x0["other"], "Short": x0["short"], "CutShort": 0, "detailed_wirelength_um": wl["x0"],
           "hbt_count": n_hbt["x0"], "hbt_capacity": 400, "power_total_W": 0.0412, "evaluator_runtime_s": 2700, "x0_tns": x0["tns"],
           "per_clock_ns": {"per_clock_ns.CLK.rise_rise.max": 0.412, "per_clock_ns.CLK.rise_rise.skew": 0.061,
                            "per_clock_ns.CLK.fall_fall.max": 0.418, "per_clock_ns.CLK.fall_fall.skew": 0.064}}
    ge = {"wns_inherited": x0["wns"], "drc_total_inherited": x0["short"] + x0["other"], "drc_Short": x0["short"], "drc_Cut_Short": 0,
          "hold_tns_safety": x0["hold_tns"], "hold_wns_safety": x0["hold_wns"], "wirelength_inherited": round(wl["x0"] * 1.02, 1),
          "clock_delta_allow_ps": {"rise_rise_skew_ps": 0.5, "fall_fall_skew_ps": 0.5}}
    gs = dict(ge, wirelength_inherited=wl["x0"], clock_delta_allow_ps={})
    cands = [("F0301", 210, 0.70, "local", "on", 1), ("F0302", 260, 0.55, None, "on", 2), ("F0303", 180, 0.80, "local", "on", 1),
             ("F0304", 330, 0.35, None, "off", 3), ("F0305", 240, 0.62, "local", "on", 2), ("F0306", 150, 0.85, "local", "on", 1),
             ("F0307", 300, 0.40, "local", "off", 3), ("F0308", 275, 0.58, "local", "on", 2), ("F0310", 290, 0.65, "local", "on", 2),
             ("F0311", 340, 0.30, None, "on", 4), ("F0312", 360, 0.45, "local", "on", 4)]
    store.x("DELETE FROM designs WHERE case_name=?", (CASE,))
    store.x("INSERT INTO designs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (CASE, "demo (synthetic)", json.dumps(x0m), "", "", "", "", 3600,
            json.dumps(ge), json.dumps(gs), len(cands), time.time()))
    for i, (tag, g, p, relief, hp, n) in enumerate(cands):
        prog = {"tag": tag, "hcsi": [{"net": "net_crit", "sink": f"sink{j}/A"} for j in range(n)], "relief": relief, "holdpad": hp}
        store.x("INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (CASE, tag, i + 1, g, p, x0["tns"] + g, x0["wns"] + 0.8, x0["hold_tns"], n, 0, 2 * n, relief, hp, json.dumps(prog)))
    for st, fails in (("cert", []), ("miss", ["Short", "drc_total"])):
        s = STATES[st]
        metrics = {"tns": s["tns"], "wns": s["wns"], "drc": s["short"] + s["other"], "Short": s["short"], "CutShort": 0, "wl": wl[st],
                   "hold_tns": s["hold_tns"], "hold_wns": s["hold_wns"], "hbt_excess": 0,
                   "clock_delta_ps": {"rise_rise_max_ps": -0.2, "rise_rise_skew_ps": 0.1, "fall_fall_max_ps": -0.1, "fall_fall_skew_ps": 0.2}}
        gain = round(100 * (1 - s["tns"] / x0["tns"]), 2)
        v6 = {"pass": not fails, "failed": fails, "gain_pct": gain if not fails else 0.0}
        strict = {"pass": False, "failed": ["clock_fall_fall_skew_ps(+0.200ps)"] + fails, "gain_pct": 0.0}
        raw = {"metrics": metrics, "judged": {"metrics": metrics, "v6": v6, "strict": strict, "raw_tns_gain_pct": gain}}
        store.x("""INSERT OR REPLACE INTO evals(uid, case_name, tag, family, arm, kind, status, tns, wns, drc, short, cutshort, hold_tns, hold_wns, wl,
                   hbt_excess, strict_pass, v6_pass, strict_fails, v6_fails, gain_pct, wall_s, at, source, origin, raw_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"demo:{s['tag']}", CASE, s["tag"], "demo", "demo-closure", "judged", "MEASURED", s["tns"], s["wns"], metrics["drc"], s["short"], 0,
                 s["hold_tns"], s["hold_wns"], wl[st], 0, 0, int(not fails), json.dumps(strict["failed"]), json.dumps(fails), v6["gain_pct"],
                 2700, time.time() - 86400, f"demo/{s['tag']}/RESULT.json", "demo", json.dumps(raw)))
    ce = STATES["cert"]
    rows = (("X0", "-", "-", {}),
            ("Baseline (terminal move)", "post-route", "-", dict(official_tns_ns=-2398.0, official_wns_ns=-3.19, Short=162, drc_total=219)),
            ("HCSI+R", "demo", "F0310", dict(official_tns_ns=ce["tns"], official_wns_ns=ce["wns"], hold_tns_ns=ce["hold_tns"],
                                             worst_hold_slack_ns=ce["hold_wns"], Short=ce["short"], drc_total=ce["short"] + ce["other"],
                                             detailed_wirelength_um=wl["cert"])))
    for meth, var, cand, over in rows:
        store.x("INSERT OR REPLACE INTO states(case_name, method, variant, candidate, metrics_json, source) VALUES(?,?,?,?,?,?)",
                (CASE, meth, var, cand, json.dumps(dict(x0m, **over)), "demo"))
    store.x("INSERT OR REPLACE INTO skills VALUES(?,?,?,?,?,?,?,?,?)",
            ("Ldemo1", "lesson", CASE, "program without relief", json.dumps({
                "lesson": "Four-sink isolation (F0312) gained 15.0% raw but added 26 shorts and failed Ge on Short; two-sink F0310 passed with 12.19%.",
                "action": "prefer 1-2 sink programs with relief on demo_f2f"}), json.dumps([]), "demo", 1, time.time() - 80000))


def seed_job(store):
    jid = "demo-closure-0001"
    t0 = time.time() - 90000
    if store.one("SELECT 1 FROM jobs WHERE id=?", (jid,)):
        return
    state = {"plan": {"programs": [{"tag": "F0310", "predicted_tns_gain_pct": 11.8, "predicted_gate_pass": 0.65,
                                    "why": "2-sink isolation with relief: best gain x pass probability"},
                                   {"tag": "F0312", "predicted_tns_gain_pct": 14.5, "predicted_gate_pass": 0.45,
                                    "why": "4 sinks: more gain, higher Short risk (bound equals X0)"}],
                      "hypothesis": "isolating the far sinks of net_crit removes most of the 3.4 ns stage delay",
                      "risks": ["Short bound equals X0 (157), so any new short fails Ge"]},
             "evals": {"F0310.": {"tag": "F0310", "status": "V6 PASS"}, "F0312.": {"tag": "F0312", "status": "measured, gate failed: Short,drc_total"}},
             "note": "synthetic demo job"}
    store.x("INSERT INTO jobs(id, kind, case_name, title, params_json, status, created, started, ended, summary_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (jid, "eco_closure", CASE, "demo_f2f ECO closure (synthetic demo)", json.dumps({"case": CASE, "budget": 2}), "done", t0, t0 + 5, t0 + 6000,
             json.dumps(state)))
    steps = [("plan", "eco_plan", 0, 40), ("critic", "eco_critic", 40, 55)]
    for tag, off in (("F0310", 60), ("F0312", 62)):
        steps += [(f"{tag}.validate", "program_validate", off, off + 8), (f"{tag}.build", "eco_build", off + 10, off + 400),
                  (f"{tag}.evaluate", "eval_replica", off + 400, off + 3100), (f"{tag}.readback", "sta_readback", off + 3100, off + 3500),
                  (f"{tag}.judge", "signoff_judge", off + 3500, off + 3502), (f"{tag}.ingest", "ingest_result", off + 3502, off + 3503)]
    steps.append(("learn", "eco_learn", 3600, 3620))
    for i, (name, tool, a, b) in enumerate(steps):
        store.x("""INSERT INTO steps(id, job_id, idx, name, tool, deps_json, args_json, pool, status, attempt, max_attempts, timeout_s, started, ended, rc,
                   outputs_json, dir) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"{jid}:{i + 1:02d}", jid, i + 1, name, tool, "[]", "{}", "cpu", "done", 1, 1, 3600, t0 + a, t0 + b, 0, "{}", "/nonexistent"))
    ev = [("job_start", "supervisor", {}), ("thought", "eco-planner", {"text": "Look at the gate bounds first: Short bound equals X0 (157)."}),
          ("tool_call", "eco-planner", {"tool": "design_info", "args": {"case": CASE}}),
          ("tool_call", "eco-planner", {"tool": "candidate_pool", "args": {"case": CASE, "limit": 30}}),
          ("plan", "eco-planner", {"plan": state["plan"]}),
          ("guard", "guard", {"rule": "critic.no_replay", "verdict": "allow", "detail": "F0310 measured 0x before"}),
          ("gate", "signoff", {"v6": {"pass": True, "failed": [], "gain_pct": 12.19}, "raw_tns_gain_pct": 12.19,
                               "strict": {"pass": False, "failed": ["clock_fall_fall_skew_ps(+0.200ps)"], "gain_pct": 0.0}}),
          ("gate", "signoff", {"v6": {"pass": False, "failed": ["Short", "drc_total"], "gain_pct": 0.0}, "raw_tns_gain_pct": 15.0,
                               "strict": {"pass": False, "failed": ["clock_fall_fall_skew_ps(+0.200ps)", "Short", "drc_total"], "gain_pct": 0.0}}),
          ("skill", "curator", {"op": "promote", "id": "Ldemo1", "lesson": "prefer 1-2 sink programs with relief", "verified": True}),
          ("job_end", "supervisor", {"status": "done"})]
    for k, (typ, agent, payload) in enumerate(ev):
        store.x("INSERT INTO events(job_id, step_id, ts, type, agent, payload_json) VALUES(?,?,?,?,?,?)",
                (jid, None, t0 + k * 600, typ, agent, json.dumps(payload)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--open", action="store_true", help="open the console in the browser")
    ap.add_argument("--no-serve", action="store_true", help="only generate the data")
    ap.add_argument("--package", metavar="OUT", help="write an offline, double-click copy of the demo console to OUT")
    ap.add_argument("--port", type=int, default=int(os.environ.get("WANGSI_DEMO_PORT", 18766)))
    a = ap.parse_args(argv)
    if os.name == "nt" and not (a.package or a.no_serve):
        # the console service uses POSIX-only pieces (fcntl lock, setsid workers, /proc), so fail early with a clear hint
        sys.exit("The local demo console runs on macOS or Linux only.\n"
                 "On Windows, use WSL: run `wsl --install` once, then `python3 wangsi.py demo` inside the WSL terminal.\n"
                 "start.bat is for connecting to a Linux compute host (see README), not for the local demo.")
    os.environ["WANGSI_PORT"] = str(a.port)          # before anything imports config
    if a.rebuild or not (DEMO / "var" / "wangsi.db").exists():
        print("generating the synthetic demo design ...", flush=True)
        generate()
        print(f"demo data in {DEMO}", flush=True)
    if a.package:
        os.environ.update(WANGSI_ROOT=str(DEMO), WANGSI_CONFIG=str(DEMO / "wangsi_config.json"))
        import runpy, tempfile
        from . import export_snapshot
        snap = Path(tempfile.mkdtemp(prefix="wangsi_demo_snap_")) / "snap"
        sys.argv = ["export_snapshot", str(snap), CASE]
        export_snapshot.main(str(snap))
        mk = runpy.run_path(str(BASE.parent / "make_offline_package.py"))
        mk["main"](str(snap), str(PKG / "ui"), a.package)
        shutil.rmtree(snap.parent, ignore_errors=True)
        return
    if a.no_serve:
        return
    os.environ.update(WANGSI_ROOT=str(DEMO), WANGSI_CONFIG=str(DEMO / "wangsi_config.json"), WANGSI_PORT=str(a.port))
    from . import daemon
    tok = daemon.token()
    url = f"http://127.0.0.1:{a.port}/?token={tok}"
    print(f"wangsiEDA demo: {url}\n(synthetic data; Ctrl+C to stop)", flush=True)
    if a.open:
        import webbrowser
        webbrowser.open(url)
    daemon.main()


if __name__ == "__main__":
    main()
