# `wangsieda` Core Package

The `wangsieda` Python package implements the wangsiEDA service, job engine, tool registry, signoff gates, 3D scene extractor, LLM client, MCP server, and local synthetic demo. It uses the **Python 3.9+ standard library only** with zero third-party runtime dependencies.

## Module Overview

| Module | Responsibility |
|---|---|
| `config.py` | Loads `wangsi_config.json` (or `WANGSI_CONFIG`), resolves paths, scheduling pools, host admission thresholds, block discovery (`blocks()`), and independent feature readiness (`features()`). |
| `store.py` | SQLite (WAL mode) persistence layer with thread-local connections and short transactions (`designs`, `states`, `evals`, `candidates`, `jobs`, `steps`, `events`, `skills`, `llm_calls`, `meta`, `marks`). |
| `engine.py` | Durable dynamic-DAG job engine. Supervises detached step workers (`setsid`), re-attaches running steps across daemon restarts via `(pid, /proc start time)`, enforces pool caps and host admission gates, and handles timeout (`SIGTERM` → 120 s grace → `SIGKILL`), retries, and cancellation. |
| `worker.py` | Isolated single-step runner. Validates arguments against the tool's JSON Schema, executes one registered tool in its own `jobs/<job>/<NN>-<step>/` directory, and atomically writes `result.json`. |
| `flows.py` | State-machine controllers that grow each job's DAG: `eco_eval`, `eco_closure`, `block_flow`, and `ask`. |
| `daemon.py` | `wangsid` HTTP + SSE server (`127.0.0.1`, per-install token auth), single-instance `fcntl` lock, background scheduler loop, and periodic catalog refresh. |
| `catalog.py` | Read-only data-lake indexer for design registries, gate bounds, canonical signed-off states, `RESULT.json` / `REJUDGE.json` evaluations, and `MODEL_LEDGER.jsonl`. |
| `llm.py` | OpenAI-compatible `/chat/completions` client with per-job call budgets, JSON extraction, automatic `response_format` fallback, and TLS CA discovery. Credentials are read at call time and never written to logs or events. |
| `mcp_server.py` | Stdio JSON-RPC 2.0 MCP server (`2024-11-05`). Executes read-only tools directly and submits mutating/long-running operations as durable jobs. |
| `scene3d.py` | Extracts 2.5D/3D signoff scenes from evaluator outputs (`6_final.def`, `violated_paths`, `5_route_drc.rpt`, `hbt_net_merge.tsv`, `route.guide`) plus independent verification counters (`verify_block`). |
| `layout.py` | Pure-Python DEF/LEF parser producing compact 2D layout JSON for block-level signoff visualization. |
| `pnr_scripts.py` | Generates per-stage OpenROAD Tcl scripts (`floorplan`, `place`, `cts`, `route`, `signoff`) with `.odb` checkpoints for `block_flow`. |
| `demo.py` | Generates a self-contained synthetic F2F design in `cloud/demo_root/`, runs the real `scene_build` pipeline, seeds catalog/job data, and serves or packages (`--package OUT`) the demo console. |
| `export_snapshot.py` | Exports a sanitized, read-only snapshot (stripping prompts, secrets, paths, hashes, and raw records) for offline sharing via `make_offline_package.py`. |
| `prompt_eval.py` | Evaluates agent prompt variants (A/B/C or custom files) on fixed synthetic scenarios using real agent code and deterministic guards (`--mock good|bad` supported for offline CI). |
| `doctor.py` | Environment and configuration diagnostics (`wangsictl doctor`) and testbench SHA-256 freezing (`wangsictl freeze-tb <block>`). |
| `selftest.py` | Offline platform self-test covering tool registration, guards, MCP handshake, and (when configured) judge parity against stored verdicts. |
| `cli.py` | Operator CLI (`jobs`, `job`, `wait`, `submit`, `retry`, `rejudge`, `events`). |

## Subpackages

- [`tools/`](tools/README.md) — Tool registry (`@tool`), JSON Schema validation, and all 29 registered tools.
- [`ui/`](ui/README.md) — Zero-dependency web console and WebGL2 3D signoff viewer.

## Execution Lifecycle

```text
HTTP API / CLI / MCP
        │
        ▼
  engine.create_job() ──▶ SQLite (jobs, steps, events)
        │
        ▼
  engine.tick() (daemon loop)
        ├── checks dependencies & scheduling pools / host admission gate
        ├── spawns worker.py in detached session (jobs/<job>/<NN>-<step>/)
        ├── finalizes result.json (retry / fail / done)
        └── invokes flows.FLOWS[kind].on_step() to append downstream steps
```
