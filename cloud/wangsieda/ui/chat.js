// "Ask wangsiEDA": natural-language questions answered by the assistant agent with read-only tools.
// Each question is an `ask` job (logged like any other job); a proposed job is only submitted when the person clicks.
"use strict";
const CHAT = {history: [], busy: false, es: null};
const T = (zh, en) => (typeof LANG !== "undefined" && LANG === "en") ? en : zh;

function chatMd(s) {   // tiny, safe markdown: escape first, then **bold**, `code`, line breaks
  return esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
}

function chatInit() {
  if (window.WANGSI_OFFLINE || document.getElementById("askBtn")) return;
  const hdr = document.querySelector("header");
  hdr.insertAdjacentHTML("beforeend", `<button id="askBtn" class="sm primary" onclick="chatToggle()"></button>`);
  document.body.insertAdjacentHTML("beforeend", `<div id="chat" class="chat">
    <div class="chat-head"><b id="chatTitle"></b><span class="sp"></span><button class="sm" onclick="chatClear()" id="chatClearBtn"></button><button class="sm" onclick="chatToggle()">×</button></div>
    <div id="chatLog" class="chat-log"></div>
    <div class="chat-in"><textarea id="chatQ" rows="2"></textarea><button class="primary" id="chatSend" onclick="chatSend()"></button></div></div>`);
  cfg().then(c => { if (c.demo) hdr.querySelector(".brand").insertAdjacentHTML("beforeend",
    `<span class="tag warn" title="synthetic demo data">${T("演示数据（合成）", "demo data (synthetic)")}</span>`); }).catch(() => {});
  $("#chatQ").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatSend(); } });
  chatLabels();
}

function chatLabels() {
  if (!document.getElementById("askBtn")) return;
  $("#askBtn").textContent = T("问 wangsiEDA", "Ask wangsiEDA");
  $("#chatTitle").textContent = T("问 wangsiEDA", "Ask wangsiEDA");
  $("#chatClearBtn").textContent = T("清空", "Clear");
  $("#chatSend").textContent = T("发送", "Send");
  $("#chatQ").placeholder = T("例如：bp_fe 现在最好的认证结果是哪个？下一步该评测哪个程序？", "e.g. What is the best certified result on bp_fe? Which program should I evaluate next?");
  if (!$("#chatLog").children.length) chatHello();
}

async function chatHello() {
  let ready = true;
  try { await cfg(); ready = !!(CFG.features && CFG.features.llm && CFG.features.llm.ok); } catch (e) { /* offline */ }
  $("#chatLog").innerHTML = `<div class="msg sys">${ready
    ? T("我只读数据来回答，答案里的数字都来自工具查询。需要评测时我会给出建议，由你点按钮提交。",
        "I answer from read-only queries; every number comes from a tool. If an evaluation would help I suggest one, and you submit it with a click.")
    : T("大模型还没有配置（见“开始使用”页或 wangsictl doctor），暂时不能提问。", "The model is not configured yet (see Getting started or wangsictl doctor).")}</div>`;
}

function chatToggle() { $("#chat").classList.toggle("open"); if ($("#chat").classList.contains("open")) $("#chatQ").focus(); }
function chatClear() { CHAT.history = []; $("#chatLog").innerHTML = ""; chatHello(); }
function chatAdd(cls, html) { const d = document.createElement("div"); d.className = "msg " + cls; d.innerHTML = html; $("#chatLog").appendChild(d); $("#chatLog").scrollTop = 1e9; return d; }

async function chatSend() {
  const q = $("#chatQ").value.trim();
  if (!q || CHAT.busy) return;
  $("#chatQ").value = "";
  chatAdd("user", esc(q));
  const live = chatAdd("bot pending", `<div class="trace"></div><div class="mu">${T("思考中…", "thinking…")}</div>`);
  CHAT.busy = true; $("#chatSend").disabled = true;
  let jid;
  try {
    jid = (await post("/api/jobs", {kind: "ask", params: {question: q, history: CHAT.history.slice(-6)}, title: "ask: " + q.slice(0, 60)})).job_id;
  } catch (e) { live.className = "msg bot err"; live.innerHTML = esc(e.message); CHAT.busy = false; $("#chatSend").disabled = false; return; }
  const tr = live.querySelector(".trace");
  const done = (html, cls) => { live.className = "msg bot " + (cls || ""); live.innerHTML = html; CHAT.busy = false; $("#chatSend").disabled = false; if (CHAT.es) { CHAT.es.close(); CHAT.es = null; } $("#chatLog").scrollTop = 1e9; };
  CHAT.es = new EventSource(`/api/stream?job=${jid}&token=${encodeURIComponent(TOKEN)}`);
  CHAT.es.onmessage = m => {
    const e = JSON.parse(m.data), p = JSON.parse(e.payload_json || "{}");
    if (e.type === "tool_call" && e.agent === "assistant")
      tr.insertAdjacentHTML("beforeend", `<div class="tc">↳ ${esc(p.tool)} <span class="mu">${esc(JSON.stringify(p.args || {}))}</span></div>`);
    else if (e.type === "answer") {
      CHAT.history.push({role: "user", content: q}, {role: "assistant", content: p.answer});
      let h = chatMd(p.answer);
      if (p.proposed_job) h += proposalCard(p.proposed_job);
      else if (p.proposal_rejected) h += `<div class="mu small">${T("（建议的作业未通过校验：", "(suggested job rejected: ")}${esc(p.proposal_rejected)})</div>`;
      const used = tr.innerHTML ? `<details class="small mu"><summary>${T("查询了 ", "queried ")}${tr.children.length}${T(" 次", " tools")}</summary>${tr.innerHTML}</details>` : "";
      h += `<div class="mu small">${used}<a href="#/job/${jid}">${T("查看完整过程", "full trace")}</a></div>`;
      done(h);
    } else if (e.type === "step_failed" || (e.type === "job_end" && p.status === "failed")) {
      done(`${T("没能回答：", "Could not answer: ")}${esc(p.error || (p.summary || {}).failed || "")} <a href="#/job/${jid}">${T("详情", "details")}</a>`, "err");
    }
  };
}

function proposalCard(j) {
  const id = "pj" + Math.random().toString(36).slice(2, 8);
  window[id] = j;
  const kindName = {eco_eval: T("ECO 单程序评测", "ECO evaluation"), eco_closure: "Agentic ECO closure", block_flow: "Block RTL→GDS"}[j.kind] || j.kind;
  return `<div class="proposal"><div><b>${T("建议作业", "Suggested job")}</b> · ${esc(kindName)}</div>
    <div class="mono small">${esc(JSON.stringify(j.params))}</div>${j.why ? `<div class="small">${esc(j.why)}</div>` : ""}
    <div class="row"><button class="primary sm" onclick="chatSubmit('${id}', this)">${T("提交这个作业", "Submit this job")}</button>
    <span class="mu small">${T("提交前不会运行任何东西", "nothing runs until you submit")}</span></div></div>`;
}

async function chatSubmit(id, btn) {
  const j = window[id];
  if (!j || !confirm(T("确认提交：", "Submit: ") + j.kind + " " + JSON.stringify(j.params))) return;
  btn.disabled = true;
  try {
    const r = await post("/api/jobs", {kind: j.kind, params: j.params, title: `${j.kind} (from chat)`});
    btn.outerHTML = `<a href="#/job/${r.job_id}">${T("已提交，查看作业 ", "submitted, open job ")}${r.job_id}</a>`;
  } catch (e) { btn.disabled = false; alert(e.message); }
}

window.addEventListener("load", () => setTimeout(chatInit, 0));
