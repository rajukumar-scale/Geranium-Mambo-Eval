// Shared helpers for the chat, admin and verify pages.

const $ = (sel, root = document) => root.querySelector(sel);

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function md(text) {
  const html = window.marked ? marked.parse(String(text ?? ""), { breaks: false, gfm: true }) : esc(text);
  return window.DOMPurify ? DOMPurify.sanitize(html) : esc(text);
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.json);
    delete opts.json;
  }
  const resp = await fetch(path, opts);
  const type = resp.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await resp.json() : await resp.text();
  if (!resp.ok) {
    if (resp.status === 403 && body && body.disabled && window.onDisabled) window.onDisabled(body);  // an admin disabled them
    const err = new Error((body && body.detail) || (typeof body === "string" && body) || resp.statusText);
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return body;
}

const fmtInt = n => (n == null ? "–" : Number(n).toLocaleString("en-US"));
const fmtTime = t => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–");
const fmtUTC = t => (t ? new Date(t * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC" : "–");
function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}
const short = (h, n = 12) => (h ? `${h.slice(0, n)}…` : "–");
const pill = v => `<span class="pill ${esc(v || "none")}">${esc(v || "no verdict")}</span>`;

// "5m ago" for lists (the full time goes in a tooltip).
function fmtAgo(t) {
  if (!t) return "–";
  const sec = Date.now() / 1000 - t;
  if (sec < 60) return "just now";
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 7 * 86400) return `${Math.floor(sec / 86400)}d ago`;
  return new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
}

// ---- admin chat lists (the chat page's "All chats" sidebar and /admin) ----
// Each chat is normalized to: attempt_id, task_id, email, last_activity, running, admin_open, passes,
// over (spend limit), blocked, disabled, spend_usd, last_cmd, last_verdict, archived (why it's finished), idle_days.
// Active = neither idle (still theirs, no work for a while) nor archived (submitted, released, or with someone else);
// those two only show under their own chips. pick = the admin page's funnel pick: count and list everything in it.
const CHAT_SORTS = [["recent", "Latest activity"], ["spend", "Highest spend"]];
const CHAT_CHIPS = [["all", "Active"], ["running", "Running"], ["admin", "/admin open"], ["passed", "Passed"], ["flagged", "Blocked / flagged"],
  ["idle", "Idle"], ["archived", "Archived"]];
const chipMatch = (chip, c, pick = false) => chip === "archived" ? !!c.archived : chip === "idle" ? !c.archived && c.idle_days != null
  : (pick || !c.archived) && ((chip === "all" && (pick || c.idle_days == null))
  || (chip === "running" && c.running) || (chip === "admin" && c.admin_open)
  || (chip === "passed" && c.passes > 0) || (chip === "flagged" && (c.blocked || c.disabled || c.over)));
const ARCHIVE_TIPS = { submitted: "They submitted this task after their last work here", released: "They no longer hold this task and didn't submit it",
  "with someone else": "This task is now with another contributor" };

function chatListPrefs() {
  try { return JSON.parse(localStorage.getItem("evp_chatlist") || "{}"); } catch (e) { return {}; }
}
function saveChatListPrefs(change) {
  try { localStorage.setItem("evp_chatlist", JSON.stringify({ ...chatListPrefs(), ...change })); } catch (e) { /* private window */ }
}

