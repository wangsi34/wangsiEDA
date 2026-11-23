// wangsiEDA console (vanilla JS, no build). All data comes from the wangsid API on the compute host.
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const f = (v, d = 2) => v === null || v === undefined || v === "" ? "—" : (typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(d)) : esc(v));
const ago = t => { if (!t) return "—"; const s = Date.now() / 1000 - t; return s < 60 ? Math.round(s) + "s" : s < 3600 ? Math.round(s / 60) + "m" : s < 86400 ? (s / 3600).toFixed(1) + "h" : (s / 86400).toFixed(1) + "d"; };
const dur = (a, b) => a ? ((b || Date.now() / 1000) - a) : null;
const fdur = s => s === null ? "—" : s < 90 ? s.toFixed(0) + "s" : s < 5400 ? (s / 60).toFixed(1) + "m" : (s / 3600).toFixed(2) + "h";
const qs = new URLSearchParams(location.search);
if (qs.get("token")) { sessionStorage.setItem("wtoken", qs.get("token")); history.replaceState(null, "", location.pathname + location.hash); }
const TOKEN = sessionStorage.getItem("wtoken") || "";
async function api(p, opt = {}) {
  if (window.WANGSI_OFFLINE) return offlineGet(p);
  const r = await fetch(p, {...opt, headers: {"X-Wangsi-Token": TOKEN, "Content-Type": "application/json", ...(opt.headers || {})}});
  if (r.status === 401) throw new Error("token missing/invalid — open the console through start.bat");
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body.error || body);
  return body;
}
const post = (p, b) => api(p, {method: "POST", body: JSON.stringify(b || {})});
// 站点能力（设计、模块、已配置的功能）：界面菜单全部由它生成，不写死设计名
let CFG = null;
async function cfg() { if (!CFG) CFG = await api("/api/config"); return CFG; }
const caseOpts = (all) => (all ? '<option value="">all cases</option>' : "") + (CFG.cases || []).map(c => `<option>${esc(c)}</option>`).join("");
const FEAT_NAMES = {eco: ["ECO 评测 / Agentic closure", "ECO evaluation / agentic closure"], scene3d: ["3D 签核评审", "3D signoff review"],
  block_flow: ["模块 RTL→GDS", "Block RTL→GDS"], llm: ["大模型 Agent", "LLM agents"]};

// 首次使用 / 未生成 3D 场景时的引导页：列出已就绪与缺失的功能及补齐方法
async function welcome() {
  await cfg();
  const zh = LANG === "zh", F = CFG.features || {};
  const rows = Object.entries(F).map(([k, v]) => `<tr><td>${v.ok ? '<span class="tag pass">ready</span>' : '<span class="tag">—</span>'}</td>
    <td><b>${esc((FEAT_NAMES[k] || [k, k])[zh ? 0 : 1])}</b></td><td class="mu">${v.ok ? "" : (zh ? "缺少：" : "missing: ") + esc(v.missing.join(", "))}</td></tr>`).join("");
  const next = [];
  if (F.block_flow && F.block_flow.ok) next.push(`<button class="primary" onclick="location.hash='#/jobs';setTimeout(()=>newJob('block_flow'),50)">${zh ? "运行示例模块 RTL→GDS" : "Run the example block RTL→GDS"}</button>`);
  if (F.scene3d && F.scene3d.ok && CFG.default_case) next.push(`<span class="mu">${zh ? "3D 场景尚未生成：在“工具 / MCP”页运行 scene_build，或提交一次 ECO 评测" : "No 3D scene yet: run scene_build from Tools, or submit an ECO evaluation"}</span>`);
  next.push(`<button onclick="openImport()">${zh ? "导入 DEF/LEF 查看版图" : "Import a DEF/LEF to view a layout"}</button>`);
  $("#view").innerHTML = `<h2>${zh ? "开始使用" : "Getting started"} <small>wangsiEDA ${esc(CFG.version || "")}</small></h2>
    <div class="grid g2"><div class="card"><h3>${zh ? "本机已就绪的功能" : "What this install can do"}</h3><table>${rows}</table>
      <p class="mu">${zh ? "在计算主机上运行 <code>bin/wangsictl doctor</code> 查看每一项的补齐方法。" : "Run <code>bin/wangsictl doctor</code> on the compute host for how to fix each item."}</p></div>
    <div class="card"><h3>${zh ? "下一步" : "Next"}</h3><div class="row" style="flex-wrap:wrap;gap:8px">${next.join("")}</div>
      ${CFG.catalog && CFG.catalog.errors && Object.keys(CFG.catalog.errors).length ? `<h3>${zh ? "数据目录" : "Catalog"}</h3><pre class="mu">${esc(JSON.stringify(CFG.catalog.errors, null, 1))}</pre>` : ""}</div></div>`;
}
function drawer(title, html) { $("#dtitle").textContent = title; $("#dbody").innerHTML = html; $("#drawer").classList.add("open"); }
function closeDrawer() { $("#drawer").classList.remove("open"); }
let TIMERS = [], ES = null;
function cleanup() { TIMERS.forEach(clearInterval); TIMERS = []; if (ES) { ES.close(); ES = null; } }
const AGENT_COL = {"eco-planner": "#c4a8ff", "critic": "#ffc56b", "guard": "#ff9b98", "signoff": "#b5f07a", "evaluator": "#7fdbe6", "pnr": "#8fb6ff",
  "physical": "#ff9ad5", "rtl-debug": "#ffc56b", "curator": "#f5df7a", "reflector": "#f5df7a", "supervisor": "#c8d1de", "scheduler": "#e7c66a"};
const av = a => `<span class="av" style="color:${AGENT_COL[a] || "#9cc0ff"}">${esc(a)}</span>`;
const gate = v => v ? `<span class="tag ${v.pass ? "pass" : "fail"}">${v.pass ? "PASS " + f(v.gain_pct) + "%" : "FAIL"}</span>${v.failed && v.failed.length ? `<span class="mu">${esc(v.failed.join(", "))}</span>` : ""}` : "—";
const st = s => `<span class="tag ${s === "done" ? "pass" : s === "failed" ? "fail" : s === "running" ? "run" : s === "queued" || s === "pending" ? "wait" : ""}">${esc(s)}</span>`;

