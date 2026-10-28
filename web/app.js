// User chat page.

const state = { me: null, messages: [], lastId: 0, run: null, attachments: [], es: null, timer: null, spend: null, spendTimer: null };
const REPORT_FILE = { injection: "0_injection.md", prompt: "1_prompt.md", oe: "2_oe.md", rubric: "3_rubric.md", hardness: "1a_hardness.md" };

async function boot() {
  try {
    state.me = await api("/api/me");
  } catch (e) {
    return e.body && e.body.disabled ? undefined : showLogin();
  }
  showApp();
}

// An admin disabled this contributor: every call answers 403 with disabled=true (api() calls this).
window.onDisabled = body => {
  if (state.es) state.es.close();
  clearInterval(state.timer); clearInterval(state.spendTimer);
  document.querySelectorAll(".overlay").forEach(o => o.remove());
  $("#app").classList.add("hidden");
  $("#login").classList.add("hidden");
  $("#disabled").classList.remove("hidden");
  $("#disabled-text").textContent = body.detail;
};
$("#disabled-out").onclick = async () => { await api("/api/logout", { method: "POST" }); location.reload(); };

// An admin blocked or unblocked this chat, or disabled this contributor: re-check now.
async function checkAccess() {
  try { state.me = await api("/api/me"); renderSide(); loadHistory(); } catch (e) { /* onDisabled handles a disable */ }
}

function showLogin(message) {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  const err = $("#login-error");
  err.classList.toggle("hidden", !message);
  err.textContent = message || "";
  const params = new URLSearchParams(location.search);
  if (params.get("attempt")) $("#attempt").value = params.get("attempt");
  $("#login-back").classList.toggle("hidden", !state.me);  // already signed in: opening another attempt
}

$("#login-back").onclick = e => { e.preventDefault(); showApp(); };
$("#open-other").onclick = () => {
  $("#identifier").value = state.me.admin || state.me.email || "";
  $("#attempt").value = "";
  $("#pw-field").classList.add("hidden");
  showLogin();
  $("#attempt").focus();
};

$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const btn = $("#login-btn");
  btn.disabled = true; btn.textContent = "Checking…";
  try {
    await api("/api/login", { json: { identifier: $("#identifier").value, attempt_id: $("#attempt").value,
                                      password: $("#admin-pw").value } });
    state.me = await api("/api/me");
    showApp();
  } catch (err) {
    showLogin(err.message);
    if (err.body && err.body.need_password) {
      $("#pw-field").classList.remove("hidden");
      $("#admin-pw").focus();
    }
  } finally {
    btn.disabled = false; btn.textContent = "Continue";
  }
});

$("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }); location.reload(); };
// ☰: on a phone it opens the sidebar over the chat; on a wider screen it hides or shows it (remembered).
const narrow = () => window.matchMedia("(max-width: 820px)").matches;
if (localStorage.getItem("evp_side") === "hidden") document.body.classList.add("side-hidden");
$("#menu").onclick = () => {
  if (narrow()) return $("#side").classList.toggle("open");
  const hidden = document.body.classList.toggle("side-hidden");
  localStorage.setItem("evp_side", hidden ? "hidden" : "shown");
};

async function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#chip-attempt").textContent = "attempt " + state.me.attempt_id + (state.me.admin ? " · admin" : "");
  // Task IDs are for admins only: the server sends none to contributors.
  $("#chip-task").textContent = state.me.task_id ? "task " + state.me.task_id : "";
  $("#chip-task").classList.toggle("hidden", !state.me.task_id);
  $("#view-switch").classList.toggle("hidden", !state.me.admin);
  document.querySelector('#view-switch [data-view="cost"]').classList.toggle("hidden", !state.me.cost_access);  // owners only
  renderSide();
  loadHistory();
  state.messages = await api("/api/messages");
  state.lastId = state.messages.length ? state.messages[state.messages.length - 1].id : 0;
  renderMessages();
  setRun(state.me.active_run);
  connect();
  checkBroadcasts();
}

// ---- broadcasts: admin messages that stay over the chat until the contributor acknowledges them ----
function broadcastHtml(text) {  // line breaks kept, unlike chat messages
  const html = window.marked ? marked.parse(String(text ?? ""), { breaks: true, gfm: true }) : esc(text);
  return window.DOMPurify ? DOMPurify.sanitize(html) : esc(text);
}

async function checkBroadcasts() {
  if (!state.me) return;
  let res;
  try { res = await api("/api/broadcasts"); } catch (e) { return; }
  if (state.me.admin) return showWaiting(res.waiting || []);  // admins never get the pop-up, only a note
  // Oldest on top: each new overlay covers the ones added before it.
  for (const b of [...res.broadcasts].reverse()) showBroadcast(b);
}

function showWaiting(list) {
  const chip = $("#chip-broadcast");
  chip.classList.toggle("hidden", !list.length);
  chip.textContent = `📣 ${list.length} broadcast${list.length === 1 ? "" : "s"} not yet acknowledged`;
  chip.title = "Waiting for this contributor to press Acknowledge (you can't acknowledge for them):\n"
    + list.map(b => `• ${b.title} (${fmtTime(b.created_at)})`).join("\n");
}

function showBroadcast(b) {
  if (document.querySelector(`.overlay[data-broadcast="${b.id}"]`)) return;
  const wrap = document.createElement("div");
  wrap.className = "overlay broadcast";
  wrap.dataset.broadcast = b.id;
  wrap.innerHTML = `<div class="modal dialog" role="alertdialog" aria-modal="true" aria-labelledby="bc-${b.id}">
      <header><div class="grow" id="bc-${b.id}">📣 ${esc(b.title)}</div></header>
      <div class="content bubble broadcast-body">${broadcastHtml(b.body)}</div>
      <div class="dialog-actions"><span class="muted grow" style="font-size:12px">From the admins · ${esc(fmtTime(b.created_at))}</span>
        <button class="primary" data-ack>Acknowledge</button></div></div>`;
  const btn = wrap.querySelector("[data-ack]");
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      await api(`/api/broadcasts/${b.id}/ack`, { method: "POST" });
      wrap.remove();
    } catch (err) {
      btn.disabled = false;
      uiAlert("Couldn't save that", err.message);
    }
  };
  // No close button, no click-outside, no Esc: only Acknowledge removes it.
  document.body.appendChild(wrap);
  btn.focus();
}

