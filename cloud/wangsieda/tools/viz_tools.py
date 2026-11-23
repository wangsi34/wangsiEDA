"""3D review tools: guide diff (what an ECO physically changed) and 3D scenes of signoff states for the review workspace."""
import gzip, json
from pathlib import Path
from . import tool, obj
from .. import config as C, store, scene3d

CASE = {"type": "string", "enum": list(C.SCENE_CASES)} if C.SCENE_CASES else {"type": "string"}
X0_RUN = C.X0_RUNS   # 每个设计的初始签核状态（X0）评测目录，在 wangsi_config.json 中配置


def x0_guide(case):
    return next((X0_RUN[case] / "results/nangate45_3D" / case).glob("*/route.guide"))


def scene_file(case, label):
    return C.VAR / "scenes" / f"{case}__{label}.json.gz"


def load_scene(case, label):
    with gzip.open(scene_file(case, label), "rt") as f:
        return json.load(f)


def program_nets(case, tags):
    nets = set()
    for t in tags:
        r = store.one("SELECT program_json FROM candidates WHERE case_name=? AND tag=?", (case, t))
        if r:
            nets |= {h["net"] for h in json.loads(r["program_json"]).get("hcsi", [])}
    return nets


def workspace(case):
    return store.meta_get(f"workspace:{case}", {"case": case, "states": [], "focus": []})


def register_state(case, label, **info):
    ws = workspace(case)
    ws["states"] = [s for s in ws["states"] if s["label"] != label] + [dict(info, label=label)]
    store.meta_set(f"workspace:{case}", ws)


def _build(case, label, run, focus, is_x0):
    sc = scene3d.build(label, run, focus_extra=focus, base_guide=None if is_x0 else x0_guide(case), meta={"case": case})
    if not is_x0:
        scene3d.diff_against(sc, load_scene(case, "X0"))
    scene3d.save(sc, f"{case}__{label}")
    return sc


@tool("guide_diff", "Global-route guide diff between X0 and a submitted ECO: nets changed and guide boxes added/removed per layer.",
      obj({"case": CASE, "guide": {"type": "string"}}, ["case", "guide"]), side_effect="compute", pool="cpu", timeout=900)
def guide_diff(ctx, case, guide):
    gd = scene3d.guide_diff(x0_guide(case), guide)
    (ctx.dir / "guide_diff.json").write_text(json.dumps(gd))
    added = sum(len(v["added"]) for v in gd["nets"].values())
    ctx.emit("guide_diff", agent="pnr", changed_nets=gd["changed_nets"][:40], added_boxes=added,
             removed_boxes=sum(len(v["removed"]) for v in gd["nets"].values()))
    return {"changed_nets": gd["changed_nets"], "file": str(ctx.dir / "guide_diff.json"), "added_boxes": added}


@tool("scene_build", "Extract the 3D scene of a final signoff state (both dies, HBTs, routing density, focus nets, violated paths, DRC) "
      "and diff it against X0. Re-extracts X0 when the ECO touched nets X0's scene does not carry yet.",
      obj({"case": CASE, "label": {"type": "string"}, "run": {"type": "string"}, "title": {"type": "string"},
           "programs": {"type": "array"}, "eval_uid": {"type": "string"}}, ["case", "label", "run"]),
      side_effect="compute", pool="cpu", timeout=3600)
def scene_build(ctx, case, label, run, title=None, programs=None, eval_uid=None):
    gd = scene3d.guide_diff(x0_guide(case), scene3d.run_dirs(run)[0] / "route.guide")
    ws = workspace(case)
    focus = set(ws.get("focus", [])) | set(gd["changed_nets"]) | program_nets(case, programs or [])
    if not scene_file(case, "X0").exists() or not focus <= set(ws.get("focus", [])):
        ctx.emit("scene_progress", agent="viz", detail=f"extracting X0 with {len(focus)} focus nets")
        _build(case, "X0", X0_RUN[case], focus, True)
        ws["focus"] = sorted(focus)
        store.meta_set(f"workspace:{case}", ws)
        register_state(case, "X0", title="X0 - initial post-route state", role="baseline", run=str(X0_RUN[case]))
    sc = _build(case, label, run, focus, False)
    register_state(case, label, title=title or label, role="eco", run=run, programs=programs or [], eval_uid=eval_uid)
    ctx.emit("scene", agent="viz", label=label, cells=len(sc["cells"]), drc=len(sc["drc"]), hbt=len(sc["hbt"]), paths=len(sc["paths"]),
             changed_nets=len(gd["changed_nets"]), seconds=sc["meta"]["seconds"])
    return {"label": label, "cells": len(sc["cells"]), "drc": len(sc["drc"]), "hbt": len(sc["hbt"]), "changed_nets": gd["changed_nets"],
            "drc_new": len(sc.get("drc_new", [])), "drc_fixed": len(sc.get("drc_fixed", [])), "hbt_new": len(sc.get("hbt_new", []))}
