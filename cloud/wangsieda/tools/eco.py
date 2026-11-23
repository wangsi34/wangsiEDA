"""ECO execution tools on the real F2F 3D signoff flow.

program_validate -> eco_build (configured ECO builder: HCSI guide edits + relief + hold pad on the fixed X0 placement)
-> eval_replica (signoff evaluator: detailed route + RCX + signoff STA, chroot, 32 threads, 1-8 h)
-> sta_readback (same-final-state clock/hold STA) -> signoff_judge (exact gate Gs + envelope gate Ge).

Validated research tools are never edited: wangsiEDA runs path-derived copies (only output/runtime path literals
change; every derivation is recorded with the source SHA-256), exactly the derivation pattern the project uses.
"""
import glob, hashlib, json, os, re, shutil, subprocess, time
from pathlib import Path
from . import tool, obj
from .catalog_tools import CASE, measured_index
from .. import catalog, config as C, store


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def rd(p):
    return json.loads(Path(p).read_text())


# ------------------------------------------------------------------ derivation of validated tools
DERIVATIONS = {name: (Path(d["source"]), [(a, b.replace("{DERIVED}", str(C.DERIVED))) for a, b in d["replacements"]])
               for name, d in C.CFG.get("derivations", {}).items()}   # 在 wangsi_config.json 中配置


def derived(name):
    src, reps = DERIVATIONS[name]
    out = C.DERIVED / (name + ".py")
    rec = C.DERIVED / (name + ".DERIVATION.json")
    s_sha = sha(src)
    if out.exists() and rec.exists() and rd(rec).get("source_sha256") == s_sha:
        return out
    text = Path(src).read_text()
    for a, b in reps:
        if text.count(a) != 1:
            raise RuntimeError(f"derivation {name}: anchor occurs {text.count(a)}x (source changed?): {a[:80]}")
        text = text.replace(a, b)
    out.write_text(text)
    rec.write_text(json.dumps({"name": name, "source": str(src), "source_sha256": s_sha, "replacements": reps,
                               "derived_sha256": sha(out), "at": time.strftime("%F %T")}, indent=1))
    return out


def fb():
    return catalog.d2().fb()


def program_of(case, tag):
    r = store.one("SELECT program_json FROM candidates WHERE case_name=? AND tag=?", (case, tag))
    if not r:
        raise ValueError(f"{tag} is not in the {case} candidate pool")
    return json.loads(r["program_json"])


# ------------------------------------------------------------------ tools
@tool("program_validate", "Static validation of one candidate ECO program with the frozen builder (enumeration, price record, X0 "
      "identity, inputs) and the no-replay rule. Zero EDA cost.", obj({"case": CASE, "tag": {"type": "string"}}, ["case", "tag"]),
      side_effect="read", pool="cpu", timeout=300)
def program_validate(ctx, case, tag):
    prog = program_of(case, tag)
    meas = measured_index(case).get(tag, [])
    c, status = catalog.d2().cand_context(case, prog)
    out = {"case": case, "tag": tag, "buildable": c is not None, "builder_status": status, "already_measured": len(meas),
           "measurements": meas[:5], "n_edits": len(prog.get("hcsi", [])), "relief": prog.get("relief"), "holdpad": prog.get("holdpad")}
    ctx.emit("validate", agent="critic", **{k: out[k] for k in ("tag", "buildable", "already_measured", "builder_status")})
    return out


@tool("eco_build", "Build an ECO submission (5_1_grt.odb + route.guide) for a candidate program with the configured ECO builder "
      "(HCSI guide surgery + optional relief/hold-pad, then the builder's own validator). Refuses programs that were already measured.",
      obj({"case": CASE, "tag": {"type": "string"}, "allow_remeasure": {"type": "boolean"}}, ["case", "tag"]),
      side_effect="runtime", pool="build", timeout=7200)
