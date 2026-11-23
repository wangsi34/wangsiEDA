// 3D signoff review of one design: guided walkthrough + free inspection. All numbers come from the evaluator outputs.
"use strict";
// 场景标签：BASE 为初始状态，CERT 为通过签核的修复，MISS 为未通过的对照；用 scene_build 生成同名场景即可。
const BASE = "X0", CERT = "CERTIFIED", MISS = "NEAR_MISS";
const RV = {viewer: null, scenes: {}, ws: null, state: "X0", step: 0, compare: true, case: null, fn: null};
// 设计简介（可选）：没有条目的设计显示通用描述
const DESIGN_NOTES = {
  bp_fe: {en: "the front end of the BlackParrot RISC-V core (PC generation, branch prediction, instruction cache)",
          zh: "BlackParrot RISC-V 处理器的前端（取指、分支预测、指令缓存）"}};
function designBlurb(lang) {
  const n = DESIGN_NOTES[RV.case];
  if (lang === "zh") return `<p><b>${esc(RV.case)}</b>${n ? " 是 " + n.zh + "，" : " "}采用两片 die 面对面键合（F2F）的 3D 结构。</p>`;
  return `<p><b>${esc(RV.case)}</b> is ${n ? n.en + ", " : "a design "}built as a face-to-face 3D IC.</p>`;
}
// 焦点线网：优先取 ECO 改动过的线网中扇出最大的；否则取 X0 中扇出最大的线网
function pickFocusNet(x0, cert) {
  const fo = n => (x0.nets[n] || {}).fanout || 0;
  const ch = (((cert && cert.guide_diff) || {}).changed_nets || []).filter(n => x0.nets[n]);
  if (ch.length) return ch.sort((a, b) => fo(b) - fo(a))[0];
  const all = Object.keys(x0.nets || {}).sort((a, b) => fo(b) - fo(a));
  return all[0] || null;
}
const clockNs = () => ((CFG && CFG.clock_period_ns) || {})[RV.case] ?? null;

async function sceneOf(label) {
  if (!RV.scenes[label]) RV.scenes[label] = await api(`/api/scene?case=${encodeURIComponent(RV.case)}&label=${encodeURIComponent(label)}&_=${Date.now()}`);
  return RV.scenes[label];
}
const n0 = v => v === null || v === undefined ? "-" : Number(v).toLocaleString(undefined, {maximumFractionDigits: 4});
const delta = (a, b, better = "lower") => {
  if (a == null || b == null) return "";
  const d = b - a, good = better === "lower" ? d < 0 : d > 0;
  return `<span class="${d === 0 ? "mu" : good ? "ok" : "bad"}">${d > 0 ? "+" : ""}${n0(Math.round(d * 1e4) / 1e4)}</span>`;
};

