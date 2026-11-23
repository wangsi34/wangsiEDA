"""Agent tools: the LLM proposes, the harness disposes.

eco_plan    ReAct planner over read-only catalog tools -> typed choice of candidate ECO programs with predictions
eco_critic  deterministic gate on the plan: in pool, never measured (no replay), buildable by the frozen builder, budget
eco_learn   compares outcome with prediction, writes a lesson/skill (verified only when the envelope gate Ge certified a gain)
rtl_debug   minimal RTL patch for a failing frozen testbench (guards: target file, unique anchor, <=3 lines, interface frozen)
pnr_diagnose knob action for a failed P&R stage / signoff (guards: whitelist, ranges, clock contract, no revisits, DAG resume point)
"""
import json, os, re, time, uuid
from pathlib import Path
from . import tool, obj, REGISTRY, call, StepContext
from .catalog_tools import CASE, measured_index
from .blockflow import KNOBS, STAGES, BLOCK
from .. import llm, store, catalog

PROMPT_OVERRIDE = {}   # {name: text}; set by the prompt evaluator, never by agents


def prompt_source(name):
    """Where the system prompt for `name` comes from: your own prompts/<name>.txt first, then the shipped default
    prompts/default/<name>.<variant>.txt (unless use_default_prompts is false). Returns (text, source) or (None, reason)."""
    from .. import config as _C
    if name in PROMPT_OVERRIDE:
        return PROMPT_OVERRIDE[name], "override"
    f = _C.ROOT / "prompts" / f"{name}.txt"
    t = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    if t and not t.startswith("#"):
        return t, f"prompts/{name}.txt"
    if _C.CFG.get("use_default_prompts", True):
        cfg_v = _C.CFG.get("default_prompt_variant")          # "B" for all agents, or {"rtl_debug": "C", ...}
        cfg_v = cfg_v.get(name) if isinstance(cfg_v, dict) else cfg_v
        v = os.environ.get("WANGSI_PROMPT_VARIANT") or cfg_v or DEFAULT_VARIANT.get(name, "B")
        d = _C.ROOT / "prompts" / "default" / f"{name}.{v}.txt"
        if d.exists():
            return d.read_text(encoding="utf-8").strip(), f"prompts/default/{name}.{v}.txt"
    return None, "not configured"


DEFAULT_VARIANT = {"eco_planner": "B", "reflector": "C", "rtl_debug": "B", "pnr_diagnose": "C", "assistant": "B"}   # prompt-eval rounds 1-2 (deepseek-flash, thinking high); see docs/提示词评测.md


def prompt(name):
    t, src = prompt_source(name)
    if not t:
        raise RuntimeError(f"未配置提示词 prompts/{name}.txt（或在配置中打开 use_default_prompts）")
    return t


READ_TOOLS = ["design_info", "signed_off_states", "candidate_pool", "eval_history", "program_evidence", "skills_lookup"]


class GuardViolation(Exception):
    pass


def guard(ctx, rule, ok, detail):
    ctx.emit("guard", agent="guard", rule=rule, verdict="allow" if ok else "deny", detail=detail)
    if not ok:
        raise GuardViolation(f"{rule}: {detail}")


