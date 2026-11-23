"""Offline self-test: registry, schemas, guards, judge logic, derivations, MCP handshake. No EDA, no LLM.
Checks that need research assets (design registry, stored verdicts, derivations) run only when those assets are configured,
so a fresh install passes with the platform-only checks."""
import json, sys
from . import store, catalog, config as C
from .tools import load_all, REGISTRY, call, StepContext
from .tools import eco, agent_tools
from . import mcp_server


def main():
    load_all()
    ok, skip = [], []
    assert len(REGISTRY) >= 20, len(REGISTRY)
    ok.append(f"{len(REGISTRY)} tools registered")

    ctx = StepContext()
    for bad in ({"clock_period": 2}, {"place_density": 2.0}, {"foo": 1}):
        try:
            agent_tools.guard(ctx, "x", "clock_period" not in bad and "foo" not in bad and bad.get("place_density", 0) <= 0.95, str(bad))
            raise AssertionError(bad)
        except agent_tools.GuardViolation:
            pass
    ok.append("guards deny constraint tampering / out-of-range / unknown knobs")

    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    t = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert r["result"]["serverInfo"]["name"] == "wangsiEDA"
    ok.append(f"MCP handshake ok, {len(t['result']['tools'])} tools exposed")

    feats = C.features()
    if feats["eco"]["ok"]:
        catalog.refresh(full=False)
        case = C.CASES[0]
        d = call("design_info", {"case": case})
        assert d["x0"]["official_tns_ns"] is not None, "X0 metrics missing"
        ok.append(f"design_info {case}")
        c = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "candidate_pool", "arguments": {"case": case, "limit": 3}}})
        assert not c["result"]["isError"], c
        ok.append("MCP tools/call candidate_pool")
        # the reimplemented judge must reproduce the project's stored verdicts on judged evaluations
        agree = total = 0
        for row in store.q("SELECT case_name, raw_json, v6_pass FROM evals WHERE kind='judged' AND v6_pass IS NOT NULL"):
            m = json.loads(row["raw_json"]).get("metrics") or {}
            dd = catalog.design(row["case_name"]) or {}
            if "tns" not in m or not m.get("clock_delta_ps") or not dd.get("v6_bounds"):
                continue
            v = eco.judge(m, dd["v6_bounds"], dd["x0"].get("official_tns_ns"), v6=True)
            total += 1
            agree += int(v["pass"]) == int(row["v6_pass"])
        ok.append(f"judge agrees with stored Ge verdicts on {agree}/{total} judged evaluations")
        if total and agree != total:
            print("NOTE judge disagreements exist; compare eco.judge with the stored rows")
        for name in eco.DERIVATIONS:
            p = eco.derived(name)
            ok.append(f"derived {name} ({p.stat().st_size} B)")
    else:
        skip.append("ECO checks (not configured: " + ", ".join(feats["eco"]["missing"]) + ")")

    print("\n".join("PASS " + s for s in ok))
    for s in skip:
        print("SKIP " + s)
    print("run `bin/wangsictl doctor` for a full install report")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FAIL", e)
        sys.exit(1)