function rvSteps() {
  const S = RV.scenes, x0 = S.X0, ws = RV.ws, st = l => ws.states.find(s => s.label === l) || {}, e310 = st(CERT).eval, e312 = st(MISS).eval;
  const stdc = x0.cells.filter(c => c[5] !== 5).length, mUp = x0.macros.filter(m => m[4] === 1).length, mBot = x0.macros.length - mUp;
  const worst = x0.paths[0], n1194 = x0.nets[RV.fn] || {}, a1194 = (S[CERT] && S[CERT].nets[RV.fn]) || {};
  const ws0 = worst.worst_stage || [];
  const gd310 = (S[CERT] && S[CERT].guide_diff) || {changed_nets: [], nets: {}};
  const added310 = Object.values(gd310.nets).reduce((a, v) => a + v.added.length, 0);
  const m = x => x.metrics || {};
  const byDie = n => { const o = {bottom: 0, upper: 0}; (n.wires || []).forEach(w => { const L = (Math.abs(w[2] - w[0]) + Math.abs(w[3] - w[1])) / 100; if (w[4] < 10) o.bottom += L; else if (w[4] > 10) o.upper += L; }); return o; };
  const d0 = byDie(n1194), d1 = byDie(a1194);
  const own = ws.states.filter(x => x.label.includes("_") && x.eval && x.eval.v6_pass && S[x.label]).slice(-1)[0];
  const steps = [
    {title: "1  Design and 3D stack", state: "X0", view: "iso", show: {cells: true, macros: true, hbt: true, routes: true, nets: false, paths: false, endpoints: false, drc: false, guides: false, density: false}, explode: 40,
     body: `${designBlurb("en")}
       <p>The bottom die holds all ${n0(stdc)} standard cells (blue: logic, purple: flip-flops, orange: buffers) and ${mBot} SRAM macros. The upper die holds ${mUp} SRAM macros
       shown as grey blocks.
       The dies are bonded metal-to-metal. The ${n0(x0.hbt.length)} copper pads on the bond interface are hybrid-bond terminals (HBT), the only way a signal moves between dies.</p>
       <table class="mt"><tr><td>Die</td><td>${(x0.die[0] / 100).toFixed(0)} x ${(x0.die[1] / 100).toFixed(0)} um</td></tr><tr><td>Routing layers</td><td>metal1-10 bottom die, metal11-20 upper die</td></tr>
       <tr><td>Clock period</td><td>${clockNs() ?? "—"} ns</td></tr><tr><td>Placement</td><td>fixed: the ECO may not move or resize any cell</td></tr></table>`},
    {title: "2  The problem: setup timing at X0", state: "X0", view: "top", show: {cells: true, macros: false, hbt: false, nets: false, paths: false, endpoints: true, drc: false, guides: false, density: false}, explode: 0,
     body: `<p>X0 is the routed design before any repair. Each coloured point is a flip-flop whose input data arrives later than the clock allows (setup violation). Yellow is slightly late, red is late by several ns.</p>
       <table class="mt"><tr><td>Setup TNS (sum of lateness)</td><td class="bad">${n0(m(x0).finish__timing__setup__tns)} ns</td></tr><tr><td>Worst setup slack</td><td class="bad">${n0(m(x0).finish__timing__setup__ws)} ns</td></tr>
       <tr><td>Violating endpoints</td><td>${n0(m(x0).finish__timing__drv__setup_violation_count)}</td></tr><tr><td>Routing DRC markers</td><td>${n0(x0.drc.length)}</td></tr></table>
       <p class="mu">The violations cluster in the PC-generation and branch-prediction logic, the area all the following steps zoom into.</p>`},
    {title: "3  Root cause: the critical net", state: "X0", view: "iso", focusNet: RV.fn, show: {cells: true, macros: false, hbt: false, nets: true, paths: true, endpoints: false, drc: false, guides: false, density: false}, explode: 0, play: 0,
     body: `<p>The red ribbon is the worst path (slack ${worst.slack} ns). Press <b>Play signal</b>: the white dot moves at the speed the signal actually travels, so it stalls where the delay is.</p>
       <p>It stalls on <b>${esc(ws0[0] || "")}</b>: ${ws0[2]} ns for a single stage${clockNs() ? `, ${(ws0[2] / clockNs()).toFixed(1)}x the clock period` : ""}. This stage sits on <b>${esc(RV.fn)}</b>, driven by one input buffer and routed as one shared tree to ${n0(n1194.fanout)} loads (${n0(n1194.length_um)} um of wire, yellow points).
       A load at the far end of the tree has to wait until the whole tree is charged.</p>
       <p><b>Fix idea (HCSI, critical-sink isolation):</b> give the few critical loads their own short branch from the driver, by editing the global-route guides only. No cell moves.</p>`},
    {title: "4  The ECO change", state: CERT, view: "iso", focusNet: RV.fn, show: {cells: false, macros: false, hbt: true, nets: true, paths: false, endpoints: false, drc: false, guides: true, density: false}, explode: 30,
     body: `<p>The selected ECO program as built and evaluated.</p>
       <p>Green boxes are the global-route guides the ECO added, green wires are the routes the detailed router actually produced on the changed nets, and grey is the routing of the same nets in X0. Green vertical lines are HBTs added by the change.</p>
       <table class="mt"><tr><td>Nets with changed guides</td><td>${gd310.changed_nets.length}</td></tr><tr><td>Guide boxes added</td><td>${n0(added310)}</td></tr>
       <tr><td>HBTs added</td><td>${(S[CERT].hbt_new || []).length} (${n0(x0.hbt.length)} -> ${n0(S[CERT].hbt.length)})</td></tr>
       <tr><td>${esc(RV.fn)} on bottom-die metal (M1-M10)</td><td>${n0(Math.round(d0.bottom))} -> ${n0(Math.round(d1.bottom))} um</td></tr>
       <tr><td>${esc(RV.fn)} on upper-die metal (M11-M20)</td><td>${n0(Math.round(d0.upper))} -> ${n0(Math.round(d1.upper))} um</td></tr><tr><td>Cells moved / resized</td><td>0</td></tr></table>
       <p>The new branches climb through hybrid-bond terminals into the upper die's thick top metal and come back down next to the critical loads. Thick metal has much lower resistance per um,
       so a longer route through it is still faster than the shared thin-metal tree. This is the 3D-specific part of the repair.</p>`},
    {title: "5  Signoff result: certified", state: CERT, view: "top", show: {cells: true, macros: false, hbt: false, nets: false, paths: false, endpoints: true, drc: false, guides: false, density: false}, explode: 0,
     body: `<p>The change is only accepted if every signoff check passes on the real evaluator: setup, hold, DRC, Short, wirelength, HBT capacity and the clock tree (latency and skew may not get worse).</p>
       ${gateTable(x0, S[CERT], e310)}
       <p>Result: envelope gate Ge <b class="${e310 && e310.v6_pass ? "ok" : "bad"}">${e310 && e310.v6_pass ? "PASS" : "FAIL"}</b>, exact gate Gs <b class="${e310 && e310.strict_pass ? "ok" : "bad"}">${e310 && e310.strict_pass ? "PASS" : "FAIL"}</b>; certified setup gain <b>${n0(e310 && e310.gain_pct)}%</b>. Points that disappeared from the map are endpoints that now meet timing.</p>`},
    {title: "6  A near miss", state: MISS, view: "iso", focusNet: RV.fn, show: {cells: false, macros: false, hbt: true, nets: true, paths: false, endpoints: false, drc: false, guides: true, density: false}, explode: 30,
     body: `<p>A second program run through the same pipeline (build, evaluator, readback, gates).</p>
       ${gateTable(x0, S[MISS], e312)}
       <p>Timing improved by ${e312 ? n0(Math.round((1 - e312.tns / m(x0).finish__timing__setup__tns) * 1e4) / 100) : "-"}%, but at least one signoff check failed, so the certified gain is 0.
       The failed check is recorded with the run.</p>`},
  ];
  if (own) {
    const sc = S[own.label], e = own.eval, tag = own.label.split("_")[0], gd = sc.guide_diff || {changed_nets: [], nets: {}};
    steps.push({title: `7  Closed loop: ${tag}, certified by wangsiEDA`, state: own.label, view: "iso", focusNet: RV.fn,
      show: {cells: false, macros: false, hbt: true, nets: true, paths: false, endpoints: false, drc: false, guides: true, density: false}, explode: 30,
      body: `<p>The planner chose <b>${tag}</b> and wangsiEDA ran it end to end:
         build, evaluator, readback, gates, then this 3D scene.</p>
         <table class="mt"><tr><td>Nets with changed guides</td><td>${gd.changed_nets.length}</td></tr><tr><td>HBTs added</td><td>${(sc.hbt_new || []).length}</td></tr></table>
         ${gateTable(x0, sc, e)}
         <p>Envelope gate Ge <b class="ok">PASS</b>, certified setup gain <b>${n0(e.gain_pct)}%</b>. The exact gate Gs still fails on
         ${esc((e.strict_fails || []).join(", "))}; both verdicts are recorded with the run.</p>`});
  }
  return steps;
}

