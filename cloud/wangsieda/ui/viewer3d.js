// F2F 3D signoff viewer (WebGL2, no external libraries).
// World units: micrometres in x/y; z is an exaggerated stack: bottom-die devices, metal1..10, bond (hbt), metal11..20, upper-die devices.
"use strict";
const V3 = (() => {
  const LAYERS = ["metal1", "metal2", "metal3", "metal4", "metal5", "metal6", "metal7", "metal8", "metal9", "metal10", "hbt",
    "metal11", "metal12", "metal13", "metal14", "metal15", "metal16", "metal17", "metal18", "metal19", "metal20"];
  const M = [[.20, .40, 1], [1, .20, .20], [.20, .85, .25], [1, .85, .10], [.95, .25, .95], [.10, .85, .95], [1, .55, .10], [.60, .45, 1], [.75, .75, .75], [.95, .95, .55]];
  const LAYER_RGB = [...M, [.85, .55, .25], ...M.slice().reverse()];
  const DRC_RGB = [[1, .25, .25], [1, .62, .15], [1, .9, .2], [.75, .45, 1], [1, .2, .7], [.7, .7, .7]];

  // ---------------------------------------------------------------- math
  const m4 = {
    persp(f, a, n, fa) { const t = 1 / Math.tan(f / 2); return [t / a, 0, 0, 0, 0, t, 0, 0, 0, 0, (fa + n) / (n - fa), -1, 0, 0, 2 * fa * n / (n - fa), 0]; },
    look(e, c, u) {
      const z = norm(sub(e, c)), x = norm(cross(u, z)), y = cross(z, x);
      return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, e), -dot(y, e), -dot(z, e), 1];
    },
    mul(a, b) { const o = new Array(16); for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k]; o[i * 4 + j] = s; } return o; },
  };
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]], dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const norm = a => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };

  // ---------------------------------------------------------------- GL helpers
  const VS = `#version 300 es
  in vec3 p; in vec4 c; uniform mat4 M; uniform float ps; out vec4 vc;
  void main(){ gl_Position = M*vec4(p,1.0); gl_PointSize = ps; vc = c; }`;
  const FS = `#version 300 es
  precision mediump float; in vec4 vc; uniform float pt; out vec4 o;
  void main(){ if(pt>0.5){ vec2 d=gl_PointCoord-0.5; if(dot(d,d)>0.25) discard; } o = vc; }`;
  const IVS = `#version 300 es
  in vec2 q; in vec4 r; in vec4 c; in float z; uniform mat4 M; out vec4 vc;
  void main(){ vec3 p = vec3(r.x + q.x*r.z, r.y + q.y*r.w, z); gl_Position = M*vec4(p,1.0); vc = c; }`;

  const BVS = `#version 300 es
  in vec3 q; in vec3 nr; in vec3 o; in vec3 sz; in vec4 c; uniform mat4 M; out vec4 vc;
  void main(){ gl_Position = M*vec4(o + q*sz, 1.0); float l = 0.42 + 0.58*max(dot(nr, normalize(vec3(0.35,0.25,0.9))), 0.0); vc = vec4(c.rgb*l, c.a); }`;
  const CUBE = (() => { const f = [[[0,0,1],[0,0,1, 1,0,1, 1,1,1, 0,1,1]],[[0,0,-1],[0,0,0, 0,1,0, 1,1,0, 1,0,0]],[[1,0,0],[1,0,0, 1,1,0, 1,1,1, 1,0,1]],
      [[-1,0,0],[0,0,0, 0,0,1, 0,1,1, 0,1,0]],[[0,1,0],[0,1,0, 0,1,1, 1,1,1, 1,1,0]],[[0,-1,0],[0,0,0, 1,0,0, 1,0,1, 0,0,1]]], v = [];
    for (const [n, p] of f) for (const i of [0, 1, 2, 0, 2, 3]) v.push(p[3 * i], p[3 * i + 1], p[3 * i + 2], ...n);
    return new Float32Array(v); })();
  class Boxes {   // shaded axis-aligned solids (wires, vias, pads) as instanced cubes
    constructor() { this.o = []; this.s = []; this.c = []; }
    add(x0, y0, z0, sx, sy, sz, col) { this.o.push(x0, y0, z0); this.s.push(sx, sy, sz); this.c.push(col[0], col[1], col[2], col[3] ?? 1); }
    upload(gl) {
      this.n = this.o.length / 3; this.vao = gl.createVertexArray(); gl.bindVertexArray(this.vao);
      const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, CUBE, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0); gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);
      const mk = (d, loc, n) => { const bb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, bb); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(d), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, n, gl.FLOAT, false, 0, 0); gl.vertexAttribDivisor(loc, 1); };
      mk(this.o, 2, 3); mk(this.s, 3, 3); mk(this.c, 4, 4); gl.bindVertexArray(null); this.o = this.s = this.c = null; return this;
    }
  }
  const b64u16 = b64 => { const bin = atob(b64), a = new Uint16Array(bin.length >> 1); for (let i = 0; i < a.length; i++) a[i] = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); return a; };

  function prog(gl, vs, fs) {
    const sh = (t, s) => { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(o)); return o; };
    const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return p;
  }

  class Batch {   // positions (xyz) + colours (rgba), drawn as LINES / TRIANGLES / POINTS
    constructor() { this.p = []; this.c = []; }
    v(x, y, z, col) { this.p.push(x, y, z); this.c.push(col[0], col[1], col[2], col[3] ?? 1); }
    line(a, b, col) { this.v(a[0], a[1], a[2], col); this.v(b[0], b[1], b[2], col); }
    quad(x0, y0, x1, y1, z, col) { this.v(x0, y0, z, col); this.v(x1, y0, z, col); this.v(x1, y1, z, col); this.v(x0, y0, z, col); this.v(x1, y1, z, col); this.v(x0, y1, z, col); }
    box(x0, y0, z0, x1, y1, z1, top, side) {
      this.quad(x0, y0, x1, y1, z1, top); this.quad(x0, y0, x1, y1, z0, side);
      const f = (a, b, c, d) => { for (const p of [a, b, c, a, c, d]) this.v(p[0], p[1], p[2], side); };
      f([x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1]); f([x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]);
      f([x1, y1, z0], [x0, y1, z0], [x0, y1, z1], [x1, y1, z1]); f([x0, y1, z0], [x0, y0, z0], [x0, y0, z1], [x0, y1, z1]);
    }
    rect(x0, y0, x1, y1, z, col) { this.line([x0, y0, z], [x1, y0, z], col); this.line([x1, y0, z], [x1, y1, z], col); this.line([x1, y1, z], [x0, y1, z], col); this.line([x0, y1, z], [x0, y0, z], col); }
    ribbon(a, b, w, col) {  // flat/vertical strip so important geometry stays visible at any zoom
      let d = [b[0] - a[0], b[1] - a[1]]; const l = Math.hypot(d[0], d[1]);
      const n = l > 1e-6 ? [-d[1] / l * w, d[0] / l * w] : [w, 0];
      const p0 = [a[0] + n[0], a[1] + n[1], a[2]], p1 = [a[0] - n[0], a[1] - n[1], a[2]], p2 = [b[0] - n[0], b[1] - n[1], b[2]], p3 = [b[0] + n[0], b[1] + n[1], b[2]];
      for (const p of [p0, p1, p2, p0, p2, p3]) this.v(p[0], p[1], p[2], col);
    }
    upload(gl) {
      this.n = this.p.length / 3; this.vao = gl.createVertexArray(); gl.bindVertexArray(this.vao);
      const b1 = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b1); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(this.p), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      const b2 = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b2); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(this.c), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 0, 0);
      gl.bindVertexArray(null); this.p = this.c = null; return this;
    }
  }

  class Instances {  // axis-aligned rectangles (cells) as instanced quads
    constructor() { this.r = []; this.c = []; this.z = []; }
    add(x, y, w, h, z, col) { this.r.push(x, y, w, h); this.c.push(...col); this.z.push(z); }
    upload(gl) {
      this.n = this.z.length; this.vao = gl.createVertexArray(); gl.bindVertexArray(this.vao);
      const q = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, q); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1]), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      const mk = (data, loc, size) => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
        gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0); gl.vertexAttribDivisor(loc, 1); };
      mk(this.r, 1, 4); mk(this.c, 2, 4); mk(this.z, 3, 1); gl.bindVertexArray(null); this.r = this.c = this.z = null; return this;
    }
  }

  // ---------------------------------------------------------------- viewer
  class Viewer {
    constructor(canvas, opts = {}) {
      this.cv = canvas; this.gl = canvas.getContext("webgl2", {antialias: true, alpha: false});
      if (!this.gl) throw new Error("WebGL2 not available in this browser");
      const gl = this.gl;
      this.P = prog(gl, VS, FS); this.PI = prog(gl, IVS, FS); this.PB = prog(gl, BVS, FS);
      ["q", "nr", "o", "sz", "c"].forEach((n, i) => gl.bindAttribLocation(this.PB, i, n)); gl.linkProgram(this.PB); this.zscale = 4;
      gl.bindAttribLocation(this.P, 0, "p"); gl.bindAttribLocation(this.P, 1, "c");
      ["q", "r", "c", "z"].forEach((n, i) => gl.bindAttribLocation(this.PI, i, n));
      gl.linkProgram(this.P); gl.linkProgram(this.PI);
      this.cam = {yaw: -0.65, pitch: 0.62, dist: 900, tx: 300, ty: 200, tz: 40};
      this.explode = 0; this.vscale = 4.0; this.show = Object.assign({cells: true, macros: true, hbt: true, nets: true, paths: true, endpoints: true, drc: false,
        guides: true, density: false, dies: true}, opts.show || {});
      this.layers = LAYERS.map(() => true); this.objs = {}; this.picks = []; this.sel = null; this.anim = null; this.onpick = opts.onpick || (() => {});
      this.bindInput(); new ResizeObserver(() => this.draw()).observe(canvas);
    }
    z(li) {  // li: -1 bottom devices, 0..20 stack index (10 = bond), 21 upper devices; process heights from the tech LEF when present
      const st = this.S && this.S.stack;
      if (st) {
        if (li < 0) return 0;
        if (li >= st.length) return (this.S.upper_device_z || 0) * this.zscale + this.explode;
        return st[li].z * this.zscale + (li >= 10 ? this.explode : 0);
      }
      const dz = this.vscale; let z = (li + 1) * dz; if (li >= 10) z += this.explode; return z;
    }
    th(li) { const st = this.S && this.S.stack; return st && st[li] ? st[li].t * this.zscale : 0; }
    tierZ(t) { return t ? this.z(21) : this.z(-1); }

    load(scene, base) {   // base: optional X0 scene for "before" ghosts of changed nets
      this.S = scene; this.B = base; this.build(); this.draw();
    }
    build() {
      const gl = this.gl, S = this.S, u = 0.01;
      Object.values(this.objs).forEach(o => { if (o && o.vao) gl.deleteVertexArray(o.vao); });
      const o = this.objs = {}; this.picks = [];
      const W = S.die[0] * u, H = S.die[1] * u; this.W = W; this.H = H;
      // dies
      const dies = new Batch(), upper = new Batch(), si = 6 * this.vscale / 4;
      dies.box(0, 0, this.tierZ(0) - si, W, H, this.tierZ(0) - .05, [.16, .16, .18, 1], [.30, .30, .33, 1]);
      upper.box(0, 0, this.tierZ(1) + .05, W, H, this.tierZ(1) + si, [.42, .42, .46, .22], [.30, .30, .33, .22]);
      upper.quad(0, 0, W, H, this.z(10), [.85, .55, .25, .05]);
      o.dieFill = dies.upload(gl); o.upperFill = upper.upload(gl);
      const fr = new Batch();
      for (const t of [0, 1]) fr.rect(0, 0, W, H, this.tierZ(t), [.55, .55, .6, 1]);
      fr.rect(0, 0, W, H, this.z(10), [.45, .45, .5, .8]);
      for (const [x, y] of [[0, 0], [W, 0], [W, H], [0, H]]) fr.line([x, y, this.tierZ(0)], [x, y, this.tierZ(1)], [.3, .35, .45, .7]);
      o.frame = fr.upload(gl);
      // cells and macros
      const crit = new Set(); (S.paths || []).slice(0, 40).forEach(p => p.pts.forEach(q => crit.add(q[0] + "," + q[1])));
      const CC = [[.33, .47, .72, 1], [.62, .48, .92, 1], [.90, .66, .28, 1], [.30, .78, .86, 1], [.2, .2, .2, 1], [.5, .5, .5, 1]];
      const ins = new Instances();
      for (const c of S.cells) { if (c[5] === 5) continue; ins.add(c[0] * u, c[1] * u, Math.max(c[2] * u, .6), c[3] * u, this.tierZ(c[4]) + .02, CC[c[5]]); }
      o.cells = ins.upload(gl);
      const mac = new Batch(), macL = new Batch();
      for (const m of S.macros) { const z = this.tierZ(m[4]), x0 = m[0] * u, y0 = m[1] * u, x1 = x0 + m[2] * u, y1 = y0 + m[3] * u;
        const h = m[4] ? -1.5 : 1.5; mac.box(x0, y0, Math.min(z, z + h), x1, y1, Math.max(z, z + h), [.40, .42, .48, .9], [.25, .26, .30, .9]);
        macL.rect(x0, y0, x1, y1, z + h + (h > 0 ? .02 : -.02), [.80, .82, .88, 1]);
        this.picks.push({p: [(x0 + x1) / 2, (y0 + y1) / 2, z], kind: "macro", name: m[5], info: `SRAM macro, ${(m[2] * u).toFixed(1)} x ${(m[3] * u).toFixed(1)} um, ${m[4] ? "upper" : "bottom"} die`}); }
      o.macros = mac.upload(gl); o.macroL = macL.upload(gl);
      // HBTs
      const hb = new Batch(), hbN = new Batch(), newH = new Set(S.hbt_new || []);
      S.hbt.forEach((h, i) => { const x = h[0] * u, y = h[1] * u, n = newH.has(i);
        const zb = this.z(10), r = S.stack && S.stack[10] ? S.stack[10].w / 2 : 1.2;   // pad size from the LEF hbt layer
        if (n) { hbN.box(x - r, y - r, zb - .3, x + r, y + r, zb + .3, [.2, 1, .45, 1], [.1, .7, .3, 1]); hbN.ribbon([x, y, this.z(9)], [x, y, this.z(11)], .35, [.2, 1, .45, 1]); }
        else { hbN.box(x - r, y - r, zb - .3, x + r, y + r, zb + .3, newH.size ? [.55, .40, .22, .5] : [.85, .55, .25, 1], [.55, .35, .15, newH.size ? .5 : 1]);
               hb.line([x, y, this.z(9)], [x, y, this.z(11)], [.85, .55, .25, newH.size ? .15 : .5]); }
        if (n) this.picks.push({p: [x, y, this.z(10)], kind: "hbt", name: h[2], info: "hybrid-bond terminal added by the ECO"}); });
      o.hbt = hb.upload(gl); o.hbtNew = hbN.upload(gl);
      // focus nets: routed wires (after) and, if a base scene is given, the same nets before (ghost)
      const changed = new Set((S.guide_diff || {}).changed_nets || []);
      const nets = new Batch(), ghost = new Batch(), pins = new Batch();
      for (const [name, n] of Object.entries(S.nets)) {
        const hot = changed.has(name), hl = this.hl === name;
        for (const w of n.wires) { const z = this.z(w[4]);
          const col = hl ? (hot ? [.35, 1, .55, 1] : [1, .85, .25, 1]) : hot ? [.25, .85, .5, .55] : LAYER_RGB[w[4]].concat([this.hl ? .35 : .85]);
          if (this.layers[w[4]]) nets.line([w[0] * u, w[1] * u, z], [w[2] * u, w[3] * u, z], col); }
        const drv = n.conn[0];
        if (drv) this.picks.push({p: [drv[2] * u, drv[3] * u, this.tierZ(drv[4])], kind: "net", name, info: `net ${name}: fanout ${n.fanout}, routed ${n.length_um} um${hot ? ", guides changed by the ECO" : ""}`});
        if (hl) for (const c of n.conn.slice(1)) pins.v(c[2] * u, c[3] * u, this.tierZ(c[4]) + .3, [.9, .9, .4, .9]);
        if (this.B && this.B.nets[name] && hot && (!this.hl || hl)) for (const w of this.B.nets[name].wires) { const z = this.z(w[4]); ghost.line([w[0] * u, w[1] * u, z], [w[2] * u, w[3] * u, z], [.55, .55, .6, .5]); }
      }
      o.nets = nets.upload(gl); o.ghost = ghost.upload(gl); o.sinks = pins.upload(gl);
      // ECO guide changes (added global-route guide boxes)
      const gd = new Batch();
      for (const [name, d] of Object.entries((S.guide_diff || {}).nets || {})) if (!this.hl || this.hl === name) for (const b of d.added) gd.rect(b[0] * u, b[1] * u, b[2] * u, b[3] * u, this.z(b[4]) + .05, [.3, 1, .6, .55]);
      o.guides = gd.upload(gl);
      // violated paths (ribbons) and endpoints
      const pa = new Batch();
      (S.paths || []).slice(0, 25).forEach((p, k) => {
        const col = k === 0 ? [1, .2, .2, 1] : [1, .45, .3, .55];
        for (let i = 1; i < p.pts.length; i++) {
          const a = p.pts[i - 1], b = p.pts[i], za = this.tierZ(a[2]) + .6, zb = this.tierZ(b[2]) + .6;
          pa.ribbon([a[0] * u, a[1] * u, za], [b[0] * u, b[1] * u, zb], k === 0 ? .9 : .4, col);
        }
        const e = p.pts[p.pts.length - 1];
        if (e) this.picks.push({p: [e[0] * u, e[1] * u, this.tierZ(e[2])], kind: "path", idx: k, name: p.end, info: `setup slack ${p.slack} ns, from ${p.start}`});
      });
      o.paths = pa.upload(gl);
      const ep = new Batch();
      for (const e of S.endpoints) { const s = e[4], k = Math.min(1, -s / 4); ep.v(e[1] * u, e[2] * u, this.tierZ(e[3]) + .4, [1, .85 - .7 * k, .2, .95]); }
      o.endpoints = ep.upload(gl);
      // DRC markers
      const dr = new Batch();
      for (const d of S.drc) { const x = (d[0] + d[2]) / 2 * u, y = (d[1] + d[3]) / 2 * u; if (this.layers[d[4]]) dr.v(x, y, this.z(d[4]), DRC_RGB[d[5]].concat([.9])); }
      o.drc = dr.upload(gl);
      // full detailed routing of every net (from the DEF), one colour per layer
      // every wire is a solid of the layer's real width (LEF WIDTH) and thickness (LEF THICKNESS) at its process height
      const ro = new Batch(), rb = new Boxes(), vb = new Boxes(); this.model = {segs: [], len: [], vias: []};
      if (S.routes) S.routes.layers.forEach((b64, li) => {
        const a = b64 ? b64u16(b64) : new Uint16Array(0); let L = 0;
        for (let i = 0; i < a.length; i += 4) L += (Math.abs(a[i + 2] - a[i]) + Math.abs(a[i + 3] - a[i + 1])) * .1;
        this.model.segs[li] = a.length / 4; this.model.len[li] = L;
        if (!a.length || this.layers[li] === false) return;
        const col = LAYER_RGB[li % LAYER_RGB.length].concat([1]), st = S.stack && S.stack[li];
        if (!st) { const z = this.z(li); for (let i = 0; i < a.length; i += 4) ro.line([a[i] * .1, a[i + 1] * .1, z], [a[i + 2] * .1, a[i + 3] * .1, z], col.slice(0, 3).concat([.55])); return; }
        const w = st.w, h = w / 2, z = this.z(li), t = Math.max(this.th(li), .05);
        for (let i = 0; i < a.length; i += 4) { const x1 = Math.min(a[i], a[i + 2]) * .1, x2 = Math.max(a[i], a[i + 2]) * .1, y1 = Math.min(a[i + 1], a[i + 3]) * .1, y2 = Math.max(a[i + 1], a[i + 3]) * .1;
          rb.add(x1 - h, y1 - h, z, x2 - x1 + w, y2 - y1 + w, t, col); }
      });
      if (S.vias && S.stack) S.vias.layers.forEach((b64, li) => {
        const a = b64 ? b64u16(b64) : new Uint16Array(0); this.model.vias[li] = a.length / 2;
        if (!a.length || this.layers[li] === false) return;
        const cw = (S.vias.cut_width || {})["via" + (li + 1)] || S.stack[li].w, z0 = this.z(li) + this.th(li), up = li + 1 < S.stack.length ? this.z(li + 1) : z0 + 1;
        const zb = Math.min(z0, up), zt = Math.max(z0, up), col = [.82, .82, .86, 1];
        for (let i = 0; i < a.length; i += 2) vb.add(a[i] * .1 - cw / 2, a[i + 1] * .1 - cw / 2, zb, cw, cw, Math.max(zt - zb, .05), col);
      });
      o.routes = ro.upload(gl); o.routeBoxes = rb.upload(gl); o.viaBoxes = vb.upload(gl);
      // routing density (per layer, only for enabled layers): coloured quads at the layer height
      const de = new Batch(), G = S.density.grid, gx = W / G[0], gy = H / G[1];
      S.density.layers.forEach((g, li) => { if (!this.layers[li]) return; const mx = Math.max(...g) || 1;
        g.forEach((v, i) => { if (v <= 0) return; const t = v / mx, x = (i % G[0]) * gx, y = Math.floor(i / G[0]) * gy;
          de.quad(x, y, x + gx, y + gy, this.z(li) + .02, [t, .25 + .5 * (1 - t), 1 - t, .08 + .35 * t]); }); });
      o.density = de.upload(gl);
    }

    matrix() {
      const c = this.cam, W = this.cv.clientWidth, H = this.cv.clientHeight;
      const e = [c.tx + c.dist * Math.cos(c.pitch) * Math.cos(c.yaw), c.ty + c.dist * Math.cos(c.pitch) * Math.sin(c.yaw), c.tz + c.dist * Math.sin(c.pitch)];
      return m4.mul(m4.persp(0.8, W / H, 1, 20000), m4.look(e, [c.tx, c.ty, c.tz], [0, 0, 1]));
    }
    draw() {
      if (!this.S) return;
      const gl = this.gl, dpr = devicePixelRatio || 1, W = this.cv.clientWidth, H = this.cv.clientHeight;
      if (this.cv.width !== W * dpr || this.cv.height !== H * dpr) { this.cv.width = W * dpr; this.cv.height = H * dpr; }
      gl.viewport(0, 0, this.cv.width, this.cv.height); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.enable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      const M = this.MV = this.matrix(), o = this.objs, sh = this.show;
      gl.useProgram(this.PI); gl.uniformMatrix4fv(gl.getUniformLocation(this.PI, "M"), false, M); gl.uniform1f(gl.getUniformLocation(this.PI, "pt"), 0);
      if (sh.cells) { gl.bindVertexArray(o.cells.vao); gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, o.cells.n); }
      gl.useProgram(this.P); const uM = gl.getUniformLocation(this.P, "M"), uPS = gl.getUniformLocation(this.P, "ps"), uPT = gl.getUniformLocation(this.P, "pt");
      gl.uniformMatrix4fv(uM, false, M);
      const d = (ob, mode, ps = 1, pt = 0) => { if (!ob || !ob.n) return; gl.uniform1f(uPS, ps * (devicePixelRatio || 1)); gl.uniform1f(uPT, pt); gl.bindVertexArray(ob.vao); gl.drawArrays(mode, 0, ob.n); };
      if (sh.dies) d(o.dieFill, gl.TRIANGLES); d(o.frame, gl.LINES);
      if (sh.macros) { d(o.macros, gl.TRIANGLES); d(o.macroL, gl.LINES); }
      if (sh.routes) {
        d(o.routes, gl.LINES);
        gl.useProgram(this.PB); gl.uniformMatrix4fv(gl.getUniformLocation(this.PB, "M"), false, M); gl.uniform1f(gl.getUniformLocation(this.PB, "pt"), 0);
        for (const ob of [o.routeBoxes, o.viaBoxes]) if (ob && ob.n) { gl.bindVertexArray(ob.vao); gl.drawArraysInstanced(gl.TRIANGLES, 0, 36, ob.n); }
        gl.useProgram(this.P);
      }
      if (sh.hbt) { d(o.hbt, gl.LINES); d(o.hbtNew, gl.TRIANGLES); }
      if (sh.nets) { d(o.ghost, gl.LINES); d(o.nets, gl.LINES); d(o.sinks, gl.POINTS, 3, 1); }
      if (sh.guides) d(o.guides, gl.LINES);
      if (sh.endpoints) d(o.endpoints, gl.POINTS, 4, 1);
      if (sh.drc) d(o.drc, gl.POINTS, 3, 1);
      if (sh.paths) d(o.paths, gl.TRIANGLES);
      gl.depthMask(false);
      if (sh.density) d(o.density, gl.TRIANGLES);
      if (sh.dies) d(o.upperFill, gl.TRIANGLES);
      gl.depthMask(true);
      if (this.anim) this.drawAnim(d);
      gl.bindVertexArray(null);
    }

    // signal propagation along a path: the dot spends time in proportion to each stage's delay
    playPath(idx, seconds = 6) {
      const p = this.S.paths[idx]; if (!p) return; const u = 0.01;
      const pts = p.pts.map(q => [q[0] * u, q[1] * u, this.tierZ(q[2]) + 1.2, q[3]]), total = pts.reduce((a, q) => a + Math.max(q[3], .01), 0);
      this.anim = {pts, total, t0: performance.now(), dur: seconds * 1000, idx};
      const step = () => { if (!this.anim) return; this.draw(); if (performance.now() - this.anim.t0 < this.anim.dur + 800) requestAnimationFrame(step); else { this.anim = null; this.draw(); } };
      requestAnimationFrame(step);
    }
    drawAnim(d) {
      const a = this.anim, f = Math.min(1, (performance.now() - a.t0) / a.dur) * a.total; let acc = 0, pos = a.pts[0];
      for (let i = 1; i < a.pts.length; i++) { const dl = Math.max(a.pts[i][3], .01);
        if (acc + dl >= f) { const t = (f - acc) / dl, A = a.pts[i - 1], B = a.pts[i]; pos = [A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t]; break; }
        acc += dl; pos = a.pts[i]; }
      const b = new Batch(); b.v(pos[0], pos[1], pos[2], [1, 1, 1, 1]); b.upload(this.gl); d(b, this.gl.POINTS, 14, 1); this.gl.deleteVertexArray(b.vao);
      if (this.onanim) this.onanim(f, a.total);
    }

    view(name) {
      const c = this.cam, W = this.W || 600, H = this.H || 400; c.tx = W / 2; c.ty = H / 2; c.tz = this.z(10);
      if (name === "top") Object.assign(c, {yaw: -Math.PI / 2, pitch: 1.55, dist: 1.25 * Math.max(W, H)});
      else if (name === "side") Object.assign(c, {yaw: -Math.PI / 2, pitch: 0.05, dist: 1.1 * W});
      else Object.assign(c, {yaw: -0.65, pitch: 0.62, dist: 1.35 * Math.max(W, H)});
      this.draw();
    }
    focus(x, y, z, dist = 420) { Object.assign(this.cam, {tx: x, ty: y, tz: z, dist, pitch: Math.max(this.cam.pitch, 0.85)}); this.draw(); }
    highlight(name) { this.hl = name || null; this.build(); this.draw(); }
    setExplode(v) { this.explode = v; this.build(); this.draw(); }
    setZScale(v) { this.zscale = v; this.build(); this.draw(); }
    setLayer(i, on) { this.layers[i] = on; this.build(); this.draw(); }

    project(p) {
      const M = this.MV, x = p[0], y = p[1], z = p[2];
      const cx = M[0] * x + M[4] * y + M[8] * z + M[12], cy = M[1] * x + M[5] * y + M[9] * z + M[13], cw = M[3] * x + M[7] * y + M[11] * z + M[15];
      if (cw <= 0) return null; return [(cx / cw * .5 + .5) * this.cv.clientWidth, (1 - (cy / cw * .5 + .5)) * this.cv.clientHeight];
    }
    pick(mx, my) {
      let best = null, bd = 10;
      for (const k of this.picks) { const s = this.project(k.p); if (!s) continue; const dd = Math.hypot(s[0] - mx, s[1] - my); if (dd < bd) { bd = dd; best = k; } }
      return best;
    }
    bindInput() {
      const cv = this.cv; let drag = null, moved = false;
      cv.addEventListener("contextmenu", e => e.preventDefault());
      cv.addEventListener("mousedown", e => { drag = {x: e.clientX, y: e.clientY, b: e.button, s: e.shiftKey}; moved = false; });
      window.addEventListener("mouseup", e => { if (drag && !moved) { const r = cv.getBoundingClientRect(); const k = this.pick(e.clientX - r.left, e.clientY - r.top); this.onpick(k); } drag = null; });
      window.addEventListener("mousemove", e => {
        if (!drag) return; const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 2) moved = true; drag.x = e.clientX; drag.y = e.clientY;
        const c = this.cam;
        if (drag.b === 2 || drag.s) this.pan(dx, dy);
        else { c.yaw -= dx * 0.006; c.pitch = Math.max(0.02, Math.min(1.56, c.pitch + dy * 0.005)); }
        this.draw();
      });
      cv.addEventListener("wheel", e => { e.preventDefault(); this.cam.dist = Math.max(15, Math.min(6000, this.cam.dist * (e.deltaY > 0 ? 1.12 : 1 / 1.12))); this.draw(); }, {passive: false});
    }
    pan(dx, dy) {  // screen drag moves the target in the ground plane
      const c = this.cam, k = c.dist / 900, right = [-Math.sin(c.yaw), Math.cos(c.yaw)], fwd = [-Math.cos(c.yaw), -Math.sin(c.yaw)];
      c.tx += (-dx * right[0] + dy * fwd[0]) * k; c.ty += (-dx * right[1] + dy * fwd[1]) * k;
    }
  }
  return {Viewer, LAYERS, LAYER_RGB, DRC_RGB};
})();
