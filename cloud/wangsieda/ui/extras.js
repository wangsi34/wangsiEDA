// Language switch, offline (file://) data source, and import of a user's own DEF/LEF into the 3D viewer.
"use strict";
let LANG = localStorage.getItem("wangsi_lang") || "zh";

// ---------------------------------------------------------------- offline data source (snapshot embedded as ui/offline-data.js)
async function offlineGet(p) {
  const D = window.WANGSI_OFFLINE, u = new URL(p, "http://x"), q = Object.fromEntries(u.searchParams);
  if (u.pathname === "/api/scene") {
    const b64 = D.scenes[q.label]; if (!b64) throw new Error("no scene " + q.label);
    const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    return JSON.parse(await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream("gzip"))).text());
  }
  let m = u.pathname.match(/^\/api\/job\/([\w-]+)$/); if (m) return D.jobs[m[1]];
  m = u.pathname.match(/^\/api\/design\/(\w+)$/); if (m) return D.routes["design_" + m[1]];
  if (u.pathname === "/api/config") return D.routes.config || {cases: [], scene_cases: [], blocks: [], features: {}, default_case: (D.routes.workspace || {}).case || null};
  const key = {"/api/overview": "overview", "/api/host": "host", "/api/workspace": "workspace", "/api/evals": "evals", "/api/jobs": "jobs",
    "/api/llm_calls": "llm_calls", "/api/tools": "tools", "/api/skills": "skills"}[u.pathname];
  if (key) return D.routes[key];
  if (u.pathname === "/api/derivations") return [];
  if (u.pathname === "/api/progress") return {};
  throw new Error(LANG === "zh" ? "该数据未包含在本地数据包中" : "not included in the local data package");
}
function offlineStream(id) {
  const es = {close() {}, addEventListener() {}};
  setTimeout(() => (window.WANGSI_OFFLINE.events[id] || []).forEach(e => es.onmessage && es.onmessage({data: JSON.stringify(e)})), 50);
  return es;
}

// ---------------------------------------------------------------- language
const ZH = {"3D 评审": "3D 评审", "作业": "作业"};
const UI_EN = {"3D 评审": "3D Review", "作业": "Jobs", "候选与状态": "Candidates", "评测记录": "Evaluations", "模型调用": "Model calls",
  "工具 / MCP": "Tools / MCP", "经验库": "Library", "主机": "Host", "导入设计": "Import design"};
function applyLang() {
  $$("#nav a").forEach(a => { a.dataset.zh = a.dataset.zh || a.textContent; a.textContent = LANG === "en" ? (UI_EN[a.dataset.zh] || a.dataset.zh) : a.dataset.zh; });
  const b = $("#langBtn"); if (b) b.textContent = LANG === "zh" ? "EN" : "中文";
  const i = $("#importBtn"); if (i) i.textContent = LANG === "zh" ? "导入设计" : "Import design";
  const bs = document.querySelector(".brand small");
  if (bs) { bs.dataset.zh = bs.dataset.zh || bs.textContent; bs.textContent = LANG === "en" ? bs.dataset.zh.replace("F2F 3D 签核 ECO 收敛平台", "F2F 3D signoff ECO closure") : bs.dataset.zh; }
  if (window.chatLabels) chatLabels();
}
function toggleLang() { LANG = LANG === "zh" ? "en" : "zh"; localStorage.setItem("wangsi_lang", LANG); applyLang(); route(); }
window.addEventListener("DOMContentLoaded", () => {
  $("header").insertAdjacentHTML("beforeend", `<button id="importBtn" class="sm" onclick="openImport()"></button><button id="langBtn" class="sm" onclick="toggleLang()"></button>`);
  applyLang();
});

