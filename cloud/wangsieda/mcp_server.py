"""MCP (Model Context Protocol) server over stdio, JSON-RPC 2.0, so any MCP client (Claude Desktop/Code, Cursor, an in-house
LangGraph agent) can drive wangsiEDA:

  read-only tools  -> executed directly (catalog, candidate pool, evidence, judge, evaluator progress, host status)
  submit_* tools   -> create durable jobs on the wangsiEDA engine (the daemon executes them; results via job_status)

Client config example: python wangsi.py mcp
"""
import json, sys, traceback
from . import store, engine
from .tools import REGISTRY, call, load_all, obj
from .tools.catalog_tools import CASE
from .tools.blockflow import BLOCK
from . import __version__

PROTOCOL = "2024-11-05"
SUBMIT = {
    "submit_eco_eval": ("eco_eval", "Evaluate one candidate ECO program on the real signoff flow (build -> signoff evaluator -> "
                        "readback -> frozen gates). Returns a job id.", obj({"case": CASE, "tag": {"type": "string"}}, ["case", "tag"])),
    "submit_eco_closure": ("eco_closure", "Agentic ECO closure: LLM plan over the catalog -> critic -> real evaluations -> lesson. "
                           "Returns a job id.", obj({"case": CASE, "budget": {"type": "integer", "minimum": 1, "maximum": 4},
                                                     "dry_run": {"type": "boolean"}}, ["case"])),
    "submit_block_flow": ("block_flow", "RTL -> GDS for a block with closed-loop RTL debug and P&R repair. Returns a job id.",
                          obj({"block": BLOCK, "policy": {"type": "string", "enum": ["llm", "rule"]}}, ["block"])),
}
EXTRA = {
    "job_status": ("Status, steps and outcome of a wangsiEDA job.", obj({"job_id": {"type": "string"}}, ["job_id"])),
}


def tools_list():
    out = [{"name": n, "description": t["description"] + f" [side effect: {t['side_effect']}]", "inputSchema": t["input_schema"]}
           for n, t in REGISTRY.items() if t["side_effect"] == "read" and t["mcp"]]
    out += [{"name": n, "description": d, "inputSchema": s} for n, (k, d, s) in SUBMIT.items()]
    out += [{"name": n, "description": d, "inputSchema": s} for n, (d, s) in EXTRA.items()]
    return out


def tools_call(name, args):
    if name in SUBMIT:
        return {"job_id": engine.create_job(SUBMIT[name][0], args, title=f"mcp:{name}"),
                "note": "queued; the wangsiEDA daemon executes it. Poll job_status."}
    if name == "job_status":
        j = store.one("SELECT id, kind, status, created, ended, summary_json FROM jobs WHERE id=?", (args["job_id"],))
        if not j:
            raise ValueError("no such job")
        j["steps"] = [{k: s[k] for k in ("name", "tool", "status", "error")} for s in engine.steps_of(args["job_id"])]
        j["state"] = json.loads(j.pop("summary_json") or "{}")
        return j
    t = REGISTRY.get(name)
    if not t or t["side_effect"] != "read":
        raise ValueError(f"tool {name} is not exposed synchronously")
    return call(name, args)


def handle(msg):
    mid, method, p = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        res = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
               "serverInfo": {"name": "wangsiEDA", "version": __version__}}
    elif method == "tools/list":
        res = {"tools": tools_list()}
    elif method == "tools/call":
        try:
            out = tools_call(p["name"], p.get("arguments") or {})
            res = {"content": [{"type": "text", "text": json.dumps(out, default=str, ensure_ascii=False)[:100000]}], "isError": False}
        except Exception as e:
            res = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    elif method == "ping":
        res = {}
    elif mid is None:
        return None  # notification (e.g. notifications/initialized)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def main():
    load_all()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            out = handle(json.loads(line))
        except Exception as e:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        if out is not None:
            sys.stdout.write(json.dumps(out, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
