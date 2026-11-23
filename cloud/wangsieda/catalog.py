"""Catalog: index the project's real data lake (read-only) into the wangsiEDA store.

Sources
  * d2.DESIGNS            design registry: candidate pool, build context, acceptance policy, readback kind, timeout
  * gate_bounds_json       envelope-gate (Ge) bounds per design; the registry's acceptance policy JSON = exact gate (Gs) bounds
  * normalized_states_json canonical signed-off states per design (X0 and every compared method) with their metrics
  * RESULT.json / REJUDGE.json anywhere under the project (replica evaluations and judged evaluations)
  * MODEL_LEDGER.jsonl     every ledgered model call made by the research agents
"""
import importlib.util, json, os, re, sys, time
from pathlib import Path
from . import config as C
from .store import x, q, one, tx, meta_set

_D2 = None


def d2():
    """Import the project's design registry module (configured as `design_registry_module`).
    Importing runs the module's top-level code, so point this only at a trusted file."""
    global _D2
    if _D2 is None:
        if not C.D2_MODULE.is_file():
            raise RuntimeError("design_registry_module is not configured (see wangsi_config.json); ECO features are disabled")
        sys.path.insert(0, str(C.D2_MODULE.parent))
        spec = importlib.util.spec_from_file_location("wangsi_d2_registry", str(C.D2_MODULE))
        _D2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_D2)
    return _D2


def rd(p):
    return json.loads(Path(p).read_text())


def case_of(text):
    names = list(C.CASES) + list(C.CASE_ALIASES)
    for c in sorted(names, key=len, reverse=True):   # longest first so "bp_multi" is not read as a shorter name
        if c in text:
            return C.CASE_ALIASES.get(c, c)
    return None


# ------------------------------------------------------------------ designs, bounds, candidates, states
def ingest_designs():
    if not (C.D2_MODULE.is_file() and C.V6_BOUNDS.is_file() and C.NORMALIZED.is_file()):
        return 0
    D = d2().DESIGNS
    v6 = rd(C.V6_BOUNDS)["designs"]
    norm = rd(C.NORMALIZED)["states"]
    n = 0
    for case, d in D.items():
        x0 = next((s["metrics"] for s in norm if s["case"] == case and s["method"] == "X0"), {})
        strict = {}
        try:
            strict = {r["id"]: r["bound"] for r in rd(d["policy"])["rules"]}
        except Exception:
            pass
        cands = ingest_candidates(case, d)
        x("""INSERT INTO designs VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(case_name) DO UPDATE SET role=excluded.role,
             x0_json=excluded.x0_json, selection=excluded.selection, context=excluded.context, policy=excluded.policy,
             readback=excluded.readback, timeout_s=excluded.timeout_s, v6_bounds_json=excluded.v6_bounds_json,
             strict_bounds_json=excluded.strict_bounds_json, n_candidates=excluded.n_candidates, updated=excluded.updated""",
          (case, d.get("role"), json.dumps(dict(x0, x0_tns=d.get("x0_tns"))), str(d["sel"]), str(d["ctx"]), str(d["policy"]),
           d.get("readback"), d.get("timeout"), json.dumps((v6.get(case) or {}).get("v6_bounds")), json.dumps(strict), cands, time.time()))
        n += 1
    for s in norm:
        x("""INSERT INTO states(case_name, method, variant, candidate, metrics_json, source) VALUES(?,?,?,?,?,?)
             ON CONFLICT(case_name, method, variant, candidate) DO UPDATE SET metrics_json=excluded.metrics_json""",
          (s["case"], s["method"], s["variant"], s["candidate"], json.dumps(s["metrics"]), str(C.NORMALIZED)))
    return n


