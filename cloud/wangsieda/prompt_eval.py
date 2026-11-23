"""Prompt evaluator: run the four LLM agents on fixed, synthetic scenarios with each prompt variant and score the results.

  python3 -m wangsieda.prompt_eval                         # all agents, variants A,B,C, 1 repeat, real model
  python3 -m wangsieda.prompt_eval --agents rtl_debug --variants B,C --repeats 3
  python3 -m wangsieda.prompt_eval --prompt-file rtl_debug=/path/to/my_prompt.txt   # score your own prompt as variant "custom"
  python3 -m wangsieda.prompt_eval --mock good|bad          # no model calls: checks that the scoring separates good from bad

It runs the real agent code (eco_plan, rtl_debug, pnr_diagnose, eco_learn) with its real guards, in a throw-away install
(temporary WANGSI_ROOT and database), so nothing in your catalog, skills or jobs is touched. The model and key come from your
wangsi_config.json / environment exactly as in production. Results: <out>/report.md, <out>/results.json (with every reply).
Cost: one full run (4 agents x 3 variants x 1 repeat) is about 60-90 model calls.
"""
import argparse, json, os, random, re, shutil, sys, tempfile, time
from pathlib import Path

PKG = Path(__file__).resolve().parent
REAL_ROOT = Path(os.environ.get("WANGSI_ROOT", str(PKG.parent)))
AGENTS = ("eco_planner", "rtl_debug", "pnr_diagnose", "reflector", "assistant")

# ====================================================================== scenarios: ECO planner
# Each candidate: tag, gain (expected_gain_ns), p (recorded p_gate), relief, holdpad, sinks. "true_p" is the pass probability
# implied by the evidence placed in the scenario (history / lessons); scoring uses gain * true_p.
PLANNER = [
    {"id": "argmax_no_replay", "case": "peval_a", "budget": 1,
     "note": "best recorded candidate is already measured; pick the best unmeasured one",
     "cands": [("F1001", 610, 0.70, "local", "on", 2), ("F1002", 540, 0.62, "local", "on", 2), ("F1003", 700, 0.55, "local", "on", 3),
               ("F1004", 480, 0.75, "local", "on", 1), ("F1005", 300, 0.80, "local", "on", 1), ("F1006", 650, 0.30, "local", "off", 4),
               ("F1007", 200, 0.85, "local", "on", 1), ("F1008", 520, 0.50, "local", "on", 2)],
     "measured": {"F1001": dict(v6_pass=1, fails=[], tns=-8300.0), "F1003": dict(v6_pass=0, fails=["Short"], tns=-8250.0)},
     "true_p": {}, "skills": []},
    {"id": "history_overrides_pgate", "case": "peval_b", "budget": 1,
     "note": "every measured program without relief failed on Short; recorded p_gate is stale",
     "cands": [("F2001", 560, 0.6, None, "on", 2), ("F2002", 590, 0.6, None, "on", 2), ("F2003", 500, 0.6, None, "on", 2),
               ("F2004", 410, 0.6, "local", "on", 2), ("F2005", 620, 0.62, None, "on", 3), ("F2006", 450, 0.55, "local", "on", 2),
               ("F2007", 300, 0.70, "local", "on", 1), ("F2008", 540, 0.66, None, "on", 2)],
     "measured": {"F2001": dict(v6_pass=0, fails=["Short"], tns=-8200.0), "F2002": dict(v6_pass=0, fails=["Short"], tns=-8150.0),
                  "F2003": dict(v6_pass=0, fails=["Short"], tns=-8290.0), "F2004": dict(v6_pass=1, fails=[], tns=-8420.0)},
     "true_p": {"F2005": 0.05, "F2008": 0.05}, "skills": []},
    {"id": "lesson_and_budget2", "case": "peval_c", "budget": 2,
     "note": "a verified lesson says holdpad-off programs fail hold; pick the best two holdpad-on programs",
     "cands": [("F3001", 700, 0.65, "local", "off", 3), ("F3002", 520, 0.60, "local", "on", 2), ("F3003", 480, 0.66, "local", "on", 2),
               ("F3004", 650, 0.62, "local", "off", 2), ("F3005", 350, 0.75, "local", "on", 1), ("F3006", 600, 0.40, "local", "on", 4),
               ("F3007", 430, 0.64, "local", "on", 2), ("F3008", 690, 0.60, "local", "off", 3)],
     "measured": {"F3009": dict(v6_pass=0, fails=["hold_tns"], tns=-8100.0), "F3010": dict(v6_pass=0, fails=["hold_tns"], tns=-8120.0)},
     "true_p": {"F3001": 0.08, "F3004": 0.08, "F3008": 0.08},
     "skills": [("lesson", "holdpad off", {"lesson": "programs with holdpad off failed hold_tns 2 of 2 times on this design",
                                           "action": "avoid holdpad off; keep hold-pad branches"}, 1)]},
]
X0 = {"official_tns_ns": -8828.7, "official_wns_ns": -4.009, "hold_tns_ns": -2.292, "worst_hold_slack_ns": -0.061, "drc_total": 4257,
      "Short": 3259, "CutShort": 0, "detailed_wirelength_um": 5.1e6, "hbt_count": 1149, "hbt_capacity": 1729, "x0_tns": -8828.7}
BOUNDS = {"wns_inherited": -4.009, "drc_total_inherited": 4257, "drc_Short": 3259, "drc_Cut_Short": 0, "hold_tns_safety": -2.292,
          "hold_wns_safety": -0.061, "wirelength_inherited": 5.2e6, "clock_delta_allow_ps": {}}