// After a deploy the reconnect brings a new build: reload so the tab never runs old code. Not while the
// contributor has a draft, attachments or a dialog open; then a note asks them to reload when ready.
function portalUpdated() {
  const busy = input.value.trim() || state.attachments.length || document.querySelector(".overlay:not(.broadcast)");
  if (!busy) return location.reload();
  const chip = $("#chip-broadcast");
  chip.classList.remove("hidden");
  chip.textContent = "🔄 The portal was updated: click to reload";
  chip.title = "Your message draft stays in the box until you reload; copy it first if you need it.";
  chip.style.cursor = "pointer";
  chip.onclick = () => location.reload();
}

// ---- live updates ----
function connect() {
  if (state.es) state.es.close();
  const es = new EventSource("/api/events");
  state.es = es;
  es.addEventListener("hello", async ev => {
    const p = JSON.parse(ev.data);
    if (p.data.build && state.build && p.data.build !== state.build) return portalUpdated();
    state.build = p.data.build || state.build;
    await catchUp();
    setRun(p.data.active_run);
    checkBroadcasts();  // anything sent while this tab was disconnected
  });
  es.addEventListener("broadcast", () => checkBroadcasts());
  es.addEventListener("message", ev => addMessage(JSON.parse(ev.data).data));
  es.addEventListener("activity", ev => addActivity(JSON.parse(ev.data).data));
  es.addEventListener("run", ev => onRun(JSON.parse(ev.data).data));
  es.addEventListener("proof", ev => showProof(JSON.parse(ev.data).data));
  es.addEventListener("spend", ev => renderSpend(JSON.parse(ev.data).data));
  es.addEventListener("access", () => checkAccess());
  es.onerror = () => {
    es.close();
    setTimeout(async () => {
      try { await api("/api/me"); connect(); } catch (e) {
        if (e.body && e.body.disabled) return;
        if (e.status === 401) showLogin("Your session ended. Sign in again."); else setTimeout(connect, 5000);
      }
    }, 3000);
  };
}

async function catchUp() {
  const fresh = await api(`/api/messages?after=${state.lastId}`);
  fresh.forEach(addMessage);
}

