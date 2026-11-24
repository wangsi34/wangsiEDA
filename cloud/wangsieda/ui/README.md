# `wangsieda.ui` — Web Console & WebGL2 3D Viewer

The wangsiEDA frontend is written in **vanilla HTML, CSS, and JavaScript** with **no build step and no third-party libraries**. It runs either live against `wangsid` (over an SSH tunnel to `127.0.0.1`) or completely offline via `打开念思.html` (`window.WANGSI_OFFLINE`).

## Files

| File | Responsibility |
|---|---|
| `index.html` | Single-page shell, navigation bar, slide-over detail drawer, and script loading order. |
| `style.css` | Dark engineering theme, responsive grids, KPI cards, tables, DAG/timeline badges, and chat panel styles. |
| `app.js` | Hash router, token auth (`X-Wangsi-Token` stored in `sessionStorage`), API helper, and views for Getting Started, Overview, Design/Signoff Landscape (SVG scatter), Jobs (DAG + live SSE stream + files), Evals, LLM Calls, Tools/MCP, and Skills. |
| `viewer3d.js` | Custom WebGL2 2.5D/3D signoff renderer (`V3`). Renders bottom/upper dies, 21 layers (`metal1`–`metal10`, `hbt`, `metal11`–`metal20`), instanced vias, SRAM macros, focus nets, violated paths, DRC markers, and adjustable vertical exaggeration. |
| `review.js` | Seven-step guided 3D signoff walkthrough (`X0` → `CERTIFIED` → `NEAR_MISS`) plus free inspection controls (layers, nets, paths, DRC, density). |
| `verify.js` | In-browser consistency checker comparing rendered 3D geometry counts and wirelength against independent DEF and evaluator report totals. |
| `extras.js` | Chinese/English (`zh` / `en`) language toggle, offline snapshot reader (`offlineGet` for `file://` packages), and local browser-side DEF/LEF/DRC importer. |
| `chat.js` | "Ask wangsiEDA" chat drawer: submits `ask` jobs, streams live `tool_call` traces over SSE, renders markdown safely (`chatMd`), and presents validated job proposal cards with explicit user confirmation. |

## Hash Routes

| Route | View |
|---|---|
| `#/` | 3D signoff review (or Getting Started when no 3D scene is configured) |
| `#/jobs` | Job list and job submission forms (`eco_eval`, `eco_closure`, `block_flow`) |
| `#/job/<id>` | Single job DAG, step timeline, live SSE event log, LLM calls, and step artifacts |
| `#/designs` / `#/design/<case>` | Gate bounds, signoff landscape scatter plot, signed-off states, candidate pool, and evaluation history |
| `#/evals` | Filterable evaluation ledger across designs |
| `#/agents` | Model call history with token usage and latency |
| `#/tools` | Tool registry browser and synchronous execution for read-only tools |
| `#/skills` | Learned lessons and verified skills |
| `#/overview` | Host telemetry, scheduling pool occupancy, and design summary |

## Syntax Check

CI validates all scripts without a bundler:

```bash
for f in cloud/wangsieda/ui/*.js; do node --check "$f"; done
```