def ingest_candidates(case, d):
    try:
        sel = rd(d["sel"])
        queue = sel.get("queue") or [e["program"] for e in sel.get("queue_top20", [])]
        scores = {}
        try:
            ctx = rd(d["ctx"])
            scores = rd(ctx["refs"]["c3_scores"]["path"]).get("tools", {})
        except Exception:
            pass
    except Exception:
        return 0
    rows = []
    for i, p in enumerate(queue):
        s = scores.get(p.get("tag"), {})
        rows.append((case, p.get("tag"), i + 1, s.get("expected_gain_ns"), s.get("P_gate_v5"), s.get("pred_TNS"), s.get("pred_WNS"),
                     s.get("pred_hold_tns_ns"), s.get("n_sinks") or len(p.get("hcsi", [])), s.get("n_pad"), s.get("new_HBT"),
                     p.get("relief"), p.get("holdpad"), json.dumps(p)))

    def w(c):
        c.execute("DELETE FROM candidates WHERE case_name=?", (case,))
        c.executemany("INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    tx(w)
    return len(rows)


# ------------------------------------------------------------------ evaluations
def _verdict(v):
    if isinstance(v, dict):
        return (1 if v.get("pass") else 0), v.get("failed") or [], v.get("gain_pct")
    if isinstance(v, list):
        return (0 if v else 1), v, None
    return None, None, None


def normalize(path, r):
    if not isinstance(r, dict):
        return None
    m = r.get("metrics") if isinstance(r.get("metrics"), dict) else None
    me = r.get("measured") if isinstance(r.get("measured"), dict) else None
    by = r.get("drc_by_type") or {}
    row = dict(uid=str(path), source=str(path), raw=r)
    if m and "tns" in m:
        row.update(tns=m.get("tns"), wns=m.get("wns"), drc=m.get("drc"), short=m.get("Short"), cutshort=m.get("CutShort"),
                   hold_tns=m.get("hold_tns"), hold_wns=m.get("hold_wns"), wl=m.get("wl"), hbt_excess=m.get("hbt_excess"),
                   clock_gate=m.get("clock_gate"), kind="judged")
    elif m and "tns_ns" in m:
        row.update(tns=m.get("tns_ns"), wns=m.get("wns_ns"), drc=m.get("drc"), short=by.get("Short"), cutshort=by.get("Cut Short", 0),
                   wl=m.get("drt_wirelength_um"), hbt_excess=m.get("hbt_excess_count"), kind="replica")
    elif me and "tns_ns" in me:
        bc = me.get("by_class") or {}
        row.update(tns=me.get("tns_ns"), wns=me.get("wns_ns"), drc=me.get("drc"), short=bc.get("Short"), cutshort=bc.get("Cut Short", 0),
                   hold_wns=(me.get("hold") or {}).get("hold_wns_ns"), wl=me.get("drt_wirelength_um"), hbt_excess=me.get("hbt_excess"),
                   kind="measured")
    else:
        return None
    if row.get("tns") is None:
        return None
    case = (m or {}).get("case") or r.get("case") or case_of(str(path))
    row["case_name"] = C.CASE_ALIASES.get(case, case)
    sp, sf, sg = _verdict(r.get("strict"))
    vp, vf, vg = _verdict(r.get("v6"))
    row.update(strict_pass=sp, strict_fails=json.dumps(sf) if sf is not None else None, v6_pass=vp,
               v6_fails=json.dumps(vf) if vf is not None else None, gain_pct=vg)
    row["tag"] = str(r.get("tag") or r.get("run_id") or Path(path).parent.name)
    row["arm"] = str(r.get("arm") or r.get("first_requested_by") or "") or None
    row["status"] = str(r.get("status") or r.get("stop") or "MEASURED")
    rep = r.get("replica") if isinstance(r.get("replica"), dict) else {}
    row["wall_s"] = r.get("wall_s") if isinstance(r.get("wall_s"), (int, float)) else rep.get("wall_s")
    rel = str(path).replace(str(C.PROJ) + "/", "")
    parts = rel.split("/")
    row["family"] = parts[-3] if len(parts) >= 3 else rel
    row["at"] = os.path.getmtime(path)
    return row


TAG_RE = re.compile(C.CFG.get("program_tag_regex", r"(?<![A-Za-z0-9])F\d{4,5}(?![0-9])"))   # 候选程序标签格式，可配置
MARK_DIRS = ("builds", "evals", "replicas", "readback", "formal_runs", "holdtns", "READBACK", "subs")


def scan_results():  # noqa: C901
    t0 = time.time()
    found = seen = 0
    marks = set()
    for s in q("SELECT case_name, candidate FROM states"):
        for t in TAG_RE.findall(s["candidate"] or ""):
            marks.add((s["case_name"], t, "signed_off_state"))
    for root in C.SCAN_ROOTS:
        for dp, dns, fns in os.walk(root, followlinks=False):
            if any(e in dp + "/" for e in C.SCAN_EXCLUDE) or os.path.ismount(dp):
                dns[:] = []
                continue
            dns[:] = [d for d in dns if not os.path.islink(os.path.join(dp, d))]
            if any(k in dp for k in MARK_DIRS):
                cs = case_of(dp)
                for t in TAG_RE.findall(os.path.basename(dp)):
                    if cs:
                        marks.add((cs, t, dp.replace(str(C.PROJ) + "/", "")[:200]))
            for fn in fns:
                if fn not in ("RESULT.json", "REJUDGE.json"):
                    continue
                seen += 1
                p = os.path.join(dp, fn)
                if fn == "RESULT.json" and os.path.exists(os.path.join(dp, "REJUDGE.json")):
                    continue  # the re-judged record supersedes
                try:
                    r = json.loads(Path(p).read_text())
                except Exception:
                    continue
                try:
                    row = normalize(p, r)
                except Exception:
                    row = None
                if not row:
                    continue
                found += 1
                upsert_eval(row, origin="research")
    for base in [C.RT] + [C.RT / d for d in C.RUNTIME_SCAN_SUBDIRS]:
        try:
            names = os.listdir(base)
        except OSError:
            names = []
        for n in names:
            cs = case_of(n)
            for t in TAG_RE.findall(n):
                if cs:
                    marks.add((cs, t, str(base / n)[-200:]))
    rows = list(marks)
    def w(c):
        c.execute("DELETE FROM marks")
        c.executemany("INSERT OR IGNORE INTO marks VALUES(?,?,?)", rows)
    tx(w)
    meta_set("catalog_scan", {"at": time.time(), "files_seen": seen, "evals": found, "marks": len(rows), "secs": round(time.time() - t0, 1)})
    return seen, found


def upsert_eval(row, origin):
    x("""INSERT INTO evals(uid, case_name, tag, family, arm, kind, status, tns, wns, drc, short, cutshort, hold_tns, hold_wns, wl,
         hbt_excess, clock_gate, strict_pass, v6_pass, strict_fails, v6_fails, gain_pct, wall_s, at, source, origin, raw_json)
         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT(uid) DO UPDATE SET status=excluded.status, tns=excluded.tns, wns=excluded.wns, drc=excluded.drc,
         short=excluded.short, cutshort=excluded.cutshort, hold_tns=excluded.hold_tns, hold_wns=excluded.hold_wns, wl=excluded.wl,
         strict_pass=excluded.strict_pass, v6_pass=excluded.v6_pass, strict_fails=excluded.strict_fails, v6_fails=excluded.v6_fails,
         gain_pct=excluded.gain_pct, raw_json=excluded.raw_json""",
      (row["uid"], row.get("case_name"), row.get("tag"), row.get("family"), row.get("arm"), row.get("kind"), row.get("status"),
       row.get("tns"), row.get("wns"), row.get("drc"), row.get("short"), row.get("cutshort"), row.get("hold_tns"), row.get("hold_wns"),
       row.get("wl"), row.get("hbt_excess"), row.get("clock_gate"), row.get("strict_pass"), row.get("v6_pass"), row.get("strict_fails"),
       row.get("v6_fails"), row.get("gain_pct"), row.get("wall_s"), row.get("at"), row.get("source"), origin,
       json.dumps(row.get("raw"), default=str)[:20000]))


# ------------------------------------------------------------------ model ledger (real past agent calls)
def ingest_ledger():
    if not C.MODEL_LEDGER.exists():
        return 0
    x("DELETE FROM llm_calls WHERE origin='research_ledger'")
    reqs, n = {}, 0
    for line in open(C.MODEL_LEDGER, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        rid = e.get("request_id")
        if e.get("event") in ("REQUESTED", "SENT", "REQUEST") and rid:
            reqs[rid] = e
        elif e.get("event") == "RETURNED":
            u = e.get("usage") or {}
            rq = reqs.get(rid, {})
            x("""INSERT INTO llm_calls(job_id, ts, agent, model, purpose, prompt_tokens, completion_tokens, latency_ms, ok, request_json,
                 response_text, origin) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (rq.get("experiment") or rq.get("arm") or "research", e.get("at_unix"), rq.get("role") or "research-agent",
               e.get("returned_model"), rq.get("purpose") or rq.get("event"), u.get("prompt_tokens"), u.get("completion_tokens"),
               e.get("latency_ms"), 1, json.dumps({k: v for k, v in rq.items() if k not in ("messages",)})[:4000], "", "research_ledger"))
            n += 1
    return n


def refresh(full=True):
    """Index whatever research assets are configured. Each source is optional: a missing or broken source is reported in
    `errors` and skipped, so a fresh install (block flow and DEF import only) still starts."""
    out, errors = {}, {}

    def step(name, fn):
        try:
            return fn()
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"
            return 0
    out["designs"] = step("designs", ingest_designs)
    out["ledger_calls"] = step("ledger", ingest_ledger)
    if full and C.SCAN_ROOTS:
        r = step("scan", scan_results)
        out["files_seen"], out["evals"] = r if isinstance(r, tuple) else (0, 0)
    out["errors"] = errors
    meta_set("catalog_refresh", dict(out, at=time.time()))
    return out


# ------------------------------------------------------------------ queries used by tools / UI
def design(case):
    d = one("SELECT * FROM designs WHERE case_name=?", (case,))
    if d:
        for k in ("x0_json", "v6_bounds_json", "strict_bounds_json"):
            d[k[:-5]] = json.loads(d.pop(k) or "null")
    return d


def measured_tags(case):
    return {r["tag"] for r in q("SELECT DISTINCT tag FROM evals WHERE case_name=?", (case,))}