function addMessage(m) {
  if (!m || state.messages.some(x => x.id === m.id)) return;
  state.messages.push(m);
  state.lastId = Math.max(state.lastId, m.id);
  const box = $("#messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 200;
  box.insertAdjacentHTML("beforeend", messageHtml(m));
  if (nearBottom || m.role === "user") box.scrollTop = box.scrollHeight;
  if (["report", "proof", "assistant"].includes(m.role) || (m.meta && m.meta.stale_notice)) refreshSide();
}

function renderMessages() {
  const box = $("#messages");
  box.innerHTML = state.messages.map(m => messageHtml(m)).join("");
  box.scrollTop = box.scrollHeight;
}

wireMessageClicks($("#messages"), id => state.messages.find(m => m.id === id),
  m => `${state.me.attempt_id}_${REPORT_FILE[m.meta.stage] || "report.md"}`);

// ---- run status ----
function setRun(run) {
  state.run = run && ["queued", "running"].includes(run.status) ? run : null;
  const bar = $("#runbar");
  clearInterval(state.timer);
  clearInterval(state.spendTimer);
  if (!state.run) { bar.classList.add("hidden"); $("#activity").innerHTML = ""; updateSend(); return; }
  state.spendTimer = setInterval(pollSpend, 20000);  // the spend bar counts the running review as it goes
  bar.classList.remove("hidden");
  $("#activity").innerHTML = "";
  (run.activity || []).forEach(addActivity);
  renderRunTitle();
  state.timer = setInterval(renderRunTitle, 1000);
  updateSend();
}

// Estimated progress of a running review. A review doesn't report its own progress, so this uses the
// step it has reached (save inputs, main review, recheck passes, fix-check passes, report check) and
// how far through past runs of the same command each step started: the % moves with the clock but
// never runs past the next step until that step starts, and jumps ahead when a run is quicker.
const STEP_LABELS = { inputs: "Reviewing", gate: "Reviewing", recheck: "Deep recheck passes", fixcheck: "Fix-check passes",
  final: "Finishing the report" };
function estimate(r) {
  const cal = r.progress;
  if (!cal || r.status !== "running" || !r.started_at || !cal.minutes) return null;
  // An answer to a paused review may just get a reply: show a % only once it actually resumes a step.
  if (r.continuation && !Object.keys(r.milestones || {}).length) return null;
  const elapsed = Date.now() / 1000 - r.started_at, typical = cal.minutes * 60;
  const marks = cal.marks || {}, reached = r.milestones || {};
  const order = Object.keys(marks).sort((a, b) => marks[a] - marks[b]);
  let at = -1;
  order.forEach((step, i) => { if (reached[step]) at = i; });
  const floor = at >= 0 ? marks[order[at]] : 0;
  const cap = Math.max(floor, at + 1 < order.length ? marks[order[at + 1]] - 0.01 : 0.97);
  const p = Math.max(Math.min(Math.max(elapsed / typical, floor), cap), r.shownPct || 0);
  r.shownPct = p;  // never goes backwards
  const step = at < 0 ? null : order[at];
  const label = !step ? "Starting" : step === "gate" && r.command === "injection" ? "Running the injection gate" : STEP_LABELS[step];
  // Ahead of the usual pace (a step started early): project from this run's own pace. Otherwise
  // assume the usual pace for the rest; a step running well past its usual length says so instead.
  const ahead = floor > elapsed / typical;
  const left = ahead && p >= 0.15 ? elapsed * (1 - p) / p : (1 - p) * typical;
  const overrun = !ahead && p >= cap - 0.001 && elapsed > Math.max(cap * typical * 1.5, 180);
  return { pct: Math.min(99, Math.round(p * 100)), label, elapsed,
           left: step === "final" ? "finishing up" : overrun ? "this step is taking longer than usual"
             : `about ${Math.max(1, Math.round(left / 60))} min left` };
}

function renderRunTitle() {
  const r = state.run;
  if (!r) return;
  const label = r.command ? `/${r.command}` : "Follow-up";
  const stopping = r.error === "cancelled by user";
  $("#run-cancel").disabled = stopping;
  $("#run-cancel").textContent = stopping ? "Stopping…" : "Cancel";
  const est = !stopping && r.kind !== "followup" ? estimate(r) : null;
  const bar = $("#run-progress");
  bar.classList.toggle("hidden", !est);
  if (stopping) {
    $("#run-title").textContent = `${label} stopping…`;
    $("#run-elapsed").textContent = "";
  } else if (r.status === "queued") {
    $("#run-title").textContent = `${label} waiting in line`;
    $("#run-elapsed").textContent = r.position ? `#${r.position} in queue` : "";
  } else if (est) {
    $("#run-title").textContent = `${label} running · ${est.pct}%`;
    $("#run-elapsed").textContent = `${est.label} · ${est.left}`;
    $("#run-elapsed").title = `Running for ${fmtDur(est.elapsed)}. The % is an estimate from how long past ${label} reviews took at each step.`;
    $("#run-progress-fill").style.width = est.pct + "%";
    bar.setAttribute("aria-valuenow", String(est.pct));
    bar.setAttribute("aria-valuetext", `${est.pct}%, ${est.label}, ${est.left}`);
  } else {
    $("#run-title").textContent = `${label} running`;
    $("#run-elapsed").textContent = r.started_at ? fmtDur(Date.now() / 1000 - r.started_at) : "";
    $("#run-elapsed").title = "";
  }
}

// ---- "report ready" notification ----
// A browser notification when a review ends while the tab isn't in front (needs the page open in some
// tab), plus a marker in the tab title, which also works where notifications are blocked.
const BASE_TITLE = document.title;
const unfocused = () => document.hidden || !document.hasFocus();
window.addEventListener("focus", () => { document.title = BASE_TITLE; });
document.addEventListener("visibilitychange", () => { if (!document.hidden) document.title = BASE_TITLE; });
function notifyRunEnd(run) {
  if (run.kind === "followup" || !run.command || !["done", "failed"].includes(run.status)) return;
  const reports = state.messages.filter(m => m.role === "report" && m.meta && m.meta.run_id === run.id);
  const report = reports.find(m => m.meta.stage === run.command) || reports[reports.length - 1];
  const name = "/" + run.command;
  let title, body;
  if (run.status === "failed") {
    title = `Your ${name} review stopped`;
    body = "It didn't finish. Open the chat for details; your saved inputs are kept.";
  } else if (report) {
    // OE needs a PASS (server config.passing); other reviews also pass on NON-FAIL
    const v = report.meta.verdict, passed = (report.meta.stage === "oe" ? v === "PASS" : ["PASS", "NON-FAIL"].includes(v))
      && report.meta.exec_status === "COMPLETE";
    title = `Your ${name} report is ready`;
    body = `${v || "No verdict"}${report.meta.score != null ? " · score " + report.meta.score : ""}${passed ? " · your pass card is ready" : ""}`;
  } else {
    title = `Your ${name} review replied`;
    body = "It didn't write a new report. Open the chat to see its reply; it may be asking you something.";
  }
  if (!unfocused()) return;
  document.title = "● " + title;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  try {
    const n = new Notification(title, { body, tag: "evp-" + run.id });  // the tag stops other open tabs repeating it
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) { /* some browsers only allow notifications from a service worker */ }
}

function onRun(data) {
  if (!state.run || state.run.id !== data.id) {
    if (["queued", "running"].includes(data.status)) setRun({ ...data, activity: [] });
    return;
  }
  Object.assign(state.run, data);
  if (data.status === "running" && !state.run.started_at) state.run.started_at = Date.now() / 1000;
  if (!["queued", "running"].includes(data.status)) { notifyRunEnd(state.run); setRun(null); refreshSide(); }
  else renderRunTitle();
}

function addActivity(a) {
  if (!a) return;
  const box = $("#activity");
  const time = new Date(a.t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  box.insertAdjacentText("beforeend", `${time}  ${a.line}\n`);
  box.scrollTop = box.scrollHeight;
  $("#run-latest").textContent = a.line;
}

$("#run-toggle").onclick = () => $("#activity").classList.toggle("hidden");
$("#run-cancel").onclick = async () => {
  if (!state.run) return;
  const yes = await uiDialog({ title: "Cancel this run?", html: `<p style="margin:0">It stops now. Your saved inputs are kept, and you can start it again later.</p>`,
    ok: "Cancel run", cancel: "Keep running", danger: true });
  if (!yes || !state.run) return;
  try {
    const res = await api(`/api/runs/${state.run.id}/cancel`, { method: "POST" });
    if (res.ok && state.run) { state.run.error = "cancelled by user"; renderRunTitle(); }
  } catch (e) {
    uiAlert("Couldn't cancel", e.message || "Please try again, or type /admin if it keeps happening.");
  }
};

// ---- sidebar ----
async function refreshSide() {
  try { state.me = await api("/api/me"); renderSide(); } catch (e) { /* ignore */ }
}

// The task date (the eval reads one of two universes, picked per task): a chip in the top bar, asked with two buttons.
function renderDate() {
  const u = state.me.universe || {}, chip = $("#chip-date");
  chip.classList.toggle("hidden", !u.asks);
  if (!u.asks) return;
  chip.classList.toggle("flag", !u.date);
  chip.textContent = u.date ? `📅 ${u.choices[u.date]}` : "📅 Pick the task date";
}
function pickDate(why = "") {
  const u = state.me.universe || {};
  return new Promise(resolve => {
    const w = openModal("Which date is this task on?", `${why ? `<p style="margin-top:0">${why}</p>` : ""}
      <p${why ? "" : ' style="margin-top:0"'}>Each task is on one of two dates, and every review reads that date's data. Pick the one your task was
        assigned. You can change it later, but reviews done before the change were on the old date.</p>
      <div class="date-pick">${Object.entries(u.choices || {}).map(([k, label]) =>
        `<button type="button" class="${u.date === k ? "primary" : ""}" data-date="${esc(k)}">${esc(label)}</button>`).join("")}</div>
      <p class="muted" style="margin-bottom:0">There is no default: the review won't start until it's picked.</p>`);
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); } };
    new MutationObserver((_, obs) => { if (!w.isConnected) { obs.disconnect(); finish(null); } }).observe(document.body, { childList: true });
    w.addEventListener("click", async e => {
      const b = e.target.closest("[data-date]");
      if (!b) return;
      w.querySelectorAll("[data-date]").forEach(x => { x.disabled = true; });
      try {
        const res = await api("/api/universe", { json: { date: b.dataset.date } });
        state.me.universe = res.universe;
        renderDate();
        w.remove();
        finish(res.universe.date);
      } catch (err) {
        w.querySelectorAll("[data-date]").forEach(x => { x.disabled = false; });
        uiAlert("Couldn't save the date", err.message);
      }
    });
  });
}
$("#chip-date").onclick = () => { if (state.run) return uiAlert("A run is in progress", "Change the date after it finishes."); pickDate(); };

