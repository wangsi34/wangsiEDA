"""Flow controllers (state machines that grow each job's DAG). Three flows ship with wangsiEDA:

eco_eval     validate -> build -> evaluate -> readback -> judge -> ingest -> cleanup         (one candidate ECO program)
eco_closure  plan (LLM, ReAct over catalog tools) -> critic -> N x eco_eval pipelines in parallel -> learn
block_flow   lint -> sim -> [rtl_debug -> lint -> sim]* -> synth -> floorplan -> place -> cts -> route -> signoff
             -> gate -> [pnr_diagnose -> resume from the earliest affected stage]* -> verify -> asset flowback
"""
import json, re, time, uuid
from pathlib import Path
from . import config as C, store
from .engine import add_step, steps_of
from .tools.blockflow import STAGES

READBACK_CASES = tuple(C.READBACK_CASES)
SCENE_CASES = tuple(C.SCENE_CASES)


def state(job):
    return json.loads(store.one("SELECT summary_json FROM jobs WHERE id=?", (job["id"],))["summary_json"] or "{}")


def save(job, st):
    store.x("UPDATE jobs SET summary_json=? WHERE id=?", (json.dumps(st, default=str), job["id"]))


def outputs(step):
    return json.loads(step["outputs_json"] or "null") or {}


def params(job):
    return json.loads(job["params_json"])


def require(feature, label, ignore=()):
    """Refuse a job up front when its tools are not configured, instead of failing several steps later."""
    f = C.features()[feature]
    miss = [m for m in f["missing"] if m not in ignore]
    if miss:
        raise ValueError(f"{label} 未就绪，缺少: {', '.join(miss)}（运行 bin/wangsictl doctor 查看补齐方法）")


class Flow:
    def check(self, p): pass
    def start(self, job): pass
    def on_step(self, job, step): pass
    def on_retry(self, job, step): pass

    def finish(self, job):
        st = state(job)
        failed = [s["name"] for s in steps_of(job["id"]) if s["status"] == "failed"]
        return ("failed" if failed or st.get("failed") else "done"), dict(st, failed_steps=failed)


# ------------------------------------------------------------------ one ECO program, real evaluator
def eco_pipeline(job, case, tag, prefix=""):
    """Append the first step of an evaluation pipeline; later steps are added as results arrive (they need concrete outputs)."""
    return add_step(job["id"], f"{prefix}validate", "program_validate", {"case": case, "tag": tag})


def eco_next(job, step, case, tag, prefix, st):
    name, o = step["name"][len(prefix):], outputs(step)
    key = prefix or "main"
    rec = st.setdefault("evals", {}).setdefault(key, {"tag": tag})
    if name in ("guide_diff", "scene"):   # visualization steps never change the evaluation outcome
        if step["status"] != "done":
            rec["viz_error"] = f"{name}: {step['error']}"
        return
    if step["status"] == "done" and str(rec.get("status", "")).startswith(f"failed at {name}"):
        rec.pop("status")  # a retried step succeeded
    if step["status"] != "done":
        rec["status"] = f"failed at {name}: {step['error']}"
        return
    if name == "validate":
        if not o.get("buildable") or o.get("already_measured"):
            rec["status"] = "rejected: " + ("already measured (no replay)" if o.get("already_measured") else o.get("builder_status", ""))
            return
        add_step(job["id"], f"{prefix}build", "eco_build", {"case": case, "tag": tag})
    elif name == "build":
        rid = f"wangsi_{case}_{tag}_{job['id'].replace('-', '')[-12:]}"
        rec.update(submission=o["submission"], run_id=rid)
        add_step(job["id"], f"{prefix}evaluate", "eval_replica", {"case": case, "submission": o["submission"], "run_id": rid})
        if case in SCENE_CASES:   # what the ECO physically changes, visible before the evaluator finishes
            add_step(job["id"], f"{prefix}guide_diff", "guide_diff", {"case": case, "guide": o["submission"] + "/route.guide"})
    elif name == "evaluate":
        rec["eval"] = o
        if case in READBACK_CASES:
            add_step(job["id"], f"{prefix}readback", "sta_readback", {"case": case, "run_id": o["run_id"], "result": o["result"], "workroot": o["workroot"]})
        else:
            judge_step(job, case, prefix, rec, {})
    elif name == "readback":
        rec["readback"] = o
        judge_step(job, case, prefix, rec, o)
    elif name == "judge":
        rec["judged"] = o
        rec["status"] = "V6 PASS" if (o.get("v6") or {}).get("pass") else "measured, gate failed: " + ",".join((o.get("v6") or {}).get("failed", []))
        add_step(job["id"], f"{prefix}ingest", "ingest_result", {"case": case, "tag": tag, "run_id": rec["run_id"], "judged": o, "eval": rec["eval"]})
        deps = [f"{prefix}ingest"]
        if case in SCENE_CASES:
            label = f"{tag}_{job['id'][-6:]}"
            rec["scene_label"] = label
            add_step(job["id"], f"{prefix}scene", "scene_build", {"case": case, "label": label, "run": rec["eval"]["workroot"],
                     "title": f"{tag} (job {job['id']})", "programs": [tag], "eval_uid": f"wangsi:{rec['run_id']}"}, deps=[f"{prefix}ingest"])
            deps.append(f"{prefix}scene")
        add_step(job["id"], f"{prefix}cleanup", "eval_cleanup", {"run_id": rec["run_id"]}, deps=deps)


