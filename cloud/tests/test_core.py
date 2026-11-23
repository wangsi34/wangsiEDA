"""核心逻辑单元测试：签核判定、guide 差分、DRC/路径解析、工具参数校验。运行：cd cloud && python3 -m unittest discover tests"""
import os, tempfile, unittest
from pathlib import Path

os.environ.setdefault("WANGSI_ROOT", tempfile.mkdtemp())
from wangsieda.tools.eco import judge
from wangsieda import scene3d
from wangsieda.tools import tool, obj, validate_args, REGISTRY

B = {"wns_inherited": -4.0, "drc_total_inherited": 100, "drc_Short": 50, "drc_Cut_Short": 0, "hold_tns_safety": -2.0,
     "hold_wns_safety": -0.1, "wirelength_inherited": 1000.0, "clock_delta_allow_ps": {"fall_fall_skew_ps": 0.0}}
M = {"tns": -900.0, "wns": -3.5, "drc": 90, "Short": 40, "CutShort": 0, "hold_tns": -1.0, "hold_wns": -0.05, "wl": 990.0, "hbt_excess": 0,
     "clock_delta_ps": {"fall_fall_skew_ps": -0.1}}


class JudgeTest(unittest.TestCase):
    def test_pass_and_gain(self):
        v = judge(M, B, -1000.0, v6=True)
        self.assertTrue(v["pass"]); self.assertAlmostEqual(v["gain_pct"], 10.0)

    def test_each_gate_blocks(self):
        for k, bad, tag in [("wns", -5.0, "wns"), ("drc", 101, "drc_total"), ("Short", 51, "Short"), ("hold_tns", -3.0, "hold_tns"),
                            ("wl", 1001.0, "wirelength"), ("hbt_excess", 1, "hbt_excess")]:
            v = judge(dict(M, **{k: bad}), B, -1000.0, v6=True)
            self.assertFalse(v["pass"]); self.assertIn(tag, v["failed"]); self.assertEqual(v["gain_pct"], 0.0)

    def test_missing_metrics_fail(self):
        for k, tag in [("wl", "wirelength"), ("CutShort", "CutShort"), ("Short", "Short"), ("hold_wns", "hold_wns")]:
            m = dict(M); m.pop(k)
            v = judge(m, B, -1000.0, v6=True)
            self.assertFalse(v["pass"]); self.assertIn(tag, v["failed"])

    def test_optional_area_bound(self):
        b = dict(B, area_inherited=100.0)
        self.assertIn("area", judge(dict(M, area=101.0), b, -1000.0, v6=True)["failed"])
        self.assertTrue(judge(dict(M, area=100.0), b, -1000.0, v6=True)["pass"])
        self.assertTrue(judge(M, B, -1000.0, v6=True)["pass"])   # bound absent -> not checked

    def test_clock_skew_and_unmeasured(self):
        self.assertFalse(judge(dict(M, clock_delta_ps={"fall_fall_skew_ps": 0.267}), B, -1000.0, v6=True)["pass"])
        self.assertIn("clock_UNMEASURED", judge(dict(M, clock_delta_ps=None), B, -1000.0, v6=True)["failed"])


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_guide_diff(self):
        a = self.d / "a.guide"; b = self.d / "b.guide"
        a.write_text("n1\n(\n0 0 100 100 metal2\n)\nn2\n(\n0 0 50 50 metal3\n)\n")
        b.write_text("n1\n(\n0 0 100 100 metal2\n200 0 300 100 metal11\n)\nn2\n(\n0 0 50 50 metal3\n)\n")
        gd = scene3d.guide_diff(a, b)
        self.assertEqual(gd["changed_nets"], ["n1"]); self.assertEqual(len(gd["nets"]["n1"]["added"]), 1)

    def test_drc_report(self):
        r = self.d / "drc.rpt"
        r.write_text("violation type: Short\n\tsrcs: net:a net:b\n\tbbox = (1.0, 2.0) - (1.5, 2.5) on Layer metal2\n"
                     "violation type: Cut Spacing\n\tbbox = (3.0, 4.0) - (3.1, 4.1) on Layer via3\n")
        d = scene3d.parse_drc(r)
        self.assertEqual(len(d), 2); self.assertEqual(d[0][5], 0); self.assertEqual(scene3d.LAYERS[d[1][4]], "metal3")

    def test_violated_paths(self):
        p = self.d / "vp"
        p.write_text("Startpoint: a (input port)\nEndpoint: r/_1_ (flip-flop)\n   0.31    0.51 v buf/Z (BUF_X1_bottom)\n"
                     "   3.38    3.89 ^ u/ZN (INV_X2_bottom)\n           3.89   data arrival time\n          -2.70   slack (VIOLATED)\n")
        allp, top = scene3d.parse_paths(p)
        self.assertEqual(top[0]["slack"], -2.70); self.assertEqual(len(top[0]["pins"]), 2)