// Search box, sort menu and filter chips in `box`; apply(list) filters and sorts a list and refreshes the chip counts.
function chatListTools(box, onChange) {
  const prefs = chatListPrefs();
  box.innerHTML = `<input class="list-search" type="search" placeholder="Search email, attempt or task">
    <label class="list-sort"><span>Sort</span><select>${CHAT_SORTS.map(([k, label]) =>
      `<option value="${k}"${(prefs.sort || "recent") === k ? " selected" : ""}>${label}</option>`).join("")}</select></label>
    <div class="fchips" role="group" aria-label="Filter chats"></div>`;
  const search = box.querySelector(".list-search");
  search.addEventListener("input", onChange);
  box.querySelector("select").onchange = e => { saveChatListPrefs({ sort: e.target.value }); onChange(); };
  box.querySelector(".fchips").addEventListener("click", e => {
    const b = e.target.closest("[data-chip]");
    if (b) { saveChatListPrefs({ chip: b.dataset.chip }); onChange(); }
  });
  return {
    // pick: the list is an admin-page funnel pick, which lists exactly its chats (idle and archived ones included).
    apply(list, { pick = false } = {}) {
      const p = chatListPrefs(), chip = p.chip || "all", sort = p.sort || "recent";
      const q = search.value.trim().toLowerCase();
      box.querySelector(".fchips").innerHTML = CHAT_CHIPS.map(([k, label]) => {
        const n = list.filter(c => chipMatch(k, c, pick)).length;
        return `<button type="button" class="fchip${k === chip ? " on" : ""}${k === "admin" && n ? " alert" : ""}" data-chip="${k}"
          aria-pressed="${k === chip}">${label} <span>${n}</span></button>`;
      }).join("");
      const recent = (a, b) => (b.last_activity || 0) - (a.last_activity || 0);
      return list.filter(c => chipMatch(chip, c, pick) && (!q || [c.attempt_id, c.task_id, c.email].some(v => (v || "").toLowerCase().includes(q))))
        .sort(sort === "spend" ? (a, b) => (b.spend_usd || 0) - (a.spend_usd || 0) || recent(a, b) : recent);
    },
  };
}

// One chat in a sidebar list. `god` = an admin's list (email on top, spend shown); attr = the click data attribute.
function chatRowHtml(c, { attr, current = false, god = false, preview = "" }) {
  const short = id => esc((id || "").slice(0, 8));
  const status = c.running ? `<span class="pill run">running</span>`
    : c.last_verdict ? `<span class="pill ${esc(c.last_verdict)}" title="Last review: /${esc(c.last_cmd)} ${esc(c.last_verdict)}">/${esc(c.last_cmd)} ${esc(c.last_verdict)}</span>`
    : `<span class="cr-none">no reviews yet</span>`;
  const badges = [
    c.admin_open && `<span class="badge alert" title="Open /admin request">● /admin</span>`,
    c.over && `<span class="badge flag" title="This attempt is over the spend limit">⚑ over</span>`,
    c.archived_from ? `<span class="badge" title="The task was re-assigned: this is the earlier holder's chat for attempt ${esc(c.archived_from)}, kept read-only">🗄 archived · earlier holder</span>`
      : c.archived && `<span class="badge archived" title="${esc(ARCHIVE_TIPS[c.archived] || "")}">🗄 ${esc(c.archived)}</span>`,
    c.idle_days != null && `<span class="badge idle" title="Still theirs, but no review or edit for ${esc(c.idle_days)} days">⏸ idle ${Math.floor(c.idle_days)}d</span>`,
    c.disabled ? `<span class="badge flag" title="This contributor is disabled">⛔ disabled</span>` : c.blocked && `<span class="badge flag" title="This chat is blocked">⛔ blocked</span>`,
  ].filter(Boolean).join("");
  const spent = "$" + Number(c.spend_usd || 0).toFixed(2);
  const ago = `<span title="${esc(fmtTime(c.last_activity))}">${fmtAgo(c.last_activity)}</span>`;
  // Task IDs are for admins only: a contributor's list arrives without them.
  const line2 = [god && `<span class="mono">${short(c.attempt_id)}</span>`, c.task_id && `task <span class="mono">${short(c.task_id)}</span>`,
    !god && ago].filter(Boolean).join(" · ");
  return `<div class="chat-row${current ? " current" : ""}" ${attr}="${esc(c.attempt_id)}" title="attempt ${esc(c.attempt_id)}${c.task_id ? ` · task ${esc(c.task_id)}` : ""}">
    <div class="cr1"><span class="cr-top${god ? "" : " mono"}">${god ? esc(c.email || "unknown") : short(c.attempt_id) + "…"}</span>${status}</div>
    <div class="cr2">${line2}</div>
    ${god || badges ? `<div class="cr3">${god ? `<span class="cr-meta">${ago} · <b title="This attempt's own spend (what the limit counts)">${spent}</b></span>` : ""}${badges}</div>` : ""}
    ${preview ? `<div class="cr-preview">${esc(preview)}</div>` : ""}
  </div>`;
}