# ====================================================================== scenarios: RTL debug
CORRECT_OP7 = "4'd7:  res = $signed(a_q) >>> shamt;"
RTL_MUT = [
    {"id": "sra_logical", "op": 7, "find": CORRECT_OP7, "bug": "4'd7:  res = a_q >> shamt;", "ok": r"res\s*=\s*(\$unsigned\()?\$signed\(a_q\)\s*>>>\s*(shamt|b_q\[3:0\])\)?\s*;"},
    {"id": "sub_as_add", "op": 1, "find": "4'd1:  res = a_q - b_q;", "bug": "4'd1:  res = a_q + b_q;", "ok": r"res\s*=\s*a_q\s*-\s*b_q\s*;"},
    {"id": "sltu_signed", "op": 9, "find": "4'd9:  res = {{(W-1){1'b0}}, (a_q < b_q)};",
     "bug": "4'd9:  res = {{(W-1){1'b0}}, ($signed(a_q) < $signed(b_q))};", "ok": r"res\s*=\s*\{\{\(W-1\)\{1'b0\}\},\s*\(\s*a_q\s*<\s*b_q\s*\)\s*\}\s*;"},
    {"id": "mulhu_low_half", "op": 11, "find": "4'd11: res = prod[2*W-1:W];", "bug": "4'd11: res = prod[W-1:0];", "ok": r"res\s*=\s*prod\[\s*2\s*\*\s*W\s*-\s*1\s*:\s*W\s*\]\s*;"},
    {"id": "sll_full_b", "op": 5, "find": "4'd5:  res = a_q << shamt;", "bug": "4'd5:  res = a_q << b_q;", "ok": r"res\s*=\s*a_q\s*<<\s*(shamt|b_q\[3:0\])\s*;"},
    # harder: looks like the correct SRA line of op 7, so the anchor must carry the 4'd6 label
    {"id": "srl_arith", "op": 6, "find": "4'd6:  res = a_q >> shamt;", "bug": "4'd6:  res = $signed(a_q) >>> shamt;", "ok": r"res\s*=\s*a_q\s*>>\s*(shamt|b_q\[3:0\])\s*;"},
    # harder: mixed signed/unsigned compare silently becomes unsigned in Verilog
    {"id": "slt_mixed_sign", "op": 8, "find": "4'd8:  res = {{(W-1){1'b0}}, ($signed(a_q) < $signed(b_q))};",
     "bug": "4'd8:  res = {{(W-1){1'b0}}, ($signed(a_q) < b_q)};", "ok": r"\$signed\(a_q\)\s*<\s*\$signed\(b_q\)"},
    # harder: bug outside the case statement, three opcodes fail
    {"id": "shamt_slice", "op": 5, "ops": [5, 6, 7], "target": "shamt = ", "find": "wire [3:0]     shamt = b_q[3:0];",
     "bug": "wire [3:0]     shamt = b_q[4:1];", "ok": r"shamt\s*=\s*b_q\[\s*3\s*:\s*0\s*\]\s*;"},
]
W = 16
M = (1 << W) - 1


def alu(op, a, b, bug=None):
    s = lambda v: v - (1 << W) if v >> (W - 1) else v
    sh = b & 0xF
    r = {0: a + b, 1: a - b, 2: a & b, 3: a | b, 4: a ^ b, 5: a << sh, 6: a >> sh, 7: s(a) >> sh, 8: int(s(a) < s(b)), 9: int(a < b),
         10: a * b, 11: (a * b) >> W}.get(op, 0)
    if bug == "sra_logical" and op == 7: r = a >> sh
    if bug == "sub_as_add" and op == 1: r = a + b
    if bug == "sltu_signed" and op == 9: r = int(s(a) < s(b))
    if bug == "mulhu_low_half" and op == 11: r = a * b
    if bug == "sll_full_b" and op == 5: r = (a << b) if b < 64 else 0
    if bug == "srl_arith" and op == 6: r = s(a) >> sh
    if bug == "slt_mixed_sign" and op == 8: r = int(a < b)
    if bug == "shamt_slice" and op in (5, 6, 7):
        sh2 = (b >> 1) & 0xF
        r = {5: a << sh2, 6: a >> sh2, 7: s(a) >> sh2}[op]
    return r & M


def sim_lines(mut):
    rnd, out, n, per = random.Random(7), [], 0, {}
    for i in range(2000):
        a, b = rnd.getrandbits(W), rnd.getrandbits(W)
        if i % 7 == 0: a |= 1 << (W - 1)
        op = i % 12
        g, e = alu(op, a, b, mut["id"]), alu(op, a, b)
        if g != e:
            n += 1
            per[op] = per.get(op, 0) + 1
            if len(out) < 5: out.append(f"MISMATCH op={op} a={a:04x} b={b:04x} got={g:04x} exp={e:04x}")
    return out + [f"OPFAIL op={o} errors={c}" for o, c in sorted(per.items())] + [f"RESULT FAIL errors={n} checks=2000"], n

# ====================================================================== scenarios: P&R diagnose
BASE_K = {"core_utilization": 50, "place_density": 0.7, "timing_driven": False, "setup_slack_margin_ps": 0, "max_buffer_percent": 20,
          "grt_layer_adjust": 0.5, "droute_iters": 64}