// ------------------------------------------------------------------ router
const ROUTES = [[/^#\/?$/, review], [/^#\/overview$/, overview], [/^#\/designs$/, designs], [/^#\/design\/(\w+)$/, design], [/^#\/evals$/, evals], [/^#\/jobs$/, jobs],
  [/^#\/job\/([\w-]+)$/, job], [/^#\/agents$/, agents], [/^#\/tools$/, tools], [/^#\/skills$/, skills]];
async function route() {
  cleanup(); closeDrawer();
  const h = location.hash || "#/";
  $$("#nav a").forEach(a => a.classList.toggle("on", h.startsWith(a.getAttribute("href")) && (a.getAttribute("href") !== "#/" || h === "#/")));
  for (const [re, fn] of ROUTES) { const m = h.match(re); if (m) { try { await fn(...m.slice(1)); } catch (e) { $("#view").innerHTML = `<div class="note bad">${esc(e.message)}</div>`; } return; } }
  $("#view").innerHTML = "not found";
}
window.addEventListener("hashchange", route);

async function hostPill() {
  try {
    const h = await api("/api/host"); const ev = h.pools.evaluator;
    $("#host").className = "pill ok";
    $("#host").textContent = `load ${h.load[0]}/${h.ncpu} · free ${h.free_home_GiB} GiB · mem ${h.mem_available_GiB == null ? "—" : h.mem_available_GiB + " GiB"} · evaluator ${ev.own_running}/${ev.own_cap} own, ${ev.host_running == null ? "—" : ev.host_running + "/" + ev.host_cap} host`;
  } catch (e) { $("#host").className = "pill bad"; $("#host").textContent = e.message; }
}

// ------------------------------------------------------------------ overview
async function overview() {
  const o = await api("/api/overview"); const c = o.counts;
  const kp = (l, v, i) => `<div class="card kpi"><span>${l}</span><b>${v}</b><i>${i || ""}</i></div>`;
  let h = `<h2>总览 <small>真实数据：F2F 3D signoff 流程（Open3DBench, Nangate45）上的全部评测、签核状态和 Agent 调用；作业在计算主机上执行</small></h2>
  <div class="grid g6">${kp("评测记录 (evaluations)", f(c.evals), "replica + judged, 来自项目数据湖")}${kp("签核状态 (signed-off states)", c.states, "X0 与各方法的签核状态")}
  ${kp("候选 ECO 程序", f(c.candidates), `${o.designs.length} designs`)}${kp("LLM 调用", c.llm_calls, "研究账本 + wangsiEDA")}${kp("工具 (MCP)", o.tools, "typed, side-effect classed")}${kp("经验资产", c.skills, "lessons / skills")}</div>
  <h3>设计 <small>点击进入签核全景</small></h3><div class="grid g4">`;
  for (const d of o.designs) {
    const b = d.best_v6;
    h += `<div class="card click design" onclick="location.hash='#/design/${d.case}'"><h4>${d.case} <span class="tag">${esc(d.role)}</span></h4>
      <div class="kv"><span>X0 setup TNS / WNS</span><span class="mono">${f(d.x0_tns)} ns / ${f(d.x0_wns, 3)}</span><span>X0 DRC / Short</span><span class="mono">${f(d.x0_drc)} / ${f(d.x0_short)}</span>
      <span>X0 hold WNS</span><span class="mono">${f(d.x0_hold_wns, 4)}</span><span>评测 / judged / Ge 通过</span><span class="mono">${f(d.evals.n)} / ${f(d.evals.judged || 0)} / ${f(d.evals.v6p || 0)}</span>
      <span>最佳 Ge 认证</span><span>${b ? `<b class="ok">${f(b.gain_pct)}%</b> <span class="mono mu">${esc(b.tag)}</span>` : '<span class="mu">无</span>'}</span>
      <span>候选池</span><span>${d.candidates}</span><span>评测器单次耗时</span><span>${fdur(d.evaluator_runtime_s)}</span></div></div>`;
  }
  h += `</div><div class="grid g2"><div><h3>计算主机与许可证池 <small>evaluator 池统计计算主机上全部评测器进程</small></h3><div class="card" id="hostcard"></div></div>
  <div><h3>最近作业</h3><div class="tbl"><table><tr><th>job</th><th>kind</th><th>对象</th><th>状态</th><th>耗时</th></tr>
  ${o.jobs.filter(j => j.kind !== "ask").map(j => `<tr class="click" onclick="location.hash='#/job/${j.id}'"><td class="mono">${j.id}</td><td>${j.kind}</td><td>${esc(j.case_name)}</td><td>${st(j.status)}</td><td>${fdur(dur(j.started, j.ended))}</td></tr>`).join("")}</table></div></div></div>
  <div class="note">数据湖刷新：${o.catalog ? new Date(o.catalog.at * 1000).toLocaleString() : "—"} · 扫描 ${o.catalog?.files_seen ?? "—"} 个 RESULT/REJUDGE 文件</div>`;
  $("#view").innerHTML = h;
  const hc = o.host; $("#hostcard").innerHTML = `<div class="kv"><span>load (1/5/15)</span><span>${hc.load.join(" / ")} of ${hc.ncpu} CPU</span><span>disk</span><span>${hc.free_home_GiB} GiB free (${hc.disk_used_pct}% used)</span>
    <span>memory available</span><span>${hc.mem_available_GiB == null ? "—" : hc.mem_available_GiB + " GiB"}</span></div><table style="margin-top:8px"><tr><th>pool</th><th>wangsi running / cap</th><th>host-wide</th></tr>
    ${Object.entries(hc.pools).map(([k, p]) => `<tr><td>${k}</td><td>${p.own_running} / ${p.own_cap}</td><td>${p.host_running != null ? p.host_running + " / " + p.host_cap : "—"}</td></tr>`).join("")}</table>`;
}

async function designs() { const o = await api("/api/overview"); const c = o.designs[0]?.case; if (c) location.hash = "#/design/" + c; else welcome(); }

// ------------------------------------------------------------------ design page
async function design(cs) {
  const d = await api("/api/design/" + cs);
  const x0 = d.x0, v6 = d.v6_bounds || {};
  let h = `<div class="row"><h2>${cs} <small>${esc(d.role)} · readback: ${esc(d.readback || "not wired")} · evaluator timeout ${fdur(d.evaluator_timeout_s)}</small></h2><span class="sp"></span>
    <button class="primary" onclick="newClosure('${cs}')">Agentic ECO closure</button></div>
  <div class="grid g6">${[["X0 setup TNS (ns)", x0.official_tns_ns], ["X0 setup WNS (ns)", x0.official_wns_ns], ["X0 hold WNS (ns)", x0.worst_hold_slack_ns], ["X0 DRC", x0.drc_total], ["X0 Short", x0.Short], ["X0 wirelength (µm)", x0.detailed_wirelength_um]]
    .map(([l, v]) => `<div class="card kpi"><span>${l}</span><b class="mono">${f(v, 3)}</b></div>`).join("")}</div>
  <div class="grid g2"><div><h3>签核全景 <small>x: 相对 X0 的 setup TNS 改善 (%), y: Short；虚线 = Ge 的 Short 上界；实心 = 通过 Ge</small></h3><div class="card"><svg id="scatter" viewBox="0 0 640 360"></svg>
    <div class="legend" id="slegend"></div></div></div>
    <div><h3>签核门 Ge（包络门，冻结） <small>结果只有在全部门通过时才计入</small></h3><div class="card"><div class="kv">
    ${Object.entries(v6).filter(([k, v]) => typeof v !== "object").map(([k, v]) => `<span>${esc(k)}</span><span class="mono">${esc(v)}</span>`).join("")}
    <span>clock_delta_allow_ps</span><span class="mono">${esc(JSON.stringify(v6.clock_delta_allow_ps || {}))}</span></div></div></div></div>
  <h3>标准签核状态 <small>X0 与各方法的签核状态</small></h3><div class="tbl"><table><tr><th>method</th><th>variant</th><th>candidate</th><th>TNS</th><th>WNS</th><th>hold TNS</th><th>hold WNS</th><th>DRC</th><th>Short</th><th>WL µm</th><th>power W</th><th>HBT excess</th></tr>
    ${d.states.map(s => `<tr><td><b>${esc(s.method)}</b></td><td>${esc(s.variant)}</td><td class="mono">${esc(s.candidate)}</td><td class="num">${f(s.official_tns_ns)}</td><td class="num">${f(s.official_wns_ns, 3)}</td>
      <td class="num">${f(s.hold_tns_ns, 3)}</td><td class="num">${f(s.worst_hold_slack_ns, 4)}</td><td class="num">${f(s.drc_total)}</td><td class="num">${f(s.Short)}</td><td class="num">${f(s.detailed_wirelength_um, 0)}</td><td class="num">${f(s.power_total_W, 4)}</td><td class="num">${f(s.hbt_excess)}</td></tr>`).join("")}</table></div>
  <h3>候选 ECO 程序 <small>HCSI 汇点隔离 + relief + hold pad；预测来自记录的预测器；touched = 项目内已出现过（不重放）</small></h3>
  <div class="tbl"><table><tr><th>rank</th><th>tag</th><th>edits</th><th>relief</th><th>holdpad</th><th>exp. gain ns</th><th>P(gate)</th><th>pred WNS</th><th>pred hold TNS</th><th>new HBT</th><th>状态</th><th></th></tr>
    ${d.candidates.map(c => `<tr class="click" onclick='drawer("${c.tag}", "<pre>"+esc(${JSON.stringify(JSON.stringify(c.edits))})+"</pre>")'><td>${c.c3_rank}</td><td class="mono">${c.tag}</td><td>${c.n_edits}</td><td>${esc(c.relief)}</td><td>${esc(c.holdpad)}</td>
      <td class="num">${f(c.expected_gain_ns, 1)}</td><td class="num">${f(c.p_gate, 3)}</td><td class="num">${f(c.pred_wns, 3)}</td><td class="num">${f(c.pred_hold_tns, 3)}</td><td class="num">${f(c.new_hbt)}</td>
      <td>${c.measured ? `<span class="tag">touched ×${c.measured}</span>` : '<span class="tag pass">untouched</span>'}</td>
      <td>${c.measured ? "" : `<button class="sm" onclick="event.stopPropagation();evalOne('${cs}','${c.tag}')">评测</button>`}</td></tr>`).join("")}</table></div>
  <h3>评测历史 <small>${d.evals.length} 条，按 TNS 排序</small></h3>${evalTable(d.evals)}`;
  $("#view").innerHTML = h;
  scatter(d);
}
function evalTable(rows, withCase) {
  return `<div class="tbl"><table><tr>${withCase ? "<th>case</th>" : ""}<th>tag</th><th>family</th><th>kind</th><th>status</th><th>TNS</th><th>WNS</th><th>DRC</th><th>Short</th><th>hold WNS</th><th>Ge</th><th>Gs</th><th>wall</th><th>origin</th></tr>
  ${rows.map(r => { const vf = r.v6_fails ? (typeof r.v6_fails === "string" ? JSON.parse(r.v6_fails) : r.v6_fails) : null;
    return `<tr class="click" onclick="showEval(${r.id})">${withCase ? `<td>${esc(r.case_name)}</td>` : ""}<td class="mono">${esc(String(r.tag).slice(0, 34))}</td><td>${esc(r.family)}</td><td>${esc(r.kind)}</td><td>${esc(String(r.status).slice(0, 26))}</td>
    <td class="num">${f(r.tns)}</td><td class="num">${f(r.wns, 3)}</td><td class="num">${f(r.drc)}</td><td class="num">${f(r.short)}</td><td class="num">${f(r.hold_wns, 4)}</td>
    <td>${r.v6_pass === null ? '<span class="mu">—</span>' : r.v6_pass ? '<span class="tag pass">PASS</span>' : `<span class="tag fail" title="${esc((vf || []).join(", "))}">FAIL</span>`}</td>
    <td>${r.strict_pass === null ? '<span class="mu">—</span>' : r.strict_pass ? '<span class="tag pass">PASS</span>' : '<span class="tag fail">FAIL</span>'}</td><td>${fdur(r.wall_s ?? null)}</td><td>${esc(r.origin)}</td></tr>`; }).join("")}</table></div>`;
}
async function showEval(id) {
  const r = await api("/api/eval/" + id); const raw = JSON.parse(r.raw_json || "{}");
  drawer(`${r.case_name} · ${r.tag}`, `<div class="kv"><span>provenance</span><span class="mono">${esc(r.source)}</span><span>family / arm</span><span>${esc(r.family)} / ${esc(r.arm)}</span>
    <span>V6 fails</span><span>${esc(r.v6_fails)}</span><span>strict fails</span><span>${esc(r.strict_fails)}</span></div><h3>原始记录</h3><pre>${esc(JSON.stringify(raw, null, 1))}</pre>`);
}
function scatter(d) {
  const W = 640, H = 360, P = 46, x0 = d.x0.official_tns_ns;
  const pts = [];
  d.states.forEach(s => s.official_tns_ns != null && s.Short != null && pts.push({x: 100 * (1 - s.official_tns_ns / x0), y: s.Short, k: s.method, pass: null, lab: s.method + " " + s.variant}));
  d.evals.forEach(e => e.tns != null && e.short != null && pts.push({x: 100 * (1 - e.tns / x0), y: e.short, k: e.origin === "wangsiEDA" ? "wangsiEDA" : e.kind === "judged" ? "judged" : "replica", pass: e.v6_pass, lab: e.tag, id: e.id}));
  if (!pts.length) return;
  const xs = pts.map(p => p.x), ys = pts.map(p => p.y); const sb = (d.v6_bounds || {}).drc_Short;
  let xl = Math.min(...xs, 0), xh = Math.max(...xs, 1), yl = Math.min(...ys), yh = Math.max(...ys, sb || 0);
  const px = (xh - xl) * .06, py = (yh - yl) * .08 || 1; xl -= px; xh += px; yl -= py; yh += py;
  const X = v => P + (W - P - 12) * (v - xl) / (xh - xl), Y = v => H - 30 - (H - 44) * (v - yl) / (yh - yl);
  const COL = {X0: "#ffffff"}, PAL = ["#f0524f", "#e8a93a", "#36c5d6", "#a77bff", "#ff9ad5", "#b5f07a"], METHODS = [];   // one color per method in this design
  d.states.forEach(s => { if (!(s.method in COL)) { COL[s.method] = PAL[METHODS.length % PAL.length]; METHODS.push(s.method); } });
  Object.assign(COL, {judged: "#4c8dff", replica: "#56627a", wangsiEDA: "#2fbf71"});
  const present = new Set(pts.map(p => p.k));
  let s = `<line x1="${P}" y1="${H - 30}" x2="${W - 10}" y2="${H - 30}" stroke="#243041"/><line x1="${X(0)}" y1="10" x2="${X(0)}" y2="${H - 30}" stroke="#3a4659" stroke-dasharray="3 3"/>`;
  if (sb) s += `<line x1="${P}" x2="${W - 10}" y1="${Y(sb)}" y2="${Y(sb)}" stroke="#f0524f" stroke-dasharray="5 4"/><text x="${W - 12}" y="${Y(sb) - 4}" text-anchor="end" fill="#f0524f" font-size="10">Ge Short bound ${sb}</text>`;
  for (let i = 0; i <= 4; i++) { const xv = xl + (xh - xl) * i / 4, yv = yl + (yh - yl) * i / 4;
    s += `<text x="${X(xv)}" y="${H - 14}" fill="#7f8ea3" font-size="10" text-anchor="middle">${xv.toFixed(1)}%</text><text x="${P - 5}" y="${Y(yv) + 3}" fill="#7f8ea3" font-size="10" text-anchor="end">${Math.round(yv)}</text>`; }
  pts.sort((a, b) => (a.k === "replica") - (b.k === "replica")).reverse();
  for (const p of pts) { const c = COL[p.k] || "#888", r = p.k === "X0" || METHODS.includes(p.k) ? 5.5 : 4;
    s += `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${r}" fill="${p.pass === 1 || p.pass === null && p.k !== "replica" ? c : "none"}" stroke="${c}" stroke-width="1.6" ${p.id ? `onclick="showEval(${p.id})" style="cursor:pointer"` : ""}><title>${esc(p.lab)}: ${p.x.toFixed(2)}%, Short ${p.y}</title></circle>`; }
  s += `<text x="${(W + P) / 2}" y="${H - 1}" fill="#7f8ea3" font-size="10" text-anchor="middle">setup TNS improvement vs X0 (%) →</text>`;
  $("#scatter").innerHTML = s;
  $("#slegend").innerHTML = Object.entries(COL).filter(([k]) => present.has(k)).map(([k, c]) => `<span><i class="sw" style="background:${c}"></i>${k}</span>`).join("") + '<span>空心 = 未判定或未通过 Ge</span>';
}
async function evalOne(cs, tag) {
  if (!confirm(`在真实签核流程上评测 ${cs}/${tag}？\n构建 → 签核评测器（约 1–8 小时）→ 回读 → 签核门`)) return;
  const r = await post("/api/jobs", {kind: "eco_eval", params: {case: cs, tag}, title: `${cs} ${tag} evaluation`}); location.hash = "#/job/" + r.job_id;
}
function newClosure(cs) {
  drawer("Agentic ECO closure · " + cs, `<div class="note">Planner（大模型，ReAct 调用只读工具）→ Critic（候选池 / 不重放 / 冻结构建器可构建 / 预算）→ 真实评测流水线（并行）→ 冻结门判定 → Reflection 写入经验资产。</div>
    <div class="row">预算（真实评测次数） <select id="nb"><option>1</option><option>2</option><option>3</option></select>
    <label><input type="checkbox" id="ndry"> 只规划不评测 (dry run)</label><button class="primary" onclick="submitClosure('${cs}')">提交</button></div>`);
}
async function submitClosure(cs) {
  const r = await post("/api/jobs", {kind: "eco_closure", params: {case: cs, budget: +$("#nb").value, dry_run: $("#ndry").checked}, title: `${cs} ECO closure`});
  location.hash = "#/job/" + r.job_id;
}

// ------------------------------------------------------------------ evals explorer
async function evals() {
  await cfg();
  $("#view").innerHTML = `<h2>评测数据湖 <small>项目里全部 RESULT / REJUDGE（只读索引）+ wangsiEDA 自己的评测</small></h2>
   <div class="row"><select id="fc">${caseOpts(true)}</select>
   <select id="fk"><option value="">all kinds</option><option>judged</option><option>replica</option><option>measured</option></select>
   <select id="fo"><option value="">all origins</option><option value="research">research</option><option value="wangsiEDA">wangsiEDA</option></select>
   <label><input type="checkbox" id="fv"> 仅通过 Ge</label><span id="fn" class="mu"></span></div><div id="et" style="margin-top:8px"></div>`;
  const load = async () => { const p = new URLSearchParams({case_name: $("#fc").value, kind: $("#fk").value, origin: $("#fo").value, v6: $("#fv").checked ? "1" : ""});
    const rows = await api("/api/evals?" + p); $("#fn").textContent = rows.length + " rows"; $("#et").innerHTML = evalTable(rows, true); };
  ["#fc", "#fk", "#fo", "#fv"].forEach(s => $(s).onchange = load); load();
}

// ------------------------------------------------------------------ jobs
async function jobs() {
  await cfg();
  const rows = await api("/api/jobs"), F = CFG.features || {};
  $("#view").innerHTML = `<div class="row"><h2>作业 <small>持久化 DAG 作业；断开 SSH / 关闭浏览器不影响执行</small></h2><span class="sp"></span>
    ${F.eco && F.eco.ok ? `<button onclick="newJob('eco_eval')">ECO 单程序评测</button><button onclick="newClosure(CFG.default_case || CFG.cases[0])">Agentic ECO closure</button>` : ""}${F.block_flow && F.block_flow.ok ? `<button class="primary" onclick="newJob('block_flow')">Block RTL→GDS</button>` : ""}</div>
    <div class="tbl" style="max-height:none"><table><tr><th>job</th><th>kind</th><th>title</th><th>对象</th><th>状态</th><th>创建</th><th>耗时</th></tr>
    ${rows.filter(j => j.kind !== "ask").map(j => `<tr class="click" onclick="location.hash='#/job/${j.id}'"><td class="mono">${j.id}</td><td>${j.kind}</td><td>${esc(j.title)}</td><td>${esc(j.case_name)}</td><td>${st(j.status)}</td><td>${ago(j.created)} ago</td><td>${fdur(dur(j.started, j.ended))}</td></tr>`).join("")}</table></div>`;
}
function newJob(kind) {
  if (kind === "block_flow") drawer("Block RTL→GDS", `<div class="note">Verilator lint → Icarus 仿真（冻结 testbench）→ RTL Debug Agent → Yosys → OpenROAD floorplan/place/CTS/route → RCX signoff → Physical Agent 诊断并从最早受影响阶段续跑 → 资产回流。时钟周期取自模块的 CONTRACT.json，是冻结契约。</div>
    <div class="row">模块 <select id="bb">${CFG.blocks.map(b => `<option>${esc(b)}</option>`).join("")}</select> 策略 <select id="bp"><option value="llm">LLM + skills</option><option value="rule">rule + skills（不调用 LLM）</option></select><button class="primary" onclick="submitBlock()">提交</button></div>`);
  else drawer("ECO 单程序评测", `<div class="row"><select id="ec">${caseOpts(false)}</select><input id="et2" placeholder="program tag"><button class="primary" onclick="evalOne($('#ec').value, $('#et2').value)">提交</button></div>`);
}
async function submitBlock() { const b = $("#bb").value; const r = await post("/api/jobs", {kind: "block_flow", params: {block: b, policy: $("#bp").value}, title: `${b} RTL→GDS (${$("#bp").value})`}); location.hash = "#/job/" + r.job_id; }

// ------------------------------------------------------------------ job page
const LANES = {eco_plan: 0, eco_critic: 0, eco_learn: 0, rtl_debug: 0, pnr_diagnose: 0, program_validate: 1, rtl_lint: 1, rtl_sim: 1, eco_build: 2, synth: 2, pnr_stage: 2,
  eval_replica: 3, sta_readback: 3, eval_cleanup: 3, signoff_judge: 4, ingest_result: 4};
const LANE_N = ["Agent / LLM", "Verify", "Build / P&R", "Evaluator", "Signoff / asset"];
let J = null, SEL = null;
async function job(id) {
  EVS = []; LAYDATA = null; TAB = "tl"; SEL = null;
  $("#view").innerHTML = `<div class="row"><h2 id="jt"></h2><span class="sp"></span><span id="jst"></span><button onclick="cancelJob('${id}')">取消作业</button></div>
  <div class="job"><div><div class="card" style="padding:6px"><svg id="dag"></svg></div><div class="legend" style="margin-top:6px">${LANE_N.map((l, i) => `<span>${i + 1}. ${l}</span>`).join("")}</div>
    <div class="card" id="jsum" style="margin-top:8px"></div></div>
  <div><div class="tabs" id="jtabs"><button data-t="tl" class="on">过程</button><button data-t="3d">3D</button><button data-t="ev">评测器</button><button data-t="lay">版图</button><button data-t="llm">LLM</button><button data-t="state">状态</button><button data-t="files">文件</button></div>
    <div id="pane"></div></div><div class="insp"><h3>Inspector</h3><div id="insp" class="mu">点击事件或 DAG 节点</div></div></div>`;
  $$("#jtabs button").forEach(b => b.onclick = () => { $$("#jtabs button").forEach(x => x.classList.toggle("on", x === b)); pane(b.dataset.t); });
  await refreshJob(id); pane("tl");
  TIMERS.push(setInterval(() => refreshJob(id), 4000));
  ES = window.WANGSI_OFFLINE ? offlineStream(id) : new EventSource(`/api/stream?job=${id}&token=${encodeURIComponent(TOKEN)}`);
  ES.onmessage = m => { const e = JSON.parse(m.data); e.p = JSON.parse(e.payload_json || "{}"); EVS.push(e); if (TAB === "tl") addEv(e); };
}
let EVS = [], TAB = "tl";
async function refreshJob(id) {
  J = await api("/api/job/" + id);
  $("#jt").innerHTML = `${esc(J.title)} <small class="mono">${J.id} · ${J.kind}</small>`; $("#jst").innerHTML = st(J.status) + ` <span class="mu">${fdur(dur(J.started, J.ended))}</span>`;
  drawDag(); summary();
  if (TAB === "ev") pane("ev", true); if (TAB === "lay" && !LAYDATA && J.state.layout) pane("lay"); if (TAB === "3d") job3d(true);
}
function drawDag() {
  const S = J.steps, W = 320, rowH = 30, top = 22, lw = W / 5;
  const pos = {}; S.forEach((s, i) => pos[s.id] = [lw * (LANES[s.tool] ?? 2) + lw / 2, top + i * rowH + 12]);
  let h = LANE_N.map((l, i) => `<text x="${lw * i + lw / 2}" y="12" text-anchor="middle" fill="#7f8ea3" font-size="9">${l}</text>`).join("");
  S.forEach((s, i) => { let src = s.deps.length ? S.filter(x => s.deps.includes(x.name)).slice(-1)[0] : null;
    if (!src && i) { const pre = s.name.includes(".") && J.kind === "eco_closure" ? s.name.split(".")[0] + "." : null;
      src = pre ? S.slice(0, i).filter(x => x.name.startsWith(pre)).slice(-1)[0] || S.find(x => x.name === "critic") : S[i - 1]; }
    if (src) { const [x1, y1] = pos[src.id], [x2, y2] = pos[s.id]; h += `<path class="edge" d="M${x1},${y1 + 9} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2 - 9}"/>`; } });
  S.forEach(s => { const [x, y] = pos[s.id], w = lw - 6;
    h += `<g class="node ${s.status} ${SEL === s.id ? "sel" : ""}" onclick="selStep('${s.id}')" style="cursor:pointer"><rect x="${x - w / 2}" y="${y - 10}" width="${w}" height="20" rx="5"/>
    <text x="${x}" y="${y + 1}" text-anchor="middle">${esc(s.name.length > 13 ? s.name.slice(0, 12) + "…" : s.name)}</text><text class="t2" x="${x}" y="${y + 9}" text-anchor="middle">${s.status === "running" ? fdur(dur(s.started)) : s.started ? fdur(dur(s.started, s.ended)) : ""}</text></g>`; });
  $("#dag").setAttribute("viewBox", `0 0 ${W} ${top + S.length * rowH + 10}`); $("#dag").innerHTML = h;
}
function summary() {
  const s = J.state; let h = `<div class="kv"><span>params</span><span class="mono">${esc(JSON.stringify(J.params))}</span><span>steps</span><span>${J.steps.length}（${J.steps.filter(x => x.status === "done").length} done）</span>
    <span>LLM</span><span>${J.llm.length} calls · ${J.llm.reduce((a, c) => a + (c.prompt_tokens || 0) + (c.completion_tokens || 0), 0)} tokens</span></div>`;
  if (s.plan) h += `<h3>计划</h3>${(s.plan.programs || []).map(p => `<div><b class="mono">${esc(p.tag)}</b> 预测 +${f(p.predicted_tns_gain_pct)}% · P(pass) ${f(p.predicted_gate_pass)}</div>`).join("")}<div class="mu">${esc(s.plan.hypothesis || "")}</div>`;
  if (s.evals) h += `<h3>评测</h3>${Object.values(s.evals).map(e => `<div><b class="mono">${esc(e.tag)}</b> ${esc(e.status || "running")}${e.judged ? "<br>" + gate(e.judged.v6) : ""}</div>`).join("")}`;
  if (s.history) h += `<h3>P&R 迭代</h3><table><tr><th>iter</th><th>result</th><th>WNS</th><th>CTS est</th></tr>${s.history.map(x => `<tr><td>${x.iter}</td><td class="${x.result === "PASS" ? "ok" : "bad"}">${esc(x.result)}</td><td>${f(x.wns, 4)}</td><td>${f(x.cts_wns, 4)}</td></tr>`).join("")}</table>`;
  if (s.failed) h += `<div class="note bad">${esc(s.failed)}</div>`;
  if (s.note) h += `<div class="note">${esc(s.note)}</div>`;
  $("#jsum").innerHTML = h;
}
async function selStep(sid) {
  SEL = sid; drawDag(); const s = J.steps.find(x => x.id === sid);
  $("#insp").innerHTML = `<div class="kv"><span>step</span><b>${esc(s.name)}</b><span>tool</span><span class="mono">${esc(s.tool)}</span><span>pool</span><span>${esc(s.pool)}</span><span>status</span><span>${st(s.status)} attempt ${s.attempt}/${s.max_attempts}</span>
   <span>pid</span><span>${s.pid || "—"}</span><span>dir</span><span class="mono">${esc(s.dir)}</span></div>${s.error ? `<pre class="bad">${esc(s.error)}</pre>` : ""}
   ${s.status === "failed" || s.status === "skipped" ? `<button class="primary" onclick="retryStep('${s.id}')">重试此步骤（同参数）</button>` : ""}<h3>args</h3><pre>${esc(JSON.stringify(s.args, null, 1))}</pre>
   <h3>outputs</h3><pre>${esc(JSON.stringify(s.outputs, null, 1))}</pre><h3>files</h3>${s.files.map(fn => `<button class="sm" onclick="showFile('${esc(s.dir)}/${fn}')">${fn}</button>`).join(" ")}`;
}
async function showFile(p) { const t = await api("/api/file?path=" + encodeURIComponent(p)); drawer(p.split("/").slice(-2).join("/"), `<pre>${esc(typeof t === "string" ? t : JSON.stringify(t, null, 1))}</pre>`); }
async function retryStep(sid) { if (confirm("重试步骤 " + sid + "？")) { await post(`/api/step/${encodeURIComponent(sid)}/retry`); refreshJob(J.id); } }
// Live 3D view of a running ECO job (designs with a configured X0 scene): X0 -> guide boxes the ECO adds -> final state vs X0, with per-die detailed-route progress.
let J3 = {key: null};
async function job3d(refresh) {
  const P = $("#pane"), cs = J.case_name;
  await cfg();
  if (!(CFG.scene_cases || []).includes(cs)) { P.innerHTML = `<div class="mu">3D view needs an X0 scene for ${esc(cs || "this job")} (x0_runs / scene_cases in wangsi_config.json).</div>`; return; }
  if (!refresh || !$("#jcv")) {
    P.innerHTML = `<div style="position:relative;height:calc(100vh - 170px)"><canvas id="jcv" style="position:absolute;inset:0;width:100%;height:100%;border:none"></canvas>
      <div class="rv-hud" id="jhud"></div><div class="rv-toggles" id="jtog"></div></div>`;
    J3.viewer = new V3.Viewer($("#jcv"), {onpick: k => { $("#insp").innerHTML = k ? `<b>${esc(k.kind)}</b> <span class="mono">${esc(k.name)}</span><p>${esc(k.info)}</p>` : ""; }});
    Object.assign(J3.viewer.show, {cells: true, macros: false, hbt: true, nets: true, guides: true, paths: false, endpoints: false});
    $("#jtog").innerHTML = ["cells", "macros", "hbt", "nets", "guides", "paths", "endpoints", "drc"]
      .map(k => `<label><input type="checkbox" data-k="${k}" ${J3.viewer.show[k] ? "checked" : ""}> ${k}</label>`).join("");
    $$("#jtog input").forEach(i => i.onchange = () => { J3.viewer.show[i.dataset.k] = i.checked; J3.viewer.draw(); });
    J3.key = null;
  }
  const by = t => J.steps.filter(s => s.tool === t);
  const sceneStep = by("scene_build").find(s => s.status === "done"), gdStep = by("guide_diff").find(s => s.status === "done");
  const key = sceneStep ? "scene:" + sceneStep.args.label : gdStep ? "gd:" + gdStep.id : "x0";
  const base = await sceneOf("X0");
  if (key !== J3.key) {
    J3.key = key;
    if (sceneStep) J3.viewer.load(await sceneOf(sceneStep.args.label), base);
    else if (gdStep) {
      const g = await api("/api/file?path=" + encodeURIComponent(gdStep.outputs.file) + "&bytes=80000000");
      J3.viewer.load(Object.assign({}, base, {guide_diff: g}), null);
    } else J3.viewer.load(base, null);
    const tags = (J.state.approved || []).map(a => a.tag).concat(J.params.tag ? [J.params.tag] : []);
    for (const t of tags) {
      try {
        const ev = await post("/api/tools/program_evidence", {case: cs, tag: t});
        const nets = ((ev.candidate || {}).program || {}).hcsi || [];
        const hit = nets.map(h => h.net).find(n => J3.viewer.S.nets[n]);
        if (hit) { J3.viewer.highlight(hit); break; }
      } catch (e) { /* evidence lookup is optional for the view */ }
    }
    J3.viewer.explode = 40; J3.viewer.build(); J3.viewer.view("iso");
  }
  const run = J.steps.find(s => s.status === "running"), ev = by("eval_replica")[0];
  let live = run ? `running: <b>${esc(run.name)}</b> (${esc(run.tool)}) ${fdur(dur(run.started))}` : `job ${esc(J.status)}`;
  if (ev && ev.status === "running") {
    try {
      const pr = await api("/api/progress?run_id=" + encodeURIComponent(ev.args.run_id)), t = pr.tiers || {};
      live += ` &nbsp; evaluator: ${esc(pr.stage)}${t.bottom ? ` &nbsp; bottom-die DRT violations ${t.bottom.join(" > ")}` : ""}${t.upper ? ` &nbsp; upper-die ${t.upper.join(" > ")}` : ""}`;
    } catch (e) { /* progress is best effort while the evaluator starts */ }
  }
  const view = key.startsWith("scene") ? "final state of this job vs X0 (grey)" : key.startsWith("gd") ? "X0 with the guide boxes this ECO adds (green)" : "X0 (ECO not built yet)";
  const jd = J.steps.find(s => s.tool === "signoff_judge" && s.status === "done");
  $("#jhud").innerHTML = `${view} &nbsp;|&nbsp; ${live}${jd ? ` &nbsp;|&nbsp; Ge ${jd.outputs.v6 && jd.outputs.v6.pass ? '<span class="ok">PASS</span>' : '<span class="bad">FAIL ' + esc(((jd.outputs.v6 || {}).failed || []).join(", ")) + "</span>"}` : ""}`;
}

async function cancelJob(id) { if (confirm("取消作业 " + id + "？（会终止它正在运行的步骤）")) { await post(`/api/job/${id}/cancel`); refreshJob(id); } }

function evHtml(e) {
  const p = e.p, t = `<span class="t">${new Date(e.ts * 1000).toLocaleTimeString()}</span>`;
  switch (e.type) {
    case "thought": return `<div class="ev think"><div class="hd">${av(e.agent)}<span class="mu">reasoning</span>${t}</div><div>${esc(p.text)}</div></div>`;
    case "llm_call": return `<div class="ev llm"><div class="hd">${av(e.agent)}<b>LLM 请求</b> ${esc(p.purpose)} <span class="mu">attempt ${p.attempt}</span>${t}</div><div class="mu mono">${esc((p.prompt_preview || "").slice(0, 220))}</div></div>`;
    case "llm_result": return `<div class="ev llm"><div class="hd">${av(e.agent)}<b>LLM 回复</b> ${p.ok ? `<span class="ok">ok</span> ${p.latency_ms} ms · ${p.tokens} tok` : `<span class="bad">${esc(p.error)}</span>`}${t}</div>${p.parsed ? `<pre>${esc(JSON.stringify(p.parsed, null, 1).slice(0, 1500))}</pre>` : ""}</div>`;
    case "tool_call": return `<div class="ev tool"><div class="hd">${av(e.agent)}<b class="mono">${esc(p.tool)}</b> <span class="mu">${esc(p.side_effect || "")}</span>${t}</div><div class="mono mu">${esc(JSON.stringify(p.args || {}).slice(0, 260))}</div></div>`;
    case "tool_result": return `<div class="ev tool"><div class="hd">${av(e.agent)}${p.ok ? "" : '<span class="bad">failed</span> '}<b class="mono">${esc(p.tool)}</b> ${f(p.seconds, 1)}s${t}</div>${p.error ? `<div class="bad">${esc(p.error)}</div>` : `<div class="mono mu">${esc((p.preview || "").slice(0, 300))}</div>`}</div>`;
    case "guard": return `<div class="ev guard ${p.verdict}"><div class="hd">${av("guard")}<b class="mono">${esc(p.rule)}</b> <span class="${p.verdict === "deny" ? "bad" : p.verdict === "correct" ? "warn" : "ok"}">${esc(p.verdict).toUpperCase()}</span> <span class="mu">${esc(p.detail)}</span>${t}</div></div>`;
    case "plan": return `<div class="ev"><div class="hd">${av(e.agent)}<b>ECO 计划</b>${t}</div>${(p.plan.programs || []).map(x => `<div><b class="mono">${esc(x.tag)}</b> — ${esc(x.why)}</div>`).join("")}<div class="mu">风险：${esc((p.plan.risks || []).join("；"))}</div></div>`;
    case "gate": return `<div class="ev"><div class="hd">${av("signoff")}<b>签核门</b>${t}</div>${p.checks ? `<div class="gate">${Object.entries(p.checks).map(([k, v]) => `<span class="${v ? "p" : "f"}">${k}</span>`).join("")}</div>` : `<div>Ge ${gate(p.v6)}</div><div>Gs ${gate(p.strict)}</div>${p.raw_tns_gain_pct != null ? `<div class="mu">raw TNS gain ${f(p.raw_tns_gain_pct)}%</div>` : ""}`}</div>`;
    case "patch": return `<div class="ev"><div class="hd">${av("rtl-debug")}<b>RTL patch</b> <span class="mu">${esc(p.source)}</span>${t}</div><div class="mu">${esc(p.diagnosis)}</div><pre>${(p.diff || "").split("\n").map(l => `<span class="diffl ${l[0] === "+" && l[1] !== "+" ? "add" : l[0] === "-" && l[1] !== "-" ? "del" : ""}">${esc(l) || " "}</span>`).join("")}</pre></div>`;
    case "knob_change": return `<div class="ev"><div class="hd">${av("physical")}<b>knob 变更</b> → 从 <b>${esc(p.resume_from)}</b> 续跑 <span class="mu">${esc(p.source)}</span>${t}</div><div class="mono">${Object.entries(p.changed).map(([k, [a, b]]) => `${k}: <span class="bad">${a}</span> → <span class="ok">${b}</span>`).join("　")}</div></div>`;
    case "eval_progress": return `<div class="ev tool"><div class="hd">${av("evaluator")}<b>evaluator</b> ${esc(p.stage)} ${p.drt_iteration ? "· DRT iter " + p.drt_iteration : ""} ${p.drt_violations !== undefined ? "· violations " + p.drt_violations : ""} · ${fdur(p.elapsed_s ?? null)}${t}</div></div>`;
    case "stage_error": return `<div class="ev guard deny"><div class="hd">${av("pnr")}<b>${esc(p.stage)} 失败</b> <span class="mono">${esc((p.errors || []).join(" "))}</span>${t}</div><pre>${esc((p.context || []).join("\n"))}</pre></div>`;
    case "skill": return `<div class="ev"><div class="hd">${av("curator")}skill <b>${esc(p.op)}</b> ${esc(p.id || (p.added || []).join(","))} ${p.verified !== undefined ? (p.verified ? '<span class="ok">verified</span>' : '<span class="mu">unverified</span>') : ""}${t}</div>${p.lesson ? `<div>${esc(p.lesson)}</div>` : ""}</div>`;
    case "artifact": return `<div class="ev tool"><div class="hd">${av(e.agent)}layout ${p.stats ? p.stats.cells + " cells, " + p.stats.wire_segments + " wire segments" : ""}${t}</div></div>`;
    default: return `<div class="ev sys">${esc(e.type)} · ${esc(e.agent)} ${esc(p.name || "")} ${p.why ? "· " + esc(p.why) : ""} ${p.error ? '<span class="bad">' + esc(p.error) + "</span>" : ""} ${p.status ? "· " + esc(p.status) : ""}${t}</div>`;
  }
}
function addEv(e) { const d = document.createElement("div"); d.innerHTML = evHtml(e); const el = d.firstElementChild; el.onclick = () => $("#insp").innerHTML = `<pre>${esc(JSON.stringify(e.p, null, 1))}</pre>`; $("#tl")?.appendChild(el); }
let LAYDATA = null;
async function pane(t, keep) {
  TAB = t; const P = $("#pane");
  if (t === "tl") { P.innerHTML = '<div id="tl"></div>'; EVS.forEach(addEv); return; }
  if (t === "3d") { J3 = {key: null}; return job3d(false); }
  if (t === "state") { P.innerHTML = `<pre>${esc(JSON.stringify(J.state, null, 1))}</pre>`; return; }
  if (t === "files") { P.innerHTML = J.steps.map(s => `<div class="card" style="margin:5px 0"><b>${esc(s.name)}</b> <span class="mono mu">${esc(s.dir)}</span><br>${s.files.map(fn => `<button class="sm" onclick="showFile('${esc(s.dir)}/${fn}')">${fn}</button>`).join(" ")}</div>`).join(""); return; }
  if (t === "llm") {
    P.innerHTML = `<table><tr><th>#</th><th>agent</th><th>purpose</th><th>tokens in/out</th><th>latency</th><th>ok</th></tr>${J.llm.map(c => `<tr class="click" onclick="showLLM(${c.id})"><td>${c.id}</td><td>${esc(c.agent)}</td><td>${esc(c.purpose)}</td><td>${f(c.prompt_tokens)} / ${f(c.completion_tokens)}</td><td>${f(c.latency_ms)} ms</td><td>${c.ok ? "ok" : "failed"}</td></tr>`).join("")}</table>`; return; }
  if (t === "ev") {
    const evs = J.steps.filter(s => s.tool === "eval_replica");
    if (!evs.length) { P.innerHTML = '<div class="mu">该作业没有签核评测器步骤</div>'; return; }
    let h = "";
    for (const s of evs) {
      const rid = s.args.run_id; let pr = {}; try { pr = await api("/api/progress?run_id=" + encodeURIComponent(rid)); } catch (e) { }
      const tr = pr.drt_violation_trace || [];
      h += `<div class="card" style="margin-bottom:10px"><div class="row"><b class="mono">${esc(rid)}</b>${st(s.status)}<span class="sp"></span><span class="mu">elapsed ${fdur(pr.elapsed_s ?? null)} · admission wait ${fdur(pr.admission_wait_s ?? null)}</span></div>
        <div class="kv" style="margin-top:6px"><span>stage</span><b>${esc(pr.stage)}</b><span>detailed-route iteration</span><span>${f(pr.drt_iteration)}</span><span>DRT violations</span><span>${f(pr.drt_violations)}</span><span>last report</span><span>${esc(pr.last_report || "—")}</span></div>
        ${tr.length > 1 ? spark(tr) : ""}<div class="row" style="margin-top:6px"><button class="sm" onclick="showFile('${esc(s.dir)}/evaluator.log')">evaluator.log</button>${s.outputs ? `<span>TNS <b>${f(s.outputs.tns)}</b> · WNS ${f(s.outputs.wns, 3)} · DRC ${f(s.outputs.drc)} · wall ${fdur(s.outputs.wall_s)}</span>` : ""}</div></div>`;
    }
    P.innerHTML = h; return;
  }
  if (t === "lay") {
    if (!J.state.layout) { P.innerHTML = '<div class="mu">版图在 block flow 的 signoff 之后生成（ECO 设计的 DEF 有数十万单元，用评测器报告查看）</div>'; return; }
    P.innerHTML = `<div class="row" id="ltg"></div><canvas id="cv" style="height:calc(100vh - 230px)"></canvas><div class="mu" id="linfo"></div>`;
    if (!LAYDATA || LAYDATA.path !== J.state.layout) { LAYDATA = await api("/api/file?path=" + encodeURIComponent(J.state.layout) + "&bytes=20000000"); LAYDATA.path = J.state.layout; }
    layout(LAYDATA);
  }
}
function spark(tr) { const W = 520, H = 70, mx = Math.max(...tr), mn = Math.min(...tr);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="70"><polyline fill="none" stroke="#4c8dff" stroke-width="2" points="${tr.map((v, i) => `${10 + i * (W - 20) / (tr.length - 1)},${H - 8 - (H - 16) * (v - mn) / ((mx - mn) || 1)}`).join(" ")}"/><text x="${W - 4}" y="12" fill="#7f8ea3" font-size="10" text-anchor="end">DRT violations per iteration (last ${tr.length})</text></svg>`; }
async function showLLM(id) { const c = await api("/api/llm?id=" + id); let msgs = []; try { msgs = JSON.parse(c.request_json); } catch (e) { }
  drawer(`LLM #${id} · ${c.agent} · ${c.purpose}`, `${(Array.isArray(msgs) ? msgs : []).map(m => `<h3>${esc(m.role)}</h3><pre>${esc(m.content)}</pre>`).join("")}<h3>response</h3><pre>${esc(c.response_text)}</pre>`); }

// ------------------------------------------------------------------ layout canvas (block flows)
function layout(d) {
  const CC = ["#121821", "#3a3f4a", "#36c5d6", "#a77bff", "#e8a93a", "#2b3d5a"], LC = {metal2: "#4c8dff", metal3: "#2fbf71", metal4: "#e8a93a", metal5: "#f06fa8", metal6: "#a77bff", metal7: "#36c5d6", metal8: "#ff8a4c", metal9: "#c8d64c", metal10: "#fff", metal1: "#5b6b82"};
  const on = {cells: 1, crit: 1, metal2: 1, metal3: 1, metal4: 1, metal5: 1};
  $("#ltg").innerHTML = ["cells", "crit", ...d.stats.layers].map(k => `<label class="mu"><input type="checkbox" data-k="${k}" ${on[k] ? "checked" : ""}> <i class="sw" style="background:${k === "crit" ? "#ff3b3b" : k === "cells" ? "#2b3d5a" : LC[k]}"></i>${k === "crit" ? "关键路径" : k}</label>`).join("");
  $("#linfo").textContent = `${d.stats.cells} cells · ${d.stats.wire_segments} wire segments · critical path ${d.crit.length} cells · die ${(d.die[2] / 100).toFixed(1)} × ${(d.die[3] / 100).toFixed(1)} µm`;
  const cv = $("#cv"); let v = null;
  const draw = () => { const dpr = devicePixelRatio || 1; cv.width = cv.clientWidth * dpr; cv.height = cv.clientHeight * dpr; const c = cv.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = cv.clientWidth, H = cv.clientHeight; if (!v) { const s = Math.min(W / d.die[2], H / d.die[3]) * .94; v = {s, x: (W - s * d.die[2]) / 2, y: (H - s * d.die[3]) / 2}; }
    const X = a => v.x + a * v.s, Y = a => H - (v.y + a * v.s); c.clearRect(0, 0, W, H); c.strokeStyle = "#4c5a70"; c.strokeRect(X(0), Y(d.die[3]), d.die[2] * v.s, d.die[3] * v.s);
    if (on.cells) for (const [x, y, w, h, k] of d.cells) { c.fillStyle = CC[k]; c.fillRect(X(x), Y(y + h), Math.max(.5, w * v.s), Math.max(.5, h * v.s)); }
    c.lineWidth = Math.max(.6, v.s * 7); c.globalAlpha = .75;
    for (const [l, a] of Object.entries(d.wires)) { if (!on[l]) continue; c.strokeStyle = LC[l] || "#fff"; c.beginPath(); for (let i = 0; i < a.length; i += 4) { c.moveTo(X(a[i]), Y(a[i + 1])); c.lineTo(X(a[i + 2]), Y(a[i + 3])); } c.stroke(); }
    c.globalAlpha = 1; if (on.crit && d.crit.length) { c.strokeStyle = "#ff3b3b"; c.lineWidth = 2.4; c.beginPath(); d.crit.forEach((ci, i) => { const [x, y, w, h] = d.cells[ci]; i ? c.lineTo(X(x + w / 2), Y(y + h / 2)) : c.moveTo(X(x + w / 2), Y(y + h / 2)); }); c.stroke();
      c.fillStyle = "#ff3b3b"; d.crit.forEach(ci => { const [x, y, w, h] = d.cells[ci]; c.fillRect(X(x) - 1, Y(y + h) - 1, w * v.s + 2, h * v.s + 2); }); } };
  $$("#ltg input").forEach(i => i.onchange = () => { on[i.dataset.k] = i.checked; draw(); });
  let drag = null; cv.onmousedown = e => drag = [e.clientX, e.clientY]; window.onmouseup = () => drag = null;
  cv.onmousemove = e => { if (!drag) return; v.x += e.clientX - drag[0]; v.y -= e.clientY - drag[1]; drag = [e.clientX, e.clientY]; draw(); };
  cv.onwheel = e => { e.preventDefault(); const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = cv.clientHeight - (e.clientY - r.top), k = e.deltaY < 0 ? 1.2 : 1 / 1.2; v.x = mx - (mx - v.x) * k; v.y = my - (my - v.y) * k; v.s *= k; draw(); };
  draw();
}

// ------------------------------------------------------------------ agents / tools / skills
async function agents() {
  const rows = await api("/api/llm_calls");
  $("#view").innerHTML = `<h2>Agent 调用 <small>研究账本里的模型调用 + wangsiEDA 作业的调用（完整 prompt / 回复可查看；密钥只在计算主机上读取，不进日志）</small></h2>
   <div class="tbl" style="max-height:none"><table><tr><th>#</th><th>时间</th><th>origin</th><th>job</th><th>agent</th><th>purpose</th><th>model</th><th>tokens in/out</th><th>latency</th></tr>
   ${rows.map(c => `<tr class="click" onclick="showLLM(${c.id})"><td>${c.id}</td><td>${new Date(c.ts * 1000).toLocaleString()}</td><td>${esc(c.origin)}</td><td class="mono">${c.job_id && c.origin === "wangsiEDA" ? `<a href="#/job/${c.job_id}">${c.job_id}</a>` : esc(c.job_id)}</td><td>${esc(c.agent)}</td><td>${esc(c.purpose)}</td><td>${esc(c.model)}</td><td>${f(c.prompt_tokens)} / ${f(c.completion_tokens)}</td><td>${f(c.latency_ms)} ms</td></tr>`).join("")}</table></div>`;
}
async function tools() {
  const [ts, dv] = await Promise.all([api("/api/tools"), api("/api/derivations")]);
  $("#view").innerHTML = `<h2>工具注册表 / MCP <small>每个能力都是带 JSON schema、副作用类别、调度池、超时、重试策略的工具；只读工具和作业提交通过 MCP 暴露</small></h2>
   <div class="note">MCP 客户端配置（经由现有 SSH 登录，stdio JSON-RPC）：<pre>{"mcpServers": {"wangsiEDA": {"command": "ssh", "args": ["your-ssh-alias", "/path/to/wangsieda/bin/wangsictl", "mcp"]}}}</pre></div>
   <div class="tbl" style="max-height:none"><table><tr><th>tool</th><th>side effect</th><th>pool</th><th>timeout</th><th>retries</th><th>MCP</th><th>description</th></tr>
   ${ts.map(t => `<tr class="click" onclick='drawer("${t.name}", "<pre>"+esc(${JSON.stringify(JSON.stringify(t.input_schema, null, 1))})+"</pre>")'><td class="mono"><b>${t.name}</b></td><td><span class="tag ${t.side_effect === "read" ? "pass" : t.side_effect === "runtime" ? "wait" : ""}">${t.side_effect}</span></td><td>${esc(t.pool)}</td><td>${fdur(t.timeout)}</td><td>${t.retries}</td><td>${t.side_effect === "read" ? "direct" : "via job"}</td><td style="white-space:normal">${esc(t.description)}</td></tr>`).join("")}</table></div>
   <h3>派生工具 <small>已验证的研究工具不被修改；wangsiEDA 运行仅替换输出/运行路径的副本，并记录源 SHA-256</small></h3>
   <div class="tbl"><table><tr><th>derived</th><th>source</th><th>source sha256</th><th>replacements</th></tr>${dv.map(x => `<tr><td class="mono">${esc(x.name)}</td><td class="mono">${esc(x.source.split("/").slice(-3).join("/"))}</td><td class="mono">${esc(x.source_sha256.slice(0, 16))}…</td><td>${x.replacements.length}</td></tr>`).join("")}</table></div>`;
}
async function skills() {
  const rows = await api("/api/skills");
  $("#view").innerHTML = `<h2>经验资产库 <small>Skills / lessons：只有通过冻结签核门（或冻结 testbench + signoff）的修复标记为 verified，才会被自动复用</small></h2>
   <div class="tbl" style="max-height:none"><table><tr><th>id</th><th>kind</th><th>case</th><th>trigger</th><th>action</th><th>verified</th><th>source job</th><th>created</th></tr>
   ${rows.map(s => `<tr><td class="mono">${esc(s.id)}</td><td>${esc(s.kind)}</td><td>${esc(s.case_name || "any")}</td><td class="mono" style="white-space:normal">${esc(s.trigger)}</td><td class="mono" style="white-space:normal;max-width:520px">${esc(s.action_json)}</td>
   <td>${s.verified ? '<span class="tag pass">verified</span>' : '<span class="tag">record</span>'}</td><td class="mono"><a href="#/job/${s.source_job}">${esc(s.source_job)}</a></td><td>${new Date(s.created * 1000).toLocaleString()}</td></tr>`).join("")}</table></div>`;
}

hostPill(); setInterval(hostPill, 20000); route();
