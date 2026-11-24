# `wangsieda.tools` — Tool Registry & Reference

Every capability exposed to agents, flow controllers, HTTP read endpoints, or MCP clients is registered with `@tool(...)` in `wangsieda/tools/__init__.py`. Each tool declares:

- **JSON Schema** (`input_schema`) validated by `validate_args()` before execution.
- **Side-effect class**:
  - `read`: reads project data only (may be called synchronously via `/api/tools/<name>` and MCP).
  - `compute`: writes only inside its own step directory.
  - `runtime`: writes a unique namespace under `<runtime_dir>/wangsi/`.
  - `llm`: calls the configured OpenAI-compatible model under the job's call budget.
  - `asset`: writes to the wangsiEDA catalog or skill/lesson library.
- **Scheduling pool** (`evaluator`, `openroad`, `build`, `llm`, `cpu`, or `None`), **timeout**, and **retry count**.

## Registered Tools (29)

### Catalog & Evidence (`catalog_tools.py`)

| Tool | Side Effect | Pool | Timeout | MCP | Description |
|---|---|---|---|---|---|
| `design_info` | `read` | — | 600 s | Yes | Design registry entry, X0 metrics, Ge/Gs gate bounds, pool size, readback config, timeout. |
| `signed_off_states` | `read` | — | 600 s | Yes | Canonical signed-off states (X0 and compared methods) for a design. |
| `candidate_pool` | `read` | — | 600 s | Yes | Candidate ECO programs with predictions (`expected_gain_ns`, `p_gate`, etc.) and measured status. |
| `eval_history` | `read` | — | 600 s | Yes | Measured evaluations for a design, ordered by setup TNS. |
| `program_evidence` | `read` | — | 600 s | Yes | Combined prediction, measurement history, and design skills for one candidate tag. |
| `skills_lookup` | `read` | — | 600 s | Yes | Lessons and skills stored in the asset library. |

### ECO Execution & Signoff (`eco.py`)

| Tool | Side Effect | Pool | Timeout | MCP | Description |
|---|---|---|---|---|---|
| `program_validate` | `read` | `cpu` | 300 s | Yes | Static validation with the frozen ECO builder plus no-replay check. |
| `eco_build` | `runtime` | `build` | 7200 s | Yes | Builds `5_1_grt.odb` + `route.guide` submission; refuses previously measured tags. |
| `eval_replica` | `runtime` | `evaluator` | 32400 s | Yes | Runs the derived signoff evaluator (detailed route, RCX, STA, DRC, HBT legality). |
| `eval_progress` | `read` | — | 600 s | Yes | Parses live detailed-route iterations, violation trace, and stage progress. |
| `sta_readback` | `runtime` | `cpu` | 10800 s | Yes | Same-final-state clock/hold STA readback using the sealed X0 template. |
| `signoff_judge` | `read` | `cpu` | 120 s | Yes | Evaluates metrics against exact gate **Gs** and envelope gate **Ge** (missing metrics fail). |
| `eval_cleanup` | `runtime` | `cpu` | 600 s | Yes | Removes large intermediate `.odb` files after readback while keeping `6_final.*` and reports. |
| `ingest_result` | `asset` | `cpu` | 60 s | Yes | Writes a judged wangsiEDA evaluation into the SQLite catalog. |
| `host_status` | `read` | — | 600 s | Yes | Returns host load, disk, available memory, and pool occupancy. |

### Block RTL-to-GDS (`blockflow.py`)

| Tool | Side Effect | Pool | Timeout | MCP | Description |
|---|---|---|---|---|---|
| `rtl_lint` | `compute` | `cpu` | 120 s | Yes | Verilator lint check on an RTL file. |
| `rtl_sim` | `compute` | `cpu` | 300 s | Yes | Icarus Verilog simulation against the block's SHA-256-frozen testbench. |
| `synth` | `compute` | `cpu` | 600 s | Yes | Yosys synthesis to Nangate45 with SDC generated from `CONTRACT.json`. |
| `pnr_stage` | `compute` | `openroad` | 1800 s (1 retry) | Yes | Runs one OpenROAD stage (`floorplan`, `place`, `cts`, `route`, `signoff`) from checkpoints. |

### LLM Agents & Deterministic Guards (`agent_tools.py`)

| Tool | Side Effect | Pool | Timeout | MCP | Description |
|---|---|---|---|---|---|
| `eco_plan` | `llm` | `llm` | 1800 s | Yes | ReAct ECO planner restricted to 6 read-only catalog tools. |
| `eco_critic` | `read` | `cpu` | 900 s | Yes | Deterministic plan gate: pool membership, no-replay, buildability, and budget. |
| `eco_learn` | `asset` | `llm` | 600 s | Yes | Post-evaluation reflection; marks lessons `verified=1` only when **Ge** passes. |
| `rtl_debug` | `llm` | `llm` | 600 s | Yes | Minimal RTL patch generator (unique anchor, ≤3 lines, frozen ports/parameters). |
| `pnr_diagnose` | `llm` | `llm` | 600 s | Yes | P&R knob recommender (whitelist, ranges, frozen clock, no revisits, resume-point correction). |

### 3D Visualization (`viz_tools.py`)

| Tool | Side Effect | Pool | Timeout | MCP | Description |
|---|---|---|---|---|---|
| `guide_diff` | `compute` | `cpu` | 900 s | Yes | Computes added/removed global-route guide boxes between X0 and an ECO submission. |
| `scene_build` | `compute` | `cpu` | 3600 s | Yes | Builds a 3D signoff scene from evaluator outputs and diffs it against X0. |

### Console Assistant (`assistant.py`)

| Tool | Side Effect | Pool | Timeout | MCP | Description |
|---|---|---|---|---|---|
| `list_jobs` | `read` | — | 600 s | Yes | Lists recent non-`ask` jobs with optional kind/status filters. |
| `job_info` | `read` | — | 600 s | Yes | Returns a job's steps and recorded outcome summary. |
| `assistant_ask` | `llm` | `llm` | 900 s | No | Natural-language Q&A over read-only tools; may propose (never submit) one validated job. |

## Adding a New Tool

1. Define the function in the appropriate module under `cloud/wangsieda/tools/` and decorate it with `@tool(name, description, obj(...), side_effect=..., pool=..., timeout=...)`.
2. Ensure `load_all()` in `tools/__init__.py` imports your module.
3. Keep all file writes inside `ctx.dir` (`compute`) or `C.RT_WANGSI` (`runtime`).
4. Add schema/logic tests in `cloud/tests/test_core.py` and run `bin/wangsictl selftest`.