# accept: list of (knob, predicate(old, new), stage) -- any one change from this list counts as a correct direction
PNR = [
    {"id": "gpl0301_overfull", "sig": "STAGE_FAIL place GPL-0301", "knobs": dict(BASE_K, core_utilization=70), "history": [],
     "ctx": "[INFO GPL-0019] Utilization: 131.6 %\n[ERROR GPL-0301] Utilization 131.6 % exceeds 100%.",
     "accept": [("core_utilization", lambda o, n: n <= 52 and n >= 20, "floorplan"), ("place_density", lambda o, n: n > o, "place")],
     "require": ["core_utilization"]},
    {"id": "gpl0302_density", "sig": "STAGE_FAIL place GPL-0302", "knobs": dict(BASE_K, place_density=0.6), "history": [],
     "ctx": "[INFO GPL-0019] Utilization: 66.3 %\n[ERROR GPL-0302] Use a higher -density or re-floorplan with a larger core area.\nGiven target density: 0.60\nSuggested target density: 0.67",
     "accept": [("place_density", lambda o, n: 0.67 <= n <= 0.95, "place"), ("core_utilization", lambda o, n: n < o, "floorplan")]},
    {"id": "setup_estimate_gap", "sig": "SIGNOFF SETUP", "knobs": dict(BASE_K), "history": [],
     "ctx": "signoff WNS -0.041 ns, TNS -0.35; pre-route CTS estimate 0.012 ns\nStartpoint: a_q[3] Endpoint: y[15] slack (VIOLATED) -0.041",
     "accept": [("setup_slack_margin_ps", lambda o, n: n >= 40, "cts"), ("timing_driven", lambda o, n: n is True, "place")]},
    {"id": "margin_saturated", "sig": "SIGNOFF SETUP", "knobs": dict(BASE_K, setup_slack_margin_ps=80),
     "history": [{"iter": 0, "knobs": dict(BASE_K), "result": "SIGNOFF SETUP"}, {"iter": 1, "knobs": dict(BASE_K, setup_slack_margin_ps=40), "result": "SIGNOFF SETUP"},
                 {"iter": 2, "knobs": dict(BASE_K, setup_slack_margin_ps=80), "result": "SIGNOFF SETUP"}],
     "ctx": "signoff WNS -0.022 ns, TNS -0.11; pre-route CTS estimate 0.071 ns (margin 80 ps already applied)",
     "accept": [("timing_driven", lambda o, n: n is True, "place"), ("core_utilization", lambda o, n: n < o, "floorplan"),
                ("max_buffer_percent", lambda o, n: n > o, "cts")]},
    {"id": "route_drc", "sig": "SIGNOFF DRC", "knobs": dict(BASE_K, place_density=0.9, droute_iters=32), "history": [],
     "ctx": "detailed_route: Number of violations = 23 (metal2 short x14, metal3 spacing x9) after 32 iterations; GRT max H overflow 0.8%",
     "accept": [("droute_iters", lambda o, n: n > o, "route"), ("grt_layer_adjust", lambda o, n: n > o, "route"),
                ("place_density", lambda o, n: n < o, "place"), ("core_utilization", lambda o, n: n < o, "floorplan")]},
    {"id": "clock_temptation", "sig": "SIGNOFF SETUP", "knobs": dict(BASE_K, setup_slack_margin_ps=150, timing_driven=True),
     "history": [{"iter": 0, "knobs": dict(BASE_K), "result": "SIGNOFF SETUP"}, {"iter": 1, "knobs": dict(BASE_K, setup_slack_margin_ps=150, timing_driven=True), "result": "SIGNOFF SETUP"}],
     "ctx": "signoff WNS -0.62 ns at clock period 1.55 ns; a 2.2 ns period would close timing. Critical path: 16x16 multiplier (prod) -> y.",
     "accept": [("core_utilization", lambda o, n: n < o, "floorplan"), ("max_buffer_percent", lambda o, n: n > o, "cts"),
                ("place_density", lambda o, n: n != o, "place")]},
]

# ====================================================================== scenarios: reflector
REFLECT = [
    {"id": "real_gain_gate_fail", "plan": {"programs": [{"tag": "F4101", "predicted_tns_gain_pct": 6.0, "predicted_gate_pass": 0.8}], "hypothesis": "isolation of 3 sinks"},
     "outcomes": [{"tag": "F4101", "status": "measured, gate failed: Short", "judged": {"raw_tns_gain_pct": 6.42, "metrics": {"tns": -8261.7, "Short": 3325, "drc": 4325},
                   "v6": {"pass": False, "failed": ["Short", "drc_total"], "gain_pct": 0.0}}}],
     "must_name": ["Short"], "any_pass": False, "numbers": ["6.42", "3325", "4325", "6.4"]},
    {"id": "pass_overpredicted", "plan": {"programs": [{"tag": "F4202", "predicted_tns_gain_pct": 8.0, "predicted_gate_pass": 0.7}], "hypothesis": "relief keeps shorts down"},
     "outcomes": [{"tag": "F4202", "status": "V6 PASS", "judged": {"raw_tns_gain_pct": 5.66, "metrics": {"tns": -8328.9, "Short": 3248},
                   "v6": {"pass": True, "failed": [], "gain_pct": 5.66}}}],
     "must_name": [], "any_pass": True, "numbers": ["5.66", "8.0", "8", "3248"]},
    {"id": "never_measured", "plan": {"programs": [{"tag": "F4303", "predicted_tns_gain_pct": 4.0, "predicted_gate_pass": 0.6}], "hypothesis": "-"},
     "outcomes": [{"tag": "F4303", "status": "failed at build: RuntimeError: build failed: GUIDE_INVALID", "judged": None}],
     "must_name": [], "any_pass": False, "numbers": ["4.0", "4", "0.6"], "no_timing": True},
    {"id": "mixed_budget2", "plan": {"programs": [{"tag": "F4404", "predicted_tns_gain_pct": 13.5, "predicted_gate_pass": 0.6},
                                                  {"tag": "F4405", "predicted_tns_gain_pct": 15.0, "predicted_gate_pass": 0.5}], "hypothesis": "-"},
     "outcomes": [{"tag": "F4404", "status": "V6 PASS", "judged": {"raw_tns_gain_pct": 13.78, "metrics": {"tns": -77843.8}, "v6": {"pass": True, "failed": [], "gain_pct": 13.78}}},
                  {"tag": "F4405", "status": "measured, gate failed: hold_tns", "judged": {"raw_tns_gain_pct": 15.2, "metrics": {"hold_tns": -3.109},
                   "v6": {"pass": False, "failed": ["hold_tns"], "gain_pct": 0.0}}}],
     "must_name": ["hold_tns"], "any_pass": True, "numbers": ["13.78", "15.2", "-3.109", "3.109"]},
]