def short(o, n=6000):
    s = json.dumps(o, default=str, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "...[truncated]"


# ------------------------------------------------------------------ ECO closure agent
ECO_SYSTEM = None   # 提示词不随仓库发布，见 prompts/eco_planner.txt


@tool("eco_plan", "LLM ECO planner: investigates the design with read-only tools and returns a typed plan (candidate programs + "
      "predictions + risks).", obj({"case": CASE, "budget": {"type": "integer", "minimum": 1, "maximum": 4},
                                   "max_rounds": {"type": "integer", "minimum": 2, "maximum": 12}}, ["case"]),
      side_effect="llm", pool="llm", timeout=1800)
def eco_plan(ctx, case, budget=1, max_rounds=8):
    tools_doc = "\n".join(f"- {n}: {REGISTRY[n]['description']} args={json.dumps(REGISTRY[n]['input_schema']['properties'])}" for n in READ_TOOLS)
    msgs = [{"role": "system", "content": prompt("eco_planner").replace("BUDGET", str(budget)) + "\nTOOLS:\n" + tools_doc},
            {"role": "user", "content": f"Design: {case}. Budget: {budget} real evaluation(s). Start by looking at the design and the evidence."}]
    for rnd in range(max_rounds):
        out = llm.ask_json(ctx, "eco-planner", msgs, f"plan_round_{rnd}", max_calls=max_rounds + 4)
        if out is None:
            raise RuntimeError("planner returned no parseable reply")
        msgs.append({"role": "assistant", "content": json.dumps(out, ensure_ascii=False)})
        ctx.emit("thought", agent="eco-planner", text=out.get("thought", ""))
        if "final" in out:
            plan = out["final"]
            plan["programs"] = (plan.get("programs") or [])[:budget]
            ctx.emit("plan", agent="eco-planner", plan=plan)
            return {"case": case, "plan": plan, "rounds": rnd + 1}
        name, args = out.get("tool"), out.get("args") or {}
        if name not in READ_TOOLS:
            obs = {"error": f"tool {name!r} not allowed; read-only tools: {READ_TOOLS}"}
            ctx.emit("guard", agent="guard", rule="planner.tool_whitelist", verdict="deny", detail=str(name))
        else:
            args.setdefault("case", case) if "case" in REGISTRY[name]["input_schema"]["properties"] else None
            try:
                ctx.emit("tool_call", agent="eco-planner", tool=name, args=args)
                obs = call(name, args, ctx)
            except Exception as e:
                obs = {"error": f"{type(e).__name__}: {e}"}
        msgs.append({"role": "user", "content": "OBSERVATION " + short(obs)})
    msgs.append({"role": "user", "content": "Round limit reached. Return your final plan now."})
    out = llm.ask_json(ctx, "eco-planner", msgs, "plan_final", max_calls=max_rounds + 4) or {}
    plan = out.get("final") or {"programs": [], "hypothesis": "no plan"}
    plan["programs"] = (plan.get("programs") or [])[:budget]
    ctx.emit("plan", agent="eco-planner", plan=plan)
    return {"case": case, "plan": plan, "rounds": max_rounds + 1}


@tool("eco_critic", "Deterministic critic for an ECO plan: each program must be in the candidate pool, never measured (no replay), and "
      "buildable by the frozen builder; enforces the evaluation budget.", obj({"case": CASE, "plan": {"type": "object"},
                                                                           "budget": {"type": "integer", "minimum": 1, "maximum": 4}}, ["case", "plan"]),
      side_effect="read", pool="cpu", timeout=900)
def eco_critic(ctx, case, plan, budget=1):
    approved, rejected = [], []
    mi = measured_index(case)
    for p in plan.get("programs", []):
        tag = str(p.get("tag", ""))
        try:
            in_pool = store.one("SELECT 1 FROM candidates WHERE case_name=? AND tag=?", (case, tag)) is not None
            guard(ctx, "critic.in_pool", in_pool, f"{tag} in {case} candidate pool")
            guard(ctx, "critic.no_replay", not mi.get(tag), f"{tag} measured {len(mi.get(tag, []))}x before")
            v = call("program_validate", {"case": case, "tag": tag}, ctx)
            guard(ctx, "critic.buildable", v["buildable"], v["builder_status"][:200])
            guard(ctx, "critic.budget", len(approved) < budget, f"{len(approved) + 1}/{budget}")
            approved.append(dict(p, tag=tag))
        except GuardViolation as e:
            rejected.append({"tag": tag, "reason": str(e)})
    if not approved:
        raise RuntimeError(f"critic approved nothing: {rejected}")
    return {"approved": approved, "rejected": rejected}


@tool("eco_learn", "Post-evaluation reflection: compares the measured, judged outcome with the plan's prediction and writes a lesson "
      "to the asset library (verified=1 only if the envelope gate Ge certified a gain).",
      obj({"case": CASE, "plan": {"type": "object"}, "outcomes": {"type": "array"}}, ["case", "plan", "outcomes"]),
      side_effect="asset", pool="llm", timeout=600)
def eco_learn(ctx, case, plan, outcomes):
    msgs = [{"role": "system", "content": prompt("reflector")},
            {"role": "user", "content": f"Design {case}. PLAN: {short(plan, 3000)}\nOUTCOMES: {short(outcomes, 6000)}"}]
    out = llm.ask_json(ctx, "reflector", msgs, "lesson", max_calls=40) or {}
    certified = any((o.get("judged") or {}).get("v6", {}).get("pass") for o in outcomes)
    sid = "L" + uuid.uuid4().hex[:8]
    store.x("INSERT INTO skills VALUES(?,?,?,?,?,?,?,?,?)", (sid, "lesson", case, re.escape(str(out.get("trigger", ""))), json.dumps(out, ensure_ascii=False),
            json.dumps([{"tag": o.get("tag"), "v6": (o.get("judged") or {}).get("v6")} for o in outcomes], default=str), ctx.job_id,
            int(certified), time.time()))
    ctx.emit("skill", agent="curator", op="promote" if certified else "record", id=sid, lesson=out.get("lesson"), verified=certified)
    return {"skill_id": sid, "verified": certified, "lesson": out}


# ------------------------------------------------------------------ block-flow agents
@tool("rtl_debug", "LLM RTL debug: proposes a minimal patch for a failing frozen-testbench simulation; guarded and applied to a copy.",
      obj({"block": BLOCK, "rtl": {"type": "string"}, "sim": {"type": "object"}}, ["block", "rtl", "sim"]),
      side_effect="llm", pool="llm", timeout=600)
def rtl_debug(ctx, block, rtl, sim):
    src = Path(rtl).read_text()
    sig = "SIM_MISMATCH " + " ".join(f"op={o}" for o in sim.get("failing_ops", []))
    skill = next((s for s in store.q("SELECT * FROM skills WHERE kind='rtl_patch' AND verified=1")
                  if re.search(s["trigger"], sig)), None)
    if skill:
        patch, source = json.loads(skill["action_json"]), "skill:" + skill["id"]
        ctx.emit("skill", agent="curator", op="hit", id=skill["id"], signature=sig)
    else:
        from .. import config as _C
        spec = _C.BLOCKS / block / "spec.md"
        numbered = "\n".join(f"{i + 1:3d}| {l}" for i, l in enumerate(src.splitlines()))
        msgs = [{"role": "system", "content": prompt("rtl_debug")},
                {"role": "user", "content": f"FILE: {block}.v\nSPEC:\n{spec.read_text()}\nSIM:\n" + "\n".join(sim.get("mismatches", [])) + f"\nRTL:\n{numbered}"}]
        patch, source = llm.ask_json(ctx, "rtl-debug", msgs, "rtl_fix"), "llm"
        if not patch:
            raise RuntimeError("no patch proposed")
    guard(ctx, "patch.schema", isinstance(patch.get("find"), str) and isinstance(patch.get("replace"), str), "find/replace are strings")
    guard(ctx, "patch.target", patch.get("file") == f"{block}.v", f"file={patch.get('file')!r}")
    n = src.count(patch.get("find") or "\0")
    guard(ctx, "patch.anchor_unique", n == 1, f"anchor occurs {n}x")
    lines = max(len(patch["find"].splitlines()), len(patch.get("replace", "").splitlines()))
    guard(ctx, "patch.size", lines <= 3, f"{lines} line(s) <= 3")
    guard(ctx, "patch.interface_frozen", not re.search(r"\b(input|output|inout|module|parameter)\b", patch["find"] + patch["replace"]),
          "no port/module/parameter edits")
    new = src.replace(patch["find"], patch["replace"], 1)
    out = ctx.dir / f"{block}.v"
    out.write_text(new)
    import difflib
    diff = "".join(difflib.unified_diff(src.splitlines(1), new.splitlines(1), "a/" + Path(rtl).name, "b/" + Path(rtl).name))
    ctx.emit("patch", agent="rtl-debug", diff=diff, diagnosis=patch.get("diagnosis", ""), source=source)
    return {"rtl": str(out), "diff": diff, "source": source, "signature": sig,
            "skill_candidate": {"kind": "rtl_patch", "trigger": re.escape(sig), "action": {k: patch[k] for k in ("file", "find", "replace")}}}


@tool("pnr_diagnose", "LLM physical-design diagnosis: picks a validated knob change and the DAG resume point after a failed stage or "
      "signoff gate (clock period is a frozen contract; configurations already tried are refused).",
      obj({"block": BLOCK, "signature": {"type": "string"}, "context": {"type": "string"}, "knobs": {"type": "object"},
           "history": {"type": "array"}, "policy": {"type": "string", "enum": ["llm", "rule"]}}, ["block", "signature", "knobs", "history"]),
      side_effect="llm", pool="llm", timeout=600)
def pnr_diagnose(ctx, block, signature, knobs, history, context="", policy="llm"):
    tried = [h["knobs"] for h in history]
    table = "\n".join(f"iter {h['iter']}: {json.dumps({k: v for k, v in h['knobs'].items() if tried and tried[0].get(k) != v})} -> "
                      f"{h['result']}" for h in history)
    action, source = None, "rule"
    skill = next((s for s in store.q("SELECT * FROM skills WHERE kind='knobs' AND verified=1") if re.search(s["trigger"], signature)), None)
    if skill and dict(knobs, **json.loads(skill["action_json"])["knobs"]) not in tried:
        action, source = json.loads(skill["action_json"]), "skill:" + skill["id"]
        ctx.emit("skill", agent="curator", op="hit", id=skill["id"], signature=signature)
    if action is None and policy == "llm":
        msgs = [{"role": "system", "content": prompt("pnr_diagnose")},
                {"role": "user", "content": f"SIGNATURE: {signature}\nKNOBS: {json.dumps(knobs)}\nTRIED:\n{table}\nCONTEXT:\n{context[-2500:]}"}]
        action = llm.ask_json(ctx, "physical", msgs, "pnr_fix")
        source = "llm" if action else "rule"
    if action is None:
        action = rule_action(signature, knobs, context)
    ctx.emit("action", agent="physical", source=source, action=action)
    new, resume = dict(knobs), "route"
    if not isinstance(action.get("knobs"), dict):
        action = dict(action, knobs={})
    bad = sorted(set(k.lower() for k in action.get("knobs", {})) & {"clock_period", "period", "sdc", "constraints"})
    guard(ctx, "contract.clock_frozen", not bad, "constraint keys " + ",".join(bad) if bad else "no constraint keys")
    unknown = [k for k in action.get("knobs", {}) if k not in KNOBS]
    guard(ctx, "schema.knob_whitelist", not unknown, f"unknown {unknown}" if unknown else f"keys {list(action.get('knobs', {}))}")
    earliest = len(STAGES)
    for k, v in action["knobs"].items():
        typ, lo, hi, stage = KNOBS[k]
        v = (v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")) if typ is bool else typ(v)
        if lo is not None:
            guard(ctx, "schema.knob_range", lo <= v <= hi, f"{k}={v} in [{lo},{hi}]")
        new[k] = v
        earliest = min(earliest, STAGES.index(stage))
    guard(ctx, "action.non_empty", new != knobs, "changes something")
    guard(ctx, "action.not_revisited", new not in tried, f"new configuration ({len(tried)} tried)")
    req = action.get("resume_from") if action.get("resume_from") in STAGES else "floorplan"
    need = STAGES[earliest]
    if STAGES.index(req) > earliest:
        ctx.emit("guard", agent="guard", rule="dag.resume_point", verdict="correct", detail=f"{req} -> {need}")
        req = need
    changed = {k: [knobs.get(k), v] for k, v in new.items() if knobs.get(k) != v}
    ctx.emit("knob_change", agent="physical", changed=changed, resume_from=req, source=source)
    return {"knobs": new, "resume_from": req, "source": source, "changed": changed, "diagnosis": action.get("diagnosis", "")}


def rule_action(sig, k, context):
    if "GPL-0301" in sig:
        m = re.search(r"Utilization:\s+([\d.]+) %", context)
        pct = float(m.group(1)) if m else 145
        return {"diagnosis": f"effective utilization {pct}% > 100%", "knobs": {"core_utilization": max(20, int(round(k["core_utilization"] * 95 / pct / 5)) * 5)},
                "resume_from": "floorplan"}
    if "GPL-0302" in sig:
        return {"diagnosis": "density below utilization", "knobs": {"place_density": min(0.95, round(k["place_density"] + 0.15, 2))}, "resume_from": "place"}
    if "SETUP" in sig and k.get("setup_slack_margin_ps", 0) < 40:
        return {"diagnosis": "post-route miss vs estimate: over-fix margin", "knobs": {"setup_slack_margin_ps": 40}, "resume_from": "cts"}
    if "SETUP" in sig and not k.get("timing_driven"):
        return {"diagnosis": "margin saturated: timing-driven placement", "knobs": {"timing_driven": True}, "resume_from": "place"}
    return {"diagnosis": "add whitespace", "knobs": {"core_utilization": max(20, k["core_utilization"] - 5), "place_density": 0.8}, "resume_from": "floorplan"}
