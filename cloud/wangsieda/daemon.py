"""wangsid: the wangsiEDA service (HTTP API + console + scheduler) bound to 127.0.0.1 on the compute host.
Reached from a workstation through an SSH tunnel; every API call needs the per-install token (the host is multi-user)."""
import fcntl, json, mimetypes, os, re, secrets, sys, threading, time, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from . import config as C, store, engine, catalog
from .tools import REGISTRY, call, describe
from .tools.eco import evaluator_progress

UI = C.PKG / "ui"
TOKEN_F = C.VAR / "token"


def token():
    if not TOKEN_F.exists():
        TOKEN_F.write_text(secrets.token_urlsafe(24))
        os.chmod(TOKEN_F, 0o600)
    return TOKEN_F.read_text().strip()


def readable(p):
    rp = Path(p).resolve()
    return any(r in rp.parents for r in (C.JOBS.resolve(), C.DERIVED.resolve(), (C.RT_WANGSI).resolve()))


def tail(path, n=200000):
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - n))
        return f.read().decode("utf-8", "replace")


def default_case():
    return (C.SCENE_CASES or C.CASES or [None])[0]


def site_config():
    """What this install can do; the console builds its menus from this instead of hard-coded design names."""
    from . import __version__
    return {"version": __version__, "cases": C.CASES, "scene_cases": C.SCENE_CASES, "readback_cases": C.READBACK_CASES,
            "blocks": C.blocks(), "features": C.features(), "default_case": default_case(),
            "clock_period_ns": C.CFG.get("clock_period_ns", {}), "demo": bool((store.meta_get("catalog_refresh") or {}).get("demo")),
            "catalog": store.meta_get("catalog_refresh")}


def overview():
    designs = []
    for d in store.q("SELECT case_name, role, x0_json, n_candidates, v6_bounds_json FROM designs ORDER BY case_name"):
        x0 = json.loads(d["x0_json"] or "{}")
        st = store.one("""SELECT count(*) n, sum(kind='judged') judged, sum(v6_pass) v6p, max(tns) best_tns FROM evals WHERE case_name=?""", (d["case_name"],))
        best = store.one("SELECT tag, tns, gain_pct, family FROM evals WHERE case_name=? AND v6_pass=1 ORDER BY tns DESC LIMIT 1", (d["case_name"],))
        designs.append({"case": d["case_name"], "role": d["role"], "x0_tns": x0.get("official_tns_ns"), "x0_wns": x0.get("official_wns_ns"),
                        "x0_drc": x0.get("drc_total"), "x0_short": x0.get("Short"), "x0_hold_wns": x0.get("worst_hold_slack_ns"),
                        "evaluator_runtime_s": x0.get("evaluator_runtime_s"), "candidates": d["n_candidates"], "evals": st,
                        "best_v6": best, "has_v6": bool(json.loads(d["v6_bounds_json"] or "null"))})
    jobs = store.q("SELECT id, kind, case_name, title, status, created, started, ended FROM jobs ORDER BY created DESC LIMIT 30")
    return {"designs": designs, "jobs": jobs, "host": engine.host_snapshot(), "catalog": store.meta_get("catalog_refresh"),
            "counts": store.one("""SELECT (SELECT count(*) FROM evals) evals, (SELECT count(*) FROM states) states,
                                    (SELECT count(*) FROM candidates) candidates, (SELECT count(*) FROM llm_calls) llm_calls,
                                    (SELECT count(*) FROM skills) skills, (SELECT count(*) FROM jobs) jobs"""),
            "tools": len(REGISTRY)}


def design_page(case):
    d = call("design_info", {"case": case})
    d["states"] = call("signed_off_states", {"case": case})["states"]
    d["candidates"] = call("candidate_pool", {"case": case, "limit": 60})["candidates"]
    d["evals"] = store.q("""SELECT id, tag, family, arm, kind, status, tns, wns, drc, short, cutshort, hold_wns, hold_tns, wl, strict_pass, v6_pass,
                            v6_fails, gain_pct, wall_s, origin FROM evals WHERE case_name=? ORDER BY tns DESC""", (case,))
    return d


