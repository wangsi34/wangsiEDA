# `cloud/prompts` — Agent Prompts & Evaluation

wangsiEDA separates prompt text from agent logic and deterministic guards. Built-in prompts for all five agents ship under `default/` in three variants, and any custom prompt placed directly in this directory overrides the built-in default.

## Loading Priority

Resolved by `wangsieda.tools.agent_tools.prompt_source(name)`:

1. `PROMPT_OVERRIDE[name]` (in-memory override used only by `prompt_eval.py`).
2. `cloud/prompts/<name>.txt` (user-authored prompt; ignored if empty or starting with `#`, and excluded from git via `.gitignore`).
3. `cloud/prompts/default/<name>.<variant>.txt` when `use_default_prompts` is `true` (default).

Variant selection order: `WANGSI_PROMPT_VARIANT` env var → `default_prompt_variant` in `wangsi_config.json` (string or per-agent dict) → built-in `DEFAULT_VARIANT`.

## Agents & Default Variants

| Agent | Default Variant | Placeholders | Expected JSON Contract |
|---|---|---|---|
| `eco_planner` | `B` | `BUDGET` | Tool step: `{thought, tool, args}` · Final: `{thought, final: {programs, hypothesis, risks}}` |
| `reflector` | `C` | — | `{lesson, trigger, action, evidence}` |
| `rtl_debug` | `B` | — | `{diagnosis, file, find, replace, confidence}` |
| `pnr_diagnose` | `C` | — | `{diagnosis, knobs, resume_from, expected_effect}` |
| `assistant` | `B` | `SITE_CONTEXT` | Tool step: `{thought, tool, args}` · Final: `{thought, final: {answer, proposed_job}}` |

Variant styles:
- **A**: Minimal instruction set.
- **B**: Structured rules and guard awareness.
- **C**: Structured rules plus one worked example.

Benchmark data and rationale for the defaults are documented in [`docs/提示词评测.md`](../../docs/提示词评测.md).

## Customizing & Scoring Prompts

Copy any `*.txt.example` to `<name>.txt`, edit it, and evaluate it on the fixed synthetic scenarios without touching production data:

```bash
# Score built-in A/B/C variants
bin/wangsictl prompt-eval

# Score your own prompt file
bin/wangsictl prompt-eval --prompt-file eco_planner=prompts/eco_planner.txt

# Offline sanity check of the scoring harness (no API key required)
python3 -m wangsieda.prompt_eval --mock good
```