// Chinese walkthrough built from the same scene data as the English one
function rvStepsZh() {
  const en = rvSteps(), S = RV.scenes, x0 = S.X0, m = x => x.metrics || {};
  const lbl = en.map(s => s.state), cert = S[lbl[3]], miss = S[lbl[5]];
  const n1194 = x0.nets[RV.fn] || {}, worst = x0.paths[0], ws0 = worst.worst_stage || [];
  const stdc = x0.cells.filter(c => c[5] !== 5).length, mUp = x0.macros.filter(q => q[4] === 1).length;
  const byDie = n => { const o = {b: 0, u: 0}; (n.wires || []).forEach(w => { const L = (Math.abs(w[2] - w[0]) + Math.abs(w[3] - w[1])) / 100; if (w[4] < 10) o.b += L; else if (w[4] > 10) o.u += L; }); return o; };
  const a1194 = (cert && cert.nets[RV.fn]) || {}, d0 = byDie(n1194), d1 = byDie(a1194), gd = (cert && cert.guide_diff) || {changed_nets: [], nets: {}};
  const st = l => RV.ws.states.find(s => s.label === l) || {};
  const zh = [
    ["1  设计与 3D 叠层", `${designBlurb("zh")}
      <p>底层 die 放置全部 ${n0(stdc)} 个标准单元和 ${x0.macros.length - mUp} 块 SRAM；上层 die 放置 ${mUp} 块 SRAM（灰色实体）。两片 die 之间的 ${n0(x0.hbt.length)} 个铜焊盘是混合键合端子（HBT），是信号跨 die 的唯一通道。勾选“全部布线”可以看到 DEF 中每一根实际走线。</p>
      <table class="mt"><tr><td>芯片尺寸</td><td>${(x0.die[0] / 100).toFixed(0)} x ${(x0.die[1] / 100).toFixed(0)} um</td></tr><tr><td>布线层</td><td>底层 metal1-10，上层 metal11-20</td></tr><tr><td>时钟周期</td><td>${clockNs() ?? "—"} ns</td></tr><tr><td>布局</td><td>固定，ECO 不移动、不改尺寸任何单元</td></tr></table>`],
    ["2  问题：初始状态的建立时间违例", `<p>X0 是修复前的布线结果。每个彩色点是一个寄存器：它的输入数据比时钟要求到得晚（建立时间违例）。黄色表示略晚，红色表示晚了几个纳秒。</p>
      <table class="mt"><tr><td>总违例 TNS</td><td class="bad">${n0(m(x0).finish__timing__setup__tns)} ns</td></tr><tr><td>最差裕量 WNS</td><td class="bad">${n0(m(x0).finish__timing__setup__ws)} ns</td></tr><tr><td>违例端点数</td><td>${n0(m(x0).finish__timing__drv__setup_violation_count)}</td></tr><tr><td>DRC 标记</td><td>${n0(x0.drc.length)}</td></tr></table>`],
    [`3  根因：一个线网驱动 ${n0(n1194.fanout)} 个负载`, `<p>红色带是最差路径（裕量 ${worst.slack} ns）。点击“播放信号”，白点按真实延迟移动，停顿的地方就是延迟所在。</p>
      <p>它停在 <b>${esc(ws0[0] || "")}</b>：这一级就用了 ${ws0[2]} ns${clockNs() ? `，约为时钟周期的 ${(ws0[2] / clockNs()).toFixed(1)} 倍` : ""}。这一级位于 <b>${esc(RV.fn)}</b> 上：一个输入缓冲器通过一棵共享走线树驱动 ${n0(n1194.fanout)} 个负载（${n0(n1194.length_um)} um，黄色点）。树末端的负载必须等整棵树充电完成。</p>
      <p><b>修复思路（HCSI 关键汇点隔离）：</b>只修改全局布线 guide，给少数关键负载从驱动端单独拉一条短分支，不移动任何单元。</p>`],
    ["4  ECO 改了什么", `<p>绿色框是 ECO 新增的全局布线 guide，绿色走线是详细布线器实际走出的线，灰色是同一批线网在 X0 中的走线，绿色焊盘是新增的 HBT。</p>
      <table class="mt"><tr><td>guide 被修改的线网</td><td>${gd.changed_nets.length}</td></tr><tr><td>新增 guide 框</td><td>${n0(Object.values(gd.nets).reduce((a, v) => a + v.added.length, 0))}</td></tr>
      <tr><td>新增 HBT</td><td>${(cert && cert.hbt_new || []).length}</td></tr><tr><td>${esc(RV.fn)} 底层金属走线</td><td>${n0(Math.round(d0.b))} -> ${n0(Math.round(d1.b))} um</td></tr>
      <tr><td>${esc(RV.fn)} 上层金属走线</td><td>${n0(Math.round(d0.u))} -> ${n0(Math.round(d1.u))} um</td></tr><tr><td>移动/改尺寸的单元</td><td>0</td></tr></table>
      <p>新分支经 HBT 爬到上层 die 的厚金属，再回到关键负载附近。厚金属单位长度电阻低得多，绕远一些仍比共享细金属树快，这是 3D 特有的修复方式。</p>`],
    ["5  签核结果", `<p>只有在真实评测器上全部签核检查都通过才算数：建立/保持时间、DRC、Short、线长、HBT 容量和时钟树（延迟与偏斜不得变差）。</p>${gateTable(x0, cert, st(lbl[3]).eval)}`],
    ["6  对照：未通过的方案", `<p>同一流程（构建、评测、回读、签核门）运行的另一个方案。</p>${gateTable(x0, miss, st(lbl[5]).eval)}<p>时序有改善，但至少一项签核检查未通过，因此认证增益为 0。</p>`],
  ];
  const out = en.map((s, i) => zh[i] ? Object.assign({}, s, {title: zh[i][0], body: zh[i][1]}) : s);
  if (en[6]) { const own = st(en[6].state), e = own.eval || {};
    out[6] = Object.assign({}, en[6], {title: `7  闭环结果：${en[6].state.split("_")[0]}`, body: `<p>平台规划 Agent 选择方案并端到端执行：构建、评测、回读、签核门，最后生成本 3D 场景。</p>${gateTable(x0, S[en[6].state], e)}
      <p>V6 签核门 <b class="${e.v6_pass ? "ok" : "bad"}">${e.v6_pass ? "通过" : "未通过"}</b>，认证增益 <b>${n0(e.gain_pct)}%</b>。</p>`}); }
  return out;
}

