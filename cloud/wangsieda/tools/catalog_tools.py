"""Read-only tools over the indexed data lake (designs, signed-off states, evaluations, candidate pool, skills)."""
import json, re
from . import tool, obj
from .. import catalog, store, config as C

# 设计名由 wangsi_config.json 的 cases 决定；未配置时不限制取值（工具内部再查设计注册表）
CASE = {"type": "string", "enum": list(C.CASES)} if C.CASES else {"type": "string"}
TAG_RE = re.compile(C.CFG.get("program_tag_regex", r"(?<![A-Za-z0-9])F\d{4,5}(?![0-9])"))


def measured_index(case):
    """Program tags that already have an evaluation anywhere in the project (wangsiEDA never re-runs them)."""
    out = {}
    for r in store.q("SELECT tag, source, status, tns, v6_pass FROM evals WHERE case_name=?", (case,)):
        for t in set(TAG_RE.findall((r["tag"] or "") + " " + r["source"])):
            out.setdefault(t, []).append({k: r[k] for k in ("status", "tns", "v6_pass", "source")})
    for r in store.q("SELECT tag, source FROM marks WHERE case_name=?", (case,)):
        out.setdefault(r["tag"], []).append({"status": "touched", "source": r["source"]})
    return out


@tool("design_info", "Design registry entry: X0 (initial post-route) metrics, envelope (Ge) and exact (Gs) gate bounds, candidate pool size, "
      "readback kind and evaluator timeout.", obj({"case": CASE}, ["case"]))
def design_info(ctx, case):
    d = catalog.design(case)
    if not d:
        raise ValueError("unknown case " + case)
    x0 = d.get("x0") or {}
    keep = ("official_tns_ns", "official_wns_ns", "hold_tns_ns", "worst_hold_slack_ns", "drc_total", "Short", "CutShort",
            "detailed_wirelength_um", "hbt_count", "hbt_capacity", "power_total_W", "setup_violating_endpoints", "hold_violating_endpoints",
            "evaluator_runtime_s", "x0_tns")
    return {"case": case, "role": d["role"], "x0": {k: x0.get(k) for k in keep}, "v6_bounds": d.get("v6_bounds"),
            "strict_bounds": d.get("strict_bounds"), "n_candidates": d["n_candidates"], "readback": d["readback"],
            "evaluator_timeout_s": d["timeout_s"]}


@tool("signed_off_states", "Canonical signed-off states for a design (X0, DRG-3D, AnchorGrid, AgenticECO, Project/HCSI) with timing, "
      "DRC, power, wirelength and HBT metrics.", obj({"case": CASE}, ["case"]))
def signed_off_states(ctx, case):
    keys = ("official_tns_ns", "official_wns_ns", "hold_tns_ns", "worst_hold_slack_ns", "drc_total", "Short", "CutShort",
            "detailed_wirelength_um", "power_total_W", "hbt_excess", "setup_violating_endpoints", "hold_violating_endpoints")
    rows = []
    for s in store.q("SELECT method, variant, candidate, metrics_json FROM states WHERE case_name=? ORDER BY method, variant", (case,)):
        m = json.loads(s["metrics_json"])
        rows.append({"method": s["method"], "variant": s["variant"], "candidate": s["candidate"], **{k: m.get(k) for k in keys}})
    return {"case": case, "states": rows}


@tool("candidate_pool", "Candidate ECO programs (HCSI sink-isolation edits + relief + hold-pad) for a design, with the recorded "
      "predictions (expected TNS gain, P(gate), predicted WNS/hold) and whether each was already measured.",
      obj({"case": CASE, "only_unmeasured": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["case"]))
def candidate_pool(ctx, case, only_unmeasured=False, limit=30):
    mi = measured_index(case)
    out = []
    for r in store.q("SELECT * FROM candidates WHERE case_name=? ORDER BY c3_rank", (case,)):
        prog = json.loads(r.pop("program_json"))
        r["n_edits"] = len(prog.get("hcsi", []))
        r["measured"] = len(mi.get(r["tag"], []))
        if only_unmeasured and r["measured"]:
            continue
        r["edits"] = [f"{h['net']} -> {h['sink']}" for h in prog.get("hcsi", [])][:8]
        out.append(r)
        if len(out) >= limit:
            break
    return {"case": case, "candidates": out, "pool_size": store.one("SELECT count(*) n FROM candidates WHERE case_name=?", (case,))["n"]}


@tool("eval_history", "Measured evaluations for a design (replica evaluator + judged results), best first; includes gate verdicts "
      "and provenance paths.", obj({"case": CASE, "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                                     "judged_only": {"type": "boolean"}}, ["case"]))
def eval_history(ctx, case, limit=40, judged_only=False):
    w = " AND kind='judged'" if judged_only else ""
    rows = store.q(f"""SELECT tag, family, arm, kind, status, tns, wns, drc, short, cutshort, hold_tns, hold_wns, wl, strict_pass,
                       v6_pass, v6_fails, gain_pct, wall_s, source, origin FROM evals WHERE case_name=?{w}
                       ORDER BY tns DESC LIMIT ?""", (case, limit))
    for r in rows:
        r["v6_fails"] = json.loads(r["v6_fails"]) if r["v6_fails"] else None
    return {"case": case, "evals": rows}


@tool("program_evidence", "Everything already known about one candidate program: recorded prediction, every measurement of it, and the "
      "skills learned on this design.", obj({"case": CASE, "tag": {"type": "string"}}, ["case", "tag"]))
def program_evidence(ctx, case, tag):
    c = store.one("SELECT * FROM candidates WHERE case_name=? AND tag=?", (case, tag))
    if c:
        c["program"] = json.loads(c.pop("program_json"))
    return {"candidate": c, "measurements": measured_index(case).get(tag, []),
            "skills": store.q("SELECT id, kind, trigger, action_json, verified FROM skills WHERE case_name=? OR case_name IS NULL", (case,))}


@tool("skills_lookup", "Lessons/skills in the wangsiEDA asset library (only verified ones are applied automatically).",
      obj({"case": {"type": "string"}}))
def skills_lookup(ctx, case=None):
    return {"skills": store.q("SELECT * FROM skills WHERE ? IS NULL OR case_name=? OR case_name IS NULL ORDER BY created DESC", (case, case))}