class FreshInstallTest(unittest.TestCase):
    """No wangsi_config.json: the platform must still start and describe what is missing."""
    def test_refresh_and_features(self):
        from wangsieda import catalog, config as C
        r = catalog.refresh(full=True)
        self.assertEqual(r["designs"], 0)
        f = C.features()
        self.assertFalse(f["eco"]["ok"]); self.assertIn("design_registry_module", f["eco"]["missing"])

    def test_flow_checks_raise_valueerror(self):
        from wangsieda.flows import FLOWS
        for kind, p in [("eco_eval", {"case": "nope", "tag": "x"}), ("eco_closure", {"case": "nope"}), ("block_flow", {"block": "nope"})]:
            with self.assertRaises(ValueError):
                FLOWS[kind].check(p)

    def test_doctor_report(self):
        from wangsieda import doctor
        r = doctor.report()
        self.assertIn("features", r); self.assertIn("eco_planner", r["prompts"])


class PromptEvalTest(unittest.TestCase):
    """The prompt evaluator must separate good from bad agent replies (scripted mock model, no API calls)."""
    def run_mock(self, mode):
        import json, subprocess, sys
        root = Path(__file__).resolve().parents[1]
        out = Path(tempfile.mkdtemp())
        env = dict(os.environ, WANGSI_ROOT=str(root), WANGSI_CONFIG=str(out / "none.json"), PYTHONPATH=str(root))
        subprocess.run([sys.executable, "-B", "-m", "wangsieda.prompt_eval", "--mock", mode, "--variants", "B", "--out", str(out)],
                       env=env, check=True, capture_output=True)
        rs = json.loads((out / "results.json").read_text())
        return {a: sum(r["score"] for r in rs if r["agent"] == a) / max(1, sum(1 for r in rs if r["agent"] == a))
                for a in ("eco_planner", "rtl_debug", "pnr_diagnose", "reflector", "assistant")}

    def test_mock_separates(self):
        good, bad = self.run_mock("good"), self.run_mock("bad")
        for a in good:
            self.assertGreaterEqual(good[a], 0.95, a); self.assertLessEqual(bad[a], 0.55, a)


class SchemaTest(unittest.TestCase):
    def test_validate_args(self):
        @tool("_t_demo", "demo", obj({"n": {"type": "integer", "minimum": 1, "maximum": 3}, "s": {"type": "string", "enum": ["a"]}}, ["n"]))
        def _t(ctx, n, s="a"):
            return n
        validate_args("_t_demo", {"n": 2})
        for bad in ({"n": 5}, {"n": "2"}, {"s": "a"}, {"n": 1, "x": 1}, {"n": 1, "s": "b"}):
            with self.assertRaises(ValueError):
                validate_args("_t_demo", bad)
        REGISTRY.pop("_t_demo")


if __name__ == "__main__":
    unittest.main()