def judge_step(job, case, prefix, rec, rb):
    e = rec["eval"]
    by = e.get("drc_by_type") or {}
    m = {"tns": e["tns"], "wns": e["wns"], "drc": e["drc"], "Short": by.get("Short"), "CutShort": by.get("Cut Short", 0 if by else None), "wl": e.get("wl"),
         "hbt_excess": e.get("hbt_excess"), "hold_tns": rb.get("hold_tns"), "hold_wns": rb.get("hold_wns"),
         "clock_values_ns": rb.get("clock_values_ns")}
    add_step(job["id"], f"{prefix}judge", "signoff_judge", {"case": case, "metrics": m})


class EcoEval(Flow):
    def check(self, p):
        if p.get("case") not in C.CASES or not p.get("tag"):
            raise ValueError(f"case (one of {C.CASES}) and tag are required")
        require("eco", "ECO 评测")

    def start(self, job):
        p = params(job)
        eco_pipeline(job, p["case"], p["tag"])

    def on_step(self, job, step):
        p, st = params(job), state(job)
        eco_next(job, step, p["case"], p["tag"], "", st)
        save(job, st)


class EcoClosure(Flow):
    def on_retry(self, job, step):
        st = state(job)
        st["learn_added"] = False  # reflect again once the retried pipeline settles
        save(job, st)

    def check(self, p):
        if p.get("case") not in C.CASES:
            raise ValueError(f"case must be one of {C.CASES}")
        if not 1 <= int(p.get("budget", 1)) <= 4:
            raise ValueError("budget must be 1..4")
        if not 2 <= int(p.get("max_rounds", 8)) <= 12:
            raise ValueError("max_rounds must be 2..12")
        require("eco", "Agentic ECO closure")
        require("llm", "ECO 规划 Agent（大模型）")

    def start(self, job):
        p = params(job)
        add_step(job["id"], "plan", "eco_plan", {"case": p["case"], "budget": int(p.get("budget", 1)), "max_rounds": int(p.get("max_rounds", 8))})

    def on_step(self, job, step):
        p, st = params(job), state(job)
        case, budget = p["case"], int(p.get("budget", 1))
        o = outputs(step)
        if step["name"] == "plan" and step["status"] == "done":
            st["plan"] = o["plan"]
            add_step(job["id"], "critic", "eco_critic", {"case": case, "plan": o["plan"], "budget": budget})
        elif step["name"] == "critic" and step["status"] == "done":
            st["approved"], st["rejected"] = o["approved"], o["rejected"]
            if p.get("dry_run"):
                st["note"] = "dry run: plan approved, no evaluation dispatched"
            else:
                for prog in o["approved"]:
                    eco_pipeline(job, case, prog["tag"], prefix=prog["tag"] + ".")
        elif "." in step["name"]:
            tag = step["name"].split(".", 1)[0]
            eco_next(job, step, case, tag, tag + ".", st)
            done = all(("judged" in r) or r.get("status", "").startswith(("failed", "rejected")) for r in st.get("evals", {}).values())
            if done and len(st.get("evals", {})) == len(st.get("approved", [])) and not st.get("learn_added"):
                st["learn_added"] = True
                outs = [dict(tag=r["tag"], judged=r.get("judged"), status=r.get("status")) for r in st["evals"].values()]
                add_step(job["id"], "learn", "eco_learn", {"case": case, "plan": st.get("plan", {}), "outcomes": outs})
        save(job, st)


