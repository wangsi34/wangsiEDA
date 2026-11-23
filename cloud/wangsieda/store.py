"""SQLite store (WAL). One connection per thread/process; every write is a short transaction."""
import json, sqlite3, threading, time
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS designs(case_name TEXT PRIMARY KEY, role TEXT, x0_json TEXT, selection TEXT, context TEXT,
  policy TEXT, readback TEXT, timeout_s INTEGER, v6_bounds_json TEXT, strict_bounds_json TEXT, n_candidates INTEGER, updated REAL);
CREATE TABLE IF NOT EXISTS states(id INTEGER PRIMARY KEY, case_name TEXT, method TEXT, variant TEXT, candidate TEXT,
  metrics_json TEXT, source TEXT, UNIQUE(case_name, method, variant, candidate));
CREATE TABLE IF NOT EXISTS evals(id INTEGER PRIMARY KEY, uid TEXT UNIQUE, case_name TEXT, tag TEXT, family TEXT, arm TEXT, kind TEXT,
  status TEXT, tns REAL, wns REAL, drc INTEGER, short INTEGER, cutshort INTEGER, hold_tns REAL, hold_wns REAL, wl REAL,
  hbt_excess INTEGER, clock_gate TEXT, strict_pass INTEGER, v6_pass INTEGER, strict_fails TEXT, v6_fails TEXT, gain_pct REAL,
  wall_s REAL, at REAL, source TEXT, origin TEXT, raw_json TEXT);
CREATE INDEX IF NOT EXISTS evals_case ON evals(case_name, tag);
CREATE TABLE IF NOT EXISTS candidates(case_name TEXT, tag TEXT, c3_rank INTEGER, expected_gain_ns REAL, p_gate REAL,
  pred_tns REAL, pred_wns REAL, pred_hold_tns REAL, n_sinks INTEGER, n_pad INTEGER, new_hbt INTEGER, relief TEXT, holdpad TEXT,
  program_json TEXT, PRIMARY KEY(case_name, tag));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT, case_name TEXT, title TEXT, params_json TEXT, status TEXT,
  created REAL, started REAL, ended REAL, error TEXT, summary_json TEXT, parent TEXT);
CREATE TABLE IF NOT EXISTS steps(id TEXT PRIMARY KEY, job_id TEXT, idx INTEGER, name TEXT, tool TEXT, deps_json TEXT,
  args_json TEXT, pool TEXT, status TEXT, pid INTEGER, pstart TEXT, attempt INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 1,
  timeout_s INTEGER, started REAL, ended REAL, rc INTEGER, outputs_json TEXT, dir TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS steps_job ON steps(job_id);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, job_id TEXT, step_id TEXT, ts REAL, type TEXT, agent TEXT, payload_json TEXT);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, kind TEXT, case_name TEXT, trigger TEXT, action_json TEXT, evidence_json TEXT,
  source_job TEXT, verified INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS llm_calls(id INTEGER PRIMARY KEY, job_id TEXT, ts REAL, agent TEXT, model TEXT, purpose TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER, latency_ms INTEGER, ok INTEGER, request_json TEXT, response_text TEXT, origin TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS marks(case_name TEXT, tag TEXT, source TEXT, PRIMARY KEY(case_name, tag, source));
"""

_local = threading.local()


def db():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.executescript(SCHEMA)
        _local.c = c
    return c


def q(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None


def x(sql, args=()):
    return db().execute(sql, args)


def tx(fn):
    c = db()
    c.execute("BEGIN IMMEDIATE")
    try:
        out = fn(c)
        c.execute("COMMIT")
        return out
    except BaseException:
        c.execute("ROLLBACK")
        raise


def event(job_id, type_, agent="system", step_id=None, **payload):
    x("INSERT INTO events(job_id, step_id, ts, type, agent, payload_json) VALUES(?,?,?,?,?,?)",
      (job_id, step_id, time.time(), type_, agent, json.dumps(payload, default=str, ensure_ascii=False)))


def meta_get(k, default=None):
    r = one("SELECT v FROM meta WHERE k=?", (k,))
    return json.loads(r["v"]) if r else default


def meta_set(k, v):
    x("INSERT INTO meta(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, default=str)))