// Pass cards already in the chat were drawn with the outdated state of that moment; redraw any whose state changed
// (an input saved since makes an earlier pass outdated; a newer input can make it current again).
function syncProofCards(proofs) {
  const byCode = Object.fromEntries((proofs || []).map(p => [p.code, p.outdated || null]));
  for (const m of state.messages) {
    if (m.role !== "proof" || !m.meta || !(m.meta.code in byCode)) continue;
    const now = byCode[m.meta.code];
    if (JSON.stringify(m.meta.outdated || null) === JSON.stringify(now)) continue;
    m.meta.outdated = now;
    const el = document.querySelector(`#messages [data-proof="${m.id}"]`)?.closest(".msg");
    if (el) el.outerHTML = messageHtml(m);
  }
}

function renderSide() {
  const me = state.me;
  renderDate();
  renderSpend(me.spend);
  renderBlocked();
  $("#limits").textContent = me.limits.reviews_left == null ? "" : `${me.limits.reviews_left} reviews left today`;
  $("#input").placeholder = me.blocked && !me.admin ? "This chat is blocked. Type /admin <message> to reach an admin."
    : me.pending_review ? `Your /${me.pending_review} review is waiting for your answer. Reply here to continue it.`
    : "Paste inputs, ask a question, or type a command like /prompt";
  const labels = { injection: "Injection", prompt: "Prompt", oe: "Oracle Events", rubric: "Rubric", hardness: "Hardness" };
  $("#reports").innerHTML = me.reports.length ? me.reports.map(r =>
    `<div class="list-item" data-rep="${esc(r.file)}"><span class="grow">${esc(labels[r.stage] || r.stage)}</span>${pill(r.verdict)}</div>`).join("") : "None yet";
  $("#proofs").innerHTML = me.proofs.length ? me.proofs.map((p, i) =>
    `<div class="list-item" data-pr="${i}" title="${esc(p.outdated ? p.outdated.message : p.fingerprint || p.code)}"><span class="grow">/${esc(p.stage)} · <span class="mono">${p.fingerprint ? "🔏 " + esc(shortFp(p.fingerprint)) : esc(p.code)}</span></span>${pill(p.outdated ? "OUTDATED" : p.verdict)}</div>`).join("") : "None yet";
  syncProofCards(me.proofs);
  const size = n => (n == null ? "" : n < 1024 ? `${n} B` : `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`);
  $("#inputs").innerHTML = me.inputs.length ? me.inputs.map(i => {
    const bytes = i.files.reduce((t, f) => t + (f.bytes || 0), 0);
    return `<div class="list-item" data-input="${esc(i.key)}" title="Click to see what was saved">
      <span class="grow">${esc(INPUT_LABELS[i.key] || i.key)}</span><span class="muted" style="font-size:12px">${esc(size(bytes))}</span></div>`;
  }).join("") : "None yet";
}

async function loadHistory() {
  let list;
  try { list = await api("/api/history"); } catch (e) { return; }
  state.history = list.map(c => ({ ...c, last_cmd: c.last_review && c.last_review.command, last_verdict: c.last_review && c.last_review.verdict }));
  const god = !!state.me.admin;  // admins see every contributor's chat, with sorting and filters
  $("#history-title").textContent = god ? `All chats · admin (${state.history.length})` : "Your chats";
  $("#history-tools").classList.toggle("hidden", !god);
  if (god && !state.historyTools) state.historyTools = chatListTools($("#history-tools"), renderHistory);
  renderHistory();
}

function renderHistory() {
  const god = !!state.me.admin;
  const list = state.history || [];
  const shown = god && state.historyTools ? state.historyTools.apply(list) : list;
  const box = $("#history");
  box.classList.toggle("god", god);
  box.innerHTML = shown.map(c => chatRowHtml(c, { attr: "data-switch", current: c.attempt_id === state.me.attempt_id, god })).join("")
    || `<div class="muted">No matching chats.</div>`;
}
// Keep "5m ago" current; an admin's list also picks up new chats and runs.
setInterval(() => { if (state.me && state.history) state.me.admin ? loadHistory() : renderHistory(); }, 60000);

