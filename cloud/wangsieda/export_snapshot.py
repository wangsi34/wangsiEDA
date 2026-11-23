"""Export a sanitized, read-only snapshot of the console data (offline review package).

Removed from every record: prompts and model request/response text, file-system paths, host names, hashes, raw evaluator
records and derivation details. What remains: design metrics, gate verdicts, job DAGs and event timelines, 3D scenes.
Usage: python3 -m wangsieda.export_snapshot OUT_DIR [CASE]
"""
import gzip, json, re, shutil, sys
from pathlib import Path
from . import config as C, store, engine
from .daemon import overview, design_page, job_page, workspace_page, default_case, site_config
from .tools import describe

DROP = {"request_json", "response_text", "raw_json", "messages", "prompt_preview", "content", "source", "workroot", "run", "dir",
        "result", "submission", "readback_dir", "file", "scene", "runtime", "trace", "log", "files", "cmd", "argv", "path", "uid",
        "context", "eval_uid", "runtime_job", "guide_sha256", "derivations"}
PATH_RE = re.compile(r"(/home/|/tmp/|/workspace/|/opt/|/root/|/data/|/scratch/|/mnt/|/nfs/|/srv/|/net/|/proj/|/Users/|[A-Za-z]:\\|\.config" + "".join("|" + re.escape(t) for t in C.CFG.get("redact_terms", [])) + ")")
HASH_RE = re.compile(r"\b[0-9a-f]{32,64}\b")


def clean(v):
    if isinstance(v, dict):
        return {k: (x if k == "routes" else clean(x)) for k, x in v.items() if k not in DROP}
    if isinstance(v, list):
        return [clean(x) for x in v]
    if isinstance(v, str):
        if PATH_RE.search(v):
            return "<server path>"
        return HASH_RE.sub("<hash>", v)
    return v


def put(out, name, obj):
    p = out / (name + ".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean(obj), ensure_ascii=False, default=str), encoding="utf-8")


def main(out):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    put(out, "overview", overview())
    sc = site_config()
    sc.pop("catalog", None)
    case = sys.argv[2] if len(sys.argv) > 2 else default_case()
    put(out, "config", dict(sc, default_case=case))
    put(out, "host", engine.host_snapshot())
    ws = workspace_page(case) if case else {"states": []}
    put(out, "workspace", ws)
    for s in ws["states"]:
        f = C.VAR / "scenes" / f"{case}__{s['label']}.json.gz"
        if not f.exists():
            continue
        sc = clean(json.load(gzip.open(f, "rt")))
        with gzip.open(out / f"scene_{s['label']}.json.gz", "wt") as g:
            json.dump(sc, g, separators=(",", ":"))
    if case and case in C.CASES:
        put(out, f"design_{case}", design_page(case))
    put(out, "evals", store.q("SELECT id, case_name, tag, family, arm, kind, status, tns, wns, drc, short, cutshort, hold_tns, hold_wns, wl, "
                              "strict_pass, v6_pass, v6_fails, strict_fails, gain_pct, wall_s, at, origin FROM evals ORDER BY at DESC"))
    jobs = store.q("SELECT id, kind, case_name, title, status, created, started, ended, error FROM jobs ORDER BY created DESC")
    put(out, "jobs", jobs)
    for j in jobs:
        put(out, f"job/{j['id']}", job_page(j["id"]))
        ev = store.q("SELECT id, step_id, ts, type, agent, payload_json FROM events WHERE job_id=? ORDER BY id", (j["id"],))
        for e in ev:
            e["payload_json"] = json.dumps(clean(json.loads(e["payload_json"] or "{}")), ensure_ascii=False)
        put(out, f"events/{j['id']}", ev)
    put(out, "llm_calls", store.q("SELECT id, job_id, ts, agent, model, purpose, prompt_tokens, completion_tokens, latency_ms, ok, origin "
                                  "FROM llm_calls ORDER BY ts DESC LIMIT 300"))
    put(out, "tools", describe())
    put(out, "skills", store.q("SELECT id, kind, case_name, trigger, action_json, verified, source_job, created FROM skills ORDER BY created DESC"))
    print("snapshot written to", out)


if __name__ == "__main__":
    main(sys.argv[1])
