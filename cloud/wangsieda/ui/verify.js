// Consistency check: the rendered 3D model against independent references (DEF counts, evaluator reports).
"use strict";
function rvVerify() {
  const v = RV.viewer, S = v.S, R = S.verify || {}, md = v.model || {segs: [], len: [], vias: []}, zh = LANG === "zh";
  const sum = a => (a || []).reduce((x, y) => x + (y || 0), 0), ev = R.evaluator || {};
  const evWl = Object.entries(ev).find(([k]) => /wirelength/i.test(k)), evHbt = Object.entries(ev).find(([k]) => /hbt.*count|count.*hbt/i.test(k));
  const x0wl = S.label === "X0" && RV.ws && RV.ws.x0 ? RV.ws.x0.detailed_wirelength_um : null;
  const evStd = ev["finish__design__instance__count__stdcell"], evMac = ev["finish__design__instance__count__macros"];
  const row = (item, model, ref, src, tol = 0) => {
    const ok = ref == null ? null : Math.abs(model - ref) <= Math.max(tol * Math.abs(ref), 1e-9);
    return `<tr><td>${item}</td><td class="num">${n0(model)}</td><td class="num">${ref == null ? "-" : n0(ref)}</td><td class="mu">${src}</td>
      <td>${ok == null ? "" : ok ? `<span class="tag pass">${zh ? "一致" : "match"}</span>` : `<span class="tag fail">${zh ? "偏差 " : "diff "}${n0(Math.round((model - ref) / (ref || 1) * 1e4) / 100)}%</span>`}</td></tr>`; };
  const cellsDrawn = S.cells.length + S.macros.length;
  let h = `<p>${zh ? "左列为 3D 模型中实际绘制的对象统计，右列为独立来源：DEF 原文计数与签核评测器自身的报告。" : "Model column: what is drawn. Reference: DEF counts and the evaluator's own reports."}</p>
    <table class="mt"><tr><th>${zh ? "检查项" : "item"}</th><th>${zh ? "3D 模型" : "model"}</th><th>${zh ? "参考值" : "reference"}</th><th>${zh ? "来源" : "source"}</th><th></th></tr>
    ${row(zh ? "布线段总数" : "routed segments", sum(md.segs), sum(R.def_segments), "DEF NETS")}
    ${row(zh ? "通孔总数" : "vias", sum(md.vias), sum(R.def_vias), "DEF NETS")}
    ${row(zh ? "总线长 (um)" : "wirelength (um)", Math.round(sum(md.len)), evWl ? evWl[1] : x0wl, evWl ? (zh ? "评测器 " : "evaluator ") + evWl[0] : (zh ? "官方记录 detailed_wirelength_um" : "official detailed_wirelength_um"), 0.002)}
    ${row(zh ? "标准单元" : "standard cells", S.cells.filter(c => c[5] !== 5).length, evStd, zh ? "评测器 6_report" : "evaluator 6_report")}
    ${row(zh ? "宏单元（SRAM）" : "macros (SRAM)", S.macros.length, evMac, zh ? "评测器 6_report" : "evaluator 6_report")}
    ${row("HBT", S.hbt.length, evHbt ? evHbt[1] : null, evHbt ? evHbt[0] : "hbt_net_merge.tsv")}
    ${row(zh ? "DRC 标记" : "DRC markers", S.drc.length, R.drc_report_count, "5_route_drc.rpt")}</table>
    <h3>${zh ? "逐层核对" : "per layer"}</h3><table class="mt"><tr><th>${zh ? "层" : "layer"}</th><th>z (um)</th><th>${zh ? "厚度" : "t"}</th><th>${zh ? "线宽" : "w"}</th><th>${zh ? "线段（模型/DEF）" : "segments model/DEF"}</th><th>${zh ? "线长 um（模型/DEF）" : "length model/DEF"}</th><th>${zh ? "通孔" : "vias"}</th></tr>
    ${(S.stack || []).map((s, i) => `<tr><td>${s.name}</td><td class="num">${s.z}</td><td class="num">${s.t}</td><td class="num">${s.w}</td>
      <td class="num">${n0(md.segs[i] || 0)} / ${n0((R.def_segments || [])[i] || 0)}</td><td class="num">${n0(Math.round(md.len[i] || 0))} / ${n0(Math.round((R.def_length_um || [])[i] || 0))}</td>
      <td class="num">${n0(md.vias[i] || 0)}</td></tr>`).join("")}</table>
    <p class="mu">${zh ? "层高度、厚度、线宽取自工艺 LEF（HEIGHT / THICKNESS / WIDTH）；上层 die 按面对面键合镜像。垂直方向可用 z scale 放大，与 KLayout 2.5D、GDS3D 的做法相同。线长容差 0.2%：模型按 DEF 线段中心线计算，评测器的详细布线统计对通孔处线端的计法略有不同。" :
      "Heights, thickness and widths come from the technology LEF; the upper die is mirrored for face-to-face bonding. z scale exaggerates the vertical axis as in KLayout 2.5D / GDS3D."}</p>`;
  drawer(zh ? "3D 模型一致性校验" : "3D model consistency check", h);
}
