"""Durable job engine.

* A job is a dynamic DAG of steps. Its controller (flows.py) is a state machine that appends steps when a step finishes
  (conditional edges, loops for repair/re-run), in the style of LangGraph graphs and OpenLane 2 Flow/Step/State.
* Every step runs one registered tool in its own detached worker process (setsid), in its own numbered directory
  (jobs/<job>/<NN>-<step>/), writing result.json. The daemon only supervises.
* Durability: all state is in SQLite; on daemon restart, running steps are re-attached by (pid, /proc start time). A worker
  that disappeared without result.json is a lost step: retried if attempts remain, else failed. SSH/UI disconnects do not matter.
* Scheduling pools act like EDA licenses: own caps per pool plus, for the evaluator, a host-wide cap that counts every
  evaluator process on the machine (pools.evaluator.marker) (other projects' replicas included), and a host admission gate (load, disk, memory).
"""
import json, os, shutil, signal, subprocess, sys, threading, time, traceback, uuid
from pathlib import Path
from . import config as C, store
from .tools import REGISTRY, load_all

load_all()
STOP = threading.Event()


def now():
    return time.time()


def pstart(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except OSError:
        return None


def alive(pid, start):
    if not pid:
        return False
    try:
        st = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return False
    return st[0] != "Z" and st[19] == start


def reap():
    """Collect exited children (workers spawned by this daemon) so they do not linger as zombies."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def count_marker(marker):
    n = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            a = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        exe = a[0].rsplit(b"/", 1)[-1]
        if len(a) > 2 and exe.startswith(b"python") and any(marker.encode() in x and x.endswith(b".py") for x in a[1:3]):
            n += 1
    return n


def host_snapshot():
    la = os.getloadavg()
    du = shutil.disk_usage(C.HOST_DISK)
    linux = sys.platform.startswith("linux")
    available_gib = None
    if linux:
        with open("/proc/meminfo") as stats:
            for line in stats:
                if line.startswith("MemAvailable:"):
                    available_gib = round(int(line.split()[1]) / 2**20, 1)
                    break
    pools = {}
    for name, p in C.POOLS.items():
        own = store.one("SELECT count(*) n FROM steps WHERE status='running' AND pool=?", (name,))["n"]
        pools[name] = {"own_running": own, "own_cap": p["own"]}
        if "marker" in p:
            pools[name].update(host_running=count_marker(p["marker"]) if linux else None, host_cap=p["global"])
    return {"load": [round(x, 2) for x in la], "ncpu": os.cpu_count(), "free_home_GiB": round(du.free / 2**30, 1),
            "disk_used_pct": round(100 * du.used / du.total, 1), "mem_available_GiB": available_gib,
            "pools": pools, "at": now()}


def pool_free(pool, snap):
    p = C.POOLS.get(pool) or {"own": 4}
    own = store.one("SELECT count(*) n FROM steps WHERE status='running' AND pool=?", (pool,))["n"]
    if own >= p["own"]:
        return False, f"{pool}: own {own}/{p['own']}"
    if "marker" in p:
        if snap["pools"][pool]["host_running"] is None or snap["mem_available_GiB"] is None:
            return False, "host admission metrics unavailable"
        g = C.HOST_GATE
        if snap["pools"][pool]["host_running"] >= p["global"]:
            return False, f"{pool}: host {snap['pools'][pool]['host_running']}/{p['global']}"
        if snap["load"][0] >= g["loadavg1_lt"] or snap["free_home_GiB"] < g["free_home_ge_GiB"] or snap["mem_available_GiB"] < g["mem_available_ge_GiB"]:
            return False, "host admission gate (load/disk/memory)"
    return True, "ok"


# ------------------------------------------------------------------ job / step API (used by daemon, flows, CLI)
def create_job(kind, params, title=None, parent=None):
    from .flows import FLOWS
    if kind not in FLOWS:
        raise ValueError("unknown job kind " + kind)
    FLOWS[kind].check(params)
    jid = time.strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    store.x("INSERT INTO jobs(id, kind, case_name, title, params_json, status, created, summary_json, parent) VALUES(?,?,?,?,?,?,?,?,?)",
            (jid, kind, params.get("case") or params.get("block"), title or kind, json.dumps(params), "queued", now(), "{}", parent))
    (C.JOBS / jid).mkdir(parents=True, exist_ok=True)
    store.event(jid, "job_created", "supervisor", kind=kind, params=params)
    return jid


def add_step(jid, name, tool, args, deps=(), pool=None, max_attempts=None, timeout=None):
    t = REGISTRY[tool]
    idx = store.one("SELECT coalesce(max(idx), 0) + 1 n FROM steps WHERE job_id=?", (jid,))["n"]
    sid = f"{jid}:{idx:02d}"
    sdir = C.JOBS / jid / f"{idx:02d}-{name}"
    store.x("""INSERT INTO steps(id, job_id, idx, name, tool, deps_json, args_json, pool, status, max_attempts, timeout_s, dir)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, jid, idx, name, tool, json.dumps(list(deps)), json.dumps(args, default=str), pool or t["pool"] or "cpu", "pending",
             max_attempts or 1 + t["retries"], timeout or t["timeout"], str(sdir)))
    store.event(jid, "step_added", "supervisor", sid, name=name, tool=tool, deps=list(deps), pool=pool or t["pool"],
                side_effect=t["side_effect"], args=args)
    return sid


