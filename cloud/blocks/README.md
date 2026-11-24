# `cloud/blocks` — Block-Level RTL-to-GDS Designs

This directory holds self-contained Verilog blocks executed by the `block_flow` controller (Verilator lint → Icarus Verilog sim → Yosys synthesis → OpenROAD P&R & signoff on Nangate45) together with the shared power distribution network script (`pdn_nangate45.tcl`).

## Auto-Discovery Convention

`wangsieda.config.blocks()` automatically discovers any subdirectory `blocks/<name>/` that contains all three required files:

| File | Required | Purpose |
|---|---|---|
| `<name>.v` | Yes | Synthesizable RTL top module (`module <name>`). |
| `tb_<name>.v` | Yes | Self-checking testbench that prints `RESULT PASS` / `RESULT FAIL` and `OPFAIL` / `MISMATCH` lines. |
| `CONTRACT.json` | Yes | Frozen clock SDC (`sdc`, `clock_period_ns`) and testbench integrity hash (`tb_sha256`). |
| `spec.md` | Optional | Natural-language specification passed to `rtl_debug` when simulation fails. |

## Freezing the Testbench

Before running `block_flow`, compute and record the testbench SHA-256 so `rtl_sim` can verify that neither agents nor edits altered the verification oracle:

```bash
cd cloud
bin/wangsictl freeze-tb rv_alu
```

## P&R Checkpoints & Allowed Knobs

`block_flow` runs five OpenROAD stages (`floorplan` → `place` → `cts` → `route` → `signoff`), saving `.odb` checkpoints after each stage so `pnr_diagnose` can resume from the earliest affected stage without redoing earlier work.

| Knob | Type | Allowed Range | Earliest Stage |
|---|---|---|---|
| `core_utilization` | `int` | `20` – `70` | `floorplan` |
| `place_density` | `float` | `0.3` – `0.95` | `place` |
| `timing_driven` | `bool` | `true` / `false` | `place` |
| `setup_slack_margin_ps` | `int` | `0` – `150` | `cts` |
| `max_buffer_percent` | `int` | `10` – `40` | `cts` |
| `grt_layer_adjust` | `float` | `0.2` – `0.7` | `route` |
| `droute_iters` | `int` | `16` – `64` | `route` |

The clock period in `CONTRACT.json` is a **frozen contract** and cannot be modified by agents or job parameters.

## Bundled Example: `rv_alu`

`rv_alu/` provides a 16-bit RV32-style ALU/MUL unit (`1.55 ns` clock target) demonstrating closed-loop RTL repair and P&R signoff closure. See [`rv_alu/spec.md`](rv_alu/spec.md) for the port and opcode table.
