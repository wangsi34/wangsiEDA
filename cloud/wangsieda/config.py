"""wangsiEDA 配置。所有与站点相关的路径、模型和资源上限都从 wangsi_config.json 读取（见 wangsi_config.example.json）。"""
import json, os
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("WANGSI_ROOT", str(PKG.parent)))
_CFG_FILE = Path(os.environ.get("WANGSI_CONFIG", str(ROOT / "wangsi_config.json")))
CFG = json.loads(_CFG_FILE.read_text(encoding="utf-8")) if _CFG_FILE.exists() else {}


def _p(key, default=""):
    v = CFG.get(key, default)
    return Path(v) if v else Path("/nonexistent/" + key)


VAR = ROOT / "var"
DB_PATH = VAR / "wangsi.db"
JOBS = ROOT / "jobs"
DERIVED = ROOT / "derived"
BLOCKS = ROOT / "blocks"

# ---- 研究项目中的只读资产（自行配置）
PROJ = _p("project_root")
SPRINT = _p("results_dir")
NORMALIZED = _p("normalized_states_json")
V6_BOUNDS = _p("gate_bounds_json")
D2_MODULE = _p("design_registry_module")
FORMAL_BUILD = _p("eco_builder_module")
MODEL_LEDGER = _p("model_ledger_jsonl")
SCAN_ROOTS = [Path(x) for x in CFG.get("scan_roots", [])]
SCAN_EXCLUDE = tuple(CFG.get("scan_exclude", ["/__pycache__/"]))

# ---- 评测器运行环境（自行配置）
ROOTFS = _p("rootfs")
RT = _p("runtime_dir")
RT_WANGSI = RT / "wangsi"
PLATFORM_DIR = _p("platform_dir")
X0_RUNS = {k: Path(v) for k, v in CFG.get("x0_runs", {}).items()}

# ---- 本机工具（自行配置）
OSS_BIN = CFG.get("oss_cad_bin", "/usr/bin")
OR_ENV = CFG.get("openroad_env", "")
NANGATE = _p("nangate45_dir")

# ---- 大模型：密钥只从环境变量或本地密钥文件读取，绝不写进仓库
MODEL_ENV = _p("llm_env_file")
MODEL = CFG.get("llm_model", "")

_LEGACY0 = bool(CFG.get("design_registry_module")) and "cases" not in CFG
# evaluator.marker: substring of the evaluator script name; every such python process on the host counts against evaluator.global
POOLS = CFG.get("pools", {"evaluator": {"own": 2, "global": 8, "marker": "sca_replica" if _LEGACY0 else CFG.get("evaluator_marker", "evaluator")}, "openroad": {"own": 3},
                          "build": {"own": 2}, "llm": {"own": 4}, "cpu": {"own": 8}})
HOST_GATE = CFG.get("host_gate", {"loadavg1_lt": 60.0, "free_home_ge_GiB": 40.0, "mem_available_ge_GiB": 32.0})
HOST_DISK = CFG.get("host_disk_path", "/")
HTTP_HOST, HTTP_PORT = "127.0.0.1", int(os.environ.get("WANGSI_PORT", CFG.get("port", 18765)))
# 设计列表完全由配置决定；未配置时为空（只启用模块流程与 DEF 导入）
# v1.0 配置（没有 cases / readbacks 等键）按 v1.0 的写死行为补默认值，升级后无需改配置
LEGACY = bool(CFG.get("design_registry_module")) and "cases" not in CFG
CASES = list(CFG.get("cases", ["bp_fe", "bp_be", "bp_multi", "swerv_wrapper"] if LEGACY else []))
CASE_ALIASES = dict(CFG.get("case_aliases", {"swerv": "swerv_wrapper"} if LEGACY else {}))
# 同状态 hold/clock 回读：{case: {"derivation": 派生工具名, "args": "result_workroot" | "final_dir", "runtime_subdir": 目录名}}
_LEGACY_RB = {"bp_fe": {"derivation": "rb_bpfe_wangsi", "args": "result_workroot", "runtime_subdir": "readback_bpfe"},
              "bp_be": {"derivation": "rb_bpbe_wangsi", "args": "final_dir", "runtime_subdir": "readback_bpbe"}}
READBACKS = dict(CFG.get("readbacks", {c: r for c, r in _LEGACY_RB.items() if r["derivation"] in CFG.get("derivations", {})}))
READBACK_CASES = list(READBACKS)
SCENE_CASES = list(CFG.get("scene_cases", list(CFG.get("x0_runs", {}))))
RUNTIME_SCAN_SUBDIRS = list(CFG.get("runtime_scan_subdirs", ["sca_ncso"] if LEGACY else []))   # 可生成 3D 场景的设计（默认：配置了 X0 的设计）
CLOCK_NAME = CFG.get("clock_name", "CLK")


def blocks():
    """示例/用户模块：blocks/<name>/ 下同时有 <name>.v、tb_<name>.v 和 CONTRACT.json 的目录。"""
    if not BLOCKS.is_dir():
        return []
    return sorted(d.name for d in BLOCKS.iterdir()
                  if (d / f"{d.name}.v").is_file() and (d / f"tb_{d.name}.v").is_file() and (d / "CONTRACT.json").is_file())


def features():
    """各功能是否已配置齐全，供界面、doctor 和自检使用。返回 {feature: {"ok": bool, "missing": [...]}}。"""
    import shutil

    def chk(items):
        miss = [k for k, ok in items if not ok]
        return {"ok": not miss, "missing": miss}
    eco_files = [("design_registry_module", D2_MODULE.is_file()), ("eco_builder_module", FORMAL_BUILD.is_file()),
                 ("gate_bounds_json", V6_BOUNDS.is_file()), ("normalized_states_json", NORMALIZED.is_file()),
                 ("runtime_dir", RT.is_dir()), ("cases", bool(CASES))]
    ob = Path(OSS_BIN)
    tools = [(t, (ob / t).exists() or shutil.which(t) is not None) for t in ("yosys", "iverilog", "vvp", "verilator")]
    orb = Path(OR_ENV) / "bin" / "openroad" if OR_ENV else None
    return {
        "eco": chk(eco_files),
        "scene3d": chk([("platform_dir", PLATFORM_DIR.is_dir()), ("x0_runs", any(Path(v).is_dir() for v in X0_RUNS.values()))]),
        "block_flow": chk(tools + [("openroad_env/bin/openroad", bool(orb and orb.exists())), ("nangate45_dir", NANGATE.is_dir()),
                                   ("blocks/*", bool(blocks()))]),
        "llm": chk([("WANGSI_LLM_API_KEY or llm_env_file", bool(os.environ.get("WANGSI_LLM_API_KEY")) or MODEL_ENV.is_file()),
                    ("llm_model", bool(MODEL))]),
    }

for d in (VAR, JOBS, DERIVED):
    d.mkdir(parents=True, exist_ok=True)