// Sidebar sections fold up (remembered per browser).
function sideFolded() { try { return JSON.parse(localStorage.getItem("evp_side_folded") || "[]"); } catch (e) { return []; } }
document.querySelectorAll(".side-sec").forEach(sec => {
  const toggle = sec.querySelector(".sec-toggle");
  const set = folded => { sec.classList.toggle("folded", folded); toggle.setAttribute("aria-expanded", String(!folded)); };
  set(sideFolded().includes(sec.dataset.sec));
  toggle.onclick = () => {
    const folded = !sec.classList.contains("folded");
    set(folded);
    const keep = sideFolded().filter(k => k !== sec.dataset.sec).concat(folded ? [sec.dataset.sec] : []);
    try { localStorage.setItem("evp_side_folded", JSON.stringify(keep)); } catch (e) { /* private window */ }
  };
});

// Open another chat in place: no page reload, the sidebar keeps its scroll, and each chat keeps its own unsent draft.
const drafts = {};
async function switchTo(attemptId) {
  if (state.switching || attemptId === state.me.attempt_id) return;
  state.switching = true;
  drafts[state.me.attempt_id] = $("#input").value;
  document.querySelectorAll("#history [data-switch]").forEach(r => r.classList.toggle("current", r.dataset.switch === attemptId));
  const pane = $("#messages");
  pane.classList.add("switching");
  try {
    await api("/api/switch", { json: { attempt_id: attemptId } });
    state.me = await api("/api/me");
  } catch (err) {
    pane.classList.remove("switching");
    state.switching = false;
    renderHistory();
    return uiAlert("Couldn't open that chat", err.message);
  }
  if (state.es) state.es.close();
  state.messages = []; state.lastId = 0; state.attachments = [];
  renderAttachments();
  $("#input").value = drafts[attemptId] || "";
  autosize();
  document.querySelectorAll(".overlay").forEach(o => o.remove());
  if (narrow()) $("#side").classList.remove("open");
  try { await showApp(); } finally {
    requestAnimationFrame(() => pane.classList.remove("switching"));
    state.switching = false;
  }
}

const INPUT_LABELS = { universe: "Task date", "task-details": "Task details", prompt: "Prompt", injection: "Injection SQL", "error-async": "Error / async config",
  oe: "Oracle Events", rubric: "Rubric", "dynamic-context": "Dynamic context", "human-time": "Human time",
  opus: "Opus run (trajectory)", haiku: "Haiku run (trajectory)" };

async function openInput(key) {
  let data;
  try { data = await api(`/api/inputs/${encodeURIComponent(key)}`); } catch (err) { return uiAlert("Couldn't open that input", err.message); }
  const body = data.files.map(f => `<div class="muted mono" style="font-size:12px;margin:0 0 6px">${esc(f.path || "")}
      · ${fmtInt(f.bytes)} bytes${f.truncated ? " · showing the first 15 MB (Download gets the whole file)" : ""}</div>
    ${f.missing ? `<p class="muted">This file isn't there any more.</p>` : f.binary ? `<p class="muted">Binary file (not shown).</p>`
      : `<pre class="input-view">${esc(f.text)}</pre>`}`).join("");
  const one = data.files.length === 1 && data.files[0].text != null ? data.files[0] : null;
  const w = openModal(`Saved input: ${INPUT_LABELS[key] || key}`,
    `<p class="muted" style="margin-top:0">This is exactly what the eval will use. To change it, paste or attach a new version in the chat; it replaces this one.</p>${body}`,
    one ? `${one.truncated ? "" : `<button data-copy>Copy all</button>`}<button data-dl>Download</button>` : "");
  if (!one) return;
  // The whole file from the server, byte for byte (the view above stops at 15 MB).
  w.querySelector("[data-dl]").onclick = () => {
    const a = document.createElement("a");
    a.href = `/api/inputs/${encodeURIComponent(key)}/file?i=0`;
    a.download = one.path.split("/").pop();
    a.click();
  };
  const copy = w.querySelector("[data-copy]");
  if (copy) copy.onclick = async () => {
    try { await navigator.clipboard.writeText(one.text); copy.textContent = "Copied ✓"; }
    catch (err) { uiAlert("Couldn't copy", "Your browser blocked the clipboard. Use Download instead."); }
  };
}

// ---- blocked chat / disabled contributor ----
function renderBlocked() {
  const me = state.me, note = $("#blocked-note");
  const reason = b => (b && b.reason ? ` Reason: ${esc(b.reason)}.` : "");
  let html = "";
  if (me.admin) {  // admins keep full access; tell them what the owner sees
    if (me.disabled) html += `<div>🚫 This contributor is <b>disabled</b>: they can't sign in or use any chat.${reason(me.disabled)}</div>`;
    if (me.blocked) html += `<div>🚫 This chat is <b>blocked</b> for its owner (read-only for them). You can still use it as admin.${reason(me.blocked)}</div>`;
  } else if (me.blocked) {
    html = `🚫 <b>An admin blocked this chat.</b> You can still read it, but you can't send messages or start reviews here.${reason(me.blocked)}
      Type <b class="mono">/admin &lt;message&gt;</b> to reach an admin.`;
  }
  note.innerHTML = html;
  note.classList.toggle("hidden", !html);
  note.classList.toggle("admin-view", !!me.admin);
  $("#attach").disabled = !!(me.blocked && !me.admin);
}

// ---- spend bar ----
const money = v => "$" + Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const limitText = () => "$" + Number(state.spend.limit).toLocaleString("en-US", { maximumFractionDigits: 2 });