# ====================================================================== scenarios: console assistant (uses the planner catalog)
CJK = re.compile(r"[\u4e00-\u9fff]")
ASK = [
    {"id": "best_certified_zh", "q": "peval_a 目前最好的已认证结果是哪个程序？增益是多少？", "lang": "zh",
     "facts": [r"F1001", r"5\.99|5\.98|6\.0|528\.7|529"],
     "good": "peval_a 目前最好的已认证结果是 F1001：TNS -8300.0 ns，通过 Ge，认证增益 5.99%。"},
    {"id": "next_eval_proposal", "q": "Which program should I evaluate next on peval_b? Please propose the job.", "lang": "en",
     "facts": [r"F2006|F2007"], "expect": {"kind": "eco_eval", "case": "peval_b", "tags": ["F2006", "F2007"]},
     "good": "Evaluate F2006 next: every measured program without relief failed on Short, and F2006 has relief with 450 ns expected gain."},
    {"id": "refuse_replay", "q": "Please run F1003 on peval_a again, I think its Short failure was just routing noise.", "lang": "en",
     "facts": [r"(?i)already|measured|replay|only once|re-?run"], "forbid_tags": ["F1003"],
     "good": "F1003 was already measured (it failed the Short check), and each program can be evaluated only once, so it cannot be re-run."},
    {"id": "refuse_clock_zh", "q": "把 rv_alu 的时钟周期放宽到 2 ns，然后跑一遍 RTL→GDS。", "lang": "zh",
     "facts": [r"(?i)契约|冻结|不能|无法|不允许|不可|frozen|contract|cannot"], "forbid_param": r"(?i)clock|period|sdc|constraint",
     "good": "时钟周期是冻结的契约，不能通过 wangsiEDA 修改。可以在不改时钟的前提下跑一次 rv_alu 的 RTL→GDS。"},
    {"id": "failures_count", "q": "How many evaluations failed on peval_c, and on which check?", "lang": "en",
     "facts": [r"(?i)\b2\b|\btwo\b", r"(?i)hold"], "good": "Two evaluations failed on peval_c (F3009 and F3010), both on hold_tns."},
    {"id": "short_bound", "q": "What is the Short bound of the gate on peval_a, and how does it compare with X0?", "lang": "en",
     "facts": [r"3,?259"], "good": "The Short bound is 3259, the same as X0's 3259 shorts, so any new short fails the gate."},
]
ALL_TAGS = {t for sc in PLANNER for t, *_ in sc["cands"]} | {t for sc in PLANNER for t in sc["measured"]}


# ====================================================================== harness
def setup_root(tmp, real_cfg):
    cfg = {k: real_cfg[k] for k in ("llm_env_file", "llm_model", "llm_extra", "llm_max_tokens", "llm_timeout_s") if k in real_cfg}
    cfg.update(cases=sorted({s["case"] for s in PLANNER}), use_default_prompts=False, oss_cad_bin=real_cfg.get("oss_cad_bin", "/usr/bin"))
    (tmp / "wangsi_config.json").write_text(json.dumps(cfg))
    shutil.copytree(REAL_ROOT / "blocks", tmp / "blocks")
    os.environ["WANGSI_ROOT"], os.environ["WANGSI_CONFIG"] = str(tmp), str(tmp / "wangsi_config.json")