function gateTable(x0, sc, e) {
  if (!e) return '<p class="mu">no judged record</p>';
  const X = RV.ws.x0 || {}, b = RV.ws.v6_bounds || {}, cd = e.clock_delta_ps || {};
  const fails = new Set((e.v6_fails || []).map(f => f.split("(")[0]));
  const row = (name, a, v, bound, better, key) => `<tr><td>${name}</td><td>${n0(a)}</td><td>${n0(v)}</td><td>${delta(a, v, better)}</td><td class="mu">${bound}</td>
      <td>${key && fails.has(key) ? '<span class="tag fail">fail</span>' : '<span class="tag pass">ok</span>'}</td></tr>`;
  return `<table class="mt gt"><tr><th>check</th><th>X0</th><th>after</th><th>change</th><th>limit</th><th></th></tr>
    ${row("setup TNS (ns)", X.official_tns_ns, e.tns, "-", "higher")}
    ${row("worst setup slack (ns)", X.official_wns_ns, e.wns, ">= " + b.wns_inherited, "higher", "wns")}
    ${row("hold TNS (ns)", X.hold_tns_ns, e.hold_tns, ">= " + b.hold_tns_safety, "higher", "hold_tns")}
    ${row("worst hold slack (ns)", X.worst_hold_slack_ns, e.hold_wns, ">= " + b.hold_wns_safety, "higher", "hold_wns")}
    ${row("DRC total", X.drc_total, e.drc, "<= " + b.drc_total_inherited, "lower", "drc_total")}
    ${row("Short", X.Short, e.short, "<= " + b.drc_Short, "lower", "Short")}
    ${row("wirelength (um)", X.detailed_wirelength_um, e.wl, "<= " + n0(b.wirelength_inherited), "lower", "wirelength")}
    ${Object.entries(cd).map(([k, v]) => `<tr><td>clock ${k.replace(/_/g, " ")}</td><td>0</td><td>${v > 0 ? "+" : ""}${v}</td><td>${v > 0 ? '<span class="bad">later</span>' : '<span class="ok">same/earlier</span>'}</td>
      <td class="mu">&lt;= +${(b.clock_delta_allow_ps || {})[k] ?? 0}</td><td>${fails.has("clock_" + k) ? '<span class="tag fail">fail</span>' : '<span class="tag pass">ok</span>'}</td></tr>`).join("")}</table>`;
}