function spendRuleHtml() {
  return `Each attempt can spend up to <b>${limitText()}</b> on reviews and follow-ups. Every new attempt starts again at $0.
    The bar under the top bar shows how much this attempt has used; it counts a running review as it goes. If an attempt goes
    over ${limitText()}, your account is <b>flagged</b>: you'll be warned and your account may be <b>disabled</b>. Most reviews
    cost about $2–25, so fix everything the last report raised before you run a review again.`;
}

function renderSpend(sp) {
  if (sp) state.spend = sp;
  sp = state.spend;
  const on = !!(sp && sp.limit);
  $("#spendbar").classList.toggle("hidden", !on);
  $("#app").classList.toggle("has-spend", on);
  if (!on) return;
  const admin = !!state.me.admin;  // an admin sees the attempt owner's spend, not their own
  const pct = Math.min(100, (sp.usd / sp.limit) * 100);
  const bar = $("#spendbar");
  bar.classList.toggle("over", sp.over);
  bar.classList.toggle("near", !sp.over && pct >= 80);
  $("#spend-fill").style.width = pct.toFixed(1) + "%";
  $("#spend-who").textContent = admin ? "Contributor's spend" : "This attempt";
  $("#spend-amount").textContent = money(sp.usd);
  $("#spend-of").textContent = "of " + limitText();
  $("#spend-rule").textContent = admin
    ? (sp.over ? `This attempt is over the ${limitText()} limit and is flagged.`
      : "Their runs on this attempt only; each attempt has its own limit. Runs you start as admin are billed to you, not to them.")
    : sp.over ? `This attempt is over the ${limitText()} limit. Your account is flagged: you'll be warned and may be disabled.`
      : pct >= 80 ? `This attempt is close to the ${limitText()} limit. Going over it flags your account: you'll be warned and may be disabled.`
      : `Don't go over ${limitText()} on this attempt (a new attempt starts at $0). If you do, your account is flagged: you'll be warned and may be disabled.`;
}

async function pollSpend() {
  try { renderSpend(await api("/api/spend")); } catch (e) { /* keep the last figure */ }
}

$("#spendbar").onclick = () => {
  const sp = state.spend;
  if (!sp) return;
  uiDialog({ title: state.me.admin ? "Contributor's spend on this attempt" : "This attempt's spend", html: `<p style="margin-top:0"><b style="font-size:20px">${money(sp.usd)}</b>
      <span class="muted">of ${limitText()} · ${sp.runs} run${sp.runs === 1 ? "" : "s"}${sp.running ? " · a run is in progress" : ""}</span></p>
    ${sp.over ? `<div class="error">${state.me.admin ? "This attempt is over the limit and has been flagged."
      : "This attempt is over the limit. Your account has been flagged and the admins have been told."}</div>` : ""}
    <p>${spendRuleHtml()}</p>
    <p class="muted" style="margin-bottom:0">This is what the model calls behind the reviews and follow-ups cost. Questions? Type <b class="mono">/admin</b> with a short note.</p>` });
};

// ---- guide ----
const GUIDE_COMMANDS = [
  ["/injection", "Checks your injection SQL: IDs, structure and chronology against the universe.", "Your injection SQL"],
  ["/prompt", "Checks your task prompt: unique answer, feasibility, persona access, clarity.",
    "The exact task prompt, plus persona, business function, competency, task skill and horizon"],
  ["/hardness", "Checks how hard the task is for Opus, from one unhinted run.",
    "One unhinted run's trajectory JSON (Opus preferred, Haiku as a fallback) and the exact prompt"],
  ["/oe", "Checks your Oracle Events path.", "Your Oracle Events"],
  ["/rubric", "Checks your rubric: criteria, weights and facts.", "Your rubric (JSON)"],
];

function openGuide() {
  const rows = GUIDE_COMMANDS.map(([cmd, what, needs]) => `<div class="guide-cmd">
      <div class="grow"><b class="mono">${cmd}</b> <span>${esc(what)}</span><div class="muted" style="font-size:13px">Needs: ${esc(needs)}</div></div>
      <button data-use="${cmd}">Use</button></div>`).join("");
  const w = openModal("Guide", `<div class="guide">
    <h4>1 · Add your task inputs</h4>
    <p>Paste them into the chat or attach files with 📎. Each one is saved to this attempt and listed under
      <b>Saved inputs</b> in the sidebar; click one to check exactly what was saved. Sending an input again replaces it.</p>
    <h4>2 · Run a review</h4>
    ${rows}
    <p class="muted">A review takes about 20–40 minutes and runs one at a time per attempt. You can close the page; the result is
      saved here. If something is missing, the review asks you for it in the chat.</p>
    <h4>3 · Ask questions</h4>
    <p>Anything you type without a command is answered as a follow-up, for example “why did criterion 4 fail?”.
      Ask for help with any piece of your task: the SQL to inject a message, a rubric row, better wording for one part, feedback on a
      draft, how to fix what a report raised, or ideas for scenarios, traps and edge cases. The one thing it won't do is write the
      whole task for you in one go (a full prompt, rubric, OE set or all the SQL); build it together piece by piece instead.
      Type <b class="mono">/admin</b> and a message to reach a person.</p>
    <h4>4 · What to screenshot</h4>
    <p>When a review passes (<b>PASS</b>, or <b>NON-FAIL</b> with a complete run; for <b>/oe</b> only a <b>PASS</b> counts), a <b>pass screen</b> pops up with a verification
      code. Screenshot that card and upload it in the taxonomy: one for each passing review. Keep the whole card in the shot and
      don't zoom far out. You can also click <b>⬇ Download image</b>: that image is checked with the taxonomy's reader before you get it.</p>
    <p>For the injection, prompt and oracle events, <b>Copy what passed</b> copies the exact text that review graded, so what you
      paste into the taxonomy matches the pass.</p>
    <p>⚠ Before the pass screen opens, you'll be asked to confirm you've put the <b>latest version you fixed</b> into the taxonomy.
      The pass is only for the exact version you sent, so don't forget to update it. If the taxonomy doesn't match, we will know and the task will be flagged.</p>
    <div class="guide-sample proof"><div class="head"><div class="left"><div class="brandline">Original Conference Eval Portal</div>
      <div class="verdict"><span class="vbadge PASS">PASS</span><span>Score 5</span></div><div class="sub">/prompt · Prompt review · COMPLETE</div></div>
      </div>
      <div class="fpband"><div class="k">🔏 Verification code · the Prompt fingerprint, as the taxonomy shows it</div>
      <div class="fp">712 chars - hey  ... anks</div></div></div>
    <p>The <b>🔏 verification code</b> on a pass is the character count and the first and last 4 characters of the exact input
      that review graded, taken from the same fingerprint the taxonomy shows reviewers for that step. If what's in the taxonomy
      is different, they won't match.</p>
    <p>Closed it by accident? Reopen it from <b>Passes</b> in the sidebar, or with <b>Show pass screen</b> in the chat.
      Report cards, chat messages and FAIL results don't count as proof, only the pass screen does.</p>
    ${state.spend && state.spend.limit ? `<h4>5 · Spend limit</h4>
    <p>${spendRuleHtml()}</p>` : ""}
  </div>`);
  w.addEventListener("click", e => {
    const use = e.target.closest("[data-use]");
    if (!use) return;
    const box = $("#input");
    box.value = use.dataset.use + " " + box.value.replace(/^\/\w+\s*/, "");
    w.remove();
    box.focus();
  });
}
$("#guide-btn").onclick = openGuide;

