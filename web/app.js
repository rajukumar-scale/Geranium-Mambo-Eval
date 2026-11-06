// User chat page.

const state = { me: null, messages: [], lastId: 0, run: null, attachments: [], es: null, timer: null, spend: null, spendTimer: null };
const REPORT_FILE = { injection: "0_injection.md", prompt: "1_prompt.md", oe: "2_oe.md", rubric: "3_rubric.md", hardness: "1a_hardness.md" };

async function boot() {
  // Asked alongside /api/me rather than after it: one round trip less over boat's slow link before the chat shows.
  const messages = api("/api/messages").catch(() => null);
  try {
    state.me = await api("/api/me");
  } catch (e) {
    return e.body && e.body.disabled ? undefined : showLogin();
  }
  showApp(messages);
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
// A phone or tablet (no mouse): its keyboard has no Shift+Enter, so Enter adds a new line and the Send button sends.
const touchOnly = () => window.matchMedia("(hover: none) and (pointer: coarse)").matches;
if (localStorage.getItem("evp_side") === "hidden") document.body.classList.add("side-hidden");
$("#menu").onclick = () => {
  if (narrow()) return $("#side").classList.toggle("open");
  const hidden = document.body.classList.toggle("side-hidden");
  localStorage.setItem("evp_side", hidden ? "hidden" : "shown");
};

async function showApp(prefetched = null) {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#chip-attempt").innerHTML = `<span class="chip-lbl">attempt </span>${esc(state.me.attempt_id)}${state.me.admin ? '<span class="chip-lbl"> · admin</span>' : ""}`;
  $("#chip-attempt").title = "attempt " + state.me.attempt_id;
  // Task IDs are for admins only: the server sends none to contributors.
  $("#chip-task").textContent = state.me.task_id ? "task " + state.me.task_id : "";
  $("#chip-task").classList.toggle("hidden", !state.me.task_id);
  $("#view-switch").classList.toggle("hidden", !state.me.admin);
  document.querySelector('#view-switch [data-view="cost"]').classList.toggle("hidden", !state.me.cost_access);  // owners only
  renderSide();
  loadHistory();
  state.messages = (await prefetched) || await api("/api/messages");
  state.lastId = state.messages.length ? state.messages[state.messages.length - 1].id : 0;
  renderMessages();
  setRun(state.me.active_run);
  connect();
  checkBroadcasts();
  showGuideIntro();
  openGuideFromUrl();
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

// ---- the guide pop-up (plan.guide_intro): read Hardness, Tool calls and Failure patterns before the first /prompt ----
// Each guide counts once its tab loads; "I've read all three" works only after all three were opened; "Later" closes it
// until the chat is opened again.
function showGuideIntro() {
  const intro = state.me && state.me.guide_intro;
  if (!intro || document.querySelector(".overlay[data-guide-intro]")) return;
  const wrap = document.createElement("div");
  wrap.className = "overlay broadcast guide-intro";
  wrap.dataset.guideIntro = "1";
  wrap.innerHTML = `<div class="modal dialog" role="alertdialog" aria-modal="true" aria-labelledby="gi-title">
      <header><div class="grow" id="gi-title">📘 Before you start: read the guides</div></header>
      <div class="content"><div class="bubble broadcast-body">${md(intro.text)}</div>
        <div class="gi-list">${intro.guides.map(g => `<button type="button" class="gi-guide${g.read ? " read" : ""}" data-gi="${esc(g.key)}">
          <span class="gi-mark" aria-hidden="true">${g.read ? "✓" : "○"}</span><span class="grow">${esc(g.label)}</span>
          <span class="gi-state">${g.read ? "Opened" : "Open"}</span></button>`).join("")}</div></div>
      <div class="dialog-actions"><span class="muted grow gi-left" style="font-size:12px"></span>
        <button data-gi-later>Later</button><button class="primary" data-gi-ack>I've read all three</button></div></div>`;
  const ack = wrap.querySelector("[data-gi-ack]");
  const refresh = () => {
    const left = intro.guides.filter(g => !g.read).length;
    ack.disabled = left > 0;
    wrap.querySelector(".gi-left").textContent = left ? `${left} still to open` : "All three opened";
  };
  wrap.addEventListener("click", async e => {
    const g = e.target.closest("[data-gi]");
    if (g) {
      const item = intro.guides.find(x => x.key === g.dataset.gi);
      await openGuide(item.key);  // loading the tab records the read on the server
      item.read = true;
      g.classList.add("read");
      g.querySelector(".gi-mark").textContent = "✓";
      g.querySelector(".gi-state").textContent = "Opened";
      return refresh();
    }
    if (e.target.closest("[data-gi-later]")) return wrap.remove();
    if (!e.target.closest("[data-gi-ack]")) return;
    ack.disabled = true;
    try {
      await api("/api/guides/ack", { method: "POST" });
      state.me.guide_intro = null;
      wrap.remove();
    } catch (err) {
      refresh();
      uiAlert("Couldn't save that", err.message);
    }
  });
  refresh();
  // Oldest on top: broadcasts added before it stay underneath; a guide opened from it opens above it.
  document.body.appendChild(wrap);
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

const PAGE_OPENED = Date.now() / 1000;
function addMessage(m) {
  if (!m || state.messages.some(x => x.id === m.id)) return;
  if (m.role === "report" && m.meta && m.meta.stage === "hardness" && (m.meta.injection_missing || m.meta.injection_stale)
      && !state.me.admin && (m.created_at || 0) > PAGE_OPENED)  // new, not old history
    setTimeout(() => showInjectGuideAfterHardness(m.meta.injection_missing ? "missing" : "stale"), 400);
  if (m.role === "system" && m.meta && m.meta.env_guide && !state.me.admin && (m.created_at || 0) > PAGE_OPENED)
    setTimeout(showEnvGuide, 400);  // their /admin request reads like a broken sandbox: the fix steps first
  state.messages.push(m);
  state.lastId = Math.max(state.lastId, m.id);
  const box = $("#messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 200;
  box.insertAdjacentHTML("beforeend", messageHtml(m));
  if (nearBottom || m.role === "user") box.scrollTop = box.scrollHeight;
  const freshStale = m.meta && m.meta.stale_notice && !state.me.admin && (m.created_at || 0) > PAGE_OPENED;
  if (["report", "proof", "assistant"].includes(m.role) || (m.meta && m.meta.stale_notice))
    refreshSide().then(() => { if (freshStale) showOutdatedPopup(); });  // new, not old history: the checklist is current
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
  if (state.me) renderPaused();
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
const FIXCHECK_ROUNDS = 4;  // server/progress.py
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
  // /rubric checks its suggested fixes in up to 4 rounds, and rounds 2+ often start after the first report check:
  // a review in round 3 is not "finishing up" (one ran 48 more minutes after it, 2026-11-01).
  const rounds = Object.keys(reached).filter(k => /^fixcheck_r\d+$/.test(k)).map(k => +k.slice(10));
  const round = rounds.length ? Math.max(...rounds) : reached.fixcheck ? 1 : 0;
  const lateRound = round > 1 && reached.final && reached[`fixcheck_r${round}`] > reached.final;
  const step = at < 0 ? null : lateRound ? "fixcheck" : order[at];
  const label = !step ? "Starting" : step === "gate" && r.command === "injection" ? "Running the injection gate"
    : step === "fixcheck" && round ? `Checking the suggested fixes · round ${round} of up to ${FIXCHECK_ROUNDS}` : STEP_LABELS[step];
  // Ahead of the usual pace (a step started early): project from this run's own pace. Otherwise
  // assume the usual pace for the rest; a step running well past its usual length says so instead.
  const ahead = floor > elapsed / typical;
  const left = ahead && p >= 0.15 ? elapsed * (1 - p) / p : (1 - p) * typical;
  const overrun = !ahead && p >= cap - 0.001 && elapsed > Math.max(cap * typical * 1.5, 180);
  return { pct: Math.min(99, Math.round(p * 100)), label, elapsed,
           left: step === "final" ? "finishing up"
             : lateRound ? (round >= FIXCHECK_ROUNDS ? "last round" : "more rounds may follow")
             : overrun ? "this step is taking longer than usual"
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
    body = `${shownVerdict(report.meta.stage, v) || "No verdict"}${report.meta.score != null ? " · score " + report.meta.score : ""}${passed ? " · your pass card is ready" : ""}`;
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
  loadKnown();  // the known data issues follow the task date
  chip.classList.toggle("hidden", !u.asks);
  if (!u.asks) return;
  chip.classList.toggle("flag", !u.date);
  chip.textContent = u.date ? `📅 ${u.choices[u.date]}` : "📅 Pick the task date";
}

// Known data issues (pilot, KNOWN_ISSUES_PILOT): base data that contradicts itself, for this task's universe.
// Both universes' lists, grouped, until the date is picked. Fetched again only when the chat or the date changes.
async function loadKnown() {
  const key = state.me.attempt_id + "|" + ((state.me.universe || {}).date || "");
  if (state.knownKey === key) return;
  state.knownKey = key;
  let res = null;
  try { res = await api("/api/known-issues"); } catch (e) { /* stays hidden until the chat or date changes */ }
  if (state.knownKey !== key) return;  // another chat or date came in meanwhile
  state.known = res;
  renderKnown();
}
function renderKnown() {
  const k = state.known;
  $("#known-sec").classList.toggle("hidden", !(k && k.enabled));
  if (!k || !k.enabled) return;
  const day = iso => new Date(iso + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  const count = k.groups.reduce((n, g) => n + g.issues.length, 0);
  // The header names the task's date (the universe the chip picked); the list's own check date goes in the intro.
  const taskDate = k.universe ? day(k.groups[0].date) : "both dates";
  $("#known-title").innerHTML = `Known data issues <span class="nowrap">(${esc(taskDate)})<span class="sec-count">${count}</span></span>`;
  $("#known-checked").textContent = `Checked ${day(k.verified)}`;
  const row = i => `<div class="list-item known-item${i.level === "watch" ? " watch" : ""}" data-known="${esc(i.id)}" title="${esc(i.title)}">
      <span class="grow">⚠ ${esc(i.id)} · ${esc(i.title)}</span>${i.level === "watch" ? `<span class="known-tag">watch</span>` : ""}</div>`;
  const hint = !k.universe && (state.me.universe || {}).asks ? `<p class="known-hint">Pick the task date (top bar) to see only your date's list.</p>` : "";
  $("#known").innerHTML = hint + k.groups.map(g =>
    (k.groups.length > 1 ? `<div class="known-group">${esc(g.label)}</div>` : "") + g.issues.map(row).join("")).join("");
}
function openKnown(id) {
  const i = (state.known ? state.known.groups : []).flatMap(g => g.issues).find(x => x.id === id);
  if (!i) return;
  uiDialog({ title: `⚠ ${i.id} · ${i.title}`, ok: "Close", html: `<div class="review-confirm">
      <p class="known-fails">${i.fails ? `Caused ${i.fails} FAIL${i.fails === 1 ? "" : "s"}` : "Caused no FAILs yet"}</p>
      <div class="rc-row"><div class="k">Don't grade</div><div>${esc(i.dont)}</div></div>
      <div class="rc-row"><div class="k">Why</div><div>${esc(i.why)}</div></div>
      ${i.safer ? `<div class="rc-row"><div class="k">Safer</div><div>${esc(i.safer)}</div></div>` : ""}
      ${i.level === "watch" ? `<p class="known-watch"><span class="known-tag">watch</span> Not always failed — check both sides before relying on it.</p>` : ""}</div>` });
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
  // Keyed by ref (run:stage): a locked pass has no code. The side list holds the full current record, so a card
  // that unlocks gets its code back from it (and one that locks loses it).
  const byRef = Object.fromEntries((proofs || []).map(p => [p.ref || p.code, p]));
  for (const m of state.messages) {
    if (m.role !== "proof" || !m.meta) continue;
    const now = byRef[m.meta.ref || m.meta.code];
    if (!now) continue;
    const was = JSON.stringify([m.meta.outdated || null, m.meta.locked || null, m.meta.code || null, m.meta.warn_later || []]);
    if (was === JSON.stringify([now.outdated || null, now.locked || null, now.code || null, now.warn_later || []])) continue;
    m.meta = { ...now };
    const el = document.querySelector(`#messages [data-proof="${m.id}"]`)?.closest(".msg");
    if (el) el.outerHTML = messageHtml(m);
  }
}

// The checklist above the passes list, and the 🎉 popup the moment a locked chat becomes fully current again.
const lastLocked = {};
function renderPassGate(gate) {
  const box = $("#pass-gate");
  if (!box) return;
  const any = gate && gate.stages.some(s => s.status !== "none");
  if (!any) { box.innerHTML = ""; box.className = ""; return; }
  const done = gate.stages.filter(s => s.status === "current").length;
  const stale = gate.todo.length > 0;
  box.className = "pass-gate" + (stale ? " is-locked" : "");
  box.innerHTML = `<div class="pg-head">${stale
      ? `⚠ <b>Send ${gate.todo.map(s => `<span class="mono">/${esc(s)}</span>`).join(", ")} again</b><span class="muted"> · outdated: re-run before you submit</span>`
      : `✅ <b>${done} of 5 current</b><span class="muted"> · passes ready to use</span>`}</div>
    <div class="pg-dots">${gate.stages.map(s => `<span class="pg-dot ${s.status}${s.locked ? " locked" : ""}" title="/${esc(s.stage)}: ${esc(s.status === "none" ? "not passed yet" : s.locked && s.status === "current" ? "current, locked until the earlier stage passes" : s.status)}">${s.locked ? "🔒 " : ""}${esc((STAGE_NAMES[s.stage] || s.stage).split(" ")[0])}</span>`).join("")}</div>
    ${stale ? `<button class="small primary" data-gateinfo>What do I do?</button>` : ""}`;
  const id = state.me.attempt_id;
  if (lastLocked[id] === true && !stale) showPassesUnlocked();
  lastLocked[id] = stale;
}
document.addEventListener("click", e => {
  const g = e.target.closest("[data-gateinfo]");
  if (g && state.me && state.me.pass_gate) {
    const gate = state.me.pass_gate;
    const first = gate.stages.find(s => s.status === "outdated");
    return showLockedPass({ stage: first ? first.stage : gate.todo[0], verdict: first ? first.verdict : "", locked: { self: true, todo: gate.todo } });
  }
  const b = e.target.closest("[data-sendcmd]");
  if (!b || !state.me) return;
  if (state.run) return uiAlert("A review is running", "Send it again when this run finishes.");
  input.value = b.dataset.sendcmd;
  send();
});

// A review that hit the time limit is paused (server/pause.py): continue it once, or start over. Hidden while a run is on.
function renderPaused() {
  const note = $("#paused-note"), p = state.me && state.me.paused_review, waiting = state.me && state.me.pending_review;
  if ((!p && !waiting) || state.run) { note.classList.add("hidden"); note.innerHTML = ""; return; }
  if (!p) {  // a review that stopped to ask something: every plain message goes to it until it's answered or ended
    note.innerHTML = `<div class="grow">💬 Your <b class="mono">/${esc(waiting)}</b> review is waiting for your answer:
        your next message goes to the review. Want a normal chat reply instead? End it first.</div>
       <div class="actions"><button class="primary" data-paused="answer">Answer the review</button>
        <button data-paused="end">End it and chat normally</button></div>`;
    note.classList.remove("hidden");
    return;
  }
  const cmd = esc(p.command);
  note.innerHTML = p.inputs_changed
    ? `<div class="grow">⏸ Your <b class="mono">/${cmd}</b> review was paused by the ${esc(p.minutes)}-minute limit, but your inputs
        changed since, so it can't continue. Run it again on the new inputs.</div>
       <div class="actions"><button class="primary" data-paused="restart">Run /${cmd} again</button></div>`
    : `<div class="grow">⏸ Your <b class="mono">/${cmd}</b> review hit the ${esc(p.minutes)}-minute limit before it finished.
        Its work so far is saved: continue it from where it stopped (once), or start over.</div>
       <div class="actions"><button class="primary" data-paused="continue">Continue review</button>
        <button data-paused="restart">Start over</button></div>`;
  note.classList.remove("hidden");
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-paused]");
  if (!b || !state.me) return;
  const what = b.dataset.paused;
  if (what === "answer") return input.focus();
  if (what === "end") {
    try { await api("/api/review/end", { method: "POST" }); } catch (err) { return uiAlert("Couldn't end it", err.message); }
    return refreshSide();
  }
  if (!state.me.paused_review) return;
  input.value = what === "continue" ? "continue" : `/${state.me.paused_review.command}`;
  send();
});

function renderSide() {
  const me = state.me;
  renderDate();
  renderSpend(me.spend);
  renderBlocked();
  renderPaused();
  $("#limits").textContent = me.limits.reviews_left == null ? "" : `${me.limits.reviews_left} reviews left today`;
  $("#input").placeholder = me.blocked && !me.admin ? "This chat is blocked. Type /admin <message> to reach an admin."
    : me.paused_review ? `Your /${me.paused_review.command} review is paused. Press Continue review, or type "continue".`
    : me.pending_review ? `Your /${me.pending_review} review is waiting for your answer. Reply here to continue it.`
    : "Paste inputs, ask a question, or type a command like /prompt";
  const labels = { injection: "Injection", prompt: "Prompt", oe: "Oracle Events", rubric: "Rubric", hardness: "Hardness" };
  $("#reports").innerHTML = me.reports.length ? me.reports.map(r =>
    `<div class="list-item" data-rep="${esc(r.file)}"><span class="grow">${esc(labels[r.stage] || r.stage)}</span>${pill(shownVerdict(r.stage, r.verdict))}</div>`).join("") : "None yet";
  $("#proofs").innerHTML = me.proofs.length ? me.proofs.map((p, i) =>
    p.locked
      ? `<div class="list-item locked" data-pr="${i}" title="Locked until ${esc((p.locked.todo || []).map(s => "/" + s).join(", "))} ${(p.locked.todo || []).length > 1 ? "are" : "is"} sent again"><span class="grow">🔒 /${esc(p.stage)} · <span class="muted">locked</span></span>${pill(p.outdated ? "OUTDATED" : "LOCKED")}</div>`
      : `<div class="list-item" data-pr="${i}" title="${esc((p.warn_later || []).length ? "Also send " + p.warn_later.map(s => "/" + s).join(", ") + " again before you submit" : p.fingerprint || p.code)}"><span class="grow">${(p.warn_later || []).length ? "⚠ " : ""}/${esc(p.stage)} · <span class="mono">${p.fingerprint ? "🔏 " + esc(shortFp(p.fingerprint)) : esc(p.code)}</span></span>${pill(p.verdict)}</div>`).join("") : "None yet";
  renderPassGate(me.pass_gate);
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
    ? (sp.over ? `This attempt used its ${limitText()} budget: their new runs are paused until an admin approves more (admin page).`
      : "Their runs on this attempt only; each attempt has its own budget. Runs you start as admin are billed to you, not to them.")
    : sp.over ? `Budget reached: new reviews and replies are paused. Type /admin with a short note if you need more; an admin can approve it.`
      : pct >= 80 ? `Close to this task's ${limitText()} budget. At ${limitText()} new reviews and replies stop until an admin approves more.`
      : `Don't go over this task's ${limitText()} budget. At ${limitText()} new reviews and replies stop until an admin approves more.`;
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
    "The exact task prompt (your assignment and task date are filled in from the platform)"],
  ["/hardness", "Checks how hard the task is for Opus, from one unhinted run.",
    "One unhinted run's trajectory JSON (Opus preferred, Haiku as a fallback) and the exact prompt"],
  ["/oe", "Checks your Oracle Events path.", "Your Oracle Events"],
  ["/rubric", "Checks your rubric: criteria, weights and facts.", "Your rubric (JSON)"],
];

// The Guide panel: "Steps" (how the portal works, the default) plus reading tabs for the guideline files the assistant
// also reads (GET /api/guides/<name>). The last tab is remembered. openGuide(tab, heading) opens a tab scrolled to a
// heading slug (common.js guideSlug); links to /?guide=<tab>#<slug> anywhere in the chat open it the same way.
const GUIDE_TABS = [["steps", "Steps"], ["hardness", "Hardness"], ["toolcalls", "Tool calls"], ["patterns", "Failure patterns"], ["known", "Known data issues"]];
const guideDocs = {};  // name -> markdown, fetched once per page load

function guideStepsHtml() {
  const rows = GUIDE_COMMANDS.map(([cmd, what, needs]) => `<div class="guide-cmd">
      <div class="grow"><b class="mono">${cmd}</b> <span>${esc(what)}</span><div class="muted" style="font-size:13px">Needs: ${esc(needs)}</div></div>
      <button data-use="${cmd}">Use</button></div>`).join("");
  return `<div class="guide">
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
      code. Screenshot that card and upload it in your Outlier task: one for each passing review. Keep the whole card in the shot and
      don't zoom far out. You can also click <b>⬇ Download image</b>: that image is checked with Outlier's reader before you get it.</p>
    <p>For the injection, prompt and oracle events, <b>Copy what passed</b> copies the exact text that review graded, so what you
      paste into your Outlier task matches the pass.</p>
    <p>⚠ Before the pass screen opens, you'll be asked to confirm you've put the <b>latest version you fixed</b> into your Outlier task.
      The pass is only for the exact version you sent, so don't forget to update it. If your Outlier task doesn't match, we will know and the task will be flagged.</p>
    <div class="guide-sample proof"><div class="head"><div class="left"><div class="brandline">Original Conference Eval Portal</div>
      <div class="verdict"><span class="vbadge PASS">PASS</span><span>Score 5</span></div><div class="sub">/prompt · Prompt review · COMPLETE</div></div>
      </div>
      <div class="fpband"><div class="k">🔏 Verification code · the Prompt fingerprint, as your Outlier task shows it</div>
      <div class="fp">712 chars - hey  ... anks</div></div></div>
    <p>The <b>🔏 verification code</b> on a pass is the character count and the first and last 4 characters of the exact input
      that review graded, taken from the same fingerprint your Outlier task shows reviewers for that step. If what's in your Outlier task
      is different, they won't match.</p>
    <p>Closed it by accident? Reopen it from <b>Passes</b> in the sidebar, or with <b>Show pass screen</b> in the chat.
      Report cards, chat messages and FAIL results don't count as proof, only the pass screen does.</p>
    ${state.spend && state.spend.limit ? `<h4>5 · Spend limit</h4>
    <p>${spendRuleHtml()}</p>` : ""}
  </div>`;
}

function openGuide(tab, heading = "") {
  let w = document.querySelector(".overlay[data-guide-panel]");
  if (!w) {
    w = openModal("Guide", `<div class="guide-tabs" role="tablist">${GUIDE_TABS.map(([k, label]) =>
        `<button type="button" role="tab" data-guide-tab="${k}">${esc(label)}</button>`).join("")}</div><div class="guide-pane"></div>`);
    w.dataset.guidePanel = "1";
    w.addEventListener("click", e => {
      const t = e.target.closest("[data-guide-tab]");
      if (t) return showGuideTab(w, t.dataset.guideTab);
      const go = e.target.closest("[data-guide-goto]");
      if (go) { e.preventDefault(); return scrollGuideTo(w, go.dataset.guideGoto); }
      const use = e.target.closest("[data-use]");
      if (!use) return;
      const box = $("#input");
      box.value = use.dataset.use + " " + box.value.replace(/^\/\w+\s*/, "");
      w.remove();
      box.focus();
    });
  }
  let last = "steps";
  try { last = localStorage.getItem("evp_guide_tab") || "steps"; } catch (e) { /* private window */ }
  const want = GUIDE_TABS.some(([k]) => k === tab) ? tab : GUIDE_TABS.some(([k]) => k === last) ? last : "steps";
  return showGuideTab(w, want, heading);
}

async function showGuideTab(w, tab, heading = "") {
  try { localStorage.setItem("evp_guide_tab", tab); } catch (e) { /* private window */ }
  w.querySelectorAll("[data-guide-tab]").forEach(b => {
    b.classList.toggle("on", b.dataset.guideTab === tab);
    b.setAttribute("aria-selected", String(b.dataset.guideTab === tab));
  });
  const pane = w.querySelector(".guide-pane"), scroller = w.querySelector(".content");
  w.dataset.guideTab = tab;
  if (tab === "steps") { pane.innerHTML = guideStepsHtml(); scroller.scrollTop = 0; return; }
  if (!guideDocs[tab]) {
    pane.innerHTML = `<p class="muted">Loading…</p>`;
    try { guideDocs[tab] = await api(`/api/guides/${encodeURIComponent(tab)}`); } catch (err) {
      if (w.dataset.guideTab === tab) pane.innerHTML = `<p class="error">Couldn't load this guide: ${esc(err.message)}</p>`;
      return;
    }
    if (w.dataset.guideTab !== tab) return;  // another tab was picked while this one loaded
  }
  pane.innerHTML = `<div class="guide-doc bubble">${md(guideDocs[tab])}</div>`;
  const doc = pane.firstElementChild;
  const seen = {};
  doc.querySelectorAll("h1, h2, h3").forEach(h => {  // data-slug, not id: guide headings must never clash with the page's ids
    let s = guideSlug(h.textContent);
    seen[s] = (seen[s] || 0) + 1;
    if (seen[s] > 1) s += "-" + (seen[s] - 1);
    h.dataset.slug = s;
  });
  const parts = [...doc.querySelectorAll("h2")];
  const checklist = parts.find(h => /checklist/i.test(h.textContent)) || parts.find(h => /^appendix/i.test(h.textContent));
  const link = (h, label) => `<a href="/?guide=${tab}#${esc(h.dataset.slug)}" data-guide-goto="${esc(h.dataset.slug)}">${esc(label)}</a>`;
  const top = document.createElement("div");
  top.className = "guide-top";
  top.innerHTML = (checklist ? `<p class="guide-jump">↓ ${link(checklist, "Jump to checklist")}</p>` : "")
    + (parts.length > 1 ? `<nav class="guide-toc" aria-label="Contents"><div class="k">Contents</div><ol>${parts.map(h =>
        `<li>${link(h, h.textContent)}</li>`).join("")}</ol></nav>` : "");
  const h1 = doc.querySelector("h1");
  if (h1) h1.after(top); else doc.prepend(top);
  scroller.scrollTop = 0;
  if (heading) scrollGuideTo(w, heading);
}

function scrollGuideTo(w, slug) {
  const heads = [...w.querySelectorAll(".guide-doc [data-slug]")];
  const s = guideSlug(decodeURIComponent(slug));
  const h = heads.find(x => x.dataset.slug === s) || heads.find(x => x.dataset.slug.startsWith(s + "-"));
  if (h) h.scrollIntoView({ block: "start" });
}

// Guide links in messages and cards (the kickoff card, the too-easy hint): open the panel in place, no page load.
document.addEventListener("click", e => {
  const a = e.target.closest("a[href]");
  if (!a || !state.me || e.metaKey || e.ctrlKey || e.shiftKey) return;
  let url;
  try { url = new URL(a.getAttribute("href"), location.href); } catch (err) { return; }
  if (url.origin !== location.origin || url.pathname !== "/" || !url.searchParams.get("guide")) return;
  if (a.dataset.guideGoto) return;  // the panel's own contents links scroll inside it
  e.preventDefault();
  openGuide(url.searchParams.get("guide"), url.hash.slice(1));
});

// A fresh load of /?guide=<tab>#<slug> (signed in): open that tab once, then tidy the address bar.
function openGuideFromUrl() {
  const params = new URLSearchParams(location.search), tab = params.get("guide");
  if (!tab) return;
  const heading = location.hash.slice(1);
  params.delete("guide");
  history.replaceState(null, "", location.pathname + (params.toString() ? "?" + params : ""));
  openGuide(tab, heading);
}

$("#guide-btn").onclick = () => openGuide();

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
  const known = e.target.closest("[data-known]");
  if (known) return openKnown(known.dataset.known);
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
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !touchOnly()) { e.preventDefault(); send(); }
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
  if (review && review[1].toLowerCase() === "hardness" && !state.me.admin
      && (state.me.inputs || []).some(i => i.key === "injection" && i.state === "saved")
      && !(await showInjectGuide({ lead: `<p><b>Before /hardness:</b> it scores the Opus run you upload, so that run must have been made
          with your injection loaded.</p>`, check: "My Opus run was made in a sandbox where I ran my injection SQL first",
        ok: "Continue to /hardness", cancel: "Cancel" }))) return;
  if (review && !(await confirmReview(review[1].toLowerCase(), text))) return;
  if (review && "Notification" in window && Notification.permission === "default") Notification.requestPermission().catch(() => {});
  $("#send").disabled = true;
  try {
    let res = await api("/api/send", { json: { text, files: state.attachments } });
    if (res.identical) {  // nothing changed since the last review of this command (server/rerun_guard.py)
      if (!(await identicalRerun(res.identical))) return;
      res = await api("/api/send", { json: { text, files: state.attachments, force: true } });
      if (res.identical) return identicalRerun(res.identical);  // it was already run again once on these inputs
    }
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

// A bare review command on exactly the inputs its last finished review graded (server/rerun_guard.py): it would get
// the same result and cost another review, so it isn't started. They can run it once more anyway, as a second opinion.
function identicalRerun(same) {
  const cmd = `/${same.command}`, ago = same.minutes_ago < 60 ? `${same.minutes_ago} min ago`
    : same.minutes_ago < 2880 ? `${Math.round(same.minutes_ago / 60)} h ago` : `${Math.round(same.minutes_ago / 1440)} days ago`;
  const verdict = shownVerdict(same.command, same.verdict) || "no verdict";
  const html = `<p style="margin-top:0">Your last <b class="mono">${esc(cmd)}</b> review (<b>${esc(verdict)}</b>, ${esc(ago)}) graded exactly
      the inputs that are saved now. Running it again on the same inputs gives the same result${same.cost ? `, and costs about ${esc("$" + Math.round(same.cost))}` : ""}.</p>
    <p>Fix what its report says first (send the corrected version here, or with the command), then run <b class="mono">${esc(cmd)}</b> again.</p>
    <p style="margin-bottom:0">${same.forced ? "It was already run once more on these same inputs, so it won't run again until something changes."
      : "If you think that review got it wrong, you can run it once more on the same inputs."}</p>`;
  return same.forced ? uiDialog({ title: "Nothing changed since your last review", html, ok: "OK" }).then(() => false)
    : uiDialog({ title: "Nothing changed since your last review", html, ok: "Run it anyway", cancel: "I'll fix it first" });
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