def eco_build(ctx, case, tag, allow_remeasure=False):
    if measured_index(case).get(tag) and not allow_remeasure:
        raise RuntimeError(f"NO_REPLAY: {case}/{tag} already has a measurement in the project")
    prog = program_of(case, tag)
    c, status = catalog.d2().cand_context(case, prog)
    if c is None:
        raise RuntimeError(status)
    job = f"wangsi_{case}_{tag}_{ctx.job_id[-8:]}"
    c["job_id"] = job
    od = ctx.dir / "build"
    ctx.log(f"eco_builder.prepare job={job}")
    ctx.emit("build_start", agent="pnr", job=job, edits=len(prog.get("hcsi", [])))
    t0 = time.time()
    fb().prepare(c, od)
    res = rd(fb().execute(od))
    ctx.log(f"build status {res.get('status')} in {time.time() - t0:.0f}s")
    if res.get("status") != "BUILT_VALIDATED":
        raise RuntimeError(f"build failed: {res.get('status')}")
    fin = C.RT / job / "final"
    sub = C.RT_WANGSI / "subs" / f"{case}_{tag}_{ctx.job_id[-8:]}"
    sub.mkdir(parents=True)
    for n in ("5_1_grt.odb", "route.guide"):
        os.link(fin / n, sub / n)
    return {"submission": str(sub), "build_status": res["status"], "runtime_job": job, "build_seconds": round(time.time() - t0, 1),
            "guide_sha256": sha(sub / "route.guide")}


@tool("eval_replica", "Run the configured signoff evaluator on a submission: detailed route, RC extraction, setup STA, DRC, HBT legality. Long-running (1-8 h); admission-gated against every "
      "replica on the host.", obj({"case": CASE, "submission": {"type": "string"}, "run_id": {"type": "string"}}, ["case", "submission", "run_id"]),
      side_effect="runtime", pool="evaluator", timeout=9 * 3600)
