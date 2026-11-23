"""Step worker: runs exactly one tool for one step in its own session, then writes result.json and exits."""
import json, os, sys, time, traceback
from pathlib import Path
from . import store
from .tools import REGISTRY, StepContext, load_all, validate_args


def main(step_id):
    load_all()
    s = store.one("SELECT * FROM steps WHERE id=?", (step_id,))
    ctx = StepContext(s["job_id"], s["id"], s["dir"])
    res_p = Path(s["dir"]) / "result.json"
    if res_p.exists():
        res_p.rename(res_p.with_name(f"result.attempt{s['attempt'] - 1}.json"))
    args = json.loads(s["args_json"])
    t0 = time.time()
    try:
        validate_args(s["tool"], args)
        ctx.emit("tool_call", agent=s["name"], tool=s["tool"], side_effect=REGISTRY[s["tool"]]["side_effect"], args=args)
        out = REGISTRY[s["tool"]]["fn"](ctx, **args)
        res = {"ok": True, "outputs": out, "seconds": round(time.time() - t0, 2)}
        ctx.emit("tool_result", agent=s["name"], tool=s["tool"], ok=True, seconds=res["seconds"],
                 preview=json.dumps(out, default=str)[:1200])
    except Exception as e:
        no_retry = type(e).__name__ in ("GuardViolation", "Budget", "ValueError") or "NO_REPLAY" in str(e)
        res = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-3000:], "no_retry": no_retry,
               "seconds": round(time.time() - t0, 2)}
        ctx.emit("tool_result", agent=s["name"], tool=s["tool"], ok=False, seconds=res["seconds"], error=res["error"])
        ctx.log(res["trace"])
    tmp = res_p.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, default=str, indent=1))
    os.replace(tmp, res_p)


if __name__ == "__main__":
    main(sys.argv[1])