def steps_of(jid):
    out = store.q("SELECT * FROM steps WHERE job_id=? ORDER BY idx", (jid,))
    for s in out:
        s["args"] = json.loads(s.pop("args_json"))
        s["deps"] = json.loads(s.pop("deps_json"))
        s["outputs"] = json.loads(s.pop("outputs_json") or "null")
    return out


def retry_step(sid):
    """Re-run a failed step (same args) and reopen its job; downstream steps are then grown by the controller as usual."""
    from .flows import FLOWS
    s = store.one("SELECT * FROM steps WHERE id=?", (sid,))
    if not s or s["status"] not in ("failed", "skipped"):
        raise ValueError("only failed/skipped steps can be retried")
    j = store.one("SELECT * FROM jobs WHERE id=?", (s["job_id"],))
    store.x("UPDATE steps SET status='pending', attempt=0, error=NULL, ended=NULL WHERE id=?", (sid,))
    store.x("UPDATE jobs SET status='running', ended=NULL WHERE id=?", (j["id"],))
    FLOWS[j["kind"]].on_retry(j, s)
    store.event(j["id"], "step_retry_manual", "operator", sid, name=s["name"])


def cancel_job(jid):
    store.x("UPDATE jobs SET status='cancelling' WHERE id=? AND status IN ('queued','running')", (jid,))


# ------------------------------------------------------------------ supervisor loop
def spawn(step):
    Path(step["dir"]).mkdir(parents=True, exist_ok=True)
    attempt = step["attempt"] + 1
    log = open(Path(step["dir"]) / "worker.log", "a")
    p = subprocess.Popen([sys.executable, "-B", "-m", "wangsieda.worker", step["id"]], cwd=str(C.ROOT), stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True, env=dict(os.environ, PYTHONPATH=str(C.ROOT)))
    store.x("UPDATE steps SET status='running', pid=?, pstart=?, attempt=?, started=?, ended=NULL, error=NULL WHERE id=?",
            (p.pid, pstart(p.pid), attempt, now(), step["id"]))
    store.event(step["job_id"], "step_start", "supervisor", step["id"], name=step["name"], tool=step["tool"], pid=p.pid, attempt=attempt,
                pool=step["pool"])
    return p


def finalize(step, lost=False):
    res_p = Path(step["dir"]) / "result.json"
    res = json.loads(res_p.read_text()) if res_p.exists() else None
    ended = (step["started"] + res["seconds"]) if res and res.get("seconds") is not None and step["started"] else now()
    if res and res.get("ok"):
        store.x("UPDATE steps SET status='done', ended=?, rc=0, outputs_json=? WHERE id=?", (ended, json.dumps(res["outputs"], default=str), step["id"]))
        store.event(step["job_id"], "step_done", "supervisor", step["id"], name=step["name"], seconds=round(now() - (step["started"] or now()), 1),
                    outputs_preview=json.dumps(res["outputs"], default=str)[:1500])
        return "done"
    err = (res or {}).get("error") or ("worker lost (no result.json)" if lost else "worker exited without result")
    if step["attempt"] < step["max_attempts"] and not (res or {}).get("no_retry"):
        store.x("UPDATE steps SET status='pending', error=? WHERE id=?", (err, step["id"]))
        store.event(step["job_id"], "step_retry", "supervisor", step["id"], name=step["name"], error=err, attempt=step["attempt"])
        return "retry"
    store.x("UPDATE steps SET status='failed', ended=?, rc=1, error=?, outputs_json=? WHERE id=?",
            (now(), err, json.dumps((res or {}).get("outputs"), default=str), step["id"]))
    store.event(step["job_id"], "step_failed", "supervisor", step["id"], name=step["name"], error=err)
    return "failed"


def kill(step, sig=signal.SIGTERM):
    try:
        os.killpg(step["pid"], sig)
    except Exception:
        pass