// ---------------------------------------------------------------- import your own design (DEF + optional LEF / DRC report)
function openImport() {
  drawer(LANG === "zh" ? "导入设计" : "Import design", `<p>${LANG === "zh" ? "选择你的设计文件，在浏览器本地解析并显示 3D，不会上传到任何地方。" : "Pick your design files; they are parsed locally in the browser."}</p>
    <table class="mt"><tr><td>DEF</td><td>${LANG === "zh" ? "必选：布局与布线（如 6_final.def）" : "required: placement and routing (e.g. 6_final.def)"}</td></tr>
    <tr><td>LEF</td><td>${LANG === "zh" ? "可选，可多选：工艺 LEF 决定层顺序，单元 LEF 决定单元尺寸" : "optional, multiple: tech LEF for layer order, cell LEF for sizes"}</td></tr>
    <tr><td>DRC</td><td>${LANG === "zh" ? "可选：OpenROAD 详细布线 DRC 报告" : "optional: OpenROAD detailed-route DRC report"}</td></tr></table>
    <input type="file" id="impFiles" multiple accept=".def,.lef,.rpt,.txt,.gz"><div id="impMsg" class="mu" style="margin-top:8px"></div>`);
  $("#impFiles").onchange = e => importFiles([...e.target.files]);
}
async function readText(f) {
  if (f.name.endsWith(".gz")) return await new Response(f.stream().pipeThrough(new DecompressionStream("gzip"))).text();
  return await f.text();
}
async function importFiles(files) {
  const msg = t => $("#impMsg").textContent = t;
  try {
    let def = "", lefs = [], drc = "";
    for (const f of files) { const t = await readText(f); if (/\bDESIGN\b[\s\S]*\bCOMPONENTS\b/.test(t.slice(0, 2e6))) def = t; else if (/^\s*MACRO\s|^\s*LAYER\s/m.test(t)) lefs.push(t); else drc = t; }
    if (!def) return msg(LANG === "zh" ? "未找到 DEF 文件" : "no DEF file found");
    msg(LANG === "zh" ? "解析中 ..." : "parsing ...");
    const sc = parseDesign(def, lefs, drc);
    RV.scenes[sc.label] = sc;
    if (!location.hash.match(/^#\/?$/)) location.hash = "#/";
    if (!RV.viewer) await review();
    Object.assign(RV.viewer.show, {cells: true, macros: true, routes: true, hbt: true, nets: false, paths: false, endpoints: false, guides: false, drc: !!drc});
    RV.viewer.layerNames = sc.layers; RV.viewer.load(sc, null); RV.viewer.view("iso");
    $("#rvHud").innerHTML = `<b>${esc(sc.label)}</b> &nbsp; ${n0(sc.cells.length)} cells &nbsp; ${sc.macros.length} macros &nbsp; ${n0(sc.routeCount)} wire segments &nbsp; ${sc.layers.length} layers &nbsp; ${sc.hbt.length} HBT &nbsp; DRC ${sc.drc.length}`;
    closeDrawer();
  } catch (e) { msg((LANG === "zh" ? "解析失败：" : "parse failed: ") + e.message); }
}
function parseDesign(def, lefs, drcText) {
  const sizes = {}, layerOrder = [];
  for (const t of lefs) {
    let cur = null;
    for (const line of t.split("\n")) { const w = line.trim().split(/\s+/);
      if (w[0] === "MACRO") cur = w[1]; else if (w[0] === "SIZE" && cur) sizes[cur] = [+w[1], +w[3]];
      else if (w[0] === "END" && w[1] === cur) cur = null;
      else if (w[0] === "LAYER" && !cur && w[1] && !layerOrder.includes(w[1]) && /TYPE\s+ROUTING/.test(t.slice(t.indexOf(line), t.indexOf(line) + 300))) layerOrder.push(w[1]); }
  }
  const dbu = +(def.match(/UNITS DISTANCE MICRONS (\d+)/) || [0, 1000])[1], k = 100 / dbu;  // -> 0.01 um
  const da = (def.match(/DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)/) || []).slice(1).map(Number);
  const sec = n => { const m = def.match(new RegExp("^" + n + "\\s+\\d+\\s*;([\\s\\S]*?)^END " + n, "m")); return m ? m[1] : ""; };
  const cells = [], macros = [], pos = {};
  for (const ent of sec("COMPONENTS").split(";")) {
    const m = ent.match(/-\s+(\S+)\s+(\S+)[\s\S]*?\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)/); if (!m) continue;
    const [, inst, master, x, y, ori] = m; let [w, h] = sizes[master] || sizes[master.replace(/_(bottom|upper)$/, "")] || [0.4, 1.4];
    if (/^(E|W|FE|FW)$/.test(ori)) [w, h] = [h, w];
    const tier = /_upper$|_top$/i.test(master) ? 1 : 0, big = w * h > 50;
    const r = [Math.round(x * k), Math.round(y * k), Math.round(w * 100), Math.round(h * 100), tier];
    pos[inst] = r;
    if (/^(FILLCELL|TAPCELL|FILL)/i.test(master)) continue;
    if (big) macros.push(r.concat([inst])); else cells.push(r.concat([/DFF|LATCH/i.test(master) ? 1 : /BUF|INV/i.test(master) ? 2 : 0]));
  }
  const used = new Set(), segs = {};
  for (const m of sec("NETS").matchAll(/(?:ROUTED|NEW)\s+(\S+)\s+(?:\d+\s+)?((?:\(\s*[-\d*]+\s+[-\d*]+(?:\s+-?\d+)?\s*\)\s*)+)/g)) {
    const pts = [...m[2].matchAll(/\(\s*([-\d*]+)\s+([-\d*]+)/g)]; if (pts.length < 2) continue;
    const x1 = +pts[0][1], y1 = +pts[0][2], x2 = pts[1][1] === "*" ? x1 : +pts[1][1], y2 = pts[1][2] === "*" ? y1 : +pts[1][2];
    used.add(m[1]); (segs[m[1]] = segs[m[1]] || []).push(x1, y1, x2, y2);
  }
  const layers = layerOrder.length ? layerOrder.filter(l => used.has(l)).concat([...used].filter(l => !layerOrder.includes(l)))
    : [...used].sort((a, b) => (+(a.match(/\d+/) || [0])[0]) - (+(b.match(/\d+/) || [0])[0]));
  const toB64 = arr => { const u = new Uint16Array(arr.map(v => Math.max(0, Math.min(65535, Math.round(v * k / 10))))); let s = ""; const b = new Uint8Array(u.buffer);
    for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode.apply(null, b.subarray(i, i + 32768)); return btoa(s); };
  const routeCount = layers.reduce((a, l) => a + (segs[l] || []).length / 4, 0);
  const hbtLayer = layers.findIndex(l => /hbt|bond|hb_/i.test(l));
  const drc = [];
  if (drcText) { let cur = null; for (const line of drcText.split("\n")) { if (line.startsWith("violation type:")) cur = line.split(":")[1].trim();
    const b = line.match(/bbox = \(([\d.]+), ([\d.]+)\) - \(([\d.]+), ([\d.]+)\) on Layer (\S+)/);
    if (b && cur) { let li = layers.indexOf(b[5]); if (li < 0) li = 0; drc.push([+b[1] * 100, +b[2] * 100, +b[3] * 100, +b[4] * 100, li, ["Short", "Metal Spacing", "Cut Spacing", "NS Metal", "Cut Short"].indexOf(cur) < 0 ? 5 : ["Short", "Metal Spacing", "Cut Spacing", "NS Metal", "Cut Short"].indexOf(cur)]); } } }
  const name = (def.match(/^DESIGN\s+(\S+)/m) || [0, "design"])[1];
  return {label: "import:" + name, die: [Math.round((da[2] || 1) * k), Math.round((da[3] || 1) * k)], layers, cells, macros, hbt: [], drc,
    drc_types: ["Short", "Metal Spacing", "Cut Spacing", "NS Metal", "Cut Short", "Other"], paths: [], endpoints: [], nets: {}, metrics: {}, routeCount, hbtLayer,
    routes: {layers: layers.map(l => toB64(segs[l] || []))}, density: {grid: [1, 1], layers: layers.map(() => [0])}};
}

// exact-label translation of static interface text (applied to every rendered node while Chinese is selected)
const ZH_LABELS = {"consistency check": "一致性校验", "z scale": "垂直放大", "signoff review": "签核评审", "show X0 routing for changed nets": "显示被修改线网在 X0 中的走线", "die gap": "die 间距", "Selection": "选中对象", "Legend": "图例", "Layers": "图层", "Next": "下一步", "Back": "上一步", "Play signal": "播放信号", "Click a net driver, path end, HBT or SRAM macro.": "点击线网驱动端、路径终点、HBT 或 SRAM 查看详情。", "nothing here": "此处无对象", "cells": "单元", "SRAM macros": "SRAM", "HBT": "HBT", "focus nets": "关键线网", "ECO guides": "ECO guide", "violated paths": "违例路径", "failing endpoints": "违例端点", "DRC markers": "DRC 标记", "routing density": "布线密度", "all routing": "全部布线", "logic cell": "逻辑单元", "flip-flop": "触发器", "buffer": "缓冲器", "HBT (green: added)": "HBT（绿色为新增）", "worst path": "最差路径", "failing endpoint (red = later)": "违例端点（越红越晚）", "changed by ECO": "ECO 修改", "same nets at X0": "X0 中的同一线网", "bottom die on": "底层 die 开", "upper die on": "上层 die 开", "off": "关", "check": "检查项", "after": "修改后", "change": "变化", "limit": "门限", "ok": "通过", "fail": "未通过", "later": "变晚", "same/earlier": "持平/提前", "setup TNS (ns)": "建立时间 TNS (ns)", "worst setup slack (ns)": "最差建立裕量 (ns)", "hold TNS (ns)": "保持时间 TNS (ns)", "worst hold slack (ns)": "最差保持裕量 (ns)", "DRC total": "DRC 总数", "Short": "短路 Short", "wirelength (um)": "线长 (um)", "Reproduce": "复现", "loading 3D scenes from the evaluator outputs ...": "正在载入 3D 场景 ...", "type": "类型", "name": "名称", "fanout": "扇出", "driver": "驱动端", "routed length": "布线长度", "length by layer (um)": "各层走线长度 (um)", "close": "关闭", "DRC: Short": "DRC：短路", "DRC: Metal Spacing": "DRC：金属间距", "DRC: Cut Spacing": "DRC：通孔间距", "DRC: NS Metal": "DRC：非标准金属", "DRC: Cut Short": "DRC：通孔短路", "DRC: Other": "DRC：其他"};
function translateNode(root) {
  if (LANG !== "zh") return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) { const t = n.nodeValue.trim(); if (ZH_LABELS[t]) n.nodeValue = n.nodeValue.replace(t, ZH_LABELS[t]); }
}
new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(x => translateNode(x.nodeType === 1 ? x : x.parentNode || document.body))))
  .observe(document.documentElement, {childList: true, subtree: true});
window.addEventListener("DOMContentLoaded", () => translateNode(document.body));
