"""Block-level RTL -> GDS tools (Verilator lint, Icarus sim against a frozen testbench, Yosys synthesis to Nangate45,
upstream OpenROAD floorplan/place/CTS/route/RCX signoff with .odb checkpoints, DEF -> layout JSON)."""
import hashlib, json, re, shutil, subprocess, time
from pathlib import Path
from . import tool, obj
from .. import config as C
from ..pnr_scripts import stage_script
from .. import layout as layout_mod

BLOCK = {"type": "string", "enum": C.blocks()} if C.blocks() else {"type": "string"}   # blocks/ 下的模块自动发现
KNOBS = {  # name: (type, lo, hi, earliest stage affected)
    "core_utilization": (int, 20, 70, "floorplan"), "place_density": (float, 0.3, 0.95, "place"),
    "timing_driven": (bool, None, None, "place"), "setup_slack_margin_ps": (int, 0, 150, "cts"),
    "max_buffer_percent": (int, 10, 40, "cts"), "grt_layer_adjust": (float, 0.2, 0.7, "route"), "droute_iters": (int, 16, 64, "route")}
STAGES = ["floorplan", "place", "cts", "route", "signoff"]
ENV = {"PATH": C.OSS_BIN + ":/usr/bin:/bin", "HOME": "/tmp", "LANG": "C.UTF-8", "OMP_NUM_THREADS": "4"}
OR_ENVIRON = dict(ENV, PYTHONHOME=C.OR_ENV) if C.OR_ENV else ENV   # PYTHONHOME only for the bundled OpenROAD


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(ctx, cmd, cwd, log, timeout, env=None):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env or ENV, capture_output=True, text=True, timeout=timeout)
        out, rc = (p.stdout or "") + (p.stderr or ""), p.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = f"TIMEOUT {timeout}s\n{e.stdout or ''}", 124
    Path(cwd, log).write_text(out)
    ctx.log(f"$ {' '.join(map(str, cmd))[:300]}\nrc={rc} {time.time() - t0:.1f}s")
    return rc, out


def block_dir(block):
    return C.BLOCKS / block


@tool("rtl_lint", "Verilator lint of an RTL file.", obj({"block": BLOCK, "rtl": {"type": "string"}}, ["block", "rtl"]),
      side_effect="compute", pool="cpu", timeout=120)
def rtl_lint(ctx, block, rtl):
    rc, out = run(ctx, ["verilator", "--lint-only", "-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", rtl], ctx.dir, "lint.log", 60)
    errs = len(re.findall(r"%Error", out))
    return {"errors": errs, "warnings": len(re.findall(r"%Warning", out)), "ok": errs == 0, "log_tail": out[-1500:]}


@tool("rtl_sim", "Simulate RTL against the block's frozen self-checking testbench (SHA-256 verified before use).",
      obj({"block": BLOCK, "rtl": {"type": "string"}}, ["block", "rtl"]), side_effect="compute", pool="cpu", timeout=300)
def rtl_sim(ctx, block, rtl):
    tb = block_dir(block) / f"tb_{block}.v"
    contract = json.loads((block_dir(block) / "CONTRACT.json").read_text())
    if not contract.get("tb_sha256"):
        raise RuntimeError(f"CONTRACT.json 未填写 tb_sha256：请先运行 bin/wangsictl freeze-tb {block} 冻结测试平台")
    if sha(tb) != contract["tb_sha256"]:
        raise RuntimeError("frozen testbench changed: refusing to simulate")
    ctx.emit("guard", agent="guard", rule="frozen.testbench", verdict="allow", detail=contract["tb_sha256"][:16])
    rc, out = run(ctx, ["iverilog", "-g2005", "-o", "simv", str(rtl), str(tb)], ctx.dir, "compile.log", 120)
    if rc == 0:
        rc, out = run(ctx, ["vvp", "-n", "simv"], ctx.dir, "sim.log", 240)
    m = re.search(r"RESULT (PASS|FAIL)(?: errors=(\d+))? checks=(\d+)", out)
    fail_ops = sorted(set(re.findall(r"OPFAIL op=(\d+)", out)))
    return {"pass": "RESULT PASS" in out, "errors": int(m.group(2) or 0) if m else -1, "checks": int(m.group(3)) if m else 0,
            "failing_ops": fail_ops, "mismatches": [l for l in out.splitlines() if l.startswith(("MISMATCH", "OPFAIL", "RESULT"))][:12]}


@tool("synth", "Yosys synthesis to Nangate45 (DFF + ABC liberty mapping, tie cells, buffers) and SDC generation from the frozen clock contract.",
      obj({"block": BLOCK, "rtl": {"type": "string"}, "workdir": {"type": "string"}}, ["block", "rtl", "workdir"]),
      side_effect="compute", pool="cpu", timeout=600)