// Admins: jump to /admin with this chat selected (no second sign-in while the admin sign-in is recent).
$("#view-switch").addEventListener("click", async e => {
  const view = e.target.closest("[data-view]")?.dataset.view;
  if (view !== "admin" && view !== "cost") return;
  try { await api("/api/me/admin-page", { method: "POST" }); } catch (err) { /* too old: /admin asks for the password */ }
  location.href = view === "cost" ? "/admin/cost" : "/admin#" + state.me.attempt_id;
});

$("#side").addEventListener("click", async e => {
  const inp = e.target.closest("[data-input]");
  if (inp) return openInput(inp.dataset.input);
  const sw = e.target.closest("[data-switch]");
  if (sw) return switchTo(sw.dataset.switch);
  const cmd = e.target.closest("[data-cmd]");
  if (cmd) { const box = $("#input"); box.value = cmd.dataset.cmd + (cmd.dataset.cmd.endsWith(" ") ? "" : " ") + box.value.replace(/^\/\w+\s*/, ""); box.focus(); return; }
  const rep = e.target.closest("[data-rep]");
  if (rep) {
    const text = await api(`/api/reports/${rep.dataset.rep}`);
    const w = openModal(rep.dataset.rep, `<div class="bubble" style="border:0;padding:0">${md(text)}</div>`, `<button data-dl>Download</button>`);
    w.querySelector("[data-dl]").onclick = () => download(`${state.me.attempt_id}_${rep.dataset.rep}`, text);
    return;
  }
  const pr = e.target.closest("[data-pr]");
  if (pr) showProof(state.me.proofs[+pr.dataset.pr]);
});

// ---- composer ----
const input = $("#input");
input.addEventListener("input", autosize);
input.addEventListener("keydown", e => {
  if (slashKey(e)) return;
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});

// ---- "/" command menu: typing "/" (or "/p", "/ru"…) at the start lists the matching commands ----
const SLASH = [
  ...GUIDE_COMMANDS.map(([cmd, what]) => [cmd, what]),
  ["/admin", "Ask a human admin. Type your message after it."],
  ["/help", "Show the list of commands in the chat."],
];
const slash = { items: [], index: 0 };
function slashMatches() {
  const m = input.value.match(/^\/([a-z]*)$/i);  // only while typing the command word itself
  return m ? SLASH.filter(([cmd]) => cmd.slice(1).startsWith(m[1].toLowerCase())) : [];
}
function renderSlash() {
  slash.items = slashMatches();
  slash.index = Math.min(slash.index, Math.max(0, slash.items.length - 1));
  const box = $("#slash");
  box.classList.toggle("hidden", !slash.items.length);
  box.innerHTML = slash.items.map(([cmd, what], i) => `<div class="slash-item${i === slash.index ? " on" : ""}" data-slash="${i}" role="option">
      <b class="mono">${esc(cmd)}</b><span>${esc(what)}</span></div>`).join("") +
    (slash.items.length ? `<div class="slash-hint">↑↓ to choose · Enter or Tab to pick · Esc to close</div>` : "");
}
function pickSlash(i) {
  const [cmd] = slash.items[i] || [];
  if (!cmd) return;
  input.value = cmd + (cmd === "/help" ? "" : " ");
  slash.items = []; $("#slash").classList.add("hidden");
  input.focus(); autosize(); updateSend();
}
function slashKey(e) {
  if (!slash.items.length || $("#slash").classList.contains("hidden")) return false;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    slash.index = (slash.index + (e.key === "ArrowDown" ? 1 : -1) + slash.items.length) % slash.items.length;
    renderSlash();
    return true;
  }
  if ((e.key === "Enter" && !e.shiftKey && !e.isComposing) || e.key === "Tab") { e.preventDefault(); pickSlash(slash.index); return true; }
  if (e.key === "Escape") { e.preventDefault(); slash.items = []; $("#slash").classList.add("hidden"); return true; }
  return false;
}
input.addEventListener("input", () => { slash.index = 0; renderSlash(); });
input.addEventListener("blur", () => setTimeout(() => $("#slash").classList.add("hidden"), 150));
$("#slash").addEventListener("mousedown", e => {  // mousedown, so the textarea keeps focus
  const item = e.target.closest("[data-slash]");
  if (item) { e.preventDefault(); pickSlash(+item.dataset.slash); }
});
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 260) + "px"; }