function openModal(title, bodyHtml, buttons = "") {
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  wrap.innerHTML = `<div class="modal"><header><div class="grow">${esc(title)}</div>${buttons}
    <button class="ghost" data-close>✕</button></header><div class="content">${bodyHtml}</div></div>`;
  wrap.addEventListener("click", e => { if (e.target === wrap || e.target.closest("[data-close]")) wrap.remove(); });
  document.body.appendChild(wrap);
  return wrap;
}

// In-page dialogs instead of the browser's alert()/confirm(). Resolve true for the main button.
// alt adds a third button; the promise then resolves "alt" for it (true = main button, false = cancel or Esc).
function uiDialog({ title, html, ok = "OK", cancel = null, danger = false, alt = null }) {
  return new Promise(resolve => {
    const wrap = document.createElement("div");
    wrap.className = "overlay";
    wrap.innerHTML = `<div class="modal dialog" role="dialog" aria-modal="true"><header><div class="grow">${esc(title)}</div></header>
      <div class="content">${html}</div>
      <div class="dialog-actions">${cancel ? `<button data-no>${esc(cancel)}</button>` : ""}${alt ? `<button data-alt>${esc(alt)}</button>` : ""}
        <button class="${danger ? "danger-solid" : "primary"}" data-yes>${esc(ok)}</button></div></div>`;
    const key = e => {
      if (e.key === "Escape") { e.preventDefault(); done(false); }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); done(true); }
    };
    function done(value) { wrap.remove(); document.removeEventListener("keydown", key, true); resolve(value); }
    wrap.addEventListener("click", e => {
      if (e.target === wrap || e.target.closest("[data-no]")) done(false);
      else if (e.target.closest("[data-alt]")) done("alt");
      else if (e.target.closest("[data-yes]")) done(true);
    });
    document.body.appendChild(wrap);
    wrap.querySelector("[data-yes]").focus();
    // Attach after this event finishes, so the Enter that opened the dialog doesn't also confirm it.
    setTimeout(() => document.addEventListener("keydown", key, true), 0);
  });
}
const uiAlert = (title, message) => uiDialog({ title, html: `<p style="margin:0">${esc(message)}</p>` });
const uiConfirm = (title, html, ok, opts = {}) => uiDialog({ title, html, ok, cancel: "Cancel", ...opts });

function download(name, text, type = "text/markdown") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

// The screenshot-ready pass screen. Same markup on the chat, admin and verify pages.
// The fingerprint as shown: exactly the line the taxonomy shows at L10 (template v317 "update to 4 instead of 40":
// first 4 and last 4 characters), made from the stored full fingerprint (v316 format, first/last 40), which holds
// everything needed. FP_SHOW is the taxonomy's number; change both together.
//   "528 chars — leonard dumped … on these numbers" -> "528 chars — leon … bers"
// The rubric's weights list (one number per row) is left out of the code: the taxonomy's screenshot check skips it
// anyway, and with 100+ rows it wrapped over many lines and the reader couldn't get through it (2026-10-27).
// rubricWeights() gives it back for its own line, outside the verification code band.
const FP_SHOW = 4;
// rubric titles count letters and digits only since 2026-10-17 ("letters"); older passes say "chars"
const RUBRIC_FP = /^(\d+ criteria, Σweight [^,]+), weights (\[[^\]]*\]) · Titles (\d+) (chars|letters) — ?(.*)$/s;
function shortFp(fp) {
  if (!fp || fp.startsWith("(empty")) return fp || "";
  const cut = (len, body) => {  // body = the whole text (<= 40 chars) or first 40 + " … " + last 40
    const chars = Array.from(body);
    return (len > FP_SHOW ? chars.slice(0, FP_SHOW).join("") : body)
      + (len > 2 * FP_SHOW ? " … " + chars.slice(-FP_SHOW).join("") : "");
  };
  const rubric = fp.match(RUBRIC_FP);
  if (rubric) return `${rubric[1]} · Titles ${rubric[3]} ${rubric[4]} — ${cut(+rubric[3], rubric[5])}`;
  const text = fp.match(/^(\d+) chars — (.*)$/s);
  return text ? `${text[1]} chars — ${cut(+text[1], text[2])}` : fp;
}
function rubricWeights(fp) {
  const rubric = (fp || "").match(RUBRIC_FP);
  return rubric ? rubric[2] : "";
}

