"""Tool registry. Every capability an agent (or an external MCP client) can use is a registered tool with a
JSON-schema input, a declared side-effect class, a scheduling pool ("license"), a timeout and a retry policy.
Tools run inside a StepContext: their own step directory, an event emitter and a log file."""
import json, os, time, traceback
from pathlib import Path
from .. import store

REGISTRY = {}

SIDE_EFFECTS = {
    "read": "reads project data only",
    "compute": "writes only inside its own step directory",
    "runtime": "writes a new, unique namespace under the evaluator runtime directory (never an existing one)",
    "llm": "paid model call (configured OpenAI-compatible model) under the job's call budget",
    "asset": "writes the wangsiEDA skill/asset library",
}


def tool(name, description, schema, side_effect="read", pool=None, timeout=600, retries=0, mcp=True):
    def deco(fn):
        REGISTRY[name] = dict(fn=fn, name=name, description=description, input_schema=schema, side_effect=side_effect,
                              pool=pool, timeout=timeout, retries=retries, mcp=mcp)
        return fn
    return deco


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


class StepContext:
    def __init__(self, job_id=None, step_id=None, step_dir=None):
        self.job_id, self.step_id = job_id, step_id
        self.dir = Path(step_dir) if step_dir else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
        self._log = open(self.dir / "step.log", "a", encoding="utf-8") if self.dir else None

    def emit(self, type_, agent="tool", **payload):
        if self.job_id:
            store.event(self.job_id, type_, agent, self.step_id, **payload)

    def log(self, text):
        if self._log:
            self._log.write(time.strftime("[%H:%M:%S] ") + str(text).rstrip() + "\n")
            self._log.flush()


def describe():
    return [{k: v for k, v in t.items() if k != "fn"} for t in REGISTRY.values()]


def validate_args(name, args):
    t = REGISTRY[name]
    sch = t["input_schema"]
    unknown = set(args) - set(sch.get("properties", {}))
    missing = [k for k in sch.get("required", []) if k not in args]
    if unknown or missing:
        raise ValueError(f"{name}: unknown args {sorted(unknown)} missing {missing}")
    for k, v in args.items():
        p = sch["properties"][k]
        ty = p.get("type")
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "number": isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": isinstance(v, bool),
              "object": isinstance(v, dict), "array": isinstance(v, list)}.get(ty, True)
        if not ok:
            raise ValueError(f"{name}: arg {k} must be {ty}")
        if "enum" in p and v not in p["enum"]:
            raise ValueError(f"{name}: arg {k} must be one of {p['enum']}")
        if ty in ("integer", "number"):
            if "minimum" in p and v < p["minimum"] or "maximum" in p and v > p["maximum"]:
                raise ValueError(f"{name}: arg {k}={v} outside [{p.get('minimum')}, {p.get('maximum')}]")


def call(name, args, ctx=None):
    """Synchronous call (read-only tools from the API/MCP; long tools go through the job engine)."""
    validate_args(name, args)
    ctx = ctx or StepContext()
    return REGISTRY[name]["fn"](ctx, **args)


def load_all():
    from . import catalog_tools, eco, blockflow, agent_tools, viz_tools, assistant  # noqa: F401  (registration side effect)
    return REGISTRY