def workspace_page(case):
    if not case:
        return {"case": None, "states": [], "x0": None, "v6_bounds": None, "strict_bounds": None}
    ws = store.meta_get(f"workspace:{case}", {"states": []})
    d = catalog.design(case)
    out = []
    for st in ws["states"]:
        e = store.one("SELECT uid, tns, wns, drc, short, cutshort, hold_tns, hold_wns, wl, hbt_excess, strict_pass, v6_pass, strict_fails, v6_fails, "
                      "gain_pct, raw_json, source, origin FROM evals WHERE uid=?", (st.get("eval_uid") or "",))
        if e:
            raw = json.loads(e.pop("raw_json") or "{}")
            m = raw.get("metrics") or (raw.get("judged") or {}).get("metrics") or {}
            e["clock_delta_ps"] = m.get("clock_delta_ps")
            e["strict_fails"] = json.loads(e["strict_fails"] or "null")
            e["v6_fails"] = json.loads(e["v6_fails"] or "null")
        out.append(dict(st, eval=e))
    return {"case": case, "states": out, "x0": d.get("x0"), "v6_bounds": d.get("v6_bounds"), "strict_bounds": d.get("strict_bounds")}


def job_page(jid):
    j = store.one("SELECT * FROM jobs WHERE id=?", (jid,))
    if not j:
        return None
    j["params"] = json.loads(j.pop("params_json"))
    j["state"] = json.loads(j.pop("summary_json") or "{}")
    j["steps"] = engine.steps_of(jid)
    for s in j["steps"]:
        s["files"] = sorted(p.name for p in Path(s["dir"]).glob("*") if p.is_file()) if Path(s["dir"]).exists() else []
    j["llm"] = store.q("SELECT id, ts, agent, purpose, prompt_tokens, completion_tokens, latency_ms, ok FROM llm_calls WHERE job_id=? ORDER BY id", (jid,))
    return j


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body, default=str, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authed(self, q):
        t = self.headers.get("X-Wangsi-Token") or q.get("token")
        return t and secrets.compare_digest(t, token())

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        if path == "/" or path.startswith("/ui/"):
            f = UI / ("index.html" if path == "/" else path[4:])
            f = f.resolve()
            if UI.resolve() in f.parents or f == (UI / "index.html").resolve():
                if f.exists():
                    return self.send(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "text/plain")
            return self.send(404, {"error": "not found"})
        if not self.authed(q):
            return self.send(401, {"error": "token required"})
        try:
            return self.route_get(path, q)
        except Exception as e:
            sys.stderr.write(traceback.format_exc())   # full trace stays in var/daemon.log
            return self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def route_get(self, path, q):
        if path == "/api/overview":
            return self.send(200, overview())
        m = re.match(r"^/api/design/([\w]+)$", path)
        if m:
            return self.send(200, design_page(m.group(1)))
        if path == "/api/evals":
            w, a = [], []
            for k in ("case_name", "kind", "origin", "family"):
                if q.get(k):
                    w.append(f"{k}=?"); a.append(q[k])
            if q.get("v6") == "1":
                w.append("v6_pass=1")
            sql = ("SELECT id, case_name, tag, family, arm, kind, status, tns, wns, drc, short, cutshort, hold_tns, hold_wns, wl, strict_pass, "
                   "v6_pass, v6_fails, strict_fails, gain_pct, wall_s, at, source, origin FROM evals")
            sql += (" WHERE " + " AND ".join(w) if w else "") + " ORDER BY at DESC LIMIT ?"
            return self.send(200, store.q(sql, (*a, int(q.get("limit", 500)))))
        m = re.match(r"^/api/eval/(\d+)$", path)
        if m:
            return self.send(200, store.one("SELECT * FROM evals WHERE id=?", (int(m.group(1)),)))
        if path == "/api/jobs":
            return self.send(200, store.q("SELECT id, kind, case_name, title, status, created, started, ended, error FROM jobs ORDER BY created DESC LIMIT 200"))
        m = re.match(r"^/api/job/([\w\-]+)$", path)
        if m:
            j = job_page(m.group(1))
            return self.send(200 if j else 404, j or {"error": "no job"})
        m = re.match(r"^/api/job/([\w\-]+)/events$", path)
        if m:
            return self.send(200, store.q("SELECT id, step_id, ts, type, agent, payload_json FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT 2000",
                                          (m.group(1), int(q.get("after", 0)))))
        if path == "/api/stream":
            return self.stream(q.get("job"), int(q.get("after", 0)))
        if path == "/api/llm":
            r = store.one("SELECT * FROM llm_calls WHERE id=?", (int(q.get("id", 0)),))
            return self.send(200, r)
        if path == "/api/llm_calls":
            return self.send(200, store.q("""SELECT id, job_id, ts, agent, model, purpose, prompt_tokens, completion_tokens, latency_ms, ok, origin
                                             FROM llm_calls ORDER BY ts DESC LIMIT 300"""))
        if path == "/api/file":
            p = q.get("path", "")
            if not readable(p) or not Path(p).is_file():
                return self.send(403, {"error": "path not readable"})
            return self.send(200, tail(p, int(q.get("bytes", 400000))), "application/json" if p.endswith(".json") else "text/plain")
        if path == "/api/progress":
            return self.send(200, evaluator_progress(q["run_id"]))
        if path == "/api/tools":
            return self.send(200, describe())
        if path == "/api/skills":
            return self.send(200, store.q("SELECT * FROM skills ORDER BY created DESC"))
        if path == "/api/host":
            return self.send(200, engine.host_snapshot())
        if path == "/api/config":
            return self.send(200, site_config())
        if path == "/api/workspace":
            return self.send(200, workspace_page(q.get("case") or default_case()))
        if path == "/api/scene":
            case, label = q.get("case") or default_case(), q.get("label", "X0")
            if not (re.match(r"^[\w\-]+$", case or "") and re.match(r"^[\w\-]+$", label)):
                return self.send(400, {"error": "bad case/label"})
            f = C.VAR / "scenes" / f"{case}__{label}.json.gz"
            if not f.exists():
                return self.send(404, {"error": "no scene"})
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/derivations":
            return self.send(200, [json.loads(p.read_text()) for p in sorted(C.DERIVED.glob("*.DERIVATION.json"))])
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self.authed(q):
            return self.send(401, {"error": "token required"})
        try:
            b = self.body()
            if u.path == "/api/jobs":
                jid = engine.create_job(b["kind"], b.get("params", {}), b.get("title"))
                return self.send(200, {"job_id": jid})
            m = re.match(r"^/api/step/([\w\-:]+)/retry$", u.path)
            if m:
                engine.retry_step(m.group(1))
                return self.send(200, {"ok": True})
            m = re.match(r"^/api/job/([\w\-]+)/cancel$", u.path)
            if m:
                engine.cancel_job(m.group(1))
                return self.send(200, {"ok": True})
            m = re.match(r"^/api/tools/(\w+)$", u.path)
            if m:
                t = REGISTRY.get(m.group(1))
                if not t or t["side_effect"] != "read":
                    return self.send(403, {"error": "only read-only tools can be called synchronously; submit a job for the rest"})
                return self.send(200, call(m.group(1), b))
            if u.path == "/api/catalog/refresh":
                return self.send(200, catalog.refresh(full=True))
            return self.send(404, {"error": "not found"})
        except Exception as e:
            if not isinstance(e, (ValueError, KeyError)):
                sys.stderr.write(traceback.format_exc())
            return self.send(400, {"error": f"{type(e).__name__}: {e}"})

    def stream(self, jid, after):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            idle = 0
            while True:
                rows = store.q("SELECT id, step_id, ts, type, agent, payload_json FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT 500", (jid, after))
                for r in rows:
                    after = r["id"]
                    self.wfile.write(b"data: " + json.dumps(r, default=str).encode() + b"\n\n")
                self.wfile.flush()
                if not rows:
                    idle += 1
                    if idle % 15 == 0:
                        self.wfile.write(b": ping\n\n"); self.wfile.flush()
                    time.sleep(1)
                else:
                    idle = 0
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def main():
    lock = open(C.VAR / "daemon.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("wangsid already running"); sys.exit(0)
    (C.VAR / "daemon.pid").write_text(str(os.getpid()))
    token()
    if not store.meta_get("catalog_refresh"):
        r = catalog.refresh(full=True)   # never raises; missing assets are listed in r["errors"]
        if r.get("errors"):
            print("catalog: some sources unavailable:", r["errors"], flush=True)
    threading.Thread(target=engine.loop, daemon=True).start()
    def periodic():
        while True:
            time.sleep(1800)
            try:
                catalog.refresh(full=True)
            except Exception:
                traceback.print_exc()
    threading.Thread(target=periodic, daemon=True).start()
    srv = ThreadingHTTPServer((C.HTTP_HOST, C.HTTP_PORT), H)
    srv.daemon_threads = True
    print(f"wangsid on {C.HTTP_HOST}:{C.HTTP_PORT} pid {os.getpid()}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