def synth(ctx, block, rtl, workdir):
    wd = Path(workdir) / "synth"
    wd.mkdir(parents=True, exist_ok=True)
    contract = json.loads((block_dir(block) / "CONTRACT.json").read_text())
    lib = f"{C.NANGATE}/Nangate45_typ.lib"
    ys = (f"read_verilog {rtl}\nsynth -top {block} -flatten\ndfflibmap -liberty {lib}\nabc -liberty {lib}\nopt_clean -purge\n"
          f"setundef -zero\nhilomap -hicell LOGIC1_X1 Z -locell LOGIC0_X1 Z\ninsbuf -buf BUF_X1 A Z\nsplitnets -ports\nopt_clean\n"
          f"tee -q -o stat.txt stat -liberty {lib}\nwrite_verilog -noattr -noexpr -nohex -nodec netlist.v\n")
    (wd / "synth.ys").write_text(ys)
    (wd / "constraints.sdc").write_text(contract["sdc"])
    rc, out = run(ctx, ["yosys", "-q", "-s", "synth.ys"], wd, "synth.log", 500)
    stat = (wd / "stat.txt").read_text() if (wd / "stat.txt").exists() else ""
    area = re.search(r"Chip area[^:]*:\s*([\d.]+)", stat)
    if rc != 0 or not (wd / "netlist.v").exists():
        raise RuntimeError("synthesis failed: " + out[-500:])
    return {"netlist": str(wd / "netlist.v"), "area_um2": float(area.group(1)) if area else None, "sdc_sha256": sha(wd / "constraints.sdc")}


@tool("pnr_stage", "Run one OpenROAD stage (floorplan|place|cts|route|signoff) from the previous stage's .odb checkpoint with validated knobs.",
      obj({"block": BLOCK, "stage": {"type": "string", "enum": STAGES}, "workdir": {"type": "string"}, "knobs": {"type": "object"}},
          ["block", "stage", "workdir", "knobs"]), side_effect="compute", pool="openroad", timeout=1800, retries=1)
def pnr_stage(ctx, block, stage, workdir, knobs):
    for k, v in knobs.items():
        if k not in KNOBS:
            raise ValueError("unknown knob " + k)
    wd = Path(workdir)
    (wd / "pnr").mkdir(parents=True, exist_ok=True)
    tcl = ctx.dir / f"{stage}.tcl"
    tcl.write_text(stage_script(stage, str(wd), knobs, top=block))
    rc, out = run(ctx, [C.OR_ENV + "/bin/openroad", "-no_init", "-no_splash", "-threads", "4", "-exit", str(tcl)], ctx.dir, f"{stage}.log", 1700,
                  env=OR_ENVIRON)
    metrics = {m.group(1): float(m.group(2)) for m in re.finditer(r"@@METRIC (\S+) (-?[\d.eE+-]+)", out)}
    for m in re.finditer(r"@@BEGIN (\S+)\n(.*?)@@END \1", out, re.S):
        (wd / "pnr" / m.group(1)).write_text(m.group(2))
    errors = re.findall(r"\[ERROR [A-Z]+-\d+\][^\n]*", out)
    if rc != 0 or errors:
        ctx.emit("stage_error", agent="pnr", stage=stage, errors=errors[:3],
                 context=[l for l in out.splitlines() if re.search(r"GPL-00\d\d|ERROR|Utilization|density", l)][-12:])
        return {"ok": False, "stage": stage, "errors": errors[:3], "metrics": metrics,
                "context": "\n".join(l for l in out.splitlines() if re.search(r"GPL-00\d\d|ERROR|Utilization|density", l))[-1500:]}
    ck = {"floorplan": "1_floorplan", "place": "2_place", "cts": "3_cts", "route": "4_route"}.get(stage)
    if ck:
        ctx.emit("checkpoint", agent="pnr", stage=stage, path=f"pnr/{ck}.odb", sha=sha(wd / f"pnr/{ck}.odb")[:16])
    res = {"ok": True, "stage": stage, "metrics": metrics}
    if stage == "signoff":
        rpt = (wd / "pnr/timing_setup.rpt").read_text(errors="replace") if (wd / "pnr/timing_setup.rpt").exists() else ""
        grp = rpt.split("Path Group: core_clock")
        res["critical_path_tail"] = grp[1][-1800:] if len(grp) > 1 else ""
        lay = wd / f"layout_{ctx.step_id[-6:] if ctx.step_id else 'x'}.json"
        st = layout_mod.def_to_layout(wd / "pnr/4_route.def", f"{C.NANGATE}/Nangate45_stdcell.lef", wd / "pnr/timing_setup.rpt", lay)
        res.update(layout=str(lay), layout_stats=st)
        ctx.emit("artifact", agent="signoff", kind="layout", path=str(lay), stats=st)
    return res