function proofHtml(p, opts = {}) {
  const t = p.totals || {};
  const total = (t.input || 0) + (t.output || 0) + (t.cache_read || 0) + (t.cache_creation || 0);
  const models = Object.entries(p.models || {}).map(([m, u]) => `${m} (${fmtInt(u.calls)} calls)`).join(", ") || "–";
  const hashes = Object.entries(p.report_hashes || {}).slice(0, 4)
    .map(([k, v]) => `<div class="wide"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
  const verifyUrl = `${location.origin}/verify/${encodeURIComponent(p.code)}`;
  const stage = { injection: "Injection", prompt: "Prompt", hardness: "Hardness", oe: "Oracle Events", rubric: "Rubric" }[p.stage] || p.stage;
  return `<div class="proof">
    <div class="head">
      <div class="left">
        <div class="brandline">Original Conference Eval Portal</div>
        <div class="verdict"><span class="vbadge ${esc(p.verdict)}">${esc(p.verdict)}</span>${p.score != null ? `<span>Score ${esc(p.score)}</span>` : ""}</div>
        <div class="sub">/${esc(p.stage)} · ${esc(stage)} review · ${esc(p.execution_status)}${opts.validity || ""}</div>
        ${p.checked_during ? `<div class="sub">Checked inside the /${esc(p.checked_during)} run · tokens and cost below are for that whole run</div>` : ""}
        ${p.admin_override ? `<div class="sub override">⚑ Admin override by ${esc(p.admin_override.by)}${p.admin_override.reason ? `: ${esc(p.admin_override.reason)}` : ""}.
          The review itself scored ${esc(p.admin_override.original_verdict || "–")}${p.admin_override.original_score != null ? ` ${esc(p.admin_override.original_score)}` : ""}.</div>` : ""}
      </div>
      ${p.fingerprint ? "" : `<div class="codebox"><div class="k">Verification code</div><div class="c">${esc(p.code)}</div></div>`}
    </div>
    ${p.fingerprint ? `<div class="fpband"><div class="k">🔏 Verification code · the ${esc(stage)} fingerprint, as the taxonomy shows it</div>
      <div class="fp" title="${esc(p.fingerprint)}">${esc(shortFp(p.fingerprint))}</div></div>` : ""}
    ${rubricWeights(p.fingerprint) ? `<div class="wband"><div class="k">Rubric weights, one per row · not part of the verification code</div>
      <div class="w">${esc(rubricWeights(p.fingerprint))}</div></div>` : ""}
    <div class="grid">
      <div><div class="k">Attempt ID</div><div class="v">${esc(p.attempt_id)}</div></div>
      ${p.task_id ? `<div><div class="k">Task ID</div><div class="v">${esc(p.task_id)}</div></div>` : ""}
      <div><div class="k">User</div><div class="v">${esc(p.user_email)}</div></div>
      <div><div class="k">Outlier ID</div><div class="v">${esc(p.user_id)}</div></div>
      <div><div class="k">Completed</div><div class="v">${esc(fmtUTC(p.finished_at))}</div></div>
      <div><div class="k">Run time</div><div class="v">${esc(fmtDur(p.duration_seconds))}</div></div>
      <div class="wide"><div class="k">Models</div><div class="v">${esc(models)}</div></div>
      <div><div class="k">Model calls</div><div class="v">${fmtInt(t.calls)}</div></div>
      <div><div class="k">Total tokens</div><div class="v">${fmtInt(total)}</div></div>
      <div><div class="k">Input tokens</div><div class="v">${fmtInt(t.input)}</div></div>
      <div><div class="k">Output tokens</div><div class="v">${fmtInt(t.output)}</div></div>
      <div><div class="k">Cache read tokens</div><div class="v">${fmtInt(t.cache_read)}</div></div>
      <div><div class="k">Cache write tokens</div><div class="v">${fmtInt(t.cache_creation)}</div></div>
      <div><div class="k">Est. cost (list price)</div><div class="v">$${Number(p.cost_usd_estimate || 0).toFixed(2)}</div></div>
      <div><div class="k">Eval version</div><div class="v">${esc((p.report_fields || {})["Eval version"] || "–")}</div></div>
      <div class="wide"><div class="k">Report SHA-256</div><div class="v">${esc(p.report_sha256)}</div></div>
      ${hashes}
    </div>
    ${p.fingerprint ? "" : `<div class="code muted">Check this pass at <span class="mono">${esc(verifyUrl)}</span></div>`}
    ${opts.foot === false ? "" : `<div class="foot"><div class="grow">📸 Take a screenshot of this screen and submit it with your task.</div>
      <button data-close>Close</button></div>`}
  </div>`;
}

// The pass screen, always in two steps, every time it opens and for everyone: first a reminder they must tick
// (the pass is for the exact version they sent, so that latest fixed version has to go into the taxonomy),
// then the card. The reminder sits outside the pass card (a cropped screenshot shows only the pass) and uses its
// own orange style, never a verdict colour.
function showProof(p) {
  const cmd = esc("/" + (p.stage || ""));
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  const card = () => {
    wrap.innerHTML = `<div class="proof-stack">${proofHtml(p)}
      <div class="taxo-strip" role="note"><span class="taxo-icon" aria-hidden="true">⚠</span><div><b>Reminder:</b> the taxonomy must have
        this exact <b class="mono">${cmd}</b> version. Update it before you submit; a mismatch gets your task flagged.</div></div></div>`;
  };
  const step = () => {
    wrap.innerHTML = `<div class="taxo-step" role="alertdialog" aria-labelledby="taxo-title">
      <div class="taxo-head"><span class="taxo-icon" aria-hidden="true">⚠</span><span id="taxo-title">Before you screenshot</span></div>
      <div class="taxo-body">
        <p>This pass is for the exact <b class="mono">${cmd}</b> you sent in this review.</p>
        <p>If you fixed it here to pass, put that <b>latest fixed version into the taxonomy</b>. Don't forget to update it.</p>
        <p class="taxo-warn">If the taxonomy doesn't match the version that passed here, we will know, and your task will be flagged.</p>
        <p class="taxo-note">If you change it again, run <b class="mono">${cmd}</b> again.</p>
        <label class="taxo-check"><input type="checkbox"> I've updated the taxonomy</label>
      </div>
      <div class="taxo-foot"><button data-close>Close</button><button class="primary" data-show disabled>Show my pass →</button></div></div>`;
    const tick = wrap.querySelector(".taxo-check input"), show = wrap.querySelector("[data-show]");
    tick.onchange = () => { show.disabled = !tick.checked; };
    show.onclick = card;
  };
  step();
  wrap.addEventListener("click", e => { if (e.target === wrap || e.target.closest("[data-close]")) wrap.remove(); });
  document.body.appendChild(wrap);
}

// A message's time: "2:26 PM" today, "Oct 13, 2:26 PM" otherwise; the full local and UTC time on hover.
function msgTime(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  const label = d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return `<span class="ts" title="${esc(d.toLocaleString([], { dateStyle: "full", timeStyle: "medium" }))} · ${esc(fmtUTC(t))}">${esc(label)}</span>`;
}

// One chat message -> HTML. Used by the user chat and the admin view.
function messageHtml(m, opts = {}) {
  const meta = m.meta || {};
  const ts = msgTime(m.created_at);
  if (m.role === "user") {
    const text = m.content || "";
    const long = text.length > 1800;
    const files = (meta.files || []).length ? `<div class="attach-list">📎 ${meta.files.map(esc).join(", ")}</div>` : "";
    return `<div class="msg user"><div class="bubble">${opts.admin ? '<div class="who">User</div>' : ""}${
      long ? `<span class="clip">${esc(text.slice(0, 1800))}…</span><span class="full hidden">${esc(text)}</span>
      <div class="more" data-expand>Show all (${fmtInt(text.length)} characters)</div>` : esc(text)}${files}<div class="ts-row">${ts}</div></div></div>`;
  }
  if (m.role === "assistant") return `<div class="msg assistant"><div class="bubble"><div class="who">Eval assistant ${ts}</div>${md(m.content)}</div></div>`;
  if (m.role === "admin") return `<div class="msg admin"><div class="bubble"><div class="who">Admin${opts.admin && meta.admin ? ` · ${esc(meta.admin)}` : ""} ${ts}</div>${md(m.content)}</div></div>`;
  if (m.role === "report") {
    const label = { injection: "Injection", prompt: "Prompt", oe: "Oracle Events", rubric: "Rubric", hardness: "Hardness" }[meta.stage] || meta.stage;
    return `<div class="msg system"><div class="bubble"><div class="rcard"><div class="grow">📋 <b>${esc(label)} report</b> ${pill(meta.verdict)}
      ${meta.score != null ? `<span class="muted">score ${esc(meta.score)}</span>` : ""}
      ${meta.exec_status && meta.exec_status !== "COMPLETE" ? `<span class="muted">· ${esc(meta.exec_status)}</span>` : ""}</div>
      ${ts}<button data-report="${m.id}">View</button><button data-download="${m.id}">Download</button></div>
      ${meta.fingerprint ? `<div class="rfp" title="${esc(meta.fingerprint)}">🔏 ${esc(shortFp(meta.fingerprint))}</div>` : ""}</div></div>`;
  }
  if (m.role === "proof") {
    return `<div class="msg system"><div class="bubble proofcard"><div class="rcard"><div class="grow">🏆 <b>/${esc(meta.stage)} ${esc(meta.verdict)}${meta.admin_override ? " (admin override)" : ""}</b>
      <span class="mono fp-inline" title="${esc(meta.fingerprint || meta.code)}">${meta.fingerprint ? "🔏 " + esc(shortFp(meta.fingerprint)) : esc(meta.code)}</span></div>${ts}<button class="primary" data-proof="${m.id}">Show pass screen</button></div></div></div>`;
  }
  const warn = m.meta && (m.meta.spend_flag || m.meta.warn) ? " warnnote" : "";
  return `<div class="msg system"><div class="bubble${warn}">${md(m.content)}<div class="ts-row">${ts}</div></div></div>`;
}

function wireMessageClicks(container, getMessage, reportName) {
  container.addEventListener("click", e => {
    const t = e.target;
    if (t.matches("[data-expand]")) {
      const b = t.closest(".bubble");
      b.querySelector(".clip").classList.add("hidden");
      b.querySelector(".full").classList.remove("hidden");
      t.remove();
    } else if (t.dataset.report) {
      const m = getMessage(+t.dataset.report);
      const label = `${m.meta.stage} report — ${m.meta.verdict || "no verdict"}`;
      const w = openModal(label, `<div class="bubble" style="border:0;padding:0">${md(m.content)}</div>`,
        `<button data-dl>Download</button>`);
      w.querySelector("[data-dl]").onclick = () => download(reportName(m), m.content);
    } else if (t.dataset.download) {
      const m = getMessage(+t.dataset.download);
      download(reportName(m), m.content);
    } else if (t.dataset.proof) {
      showProof(getMessage(+t.dataset.proof).meta);
    }
  });
}


// ---- light / dark theme (any button with data-theme-toggle switches it; the choice is remembered) ----
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("evp_theme", theme);
  document.querySelectorAll("[data-theme-toggle]").forEach(b => {
    b.textContent = theme === "dark" ? "☀ Light" : "☾ Dark";
    b.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  });
}
document.querySelectorAll("[data-theme-toggle]").forEach(b =>
  b.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")));
applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