KILL_GRACE_S = 120   # after SIGTERM, a step that is still alive this long is killed with SIGKILL


def tick():
    from .flows import FLOWS
    reap()
    snap = host_snapshot()
    # 1) supervise running steps
    for s in store.q("SELECT * FROM steps WHERE status='running'"):
        if alive(s["pid"], s["pstart"]):
            over = now() - (s["started"] or now()) - (s["timeout_s"] or 0)
            if s["timeout_s"] and over > KILL_GRACE_S and str(s.get("error") or "").startswith("timeout"):
                kill(s, signal.SIGKILL)
            elif s["timeout_s"] and over > 0 and not s.get("error"):
                kill(s)
                store.x("UPDATE steps SET error=? WHERE id=?", (f"timeout after {s['timeout_s']}s", s["id"]))
                store.event(s["job_id"], "step_timeout", "supervisor", s["id"], name=s["name"], timeout_s=s["timeout_s"])
            continue
        st = finalize(s, lost=not (Path(s["dir"]) / "result.json").exists())
        job = store.one("SELECT * FROM jobs WHERE id=?", (s["job_id"],))
        if st in ("done", "failed") and job["status"] == "running":
            ctl = FLOWS[job["kind"]]
            try:
                ctl.on_step(job, store.one("SELECT * FROM steps WHERE id=?", (s["id"],)))
            except Exception as e:
                store.event(job["id"], "controller_error", "supervisor", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
    # 2) start / cancel jobs
    for j in store.q("SELECT * FROM jobs WHERE status IN ('queued','cancelling')"):
        if j["status"] == "queued":
            store.x("UPDATE jobs SET status='running', started=? WHERE id=?", (now(), j["id"]))
            store.event(j["id"], "job_start", "supervisor")
            try:
                FLOWS[j["kind"]].start(dict(j, status="running"))
            except Exception as e:
                store.x("UPDATE jobs SET status='failed', ended=?, error=? WHERE id=?", (now(), str(e), j["id"]))
                store.event(j["id"], "job_end", "supervisor", status="failed", error=str(e))
        else:
            for s in store.q("SELECT * FROM steps WHERE job_id=? AND status='running'", (j["id"],)):
                kill(s)
            store.x("UPDATE steps SET status='cancelled' WHERE job_id=? AND status IN ('pending','running')", (j["id"],))
            store.x("UPDATE jobs SET status='cancelled', ended=? WHERE id=?", (now(), j["id"]))
            store.event(j["id"], "job_end", "supervisor", status="cancelled")
    # 3) dispatch ready steps
    for s in store.q("""SELECT s.* FROM steps s JOIN jobs j ON j.id=s.job_id WHERE s.status='pending' AND j.status='running'
                        ORDER BY j.created, s.idx"""):
        deps = json.loads(s["deps_json"])
        if deps:
            st = {r["name"]: r["status"] for r in store.q("SELECT name, status FROM steps WHERE job_id=?", (s["job_id"],))}
            if any(st.get(d) in ("failed", "cancelled") for d in deps):
                store.x("UPDATE steps SET status='skipped' WHERE id=?", (s["id"],))
                store.event(s["job_id"], "step_skipped", "supervisor", s["id"], name=s["name"], reason="dependency failed")
                continue
            if any(st.get(d) != "done" for d in deps):
                continue
        ok, why = pool_free(s["pool"], snap)
        if not ok:
            last = store.one("SELECT payload_json FROM events WHERE step_id=? AND type='step_waiting' ORDER BY id DESC LIMIT 1", (s["id"],))
            if not last or json.loads(last["payload_json"]).get("why") != why:
                store.event(s["job_id"], "step_waiting", "scheduler", s["id"], name=s["name"], why=why)
            continue
        spawn(s)
        snap = host_snapshot()
    # 4) finish jobs with nothing left
    for j in store.q("SELECT * FROM jobs WHERE status='running'"):
        n = store.one("SELECT count(*) n FROM steps WHERE job_id=? AND status IN ('pending','running')", (j["id"],))["n"]
        if n == 0:
            ctl = FLOWS[j["kind"]]
            status, summary = ctl.finish(j)
            store.x("UPDATE jobs SET status=?, ended=?, summary_json=? WHERE id=? AND status='running'",
                    (status, now(), json.dumps(summary, default=str), j["id"]))
            store.event(j["id"], "job_end", "supervisor", status=status, summary=summary)


def loop(interval=2.0):
    store.event(None, "daemon_start", "supervisor", pid=os.getpid())
    while not STOP.is_set():
        try:
            tick()
        except Exception as e:
            store.event(None, "scheduler_error", "supervisor", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        STOP.wait(interval)