async function review() {
  await cfg();
  RV.case = CFG.default_case; RV.scenes = {};
  RV.ws = RV.case ? await api("/api/workspace?case=" + encodeURIComponent(RV.case)).catch(() => null) : null;
  if (!RV.ws || !RV.ws.states.some(x => x.label === BASE)) return welcome();
  $("#view").innerHTML = `<div class="rv">
    <div class="rv-bar"><b>${esc(RV.case)}</b><span class="mu">signoff review</span>
      <span class="seg" id="rvState"></span><label class="mu"><input type="checkbox" id="rvCmp" checked> show X0 routing for changed nets</label>
      <span class="sp"></span><span class="seg"><button data-v="iso">3D</button><button data-v="top">Top</button><button data-v="side">Side</button></span>
      <label class="mu">die gap <input type="range" id="rvExp" min="0" max="120" value="40"></label>
      <label class="mu">z scale <input type="range" id="rvZs" min="1" max="20" value="4"></label><button class="sm" onclick="rvVerify()">consistency check</button></div>
    <div class="rv-body"><div class="rv-left" id="rvSteps"></div>
      <div class="rv-main"><canvas id="rvCanvas"></canvas><div class="rv-hud" id="rvHud"></div><div class="rv-toggles" id="rvTog"></div></div>
      <div class="rv-right"><h3>Selection</h3><div id="rvSel" class="mu">Click a net driver, path end, HBT or SRAM macro.</div><h3>Legend</h3><div id="rvLeg"></div><h3>Layers</h3><div id="rvLayers"></div></div></div></div>`;
  $("#rvHud").textContent = "loading 3D scenes from the evaluator outputs ...";
  await Promise.all(RV.ws.states.map(x => sceneOf(x.label).catch(() => null)));
  RV.fn = pickFocusNet(RV.scenes.X0, RV.scenes[CERT]);
  RV.viewer = new V3.Viewer($("#rvCanvas"), {onpick: rvPick});
  RV.steps = (LANG === "zh" ? rvStepsZh() : rvSteps()).filter(st => RV.scenes[st.state]);
  const order = [BASE, CERT, MISS], labels = order.concat(RV.ws.states.map(x => x.label).filter(l => !order.includes(l)));
  $("#rvState").innerHTML = labels.map(l => `<button data-s="${l}" title="${esc((RV.ws.states.find(x => x.label === l) || {}).title || l)}">${l.split("_")[0]}${l.includes("_") ? "*" : ""}</button>`).join("");
  $$("#rvState button").forEach(b => b.onclick = () => rvSetState(b.dataset.s));
  $$(".rv-bar [data-v]").forEach(b => b.onclick = () => RV.viewer.view(b.dataset.v));
  $("#rvExp").oninput = e => RV.viewer.setExplode(+e.target.value);
  $("#rvZs").oninput = e => RV.viewer.setZScale(+e.target.value);
  $("#rvCmp").onchange = e => { RV.compare = e.target.checked; rvSetState(RV.state); };
  const TOG = [["routes", LANG === "zh" ? "全部布线" : "all routing"], ["cells", "cells"], ["macros", "SRAM macros"], ["hbt", "HBT"], ["nets", "focus nets"], ["guides", "ECO guides"], ["paths", "violated paths"],
    ["endpoints", "failing endpoints"], ["drc", "DRC markers"], ["density", "routing density"]];
  $("#rvTog").innerHTML = TOG.map(([k, l]) => `<label><input type="checkbox" data-k="${k}"> ${l}</label>`).join("");
  $$("#rvTog input").forEach(i => i.onchange = () => { RV.viewer.show[i.dataset.k] = i.checked; RV.viewer.draw(); });
  $("#rvLeg").innerHTML = `<div class="lg"><i style="background:#4a5f80"></i>logic cell</div><div class="lg"><i style="background:#8c6bd9"></i>flip-flop</div><div class="lg"><i style="background:#d99e40"></i>buffer</div>
    <div class="lg"><i style="background:#c7cbd6"></i>HBT (green: added)</div><div class="lg"><i style="background:#ff3333"></i>worst path</div><div class="lg"><i style="background:#ffd633"></i>failing endpoint (red = later)</div>
    <div class="lg"><i style="background:#40ff8c"></i>changed by ECO</div><div class="lg"><i style="background:#8c8c99"></i>same nets at X0</div>
    ${V3.DRC_RGB.map((c, i) => `<div class="lg"><i style="background:rgb(${c.map(v => v * 255).join(",")})"></i>DRC: ${esc(RV.scenes.X0.drc_types[i])}</div>`).join("")}`;
  $("#rvLayers").innerHTML = `<div class="row"><button class="sm" onclick="rvLayers(0,10,true)">bottom die on</button><button class="sm" onclick="rvLayers(0,10,false)">off</button>
      <button class="sm" onclick="rvLayers(11,21,true)">upper die on</button><button class="sm" onclick="rvLayers(11,21,false)">off</button></div>
    <div class="lay">${V3.LAYERS.map((l, i) => `<label><input type="checkbox" data-l="${i}" checked><i style="background:rgb(${V3.LAYER_RGB[i].map(v => v * 255).join(",")})"></i>${l}</label>`).join("")}</div>`;
  $$("#rvLayers input").forEach(i => i.onchange = () => RV.viewer.setLayer(+i.dataset.l, i.checked));
  renderSteps(); rvGo(0);
}
function rvLayers(a, b, on) { for (let i = a; i < b; i++) { RV.viewer.layers[i] = on; const el = $(`#rvLayers input[data-l="${i}"]`); if (el) el.checked = on; } RV.viewer.build(); RV.viewer.draw(); }

