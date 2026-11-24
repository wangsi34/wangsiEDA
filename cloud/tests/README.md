# `cloud/tests` — Test Suite & Verification Guide

All unit tests run using Python's built-in `unittest` module with no external test runner or private design data required.

## Running Tests

From the `cloud/` directory:

```bash
# 1. Core unit tests (gates, parsers, schema validation, fresh install, mock prompt-eval)
python3 -B -m unittest discover -v tests

# 2. Platform self-test (tool registry, guards, MCP handshake; judge parity when ECO assets are configured)
bin/wangsictl selftest

# 3. Offline prompt-evaluator sanity check (no API key required)
python3 -B -m wangsieda.prompt_eval --mock good
python3 -B -m wangsieda.prompt_eval --mock bad

# 4. Bytecode compilation check (from repository root)
python3 -m compileall -q cloud/wangsieda wangsi.py make_offline_package.py
```

## What `test_core.py` Covers

| Test Case | Coverage |
|---|---|
| `JudgeTest` | Envelope gate **Ge** pass/gain calculation, every individual gate blocking condition, missing-metric failures, optional area bound, and clock skew / `clock_UNMEASURED` handling. |
| `ParserTest` | `scene3d.guide_diff`, `scene3d.parse_drc` (including cut-layer mapping), and `scene3d.parse_paths`. |
| `FreshInstallTest` | Clean startup with no `wangsi_config.json`, `catalog.refresh`, `config.features()`, explicit `ValueError` in flow parameter checks, and `doctor.report()`. |
| `PromptEvalTest` | Verifies that `prompt_eval` cleanly separates `--mock good` (≥ 0.95) from `--mock bad` (≤ 0.55) across all 5 agents. |
| `SchemaTest` | `tools.validate_args` checks for types, enums, numeric bounds, required keys, and unknown arguments. |

## Continuous Integration

GitHub Actions (`.github/workflows/ci.yml`) runs three jobs on every push and pull request:
1. **`python`** (Python 3.9–3.13): `compileall`, `unittest`, `wangsictl selftest`, and `prompt_eval --mock good|bad` score separation.
2. **`demo`** (Python 3.12): generates the synthetic demo, starts the server, and checks HTTP 200 (with token) and HTTP 401 (without token) on core API endpoints.
3. **`frontend`** (Node 22): `node --check` syntax validation on every `cloud/wangsieda/ui/*.js` file.