def eval_replica(ctx, case, submission, run_id):
    if not re.match(r"^wangsi_[A-Za-z0-9_]+$", run_id):
        raise ValueError("run_id must start with wangsi_")
    timeout = int((catalog.design(case) or {}).get("timeout_s") or 16200)
    tool_py = derived("evaluator_wangsi" if "evaluator_wangsi" in DERIVATIONS else "sca_replica_wangsi")   # new name, v1.0 name
    log = ctx.dir / "evaluator.log"
    ctx.emit("eval_start", agent="evaluator", run_id=run_id, timeout_s=timeout,
             runtime=f"{C.RT_WANGSI}/replica/{run_id}")
    with open(log, "w") as lg:
        p = subprocess.Popen(["nice", "-n", "5", "python3", "-B", str(tool_py), run_id, case, submission, str(timeout)],
                             stdout=lg, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        last = 0
        while p.poll() is None:
            time.sleep(30)
            if time.time() - last > 300:
                last = time.time()
                ctx.emit("eval_progress", agent="evaluator", **evaluator_progress(run_id))
    res_p = C.DERIVED / "replicas" / run_id / "RESULT.json"
    if not res_p.exists():
        raise RuntimeError(f"evaluator produced no RESULT.json (rc={p.returncode})")
    r = rd(res_p)
    m = r.get("metrics") or {}
    out = {"run_id": run_id, "result": str(res_p), "stop": r.get("stop"), "returncode": r.get("returncode"), "wall_s": r.get("wall_s"),
           "tns": m.get("tns_ns"), "wns": m.get("wns_ns"), "drc": m.get("drc"), "drc_by_type": r.get("drc_by_type"),
           "wl": m.get("drt_wirelength_um"), "hbt_excess": m.get("hbt_excess_count"), "legal": m.get("legal"),
           "workroot": str(C.RT_WANGSI / "replica" / run_id / "work/evaluator-work")}
    if r.get("stop") != "EXIT" or m.get("tns_ns") is None:
        raise RuntimeError(f"evaluator did not finish cleanly: stop={r.get('stop')} rc={r.get('returncode')}")
    return out


def evaluator_progress(run_id):
    """Parse the live evaluator log (detailed-route iterations, violations, report stages)."""
    rt = C.RT_WANGSI / "replica" / run_id
    out = {"run_id": run_id, "stage": "queued/admission"}
    logs = sorted(glob.glob(str(rt / "work/evaluator-work/*/logs/nangate45_3D/*/*/*.log")), key=os.path.getmtime)
    rep = C.DERIVED / "replicas" / run_id / "replica.log"
    txt = ""
    for f in ([str(rep)] if rep.exists() else []) + logs[-2:]:
        try:
            with open(f, "rb") as fh:
                fh.seek(max(0, os.path.getsize(f) - 400000))
                txt += fh.read().decode("utf-8", "replace")
        except OSError:
            pass
    it = re.findall(r"Start (\d+)(?:st|nd|rd|th) (?:optimization|stubborn) iteration", txt)
    viol = re.findall(r"Number of violations = (\d+)", txt)
    if logs:
        name = Path(logs[-1]).name
        out["stage"] = {"drt_pass_bottom.log": "detailed route, bottom die", "drt_pass_upper.log": "detailed route, upper die",
                        "drt_post_hooks.log": "route merge / HBT connection", "5_2_route.log": "detailed route",
                        "6_report.log": "signoff STA and reports"}.get(name, name)
    if it:
        out.update(stage="detailed_route", drt_iteration=int(it[-1]))
    if viol:
        out["drt_violations"] = int(viol[-1])
        out["drt_violation_trace"] = [int(v) for v in viol[-12:]]
    for tier in ("bottom", "upper"):
        for f in glob.glob(str(rt / f"work/evaluator-work/*/logs/nangate45_3D/*/*/drt_pass_{tier}.log")):
            try:
                v = [int(x) for x in re.findall(r"Number of violations = (\d+)", Path(f).read_text(errors="replace"))]
            except OSError:
                v = []
            if v:
                out.setdefault("tiers", {})[tier] = v
    fins = re.findall(r"finish (report_\w+)", txt)
    if fins:
        out.update(stage="signoff_reports", last_report=fins[-1])
    if "Evaluation complete" in txt:
        out["stage"] = "complete"
    st = C.DERIVED / "replicas" / run_id / "START.json"
    if st.exists():
        s = rd(st)
        out["elapsed_s"] = round(time.time() - s["at_unix"])
        out["admission_wait_s"] = s.get("admission_wait_s")
    return out


@tool("eval_progress", "Live progress of a running evaluator replica (stage, detailed-route iteration, violation trace, elapsed).",
      obj({"run_id": {"type": "string"}}, ["run_id"]))
def eval_progress(ctx, run_id):
    return evaluator_progress(run_id)


@tool("sta_readback", "Same-final-state clock/hold STA on the evaluator's final state (hold WNS/TNS, per-clock latency/skew), using "
      "the sealed X0 template for the design (designs listed under readbacks in wangsi_config.json).",
      obj({"case": CASE, "run_id": {"type": "string"}, "result": {"type": "string"}, "workroot": {"type": "string"}},
          ["case", "run_id", "result", "workroot"]), side_effect="runtime", pool="cpu", timeout=3 * 3600)
def sta_readback(ctx, case, run_id, result, workroot):
    rb = C.READBACKS.get(case)
    if not rb:
        raise RuntimeError(f"readback for {case} is not configured (readbacks in wangsi_config.json); gate will report hold/clock UNMEASURED")
    tool_py, outdir = derived(rb["derivation"]), C.DERIVED / "readbacks" / case / run_id
    if rb.get("args", "result_workroot") == "final_dir":
        fin = glob.glob(f"{workroot}/*/results/*/{case}/*/6_final.odb")
        if len(fin) != 1:
            raise RuntimeError(f"final state not unique: {fin}")
        argv = [run_id, str(Path(fin[0]).parent)]
    else:
        argv = [run_id, result, workroot]
    rt_base = C.RT_WANGSI / rb.get("runtime_subdir", f"readback_{case}")
    for d in (rt_base, outdir.parent):
        d.mkdir(parents=True, exist_ok=True)
    rt_dir = rt_base / run_id
    if outdir.exists() and not (outdir / "STA_SENT.json").exists() and not rt_dir.exists():
        # an earlier attempt aborted before any STA was sent: keep it for audit, free the identity
        outdir.rename(outdir.with_name(outdir.name + f".aborted_{int(time.time())}"))
        ctx.emit("guard", agent="guard", rule="readback.no_replay", verdict="allow",
                 detail="previous attempt aborted before STA (no measurement taken); archived and retried")
    with open(ctx.dir / "readback.log", "w") as lg:
        rc = subprocess.run(["python3", "-B", str(tool_py), *argv], stdout=lg, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            timeout=3 * 3600).returncode
    if rc != 0:
        raise RuntimeError(f"readback rc={rc}")
    return dict(parse_readback(outdir), rc=rc)


def parse_readback(outdir):
    """Read a finished same-state readback (accepts both RESULT layouts the readback scripts write)."""
    outdir = Path(outdir)
    r = rd(outdir / "RESULT.json") if (outdir / "RESULT.json").exists() else {}
    hold_tns = r.get("hold_tns_ns") if isinstance(r.get("hold_tns_ns"), (int, float)) else None
    if hold_tns is None and (outdir / "STA.log").exists():
        for line in open(outdir / "STA.log", errors="replace"):
            if line.startswith("tns min"):
                hold_tns = float(line.split()[-1])
    t = r.get("timing") or {}
    clocks = r.get("clock_values_ns") or (r.get("clocks") or {}).get("metrics_ns")
    return {"status": r.get("status") or ("COMPLETE" if r.get("complete") else None), "hold_wns": t.get("hold_wns_ns", r.get("hold_wns_ns")),
            "hold_tns": hold_tns, "clock_values_ns": clocks, "readback_dir": str(outdir)}


def _x0_clock(case):
    st = store.one("SELECT metrics_json FROM states WHERE case_name=? AND method='X0'", (case,))
    pc = json.loads(st["metrics_json"]).get("per_clock_ns") if st else None
    pc = json.loads(pc) if isinstance(pc, str) else pc
    if not pc:
        return None
    k = C.CLOCK_NAME
    return {e: (float(pc[f"per_clock_ns.{k}.{e}.max"]), float(pc[f"per_clock_ns.{k}.{e}.skew"])) for e in ("rise_rise", "fall_fall")}


def judge(m, b, x0_tns, v6=False):
    """Same rule set as the project's reference judge: every bound is a hard signoff gate."""
    f = []
    if m.get("hbt_excess") not in (None, 0): f.append("hbt_excess")
    if m.get("wns") is None or m["wns"] < b["wns_inherited"]: f.append("wns")
    if m.get("drc") is None or m["drc"] > b["drc_total_inherited"]: f.append("drc_total")
    if m.get("Short") is None or m["Short"] > b["drc_Short"]: f.append("Short")
    if m.get("CutShort") is None or m["CutShort"] > b["drc_Cut_Short"]: f.append("CutShort")
    if m.get("hold_tns") is None or m["hold_tns"] < b["hold_tns_safety"]: f.append("hold_tns")
    if m.get("hold_wns") is None or m["hold_wns"] < b["hold_wns_safety"]: f.append("hold_wns")
    if m.get("wl") is None or m["wl"] > b["wirelength_inherited"]: f.append("wirelength")
    for key, bound in (("area", "area_inherited"), ("instances", "instance_count_inherited")):   # optional bounds
        if bound in b and (m.get(key) is None or m[key] > b[bound]): f.append(key)
    cd = m.get("clock_delta_ps")
    if not cd:
        f.append("clock_UNMEASURED")
    else:
        allow = b.get("clock_delta_allow_ps", {}) if v6 else {}
        for k, v in cd.items():
            if v > max(0.0, allow.get(k, 0.0)) + 1e-3: f.append("clock_%s(+%.3fps)" % (k, v))
    passed = not f
    return {"pass": passed, "failed": f, "L": (-m["tns"] if passed else -x0_tns),
            "gain_pct": round(100 * (1 - m["tns"] / x0_tns), 3) if passed else 0.0}


@tool("signoff_judge", "Judge measured metrics against the exact gate Gs (strict, X0 values) and the envelope gate Ge (re-route envelope) (timing, hold, DRC, "
      "Short, Cut-Short, wirelength, HBT, clock latency/skew). Returns certified gain only if every gate passes.",
      obj({"case": CASE, "metrics": {"type": "object"}}, ["case", "metrics"]), side_effect="read", pool="cpu", timeout=120)
def signoff_judge(ctx, case, metrics):
    d = catalog.design(case)
    if not d:
        raise ValueError(f"unknown case {case}")
    x0_tns = (d.get("x0") or {}).get("official_tns_ns") or (d.get("x0") or {}).get("x0_tns")
    if not x0_tns:
        raise ValueError(f"{case}: X0 TNS unknown, cannot compute gain")
    m = dict(metrics)
    cv = m.pop("clock_values_ns", None)
    x0c = _x0_clock(case)
    if cv and x0c and "clock_delta_ps" not in m:
        m["clock_delta_ps"] = {}
        for e, (mx, sk) in x0c.items():
            t = cv[C.CLOCK_NAME][e]
            m["clock_delta_ps"][e + "_max_ps"] = round((float(t["max"]) - mx) * 1000, 4)
            m["clock_delta_ps"][e + "_skew_ps"] = round((float(t["skew"]) - sk) * 1000, 4)
    out = {"case": case, "metrics": m, "x0_tns": x0_tns, "raw_tns_gain_pct": round(100 * (1 - m["tns"] / x0_tns), 3) if m.get("tns") else None}
    if d.get("strict_bounds"):
        out["strict"] = judge(m, d["strict_bounds"], x0_tns)
    if d.get("v6_bounds"):
        out["v6"] = judge(m, d["v6_bounds"], x0_tns, v6=True)
    ctx.emit("gate", agent="signoff", strict=out.get("strict"), v6=out.get("v6"), raw_tns_gain_pct=out["raw_tns_gain_pct"])
    return out


@tool("eval_cleanup", "Delete the evaluator's large intermediate route databases (5_2_route*, 5_route, 6_1_fill) for a finished "
      "wangsi run after readback, keeping 6_final.* and all reports (same policy the project uses).",
      obj({"run_id": {"type": "string"}}, ["run_id"]), side_effect="runtime", pool="cpu", timeout=600)
def eval_cleanup(ctx, run_id):
    if not run_id.startswith("wangsi_"):
        raise ValueError("only wangsi runs")
    freed, n = 0, 0
    for pat in ("5_2_route*.odb", "5_route.odb", "6_1_fill.odb"):
        for f in (C.RT_WANGSI / "replica" / run_id / "work/evaluator-work").glob("*/results/nangate45_3D/*/*/" + pat):
            freed += f.stat().st_size; n += 1; f.unlink()
    return {"deleted_files": n, "freed_GiB": round(freed / 2**30, 2)}


@tool("ingest_result", "Record a finished wangsi evaluation (metrics + verdicts + provenance) in the catalog.",
      obj({"case": CASE, "tag": {"type": "string"}, "run_id": {"type": "string"}, "judged": {"type": "object"}, "eval": {"type": "object"}},
          ["case", "tag", "run_id", "judged", "eval"]), side_effect="asset", pool="cpu", timeout=60)
def ingest_result(ctx, case, tag, run_id, judged, eval):
    m = judged["metrics"]
    row = dict(uid=f"wangsi:{run_id}", case_name=case, tag=tag, family="wangsiEDA", arm=ctx.job_id, kind="judged", status="MEASURED",
               tns=m.get("tns"), wns=m.get("wns"), drc=m.get("drc"), short=m.get("Short"), cutshort=m.get("CutShort"),
               hold_tns=m.get("hold_tns"), hold_wns=m.get("hold_wns"), wl=m.get("wl"), hbt_excess=m.get("hbt_excess"),
               strict_pass=int(judged.get("strict", {}).get("pass", False)), v6_pass=int(judged.get("v6", {}).get("pass", False)),
               strict_fails=json.dumps(judged.get("strict", {}).get("failed")), v6_fails=json.dumps(judged.get("v6", {}).get("failed")),
               gain_pct=judged.get("v6", {}).get("gain_pct"), wall_s=eval.get("wall_s"), at=time.time(), source=eval.get("result"),
               raw=dict(judged=judged, eval=eval))
    catalog.upsert_eval(row, origin="wangsiEDA")
    return {"recorded": row["uid"]}


@tool("host_status", "Host capacity and admission state: load, free disk/memory, replicas running host-wide, wangsiEDA pools.", obj({}))
def host_status(ctx):
    from ..engine import host_snapshot
    return host_snapshot()