function renderSteps() {
  $("#rvSteps").innerHTML = RV.steps.map((s, i) => `<div class="st ${i === RV.step ? "on" : ""}" data-i="${i}"><div class="sth">${esc(s.title)}</div>${i === RV.step ? `<div class="stb">${s.body}
     <div class="row">${s.play !== undefined ? `<button class="primary" onclick="RV.viewer.playPath(${s.play}, 7)">Play signal</button>` : ""}${s.focusNet ? `<button onclick="rvFocusNet('${s.focusNet}')">Zoom to ${s.focusNet}</button>` : ""}
     <span class="sp"></span>${i > 0 ? `<button onclick="rvGo(${i - 1})">Back</button>` : ""}${i < RV.steps.length - 1 ? `<button class="primary" onclick="rvGo(${i + 1})">Next</button>` : ""}</div></div>` : ""}</div>`).join("") +
    `<div class="st"><div class="sth">Reproduce</div><div class="stb mu">Every number on this page comes from the evaluator outputs listed under Data. To evaluate another candidate program of this design, open Jobs and submit an ECO evaluation; its 3D view updates while it runs.</div></div>`;
  $$("#rvSteps .st[data-i]").forEach(el => el.querySelector(".sth").onclick = () => rvGo(+el.dataset.i));
}
async function rvGo(i) {
  RV.step = i; const s = RV.steps[i]; renderSteps(); RV.viewer.hl = null;
  Object.assign(RV.viewer.show, s.show); $$("#rvTog input").forEach(el => el.checked = !!RV.viewer.show[el.dataset.k]);
  $("#rvExp").value = s.explode; RV.viewer.explode = s.explode;
  await rvSetState(s.state); RV.viewer.view(s.view);
  if (s.focusNet) rvFocusNet(s.focusNet);
}
async function rvSetState(label) {
  RV.state = label; $$("#rvState button").forEach(b => b.classList.toggle("on", b.dataset.s === label));
  const sc = await sceneOf(label);
  RV.viewer.load(sc, label !== "X0" && RV.compare ? RV.scenes.X0 : null);
  const st = RV.ws.states.find(s => s.label === label) || {}, e = st.eval, m = sc.metrics || {};
  $("#rvHud").innerHTML = `<b>${esc(st.title || label)}</b> &nbsp; setup TNS ${n0(m.finish__timing__setup__tns)} ns &nbsp; WNS ${n0(m.finish__timing__setup__ws)} ns &nbsp;
    violating endpoints ${n0(m.finish__timing__drv__setup_violation_count)} &nbsp; DRC ${n0(sc.drc.length)} &nbsp; HBT ${n0(sc.hbt.length)}
    ${e ? ` &nbsp; Ge ${e.v6_pass ? '<span class="ok">PASS</span>' : '<span class="bad">FAIL</span>'}` : ""}`;
}
function rvFocusNet(name) {
  const sc = RV.scenes[RV.state], n = sc.nets[name]; if (!n) return;
  const d = n.conn[0], u = 0.01; RV.viewer.highlight(name); RV.viewer.focus(d[2] * u, d[3] * u, RV.viewer.tierZ(d[4]));
  rvPick({kind: "net", name, info: `net ${name}: fanout ${n.fanout}, routed ${n.length_um} um`});
}
function rvPick(k) {
  if (!k) { $("#rvSel").innerHTML = '<span class="mu">nothing here</span>'; return; }
  let h = `<div class="kv"><span>type</span><b>${k.kind}</b><span>name</span><span class="mono">${esc(k.name)}</span></div><p>${esc(k.info)}</p>`;
  const sc = RV.scenes[RV.state];
  if (k.kind === "net" && sc.nets[k.name]) {
    const n = sc.nets[k.name], b = RV.scenes.X0.nets[k.name], gd = ((sc.guide_diff || {}).nets || {})[k.name];
    const perLayer = {}; n.wires.forEach(w => perLayer[V3.LAYERS[w[4]]] = (perLayer[V3.LAYERS[w[4]]] || 0) + (Math.abs(w[2] - w[0]) + Math.abs(w[3] - w[1])) / 100);
    h += `<table class="mt"><tr><td>fanout</td><td>${n.fanout}</td></tr><tr><td>driver</td><td class="mono">${esc(n.conn[0][0])}/${esc(n.conn[0][1])}</td></tr>
      <tr><td>routed length</td><td>${n0(n.length_um)} um${b && RV.state !== "X0" ? ` (X0: ${n0(b.length_um)})` : ""}</td></tr>
      ${gd ? `<tr><td>guide boxes added / removed</td><td>${gd.added.length} / ${gd.removed.length}</td></tr>` : ""}</table>
      <div class="mu">length by layer (um)</div><div class="mono">${Object.entries(perLayer).map(([l, v]) => `${l} ${v.toFixed(0)}`).join(" · ")}</div>`;
  }
  if (k.kind === "path") {
    const p = sc.paths[k.idx];
    h += `<button class="primary" onclick="RV.viewer.playPath(${k.idx}, 7)">Play signal</button><table class="mt">${p.pts.map((q, i) => `<tr><td>${i}</td><td>${q[2] ? "upper" : "bottom"} die</td><td>${q[3]} ns</td></tr>`).join("")}</table>`;
  }
  $("#rvSel").innerHTML = h;
}