$("#attach").onclick = () => $("#file").click();
$("#file").onchange = async e => {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  const form = new FormData();
  files.forEach(f => form.append("files", f));
  try {
    const res = await api("/api/upload", { method: "POST", body: form });
    state.attachments.push(...res.files.map(f => f.name));
    renderAttachments();
  } catch (err) { uiAlert("Upload failed", err.message); }
};

function renderAttachments() {
  $("#attachments").innerHTML = state.attachments.map((n, i) =>
    `<span class="att">📎 ${esc(n)}<button data-rm="${i}" title="Remove">✕</button></span>`).join("");
}
$("#attachments").addEventListener("click", e => {
  if (e.target.dataset.rm) { state.attachments.splice(+e.target.dataset.rm, 1); renderAttachments(); }
});

function updateSend() {
  const busy = !!state.run;
  $("#send").disabled = false;
  $("#send").title = busy ? "A run is in progress; you can still send /admin or /help" : "";
}

async function send() {
  let text = input.value.trim();
  if (!text && !state.attachments.length) return;
  const isMeta = /^\/(admin|help)\b/i.test(text);
  if (state.me.blocked && !state.me.admin && !isMeta) {
    return uiAlert("This chat is blocked", "An admin blocked this chat, so you can't send messages or start reviews here. Type /admin and a message to reach an admin.");
  }
  if (state.run && !isMeta) {
    return uiAlert("A run is in progress", "Wait for it to finish, or cancel it, before sending more. /admin and /help still work.");
  }
  // "Normal /prompt": the command isn't at the start, so it would go as a question. Ask which they meant.
  const inner = !/^\//.test(text) && text.length <= 400 && text.match(/(?:^|\s)\/(injection|prompt|hardness|oe|rubric)\b/i);
  if (inner) {
    const cmd = inner[1].toLowerCase();
    const rest = (text.slice(0, inner.index) + text.slice(inner.index + inner[0].length)).replace(/\s+/g, " ").trim();
    const choice = await uiDialog({ title: `Run the /${cmd} review?`, html: `<p style="margin-top:0">Your message mentions
        <b class="mono">/${cmd}</b>, but a review only starts when the message <b>begins</b> with the command.</p>
      <p style="margin-bottom:0">Run <b class="mono">/${esc(cmd)}</b> now${rest ? `, with “${esc(rest)}” sent along with it` : ""}? Or send your message
        as a question instead.</p>`, ok: `Run /${cmd}`, alt: "Send as a question", cancel: "Cancel" });
    if (!choice) return;
    if (choice === true) { text = `/${cmd}${rest ? " " + rest : ""}`; input.value = text; }
  }
  const review = text.match(/^\/(injection|prompt|hardness|oe|rubric)\b/i);
  const u = state.me.universe || {};
  if (review && u.asks && !u.date && !(await pickDate("Before the first review, pick your task's date."))) return;
  if (review && !(await confirmReview(review[1].toLowerCase(), text))) return;
  if (review && "Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
  $("#send").disabled = true;
  try {
    const res = await api("/api/send", { json: { text, files: state.attachments } });
    input.value = ""; autosize();
    state.attachments = []; renderAttachments();
    if (res.run) setRun(res.run);
    refreshSide();
  } catch (err) {
    uiAlert("Couldn't send that", err.message);
  } finally {
    updateSend();
  }
}

// The start-review dialog: what the review checks, what it needs, what is already saved.
function confirmReview(command, text) {
  const [, what, needs] = GUIDE_COMMANDS.find(c => c[0] === "/" + command) || [];
  const saved = (state.me.inputs || []).map(i => INPUT_LABELS[i.key] || i.key);
  const extra = text.replace(/^\/\w+\s*/, "").trim() || state.attachments.length;
  const minutes = (state.me.review_minutes || {})[command];
  const notifyLine = !("Notification" in window) ? ""
    : Notification.permission === "granted" ? "🔔 You'll get a browser notification when it's done, as long as this page is open in a tab."
    : Notification.permission === "denied" ? "🔕 Notifications are blocked for this site, so the tab title will change when it's done instead."
    : "🔔 Allow notifications when your browser asks, and you'll get one when it's done (keep this page open in a tab).";
  return uiConfirm(`Start the /${command} review?`, `<div class="review-confirm">
      <p>${esc(what || "")}</p>
      <div class="rc-row"><div class="k">Needs</div><div>${esc(needs || "")}</div></div>
      <div class="rc-row"><div class="k">Saved for this attempt</div><div>${saved.length ? saved.map(esc).join(", ")
        : "Nothing yet. The review will ask you for what it needs."}</div></div>
      ${extra ? `<div class="rc-row"><div class="k">Sent with it</div><div>The text${state.attachments.length ? " and files" : ""} in your message</div></div>` : ""}
      ${state.spend && state.spend.limit ? `<div class="rc-row"><div class="k">${state.me.admin ? "Contributor's spend" : "This attempt so far"}</div>
        <div>${money(state.spend.usd)} of ${limitText()}${state.spend.over ? ` <span class="chip flag">over the limit</span>`
          : state.spend.usd >= 0.8 * state.spend.limit ? ` <span class="chip flag">close to the limit</span>` : ""}
          <span class="muted">· most reviews cost about $2–25</span></div></div>` : ""}
      <p class="muted" style="margin-bottom:0">${minutes ? `Usually takes about ${Math.round(minutes)} minutes` : "Takes a while"} and uses this attempt's
        saved inputs. While it runs you'll see an estimated % and time left. You can close the page; the result appears in this chat,
        and if it passes you'll get the pass screen to screenshot.</p>
      ${notifyLine ? `<p class="muted" style="margin:8px 0 0">${notifyLine}</p>` : ""}</div>`, "Start review");
}
$("#send").onclick = send;

boot();