def seed_planner(store):
    for sc in PLANNER:
        case = sc["case"]
        store.x("INSERT INTO designs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (case, "eval", json.dumps(X0), "", "", "", "", 16200,
                json.dumps(BOUNDS), json.dumps(BOUNDS), len(sc["cands"]), time.time()))
        for i, (tag, g, p, relief, hp, n) in enumerate(sc["cands"]):
            prog = {"tag": tag, "hcsi": [{"net": f"net{100 + j}", "sink": f"u{i}_{j}/D"} for j in range(n)], "relief": relief, "holdpad": hp}
            store.x("INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (case, tag, i + 1, g, p, X0["official_tns_ns"] + g, -3.9, -2.2,
                    n, 0, 2 * n, relief, hp, json.dumps(prog)))
        for tag, m in sc["measured"].items():
            store.x("""INSERT INTO evals(uid, case_name, tag, family, kind, status, tns, wns, drc, short, hold_tns, v6_pass, v6_fails, gain_pct, source, origin, raw_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"seed:{case}:{tag}", case, tag, "seed", "judged", "MEASURED", m["tns"], -3.8, 4200, 3240 if "Short" not in m["fails"] else 3330,
                     -2.5 if "hold_tns" in m["fails"] else -2.2, m["v6_pass"], json.dumps(m["fails"]),
                     round(100 * (1 - m["tns"] / X0["official_tns_ns"]), 2) if m["v6_pass"] else 0.0, f"seed/{tag}/REJUDGE.json", "research", "{}"))
        for kind, trig, action, ver in sc["skills"]:
            store.x("INSERT INTO skills VALUES(?,?,?,?,?,?,?,?,?)", (f"L{case}", kind, case, trig, json.dumps(action), "[]", "seed", ver, time.time()))


def true_score(sc, tag):
    c = {t: (g, p) for t, g, p, *_ in sc["cands"]}
    if tag not in c or tag in sc["measured"]:
        return 0.0
    g, p = c[tag]
    return g * sc["true_p"].get(tag, p)


def job_llm(store, jid):
    r = store.one("SELECT count(*) n, sum(ok=0) bad, coalesce(sum(prompt_tokens+completion_tokens),0) t FROM llm_calls WHERE job_id=?", (jid,))
    return {"calls": r["n"], "unparsed": r["bad"] or 0, "tokens": r["t"]}


def run_planner(T, sc, jid, step_dir):
    out, err = None, None
    try:
        out = T["eco_plan"](T["ctx"](jid, jid, step_dir), case=sc["case"], budget=sc["budget"], max_rounds=8)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    st = T["store"]
    tool_calls = st.one("SELECT count(*) n FROM events WHERE job_id=? AND type='tool_call' AND agent='eco-planner'", (jid,))["n"]
    denied = st.one("SELECT count(*) n FROM events WHERE job_id=? AND type='guard' AND payload_json LIKE '%planner.tool_whitelist%'", (jid,))["n"]
    progs = ((out or {}).get("plan") or {}).get("programs") or []
    tags = [str(p.get("tag")) for p in progs if isinstance(p, dict)]
    pool = {t for t, *_ in sc["cands"]}
    valid = [t for t in dict.fromkeys(tags) if t in pool and t not in sc["measured"]]
    best = sorted((true_score(sc, t) for t in pool), reverse=True)[:sc["budget"]]
    quality = sum(true_score(sc, t) for t in valid) / sum(best) if sum(best) else 0
    preds = all(isinstance(p.get("predicted_tns_gain_pct"), (int, float)) and isinstance(p.get("predicted_gate_pass"), (int, float))
                and 0 <= p["predicted_gate_pass"] <= 1 for p in progs if isinstance(p, dict)) and bool(progs)
    llm_ = job_llm(st, jid)
    checks = {"finished": out is not None and bool(progs), "all_valid": bool(tags) and len(valid) == len(tags) == sc["budget"],
              "no_replay": not any(t in sc["measured"] for t in tags), "used_tools": 2 <= tool_calls <= 6, "no_illegal_tool": denied == 0,
              "predictions": preds, "parsed": llm_["unparsed"] == 0}
    score = 0.5 * quality + 0.5 * sum(checks.values()) / len(checks)
    return {"score": round(score, 3), "quality": round(quality, 3), "checks": checks, "picked": tags, "tool_calls": tool_calls, "error": err,
            "llm": llm_, "reply": (out or {}).get("plan")}


def run_rtl(T, mut, jid, step_dir, correct_src, sim_ok):
    bugged = correct_src.replace(mut["find"], mut["bug"])
    assert bugged != correct_src and bugged.count(mut["bug"]) == 1
    rtl = Path(step_dir) / "in" / "rv_alu.v"
    rtl.parent.mkdir(parents=True, exist_ok=True)
    rtl.write_text(bugged)
    lines, n = sim_lines(mut)
    sim = {"pass": False, "failing_ops": [str(o) for o in mut.get("ops", [mut["op"]])], "mismatches": lines}
    out, err, guard = None, None, None
    try:
        out = T["rtl_debug"](T["ctx"](jid, jid, step_dir), block="rv_alu", rtl=str(rtl), sim=sim)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        guard = type(e).__name__ == "GuardViolation"
    fixed = minimal = False
    if out:
        new = Path(out["rtl"]).read_text()
        a, b = correct_src.splitlines(), new.splitlines()
        tgt = mut.get("target") or f"4'd{mut['op']}:"
        if len(a) == len(b):
            diff = [i for i, (x, y) in enumerate(zip(a, b)) if x.strip() != y.strip()]
            minimal = all(tgt in a[i] for i in diff)
            line = next((l for l in b if tgt in l), "")
            fixed = minimal and bool(re.search(mut["ok"], line))
        if sim_ok and shutil.which("iverilog"):
            fixed = sim_pass(out["rtl"])
    reply = T["store"].one("SELECT response_text FROM llm_calls WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,))
    checks = {"parsed": job_llm(T["store"], jid)["unparsed"] == 0, "guards_pass": out is not None, "fixed": fixed, "only_target_line": minimal}
    score = 0.6 * fixed + 0.4 * sum(checks.values()) / len(checks)
    return {"score": round(score, 3), "checks": checks, "error": err, "guard_violation": guard, "llm": job_llm(T["store"], jid),
            "reply": ((reply or {}).get("response_text") or "")[:1500]}


def sim_pass(rtl):
    import subprocess
    tb = REAL_ROOT / "blocks/rv_alu/tb_rv_alu.v"
    d = Path(rtl).parent
    r = subprocess.run(["iverilog", "-g2005", "-o", str(d / "simv"), rtl, str(tb)], capture_output=True, text=True)
    if r.returncode:
        return False
    r = subprocess.run(["vvp", "-n", str(d / "simv")], capture_output=True, text=True, timeout=120)
    return "RESULT PASS" in r.stdout


def run_pnr(T, sc, jid, step_dir):
    out, err = None, None
    try:
        out = T["pnr_diagnose"](T["ctx"](jid, jid, step_dir), block="rv_alu", signature=sc["sig"], context=sc["ctx"], knobs=sc["knobs"],
                                history=sc["history"], policy="llm")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    st = T["store"]
    ev = st.one("SELECT payload_json FROM events WHERE job_id=? AND type='action' ORDER BY id DESC LIMIT 1", (jid,))
    action = json.loads(ev["payload_json"]).get("action") if ev else None
    src = json.loads(ev["payload_json"]).get("source") if ev else None
    changed = (out or {}).get("changed") or {}
    direction = bool(changed) and all(any(k == ak and pred(*v) for ak, pred, _ in sc["accept"]) for k, v in changed.items()) \
        and all(r in changed for r in sc.get("require", []))
    want_stage = None
    if direction:
        order = ["floorplan", "place", "cts", "route"]
        want_stage = order[min(order.index(next(s for ak, _, s in sc["accept"] if ak == k)) for k in changed)]
    asked = (action or {}).get("resume_from")
    checks = {"model_answered": src == "llm", "guards_pass": out is not None, "right_direction": direction,
              "resume_exact": direction and asked == want_stage, "at_most_2_knobs": 0 < len(changed) <= 2,
              "no_clock_touch": "contract.clock_frozen" not in (err or "")}
    score = 0.4 * (direction and checks["guards_pass"] and checks["model_answered"]) + 0.6 * sum(checks.values()) / len(checks)
    return {"score": round(score, 3), "checks": checks, "changed": changed, "asked_resume": asked, "want_resume": want_stage, "error": err,
            "llm": job_llm(st, jid), "reply": action}


NEG = re.compile(r"(not|never|n't|fail\w*|reject\w*|without)\s+(\w+\s+){0,2}$", re.I)


def claims_pass(text, tags):
    subj = r"(\b(" + "|".join(map(re.escape, tags)) + r")\b|\bprograms?\b|\bit\b|\bgate\b|\bECO\b)"
    for sent in re.split(r"(?<=[.;])\s+", text):
        for m in re.finditer(r"\b(certified|accepted|passed|passes)\b", sent, re.I):
            before = sent[:m.start()]
            if re.search(subj, before[-60:], re.I) and not NEG.search(before[-40:]) \
                    and not re.search(r"\b(until|unless|before|once|if|when)\b", before[-40:], re.I):
                return True
    return False


def run_reflect(T, sc, jid, step_dir):
    out, err = None, None
    try:
        out = T["eco_learn"](T["ctx"](jid, jid, step_dir), case="peval_a", plan=sc["plan"], outcomes=sc["outcomes"])
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    les = (out or {}).get("lesson") or {}
    keys = all(isinstance(les.get(k), str) and les.get(k).strip() for k in ("lesson", "trigger", "action", "evidence"))
    text = " ".join(str(les.get(k, "")) for k in ("lesson", "action", "evidence"))
    trig = str(les.get("trigger", ""))
    tags = [o["tag"] for o in sc["outcomes"]]
    checks = {
        "fields": keys,
        "trigger_literal_short": 0 < len(trig) <= 60 and not re.search(r"[\\^$*+?{}\[\]|]", trig),
        "cites_numbers": any(n in str(les.get("evidence", "")) for n in sc["numbers"]),
        "cites_tag": any(t in text for t in tags),
        "names_failed_check": all(c.lower() in text.lower() for c in sc["must_name"]),
        "no_false_pass": sc["any_pass"] or not claims_pass(text, tags),
        "actionable": bool(re.search(r"\b(prefer|avoid|recalibrat\w*|lower|raise|reduce|increase|use|skip|keep|require|check)\b", str(les.get("action", "")), re.I)),
    }
    if sc.get("no_timing"):
        given = json.dumps([sc["plan"], sc["outcomes"]])     # every number in the lesson must come from the input
        nums = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?=\s*(?:%|ns\b|ps\b))", text)   # timing-like values only
        checks["no_invented_timing"] = all(n in given or n.lstrip("-") in given for n in nums)
    score = sum(checks.values()) / len(checks)
    return {"score": round(score, 3), "checks": checks, "error": err, "llm": job_llm(T["store"], jid), "reply": les}


def run_ask(T, sc, jid, step_dir):
    out, err = None, None
    try:
        out = T["assistant_ask"](T["ctx"](jid, jid, step_dir), question=sc["q"])
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    ans = (out or {}).get("answer", "")
    raw = (out or {}).get("proposed_job_raw")
    facts = bool(out) and all(re.search(f, ans) for f in sc["facts"])
    prop_ok = True
    if "expect" in sc:
        e = sc["expect"]
        prm = (raw or {}).get("params") or {} if isinstance(raw, dict) else {}
        prop_ok = isinstance(raw, dict) and raw.get("kind") == e["kind"] and prm.get("case") == e["case"] and prm.get("tag") in e["tags"]
    if sc.get("forbid_tags") and raw:
        prop_ok = prop_ok and not any(t in json.dumps(raw) for t in sc["forbid_tags"])
    if sc.get("forbid_param") and raw:
        prop_ok = prop_ok and not re.search(sc["forbid_param"], json.dumps((raw or {}).get("params", {}) if isinstance(raw, dict) else raw))
    tags = set(re.findall(r"\bF\d{4}\b", ans))
    zh = bool(CJK.search(ans))
    checks = {"parsed": job_llm(T["store"], jid)["unparsed"] == 0 and out is not None,
              "grounded": bool((out or {}).get("tools_used")) or sc["id"] == "refuse_clock_zh",
              "facts": facts, "proposal_ok": prop_ok, "language": zh == (sc["lang"] == "zh"),
              "no_invented_tags": tags <= ALL_TAGS,
              "no_claim_started": not re.search(r"(?i)\b(i have|i've|i) (started|submitted|launched)\b|已(提交|启动|开始运行)", ans)}
    score = 0.5 * (facts and prop_ok) + 0.5 * sum(checks.values()) / len(checks)
    return {"score": round(score, 3), "checks": checks, "error": err, "llm": job_llm(T["store"], jid),
            "reply": {"answer": ans, "proposed_job_raw": raw, "tools_used": (out or {}).get("tools_used")}}


# ====================================================================== mock model (validates the scoring, no API calls)
def mock_factory(mode, store):
    def ask(ctx, agent, messages, purpose, max_calls=12, max_tokens=6000, attempts=2):
        good = mode == "good"
        user = messages[-1]["content"]
        if agent == "eco-planner":
            n_obs = sum(1 for m in messages if m["role"] == "user" and m["content"].startswith("OBSERVATION"))
            case = re.search(r"Design: (\w+)", messages[1]["content"]).group(1)
            sc = next(s for s in PLANNER if s["case"] == case)
            if good and n_obs < 3:
                step = [("design_info", {}), ("candidate_pool", {"only_unmeasured": True, "limit": 30}), ("eval_history", {"judged_only": True})][n_obs]
                return _log(store, ctx, {"thought": "look", "tool": step[0], "args": step[1]})
            pool = [t for t, *_ in sc["cands"]]
            pick = sorted(pool, key=lambda t: -true_score(sc, t))[:sc["budget"]] if good else \
                sorted(pool, key=lambda t: -next(g * p for tt, g, p, *_ in sc["cands"] if tt == t))[:sc["budget"]]
            return _log(store, ctx, {"thought": "done", "final": {"programs": [{"tag": t, "predicted_tns_gain_pct": 5.0, "predicted_gate_pass": 0.6} for t in pick],
                                                                 "hypothesis": "h", "risks": ["Short"]}})
        if agent == "rtl-debug":
            mut = next(m for m in RTL_MUT if m["bug"] in user)
            if good:
                return _log(store, ctx, {"diagnosis": "d", "file": "rv_alu.v", "find": mut["bug"], "replace": mut["find"], "confidence": 0.9})
            ln = next(l for l in user.splitlines() if mut["bug"] in l)
            return _log(store, ctx, {"diagnosis": "d", "file": "rv_alu.v", "find": ln.strip(), "replace": mut["find"], "confidence": 0.9})
        if agent == "physical":
            sig = re.search(r"SIGNATURE: (.*)", user).group(1)
            ctxt = user
            sc = next(s for s in PNR if s["sig"] == sig and s["ctx"][-200:] in ctxt)
            if not good:
                return _log(store, ctx, {"diagnosis": "relax", "knobs": {"clock_period": 2.2}, "resume_from": "floorplan"})
            k, pred, stage = sc["accept"][0]
            old = sc["knobs"][k]
            val = {"core_utilization": 40, "place_density": 0.8 if old < 0.8 else 0.75, "timing_driven": True, "setup_slack_margin_ps": 60,
                   "max_buffer_percent": 30, "droute_iters": 64, "grt_layer_adjust": 0.6}[k]
            return _log(store, ctx, {"diagnosis": "d", "knobs": {k: val}, "resume_from": stage, "expected_effect": "e"})
        if agent == "assistant":
            q = next(m["content"] for m in messages if m["role"] == "user" and not m["content"].startswith("OBSERVATION"))
            sc = next(x for x in ASK if x["q"] == q)
            n_obs = sum(1 for m in messages if m["role"] == "user" and m["content"].startswith("OBSERVATION"))
            if good and n_obs == 0 and sc["id"] != "refuse_clock_zh":
                case = re.search(r"peval_\w", q).group(0)
                return _log(store, ctx, {"thought": "look", "tool": "eval_history", "args": {"case": case}})
            if not good:
                bad = {"next_eval_proposal": {"kind": "eco_eval", "params": {"case": "peval_b", "tag": "F2005"}},
                       "refuse_replay": {"kind": "eco_eval", "params": {"case": "peval_a", "tag": "F1003"}},
                       "refuse_clock_zh": {"kind": "block_flow", "params": {"block": "rv_alu", "policy": "rule", "clock_period": 2}}}.get(sc["id"])
                return _log(store, ctx, {"thought": "-", "final": {"answer": "Sure, I have started it. Best is F9999.", "proposed_job": bad}})
            prop = None
            if "expect" in sc:
                prop = {"kind": "eco_eval", "params": {"case": "peval_b", "tag": "F2006"}, "why": "best gated value"}
            if sc["id"] == "refuse_clock_zh":
                prop = {"kind": "block_flow", "params": {"block": "rv_alu", "policy": "rule"}, "why": "same clock"}
            return _log(store, ctx, {"thought": "-", "final": {"answer": sc["good"], "proposed_job": prop}})
        if agent == "reflector":
            outs = json.loads(user.split("OUTCOMES: ", 1)[1].split("...[truncated]")[0])
            if not good:
                return _log(store, ctx, {"lesson": "All programs were certified and passed.", "trigger": ".*", "action": "nothing", "evidence": "fine"})
            o = outs[-1]
            j = o.get("judged") or {}
            failed = (j.get("v6") or {}).get("failed") or []
            ev = f"{o['tag']}: raw gain {j.get('raw_tns_gain_pct')}%" if j else f"{o['tag']}: {o['status']}"
            for oo in outs:
                if oo.get("judged"):
                    ev += f"; {oo['tag']} raw {oo['judged'].get('raw_tns_gain_pct')}"
            if not j:
                ev = f"{o['tag']}: {o['status']} (predicted 4.0)"
            return _log(store, ctx, {"lesson": f"{o['tag']} " + (f"was rejected on {', '.join(failed)}" if failed else "outcome recorded"),
                                     "trigger": "program without relief", "action": "avoid such programs", "evidence": ev})
        return None
    return ask


def _log(store, ctx, obj):
    store.x("INSERT INTO llm_calls(job_id, ts, agent, model, purpose, ok, origin) VALUES(?,?,?,?,?,?,?)",
            (ctx.job_id, time.time(), "mock", "mock", "mock", 1, "wangsiEDA"))
    return obj


# ====================================================================== main
def load_variants(agents, variants, files):
    out = {}
    for a in agents:
        for v in variants:
            f = REAL_ROOT / "prompts" / "default" / f"{a}.{v}.txt"
            if f.exists():
                out.setdefault(a, {})[v] = f.read_text(encoding="utf-8").strip()
    for spec in files:
        a, path = spec.split("=", 1)
        out.setdefault(a, {})["custom:" + Path(path).stem] = Path(path).read_text(encoding="utf-8").strip()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", default=",".join(AGENTS))
    ap.add_argument("--variants", default="A,B,C")
    ap.add_argument("--prompt-file", action="append", default=[], help="agent=path, scored as an extra variant")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--mock", choices=["good", "bad"])
    ap.add_argument("--out", default=None)
    ap.add_argument("--sim", action="store_true", help="score RTL fixes by running the frozen testbench (needs iverilog)")
    a = ap.parse_args(argv)
    agents = [x for x in a.agents.split(",") if x]
    real_cfg_f = Path(os.environ.get("WANGSI_CONFIG", str(REAL_ROOT / "wangsi_config.json")))
    real_cfg = json.loads(real_cfg_f.read_text()) if real_cfg_f.exists() else {}
    out_dir = Path(a.out or (REAL_ROOT / "var" / "prompt_eval" / time.strftime("%Y%m%d-%H%M%S")))
    variants = load_variants(agents, [v for v in a.variants.split(",") if v], a.prompt_file)
    correct_src = (REAL_ROOT / "blocks/rv_alu/rv_alu.v").read_text().replace("4'd7:  res = a_q >> shamt;", CORRECT_OP7)

    tmp = Path(tempfile.mkdtemp(prefix="wangsi_peval_"))
    setup_root(tmp, real_cfg)
    from . import store, llm                                   # noqa: E402  (after WANGSI_ROOT is set)
    from .tools import load_all, StepContext
    from .tools import agent_tools as AT
    load_all()
    if not a.mock:
        try:
            llm._creds()
        except Exception as e:
            sys.exit(f"{e}\n(or use --mock good|bad to test the evaluator without a model)")
        if not real_cfg.get("llm_model"):
            sys.exit("llm_model is not set in wangsi_config.json")
        ok, msg = llm.ping()
        print(("model check: " if ok else "model check FAILED: ") + msg, flush=True)
        if not ok:
            sys.exit("Stopping before the evaluation: fix the model connection first (nothing was scored).")
    seed_planner(store)
    if a.mock:
        llm.ask_json = mock_factory(a.mock, store)
    from .tools import assistant as AS
    T = {"store": store, "ctx": StepContext, "eco_plan": AT.eco_plan, "rtl_debug": AT.rtl_debug, "pnr_diagnose": AT.pnr_diagnose,
         "eco_learn": AT.eco_learn, "assistant_ask": AS.assistant_ask}
    scen = {"eco_planner": PLANNER, "rtl_debug": RTL_MUT, "pnr_diagnose": PNR, "reflector": REFLECT, "assistant": ASK}
    name = {a: a for a in AGENTS}
    results, k = [], 0
    total = sum(len(variants.get(ag, {})) * len(scen[ag]) * a.repeats for ag in agents)
    for ag in agents:
        for v, text in variants.get(ag, {}).items():
            AT.PROMPT_OVERRIDE.clear()
            AT.PROMPT_OVERRIDE[name[ag]] = text
            for rep in range(a.repeats):
                for sc in scen[ag]:
                    k += 1
                    jid = f"peval-{ag}-{re.sub(r'[^A-Za-z0-9]', '', v)}-{sc['id']}-{rep}"
                    sd = tmp / "jobs" / jid
                    t0 = time.time()
                    if ag == "eco_planner":
                        r = run_planner(T, sc, jid, sd)
                    elif ag == "rtl_debug":
                        r = run_rtl(T, sc, jid, sd, correct_src, a.sim)
                    elif ag == "pnr_diagnose":
                        r = run_pnr(T, sc, jid, sd)
                    elif ag == "assistant":
                        r = run_ask(T, sc, jid, sd)
                    else:
                        r = run_reflect(T, sc, jid, sd)
                    r.update(agent=ag, variant=v, scenario=sc["id"], repeat=rep, seconds=round(time.time() - t0, 1))
                    results.append(r)
                    if not a.mock and r["llm"]["calls"] and r["llm"]["tokens"] == 0 and len(results) <= 2:
                        last = T["store"].one("SELECT response_text FROM llm_calls WHERE job_id=? ORDER BY id DESC LIMIT 1", (jid,))
                        sys.exit("No tokens came back from the model; last error: " + str((last or {}).get("response_text"))[:300])
                    print(f"[{k}/{total}] {ag:<12} {v:<10} {sc['id']:<26} score {r['score']:.2f}  "
                          + " ".join(f"{c}={'Y' if ok else 'n'}" for c, ok in r["checks"].items()), flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    rep = report(results, model=("mock:" + a.mock) if a.mock else f"{real_cfg.get('llm_model')} {json.dumps(real_cfg.get('llm_extra', {}))}")
    (out_dir / "report.md").write_text(rep, encoding="utf-8")
    print("\n" + rep)
    print(f"report: {out_dir / 'report.md'}\nraw:    {out_dir / 'results.json'}")
    shutil.rmtree(tmp, ignore_errors=True)
    return results


def report(results, model):
    from collections import defaultdict
    by = defaultdict(list)
    for r in results:
        by[(r["agent"], r["variant"])].append(r)
    lines = [f"# Prompt evaluation ({model}, {time.strftime('%Y-%m-%d %H:%M')})", "",
             "| agent | variant | mean score | min | runs | model calls | tokens | failing checks (count) |", "|---|---|---|---|---|---|---|---|"]
    best = {}
    for (ag, v), rs in sorted(by.items()):
        sc = [r["score"] for r in rs]
        mean = sum(sc) / len(sc)
        fails = defaultdict(int)
        for r in rs:
            for c, ok in r["checks"].items():
                if not ok:
                    fails[c] += 1
        calls = sum(r["llm"]["calls"] for r in rs)
        toks = sum(r["llm"]["tokens"] or 0 for r in rs)
        lines.append(f"| {ag} | {v} | {mean:.3f} | {min(sc):.2f} | {len(rs)} | {calls} | {toks} | "
                     + (", ".join(f"{c} ({n})" for c, n in sorted(fails.items(), key=lambda x: -x[1])) or "-") + " |")
        if ag not in best or (mean, -toks) > best[ag][1:]:
            best[ag] = (v, mean, -toks)
    lines += ["", "Best variant per agent (ties broken by fewer tokens): " + ", ".join(f"{ag} -> {v} ({m:.3f})" for ag, (v, m, _) in best.items()), "",
              "## Per scenario", "", "| agent | variant | scenario | score | notes |", "|---|---|---|---|---|"]
    for r in results:
        note = r.get("error") or ""
        if r["agent"] == "eco_planner":
            note = f"picked {r['picked']} quality {r['quality']} tools {r['tool_calls']} " + note
        if r["agent"] == "assistant":
            note = f"{(r['reply'] or {}).get('answer', '')[:110]} | job {json.dumps((r['reply'] or {}).get('proposed_job_raw'), ensure_ascii=False)[:60]} " + note
        if r["agent"] == "pnr_diagnose":
            note = f"changed {r['changed']} resume {r['asked_resume']} (want {r['want_resume']}) " + note
        lines.append(f"| {r['agent']} | {r['variant']} | {r['scenario']} | {r['score']:.2f} | {str(note)[:160].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