# ------------------------------------------------------------------ block RTL -> GDS with closed-loop repair
INITIAL_KNOBS = {"core_utilization": 70, "place_density": 0.9, "timing_driven": False, "setup_slack_margin_ps": 0,
                 "max_buffer_percent": 20, "grt_layer_adjust": 0.5, "droute_iters": 64}


class BlockFlow(Flow):
    MAX_RTL_FIX, MAX_PNR_ITERS = 3, 6

    def check(self, p):
        if p.get("block") not in C.blocks():
            raise ValueError(f"block must be one of {C.blocks()} (blocks/<name>/ with <name>.v, tb_<name>.v, CONTRACT.json)")
        if p.get("policy", "llm") not in ("llm", "rule"):
            raise ValueError("policy must be llm or rule")
        from .tools.blockflow import KNOBS
        bad = sorted(set(p.get("knobs") or {}) - set(KNOBS))
        if bad:
            raise ValueError(f"unknown knobs {bad}; allowed: {sorted(KNOBS)} (the clock period is a frozen contract)")
        require("block_flow", "模块流程", ignore=("blocks/*",))
        if p.get("policy", "llm") == "llm":
            require("llm", "LLM 策略（可改用 rule 策略，不需要大模型）")

    def start(self, job):
        p = params(job)
        wd = C.JOBS / job["id"] / "work"
        wd.mkdir(parents=True, exist_ok=True)
        rtl = str(C.BLOCKS / p["block"] / f"{p['block']}.v")
        save(job, {"rtl": rtl, "workdir": str(wd), "knobs": dict(INITIAL_KNOBS, **p.get("knobs", {})), "rtl_fixes": 0, "iter": 0,
                   "history": [], "metrics": {}, "skill_candidates": []})
        add_step(job["id"], "lint", "rtl_lint", {"block": p["block"], "rtl": rtl})

    def stage(self, job, st, stage):
        add_step(job["id"], f"{stage}.i{st['iter']}", "pnr_stage", {"block": params(job)["block"], "stage": stage,
                 "workdir": st["workdir"], "knobs": st["knobs"]})

    def on_step(self, job, step):
        p, st = params(job), state(job)
        o, name, block = outputs(step), step["name"], p["block"]
        base = name.split(".")[0]
        if step["status"] != "done":
            st["failed"] = f"{name}: {step['error']}"
        elif base == "lint":
            if not o["ok"]:
                st["failed"] = "lint errors"
            else:
                add_step(job["id"], "sim", "rtl_sim", {"block": block, "rtl": st["rtl"]})
        elif base == "sim":
            if o["pass"]:
                if st.get("final_check"):
                    st["verified"] = True
                    self.flowback(job, st)
                else:
                    add_step(job["id"], "synth", "synth", {"block": block, "rtl": st["rtl"], "workdir": st["workdir"]})
            elif st["rtl_fixes"] >= self.MAX_RTL_FIX:
                st["failed"] = "RTL fix budget exhausted"
            else:
                st["rtl_fixes"] += 1
                add_step(job["id"], "debug", "rtl_debug", {"block": block, "rtl": st["rtl"], "sim": o})
        elif base == "debug":
            st["rtl"] = o["rtl"]
            if o["source"] == "llm":
                st["skill_candidates"].append(o["skill_candidate"])
            add_step(job["id"], "lint", "rtl_lint", {"block": block, "rtl": st["rtl"]})
        elif base == "synth":
            st["metrics"]["synth_area_um2"] = o["area_um2"]
            self.stage(job, st, "floorplan")
        elif base in STAGES:
            st["metrics"].update(o.get("metrics", {}))
            if not o["ok"]:
                sig = f"STAGE_FAIL {base} " + " ".join(re.findall(r"ERROR ([A-Z]+-\d+)", " ".join(o["errors"])))
                self.diagnose(job, st, sig, o.get("context", ""))
            elif base != "signoff":
                self.stage(job, st, STAGES[STAGES.index(base) + 1])
            else:
                m = st["metrics"]
                checks = {"setup_wns>=0": m.get("signoff.wns_setup", -1) >= 0, "hold_wns>=0": m.get("signoff.wns_hold", -1) >= 0,
                          "drc==0": m.get("route.drc_count", 1) == 0, "all_nets_routed": m.get("signoff.unrouted", 1) == 0}
                store.event(job["id"], "gate", "signoff", step["id"], checks=checks, passed=all(checks.values()),
                            wns=m.get("signoff.wns_setup"), cts_wns=m.get("cts.wns_setup"))
                st["layout"] = o.get("layout")
                bad = [k for k, v in checks.items() if not v]
                st["history"].append({"iter": st["iter"], "knobs": dict(st["knobs"]), "result": "PASS" if not bad else "SIGNOFF " + " ".join(bad),
                                      "wns": m.get("signoff.wns_setup"), "cts_wns": m.get("cts.wns_setup"), "tns": m.get("signoff.tns_setup"),
                                      "drc": m.get("route.drc_count"), "area": m.get("signoff.design_area_um2")})
                if not bad:
                    st["final_check"] = True
                    add_step(job["id"], "sim", "rtl_sim", {"block": block, "rtl": st["rtl"]})
                else:
                    sig = "SIGNOFF " + " ".join("SETUP" if "setup" in b else "HOLD" if "hold" in b else "DRC" if "drc" in b else "UNROUTED" for b in bad)
                    self.diagnose(job, st, sig, f"signoff WNS {m.get('signoff.wns_setup')} ns, TNS {m.get('signoff.tns_setup')}; pre-route CTS "
                                  f"estimate {m.get('cts.wns_setup')} ns\n" + o.get("critical_path_tail", ""))
        elif base == "diagnose":
            st["knobs"], st["iter"] = o["knobs"], st["iter"] + 1
            if o["source"] not in ("rule",) and not o["source"].startswith("skill"):
                st["skill_candidates"].append({"kind": "knobs", "trigger": re.escape(st["last_sig"]), "action": {"knobs": {k: v[1] for k, v in o["changed"].items()},
                                                                                                           "resume_from": o["resume_from"]}})
            for s in STAGES[:STAGES.index(o["resume_from"])]:
                store.event(job["id"], "node_skip", "pnr", None, node=s, iter=st["iter"], reason=f"checkpoint reused (resume_from={o['resume_from']})")
            self.stage(job, st, o["resume_from"])
        save(job, st)

    def diagnose(self, job, st, sig, context):
        if st["iter"] + 1 >= self.MAX_PNR_ITERS:
            st["failed"] = f"P&R iteration budget exhausted ({self.MAX_PNR_ITERS})"
            store.event(job["id"], "guard", "guard", None, rule="budget.pnr_iters", verdict="deny", detail=st["failed"])
            return
        if not st["history"] or st["history"][-1]["iter"] != st["iter"]:
            st["history"].append({"iter": st["iter"], "knobs": dict(st["knobs"]), "result": sig})
        st["last_sig"] = sig
        add_step(job["id"], "diagnose", "pnr_diagnose", {"block": params(job)["block"], "signature": sig, "context": context[-3000:],
                 "knobs": st["knobs"], "history": st["history"], "policy": params(job).get("policy", "llm")})

    def flowback(self, job, st):
        added = []
        for c in st["skill_candidates"]:
            if store.one("SELECT 1 FROM skills WHERE kind=? AND trigger=?", (c["kind"], c["trigger"])):
                continue
            sid = "S" + uuid.uuid4().hex[:8]
            store.x("INSERT INTO skills VALUES(?,?,?,?,?,?,?,?,?)", (sid, c["kind"], None, c["trigger"], json.dumps(c["action"]),
                    json.dumps({"verified_by": "signoff gate + frozen testbench", "history": st["history"]}, default=str), job["id"], 1, time.time()))
            added.append(sid)
        store.event(job["id"], "skill", "curator", None, op="promote" if added else "none", added=added)


# ------------------------------------------------------------------ one question from the console chat
class AskFlow(Flow):
    def check(self, p):
        q = p.get("question")
        if not isinstance(q, str) or not q.strip() or len(q) > 2000:
            raise ValueError("question must be a non-empty string of at most 2000 characters")
        if not isinstance(p.get("history", []), list):
            raise ValueError("history must be a list of {role, content}")
        require("llm", "提问（大模型）")

    def start(self, job):
        p = params(job)
        add_step(job["id"], "answer", "assistant_ask", {"question": p["question"], "history": p.get("history", [])})

    def on_step(self, job, step):
        st = state(job)
        if step["status"] == "done":
            st.update(outputs(step))
        else:
            st["failed"] = step["error"]
        save(job, st)


FLOWS = {"eco_eval": EcoEval(), "eco_closure": EcoClosure(), "block_flow": BlockFlow(), "ask": AskFlow()}
