"""安装诊断：逐项检查配置、工具、提示词和密钥，告诉用户哪些功能可用、缺什么、怎么补。

  python3 -m wangsieda.doctor            诊断报告（不运行 EDA，不调用模型）
  python3 -m wangsieda.doctor --json     机器可读
  python3 -m wangsieda.doctor freeze-tb <block>   计算 tb_<block>.v 的 SHA-256 并写入 CONTRACT.json
"""
import hashlib, json, socket, sys
from pathlib import Path
from . import config as C

FIX = {
    "design_registry_module": "在 wangsi_config.json 填 design_registry_module（提供 DESIGNS / fb() / cand_context）",
    "eco_builder_module": "在 wangsi_config.json 填 eco_builder_module（提供 validate / prepare / execute）",
    "gate_bounds_json": "在 wangsi_config.json 填 gate_bounds_json（签核门上下界）",
    "normalized_states_json": "在 wangsi_config.json 填 normalized_states_json（X0 及各方法的签核状态）",
    "runtime_dir": "在 wangsi_config.json 填 runtime_dir（评测器运行目录）",
    "cases": "在 wangsi_config.json 填 cases，例如 [\"bp_fe\"]",
    "platform_dir": "在 wangsi_config.json 填 platform_dir（含 lef_bottom/ lef_upper/ lef/）",
    "x0_runs": "在 wangsi_config.json 填 x0_runs：{设计: X0 评测目录}",
    "openroad_env/bin/openroad": "在 wangsi_config.json 填 openroad_env（其下有 bin/openroad）",
    "nangate45_dir": "在 wangsi_config.json 填 nangate45_dir（含 Nangate45_typ.lib / Nangate45_stdcell.lef）",
    "blocks/*": "在 blocks/<名字>/ 放 <名字>.v、tb_<名字>.v、CONTRACT.json",
    "WANGSI_LLM_API_KEY or llm_env_file": "设置环境变量 WANGSI_LLM_API_KEY / WANGSI_LLM_BASE_URL，或在配置里填 llm_env_file（chmod 600）",
    "llm_model": "在 wangsi_config.json 填 llm_model",
}
for t in ("yosys", "iverilog", "vvp", "verilator"):
    FIX[t] = f"安装 {t}（例如 oss-cad-suite），并在 wangsi_config.json 填 oss_cad_bin"

FEATURE_NAMES = {"eco": "ECO 评测 / Agentic closure", "scene3d": "3D 签核评审", "block_flow": "模块 RTL→GDS", "llm": "大模型 Agent"}
AGENT_PROMPTS = {"eco": ["eco_planner", "reflector"], "block_flow": ["rtl_debug", "pnr_diagnose"]}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def prompt_state(name):
    from .tools.agent_tools import prompt_source
    t, src = prompt_source(name)
    if not t:
        return "missing"
    return "ok" if src.startswith("prompts/") and "/default/" not in src else "default:" + src


def report():
    feats = C.features()
    out = {"python": sys.version.split()[0], "config_file": str(C._CFG_FILE), "config_found": C._CFG_FILE.exists(),
           "features": feats, "prompts": {}, "blocks": {}, "port": C.HTTP_PORT}
    for names in AGENT_PROMPTS.values():
        for n in names:
            out["prompts"][n] = prompt_state(n)
    for b in C.blocks():
        con = json.loads((C.BLOCKS / b / "CONTRACT.json").read_text())
        want = con.get("tb_sha256")
        out["blocks"][b] = "frozen" if want and want == sha(C.BLOCKS / b / f"tb_{b}.v") else ("changed" if want else "not frozen")
    s = socket.socket()
    try:
        out["port_in_use"] = s.connect_ex(("127.0.0.1", C.HTTP_PORT)) == 0
    finally:
        s.close()
    return out


def show(r):
    mark = {True: "[ok]", False: "[--]"}
    print(f"wangsiEDA doctor · Python {r['python']} · 配置文件 {r['config_file']} {'(已找到)' if r['config_found'] else '(未找到，全部使用默认值)'}")
    if C.LEGACY:
        print(f"[..] 检测到 v1.0 配置：按旧行为补默认值 cases={C.CASES}，回读={C.READBACK_CASES}。建议把这些键写进配置（见 wangsi_config.example.json）")
    print()
    for k, v in r["features"].items():
        print(f"{mark[v['ok']]} {FEATURE_NAMES.get(k, k)}")
        for m in v["missing"]:
            print(f"      缺少 {m}: {FIX.get(m, '')}")
    print()
    for feat, names in AGENT_PROMPTS.items():
        for n in names:
            st = r["prompts"][n]
            if st == "missing":
                print(f"[--] 提示词 {n}：未配置；打开 use_default_prompts 使用内置提示词，或复制 prompts/{n}.txt.example 自行编写（{FEATURE_NAMES[feat]} 的 LLM 模式需要）")
            elif st.startswith("default:"):
                print(f"[ok] 提示词 {n}：内置 {st[8:]}（可用 prompts/{n}.txt 覆盖；用 wangsictl prompt-eval 比较）")
            else:
                print(f"[ok] 提示词 {n}：prompts/{n}.txt（自定义）")
    for b, st in r["blocks"].items():
        hint = "" if st == "frozen" else f"；运行 bin/wangsictl freeze-tb {b}" if st == "not frozen" else "；测试平台与 CONTRACT.json 不一致，确认改动后重新 freeze-tb"
        print(f"{mark[st == 'frozen']} 模块 {b}：测试平台 {st}{hint}")
    print(f"[..] 端口 {r['port']} {'已被占用（服务可能已在运行）' if r['port_in_use'] else '空闲'}")
    ready = [FEATURE_NAMES[k] for k, v in r["features"].items() if v["ok"]]
    print()
    print("可用功能：" + ("、".join(ready) if ready else "仅 DEF 导入与界面浏览") + "。DEF/LEF 导入始终可用。")


def freeze_tb(block):
    d = C.BLOCKS / block
    tb, con_p = d / f"tb_{block}.v", d / "CONTRACT.json"
    if not tb.is_file() or not con_p.is_file():
        sys.exit(f"找不到 {tb} 或 {con_p}")
    con = json.loads(con_p.read_text())
    old, new = con.get("tb_sha256"), sha(tb)
    con["tb_sha256"] = new
    con_p.write_text(json.dumps(con, indent=1, ensure_ascii=False) + "\n")
    print(f"{block}: tb_sha256 = {new}" + (f"（原为 {old}）" if old and old != new else ""))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["freeze-tb"]:
        if len(a) < 2:
            sys.exit("用法: python3 -m wangsieda.doctor freeze-tb <block>")
        freeze_tb(a[1])
    elif "--json" in a:
        print(json.dumps(report(), ensure_ascii=False, indent=1))
    else:
        show(report())
