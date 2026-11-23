"""Operator CLI: python3 -m wangsieda.cli jobs | job <id> | wait [secs] | submit <kind> '<json params>' | events <id> [n]"""
import json, sys, time
from . import store, engine


def show(j, verbose=True):
    print(f"JOB {j['id']} {j['kind']} {j['status']} {j.get('title') or ''} {j.get('error') or ''}")
    if not verbose:
        return
    for s in engine.steps_of(j["id"]):
        dur = round((s["ended"] or time.time()) - s["started"], 1) if s["started"] else "-"
        print(f"   {s['idx']:>2} {s['name']:<22} {s['tool']:<16} {s['status']:<9} {dur:>7}s {(s['error'] or '')[:160]}")
    st = json.loads(store.one("SELECT summary_json FROM jobs WHERE id=?", (j["id"],))["summary_json"] or "{}")
    keep = {k: st.get(k) for k in ("history", "plan", "approved", "rejected", "failed", "verified", "note", "evals") if st.get(k) is not None}
    print("   state:", json.dumps(keep, default=str)[:1500])


def rejudge(jid, reason):
    """Re-read an existing readback (no new STA) and re-judge; used after a parser fix. Logged on the job."""
    from .tools import load_all, call, StepContext
    from .tools.eco import parse_readback
    from .flows import state, save
    load_all()
    job = store.one("SELECT * FROM jobs WHERE id=?", (jid,))
    case = json.loads(job["params_json"])["case"]
    steps = engine.steps_of(jid)
    st = state(job)
    for key, rec in st.get("evals", {}).items():
        prefix = "" if key == "main" else key
        rb = next(s for s in steps if s["name"] == f"{prefix}readback" and s["status"] == "done")
        parsed = parse_readback(rb["outputs"]["readback_dir"])
        e = rec["eval"]; by = e.get("drc_by_type") or {}
        m = {"tns": e["tns"], "wns": e["wns"], "drc": e["drc"], "Short": by.get("Short"), "CutShort": by.get("Cut Short", 0 if by else None), "wl": e.get("wl"),
             "hbt_excess": e.get("hbt_excess"), "hold_tns": parsed["hold_tns"], "hold_wns": parsed["hold_wns"], "clock_values_ns": parsed["clock_values_ns"]}
        ctx = StepContext(jid, None, None)
        store.event(jid, "rejudge", "operator", None, reason=reason, readback=parsed["readback_dir"], new_sta=0)
        j = call("signoff_judge", {"case": case, "metrics": m}, ctx)
        call("ingest_result", {"case": case, "tag": rec["tag"], "run_id": rec["run_id"], "judged": j, "eval": e}, ctx)
        rec["judged"] = j
        rec["status"] = "V6 PASS" if (j.get("v6") or {}).get("pass") else "measured, gate failed: " + ",".join((j.get("v6") or {}).get("failed", []))
        print(rec["tag"], json.dumps({"raw_gain": j["raw_tns_gain_pct"], "v6": j.get("v6"), "strict": j.get("strict")})[:600])
    save(job, st)


USAGE = "usage: python3 -m wangsieda.cli jobs | job <id> | wait [secs] | submit <kind> '<json>' [title] | retry <step_id> | rejudge <job_id> [reason] | events <job_id> [n]"


def main(a):
    cmd = a[0] if a else "jobs"
    need = {"job": 2, "submit": 3, "retry": 2, "rejudge": 2, "events": 2}
    if cmd in ("-h", "--help", "help") or len(a) < need.get(cmd, 1):
        print(USAGE); return
    if cmd == "job" and not store.one("SELECT 1 FROM jobs WHERE id=?", (a[1],)):
        print("no such job", a[1]); return
    if cmd == "jobs":
        for j in store.q("SELECT * FROM jobs ORDER BY created DESC LIMIT 20"):
            show(j, verbose=False)
    elif cmd == "job":
        show(store.one("SELECT * FROM jobs WHERE id=?", (a[1],)))
    elif cmd == "wait":
        t0, lim = time.time(), float(a[1]) if len(a) > 1 else 540
        while time.time() - t0 < lim:
            if not store.one("SELECT count(*) n FROM jobs WHERE status IN ('queued','running')")["n"]:
                break
            time.sleep(5)
        for j in store.q("SELECT * FROM jobs ORDER BY created DESC LIMIT 5"):
            show(j)
    elif cmd == "submit":
        print(engine.create_job(a[1], json.loads(a[2]), a[3] if len(a) > 3 else None))
    elif cmd == "retry":
        engine.retry_step(a[1]); print("retrying", a[1])
    elif cmd == "rejudge":
        rejudge(a[1], a[2] if len(a) > 2 else "parser fix")
    elif cmd == "events":
        for e in store.q("SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT ?", (a[1], int(a[2]) if len(a) > 2 else 30))[::-1]:
            print(time.strftime("%H:%M:%S", time.localtime(e["ts"])), e["type"], e["agent"], e["payload_json"][:220])
    else:
        print(USAGE)


if __name__ == "__main__":
    main(sys.argv[1:])
