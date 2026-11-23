"""Natural-language entry ("ask wangsiEDA"): an LLM answers questions about designs, candidates, evaluations, jobs and the host
using read-only tools only. It may PROPOSE one job; the harness validates the proposal and the person submits it with a click.
Nothing is ever submitted, edited or deleted by this tool."""
import json, re
from . import tool, obj, REGISTRY, call
from .agent_tools import prompt, short, guard, GuardViolation
from .catalog_tools import measured_index
from .. import llm, store, config as C

ASK_TOOLS = ["design_info", "signed_off_states", "candidate_pool", "eval_history", "program_evidence", "skills_lookup",
             "host_status", "eval_progress", "list_jobs", "job_info"]
JOB_KINDS = ("eco_eval", "eco_closure", "block_flow")
JOB_PARAMS = {"eco_eval": {"case", "tag"}, "eco_closure": {"case", "budget", "dry_run", "max_rounds"}, "block_flow": {"block", "policy"}}
MAX_ROUNDS = 6


@tool("list_jobs", "Recent wangsiEDA jobs (id, kind, design or block, status, start/end time, error). Excludes chat questions.",
      obj({"limit": {"type": "integer", "minimum": 1, "maximum": 50}, "kind": {"type": "string", "enum": list(JOB_KINDS)},
           "status": {"type": "string"}}))
def list_jobs(ctx, limit=10, kind=None, status=None):
    w, a = ["kind != 'ask'"], []
    if kind:
        w.append("kind=?"); a.append(kind)
    if status:
        w.append("status=?"); a.append(status)
    return {"jobs": store.q(f"SELECT id, kind, case_name, title, status, created, started, ended, error FROM jobs WHERE {' AND '.join(w)} "
                            "ORDER BY created DESC LIMIT ?", (*a, limit))}


@tool("job_info", "One job's steps (name, tool, status, error) and its recorded outcome (plan, evaluations and gate verdicts).",
      obj({"job_id": {"type": "string"}}, ["job_id"]))
def job_info(ctx, job_id):
    j = store.one("SELECT id, kind, case_name, title, status, created, started, ended, error, summary_json FROM jobs WHERE id=?", (job_id,))
    if not j:
        raise ValueError("no such job " + job_id)
    st = json.loads(j.pop("summary_json") or "{}")
    keep = {k: st.get(k) for k in ("plan", "approved", "rejected", "evals", "failed", "verified", "note", "history", "metrics") if st.get(k)}
    j["outcome"] = keep
    j["steps"] = store.q("SELECT name, tool, status, error FROM steps WHERE job_id=? ORDER BY idx", (job_id,))
    return j


def site_context():
    f = C.features()
    return {"designs": C.CASES, "blocks": C.blocks(), "ready_features": [k for k, v in f.items() if v["ok"]],
            "gates": "Ge = envelope gate (fields v6_pass / v6_fails / v6_bounds); Gs = exact gate (strict_pass / strict_fails)"}


def check_proposal(p):
    """Return (job or None, reason). A proposal becomes a button only if it would be accepted by the job engine."""
    if not p:
        return None, None
    if not isinstance(p, dict) or p.get("kind") not in JOB_KINDS or not isinstance(p.get("params"), dict):
        return None, f"not a valid job proposal (kind must be one of {list(JOB_KINDS)} with a params object)"
    kind, params = p["kind"], dict(p["params"])
    extra = sorted(set(params) - JOB_PARAMS[kind])
    if extra:
        return None, f"parameters not allowed for {kind}: {extra}"
    try:
        from ..flows import FLOWS
        FLOWS[kind].check(params)
        if kind == "eco_eval":
            if not store.one("SELECT 1 FROM candidates WHERE case_name=? AND tag=?", (params["case"], params["tag"])):
                return None, f"{params['tag']} is not in the {params['case']} candidate pool"
            if measured_index(params["case"]).get(params["tag"]):
                return None, f"{params['tag']} was already measured (no replay)"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    return {"kind": kind, "params": params, "why": str(p.get("why", ""))[:500]}, None


@tool("assistant_ask", "Answer one question about this wangsiEDA install with read-only tools; may propose (never submit) one job.",
      obj({"question": {"type": "string"}, "history": {"type": "array"}}, ["question"]), side_effect="llm", pool="llm", timeout=900, mcp=False)
def assistant_ask(ctx, question, history=None):
    tools_doc = "\n".join(f"- {n}: {REGISTRY[n]['description']} args={json.dumps(REGISTRY[n]['input_schema']['properties'])}" for n in ASK_TOOLS)
    system = prompt("assistant").replace("SITE_CONTEXT", json.dumps(site_context(), ensure_ascii=False)) + "\nTOOLS:\n" + tools_doc
    msgs = [{"role": "system", "content": system}]
    for h in (history or [])[-6:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:2000]})
    msgs.append({"role": "user", "content": question[:2000]})
    used, out = [], None
    for rnd in range(MAX_ROUNDS):
        out = llm.ask_json(ctx, "assistant", msgs, f"ask_round_{rnd}", max_calls=MAX_ROUNDS + 3)
        if out is None:
            raise RuntimeError("the model returned no parseable reply")
        msgs.append({"role": "assistant", "content": json.dumps(out, ensure_ascii=False)})
        if out.get("thought"):
            ctx.emit("thought", agent="assistant", text=str(out["thought"])[:600])
        if "final" in out:
            break
        name, args = out.get("tool"), out.get("args") or {}
        if name not in ASK_TOOLS:
            obs = {"error": f"tool {name!r} is not available; read-only tools: {ASK_TOOLS}"}
            ctx.emit("guard", agent="guard", rule="assistant.tool_whitelist", verdict="deny", detail=str(name))
        else:
            props = REGISTRY[name]["input_schema"]["properties"]
            if "case" in props and "case" not in args and len(C.CASES) == 1:
                args["case"] = C.CASES[0]
            try:
                ctx.emit("tool_call", agent="assistant", tool=name, args=args)
                obs = call(name, args, ctx)
                used.append(name)
            except Exception as e:
                obs = {"error": f"{type(e).__name__}: {e}"}
        msgs.append({"role": "user", "content": "OBSERVATION " + short(obs, 6000)})
    else:
        msgs.append({"role": "user", "content": "Round limit reached. Give your final answer now from what you have."})
        out = llm.ask_json(ctx, "assistant", msgs, "ask_final", max_calls=MAX_ROUNDS + 3) or {}
    final = out.get("final") if isinstance(out.get("final"), dict) else {"answer": str(out.get("final") or "")}
    answer = str(final.get("answer") or "").strip() or "(no answer)"
    raw = final.get("proposed_job")
    job, reason = check_proposal(raw)
    if raw:
        ctx.emit("guard", agent="guard", rule="assistant.proposal", verdict="allow" if job else "deny", detail=reason or json.dumps(job, ensure_ascii=False))
    ctx.emit("answer", agent="assistant", answer=answer[:3000], proposed_job=job, proposal_rejected=reason)
    return {"answer": answer, "proposed_job": job, "proposed_job_raw": raw, "proposal_rejected": reason, "tools_used": used}
