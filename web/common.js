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
  // A read that hits a blip on boat's public link ("Failed to fetch", or a 502/503/504 from its edge while the portal
  // answered fine, 2026-11-01) is tried again twice before it's an error. Only reads: a POST could run twice.
  const read = !opts.method || ["GET", "HEAD"].includes(opts.method.toUpperCase());
  let resp;
  for (const wait of read ? [1000, 3000, null] : [null]) {
    try {
      resp = await fetch(path, opts);
      if (!(read && wait !== null && [502, 503, 504].includes(resp.status))) break;
    } catch (err) {
      if (wait === null || !(err instanceof TypeError)) throw err;
    }
    await new Promise(r => setTimeout(r, wait));
  }
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
const shownVerdict = (stage, v) => v;

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
const CHAT_CHIPS = [["all", "Active"], ["running", "Running"], ["admin", "/admin open"], ["budget", "Over budget"], ["passed", "Passed"], ["flagged", "Blocked / flagged"],
  ["idle", "Idle"], ["archived", "Archived"]];
const chipMatch = (chip, c, pick = false) => chip === "archived" ? !!c.archived : chip === "idle" ? !c.archived && c.idle_days != null
  : (pick || !c.archived) && ((chip === "all" && (pick || c.idle_days == null))
  || (chip === "running" && c.running) || (chip === "admin" && c.admin_open) || (chip === "budget" && c.budget_open)
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
    : c.last_verdict ? (v => `<span class="pill ${esc(v)}" title="Last review: /${esc(c.last_cmd)} ${esc(v)}">/${esc(c.last_cmd)} ${esc(v)}</span>`)(shownVerdict(c.last_cmd, c.last_verdict))
    : `<span class="cr-none">no reviews yet</span>`;
  const badges = [
    c.admin_open && `<span class="badge alert" title="Open /admin request">● /admin</span>`,
    c.budget_open ? `<span class="badge flag" title="At or over its $${esc(Math.round(c.budget_limit || 0))} budget, no increase approved yet: new reviews and replies are paused">⚑ over budget</span>`
      : c.over && `<span class="badge" title="Reached its budget earlier; an admin approved more">⚑ budget raised</span>`,
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
// The fingerprint as shown: exactly the line the taxonomy builds at L-1/L10 since v360 ("Uses plain hyphen and
// three dots for OCR compatibility"): first 4 and last 4 characters (v317), made from the stored full fingerprint
// (v316 format, first/last 40), which holds everything needed. FP_SHOW is the taxonomy's number; change both together.
//   "528 chars — leonard dumped … on these numbers" -> "528 chars - leon ... bers"
//   rubric: "12 criteria, weight 18 | Titles 548 letters - TheA ... nli"
// Only plain characters, so the taxonomy's screenshot reader has nothing to convert: the em dash, ellipsis, Σ and
// middle dot it used to be told to normalize were where it misread (2026-10-26/27).
// The rubric's weights list (one number per row) is left out of the code: the taxonomy's screenshot check skips it
// anyway, and with 100+ rows it wrapped over many lines and the reader couldn't get through it (2026-10-27).
// rubricWeights() gives it back for its own line, outside the verification code band.
const FP_SHOW = 4;
// rubric titles count letters and digits only since 2026-10-17 ("letters"); older passes say "chars"
const RUBRIC_FP = /^(\d+ criteria), Σweight ([^,]+), weights (\[[^\]]*\]) · Titles (\d+) (chars|letters) — ?(.*)$/s;
function shortFp(fp) {
  if (!fp) return "";
  if (fp.startsWith("(empty")) return fp.replace(/—/g, "-");
  const cut = (len, body) => {  // body = the whole text (<= 40 chars) or first 40 + " … " + last 40
    const chars = Array.from(body);
    return (len > FP_SHOW ? chars.slice(0, FP_SHOW).join("") : body)
      + (len > 2 * FP_SHOW ? " ... " + chars.slice(-FP_SHOW).join("") : "");
  };
  const rubric = fp.match(RUBRIC_FP);
  if (rubric) return `${rubric[1]}, weight ${rubric[2]} | Titles ${rubric[4]} ${rubric[5]} - ${cut(+rubric[4], rubric[6])}`;
  const text = fp.match(/^(\d+) chars — (.*)$/s);
  return text ? `${text[1]} chars - ${cut(+text[1], text[2])}` : fp;
}
// The taxonomy's reader (GPT-4o) drops the hyphens a code starts with ("5446 chars - -- 1 ..." read as
// "5446 chars - 1 ...": its own rule folds "- --" into one separator), drops the space in "}' )" and reads O as 0.
// Spelling the first and last characters out under the code, by name and symbol, fixed most of it: on the 21
// injection passes it misread (2026-10-30), the real reader read 39 of 41 right with these lines, 25 of 42 without.
const CHAR_NAMES = { "-": "hyphen", "'": "apostrophe", '"': "double quote", "`": "backtick", "(": "left parenthesis",
  ")": "right parenthesis", "{": "left brace", "}": "right brace", "[": "left bracket", "]": "right bracket",
  "<": "less-than", ">": "greater-than", ",": "comma", ".": "period", ";": "semicolon", ":": "colon", "_": "underscore",
  "/": "slash", "\\": "backslash", "|": "pipe", "=": "equals", "+": "plus", "*": "asterisk", "%": "percent",
  "$": "dollar", "#": "hash", "@": "at sign", "&": "ampersand", "!": "exclamation mark", "?": "question mark",
  "~": "tilde", "^": "caret" };
function spellChars(text) {
  return Array.from(text).map(ch => ch === " " ? "space" : CHAR_NAMES[ch] ? `${CHAR_NAMES[ch]} (${ch})`
    : /[0-9]/.test(ch) ? `digit ${ch}` : /[A-Z]/.test(ch) ? `capital letter ${ch}` : /[a-z]/.test(ch) ? `small letter ${ch}`
    : ch).join(", ");
}
function spelledCode(code) {
  if (!code || code.startsWith("(empty")) return [];
  const count = t => Array.from(t).length;
  const cut = code.match(/^.*? - (.*?) \.\.\. (.*)$/s);
  if (cut) return [`First ${count(cut[1])} characters: ${spellChars(cut[1])}`, `Last ${count(cut[2])} characters: ${spellChars(cut[2])}`];
  const whole = code.match(/^.*? - (.*)$/s);
  return [`${whole ? "Characters after the hyphen" : "Spelled out"}: ${spellChars(whole ? whole[1] : code)}`];
}
// Symbols the reader still misreads off a screenshot, even with the spelled-out lines: it read the tail "{}')" as
// "{')" in 12 of 16 screenshots on 8 screen sizes, while the "Download image" file read right 9 of 9 (2026-11-03,
// attempt 6adfee3b47f7621feba5b96c). When the first or last characters have brackets, braces or quotes, the card
// says to upload the downloaded image instead of a screenshot.
const RISKY_CHARS = /[()[\]{}<>'"`]/;
function screenshotRisky(code) {
  if (!code || code.startsWith("(empty")) return false;
  const cut = code.match(/^.*? - (.*?) \.\.\. (.*)$/s) || code.match(/^.*? - (.*)$/s);
  return !!cut && cut.slice(1).some(part => RISKY_CHARS.test(part));
}
function rubricWeights(fp) {
  const rubric = (fp || "").match(RUBRIC_FP);
  return rubric ? rubric[3] : "";
}

// A pass whose review read an input that has changed since (server: staleness.check). Shown on the pass screen,
// so a screenshot of an outdated pass says so, and on /verify.
function staleBand(p) {
  if (!p.outdated) return "";
  return `<div class="staleband"><div class="k">⚠ Outdated pass</div><div class="m">${esc(p.outdated.message)}</div></div>`;
}

// ---- Locked passes (server: staleness.gate, 2026-11-05) -------------------------------------------------------
// After a contributor changes an input that an earlier pass read, every pass is locked (no code, no image) until the
// outdated stages are sent again; then they all unlock together. A first pass through the stages is never locked.
const passGate = () => (typeof state !== "undefined" && state.me && state.me.pass_gate) || null;

// "/a", "/a and /b", "/a, /b and /c"; html=true wraps each in a mono <b>.
function stageList(stages, html = false) {
  const names = stages.map(s => html ? `<b class="mono">/${esc(s)}</b>` : "/" + s);
  return names.length < 2 ? names.join("") : names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}
function laterNote(stages) {
  const list = stageList(stages);
  return `Your ${list} ${stages.length > 1 ? "passes are" : "pass is"} outdated: you changed your inputs after ${stages.length > 1 ? "they" : "it"} passed. ` +
    `Send ${list} again before you submit. Submitting older ${list} screenshots gets your task sent back and penalized.`;
}
function laterBand(p) {
  if (!(p.warn_later || []).length) return "";
  return `<div class="laterband"><div class="k">⚠ Also send ${p.warn_later.map(s => `<b class="mono">/${esc(s)}</b>`).join(", ")} again before you submit</div>
    <div class="m">${esc(laterNote(p.warn_later))}</div></div>`;
}

function lockStepsHtml() {
  return `<div class="lock-steps" aria-hidden="true">
    <div class="ls"><div class="ls-n">1</div><div class="ls-i">✏️</div><div class="ls-t">You changed an input<br><span>SQL, prompt, rubric or stumping</span></div></div>
    <div class="ls-arrow">→</div>
    <div class="ls"><div class="ls-n">2</div><div class="ls-i">🔁</div><div class="ls-t">Send the outdated stages<br><span>in the order shown</span></div></div>
    <div class="ls-arrow">→</div>
    <div class="ls"><div class="ls-n">3</div><div class="ls-i">🔓</div><div class="ls-t">Each card unlocks<br><span>as you go, in order</span></div></div>
  </div>`;
}

function lockChecklistHtml(todo, opts = {}) {
  const gate = passGate();
  const rows = gate ? gate.stages : (todo || []).map(s => ({ stage: s, status: "outdated" }));
  const next = (gate ? gate.todo : todo || [])[0];
  return `<div class="lock-list">${rows.map(r => {
    const name = STAGE_NAMES[r.stage] || r.stage;
    if (r.status === "outdated") return `<div class="lk-row todo"><span class="lk-ic">🔁</span><span class="lk-name">/${esc(r.stage)} <span class="muted">${esc(name)}</span></span>
      <span class="lk-why">${esc((r.changed || []).length ? "your " + r.changed.join(", ") + " changed" : "send it again")}</span>
      ${opts.buttons ? `<button class="small${r.stage === next ? " primary" : ""}" data-sendcmd="/${esc(r.stage)}">Send /${esc(r.stage)}</button>` : ""}</div>`;
    if (r.status === "current") return `<div class="lk-row ok"><span class="lk-ic">✅</span><span class="lk-name">/${esc(r.stage)} <span class="muted">${esc(name)}</span></span>
      <span class="lk-why">${esc(r.verdict || "")} · current</span></div>`;
    return `<div class="lk-row none"><span class="lk-ic">○</span><span class="lk-name">/${esc(r.stage)} <span class="muted">${esc(name)}</span></span>
      <span class="lk-why">not passed yet</span></div>`;
  }).join("")}</div>`;
}

function lockedProofHtml(p, opts = {}) {
  const stage = STAGE_NAMES[p.stage] || p.stage;
  const todo = (p.locked && p.locked.todo) || [];
  return `<div class="proof locked">
    <div class="head"><div class="left">
      <div class="brandline">Geranium Mambo Eval Portal</div>
      <div class="verdict"><span class="vbadge ${esc(p.verdict)}">${esc(p.verdict)}</span>${p.score != null ? `<span>Score ${esc(p.score)}</span>` : ""}</div>
      <div class="sub">/${esc(p.stage)} · ${esc(stage)} review</div></div></div>
    <div class="lockband"><div class="lb-ic">🔒</div><div><div class="k">Locked · not for upload</div>
      <div class="m">${p.locked && p.locked.self
        ? `This pass was graded on an older version of your inputs.`
        : `An earlier stage is outdated, and this card comes after it.`} Send ${todo.map(s => `<b class="mono">/${esc(s)}</b>`).join(", ")} again
        to unlock it.</div></div></div>
    ${opts.foot === false ? "" : `<div class="foot"><div class="grow muted">No verification code or image while locked.</div>
      <button data-close>Close</button></div>`}
  </div>`;
}

// The popup for a locked pass: what happened, the three steps, the live checklist with one-click commands.
function showLockedPass(p) {
  const todo = (p.locked && p.locked.todo) || [];
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  wrap.innerHTML = `<div class="lock-dialog" role="alertdialog" aria-labelledby="lock-title">
    <div class="lock-head"><span class="lock-big" aria-hidden="true">🔒</span><div>
      <div id="lock-title" class="lock-title">This pass card is locked for now</div>
      <div class="lock-sub">Your <b class="mono">/${esc(p.stage)}</b> ${esc(p.verdict)} is saved. You changed your inputs after some passes,
        so those (and the cards after them) no longer match what you'll submit.</div></div></div>
    ${lockStepsHtml()}
    <div class="lock-sec">Your checklist</div>
    ${lockChecklistHtml(todo, { buttons: typeof send === "function" })}
    <div class="lock-note">💡 <b>Tip:</b> send the stages again in order: each card unlocks as soon as it and the stages before it are current.
      Older pass images don't count: submitting one gets your task sent back and penalized, and the team is notified.</div>
    <div class="lock-foot"><button data-close>Got it</button></div></div>`;
  wrap.addEventListener("click", e => { if (e.target === wrap || e.target.closest("[data-close]") || e.target.closest("[data-sendcmd]")) wrap.remove(); });
  document.body.appendChild(wrap);
}

// Shown the moment a change makes earlier passes outdated (the chat's stale notice, staleness.notice): what changed,
// what to send again in order, and what happens if they don't.
function showOutdatedPopup() {
  const gate = passGate();
  if (!gate || !gate.todo.length) return;
  // A pass popup already open (with its own later-stage warning) says the same thing: don't stack another on it.
  if (document.querySelector(".overlay .lock-dialog, .overlay .taxo-step, .overlay .proof-stack")) return;
  const changed = [...new Set(gate.stages.flatMap(s => s.status === "outdated" ? s.changed || [] : []))];
  const what = changed.length ? changed.join(" and ") : "inputs";
  const first = gate.todo[0], list = stageList(gate.todo, true);
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  wrap.innerHTML = `<div class="lock-dialog" role="alertdialog" aria-labelledby="outdated-title">
    <div class="lock-head"><span class="lock-big" aria-hidden="true">✏️</span><div>
      <div id="outdated-title" class="lock-title">You changed your ${esc(what)}</div>
      <div class="lock-sub">${gate.todo.length} of your passes ${gate.todo.length > 1 ? "were" : "was"} graded on the old version, so ${gate.todo.length > 1 ? "they" : "it"} no longer count${gate.todo.length > 1 ? "" : "s"}.
        Their cards (and the cards after them) are 🔒 locked until you send them again.</div></div></div>
    ${lockStepsHtml()}
    <div class="lock-sec">Send these again, in this order</div>
    ${lockChecklistHtml(gate.todo, { buttons: typeof send === "function" })}
    <div class="lock-note penalty">🚫 <b>Re-run ${list} before you submit.</b> Submitting a task with an older pass screenshot gets it sent back
      and penalized. Submits are checked automatically and the team is notified.</div>
    <div class="lock-foot"><button data-close>Later</button>${typeof send === "function" ? `<button class="primary" data-sendcmd="/${esc(first)}">Send /${esc(first)} now</button>` : ""}</div></div>`;
  wrap.addEventListener("click", e => { if (e.target === wrap || e.target.closest("[data-close]") || e.target.closest("[data-sendcmd]")) wrap.remove(); });
  document.body.appendChild(wrap);
}

// Shown once when the last outdated stage passes: every pass is usable again.
function showPassesUnlocked() {
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  wrap.innerHTML = `<div class="lock-dialog unlocked" role="alertdialog" aria-labelledby="unlock-title">
    <div class="lock-head"><span class="lock-big" aria-hidden="true">🎉</span><div>
      <div id="unlock-title" class="lock-title">All your passes are unlocked</div>
      <div class="lock-sub">Every stage now matches your saved inputs. Open each pass from <b>Passes</b> on the left and upload it in your Outlier task.</div></div></div>
    ${lockChecklistHtml([], {})}
    <div class="lock-foot"><button class="primary" data-close>Show my passes</button></div></div>`;
  wrap.addEventListener("click", e => { if (e.target === wrap || e.target.closest("[data-close]")) wrap.remove(); });
  document.body.appendChild(wrap);
}

function proofHtml(p, opts = {}) {
  if (p.locked) return lockedProofHtml(p, opts);
  const t = p.totals || {};
  const total = (t.input || 0) + (t.output || 0) + (t.cache_read || 0) + (t.cache_creation || 0);
  const models = Object.entries(p.models || {}).map(([m, u]) => `${m} (${fmtInt(u.calls)} calls)`).join(", ") || "–";
  const hashes = Object.entries(p.report_hashes || {}).slice(0, 4)
    .map(([k, v]) => `<div class="wide"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
  const verifyUrl = `${location.origin}/verify/${encodeURIComponent(p.code)}`;
  const risky = !!p.fingerprint && screenshotRisky(shortFp(p.fingerprint));
  const stage = { injection: "Injection", prompt: "Prompt", hardness: "Hardness", oe: "Oracle Events", rubric: "Rubric" }[p.stage] || p.stage;
  // Screenshot-safe (2026-10-27): the taxonomy reads the code off a screenshot with GPT-4o, and it misread small
  // text in tall cards. So the card is short (header, the code large, four IDs) and everything else is folded
  // under "More details", which a screenshot doesn't need.
  return `<div class="proof">
    <div class="head">
      <div class="left">
        <div class="brandline">Geranium Mambo Eval Portal</div>
        <div class="verdict"><span class="vbadge ${esc(p.verdict)}">${esc(p.verdict)}</span>${p.score != null ? `<span>Score ${esc(p.score)}</span>` : ""}</div>
        <div class="sub">/${esc(p.stage)} · ${esc(stage)} review · ${esc(p.execution_status)}${opts.validity || ""}</div>
        ${p.checked_during ? `<div class="sub">Checked inside the /${esc(p.checked_during)} run · tokens and cost below are for that whole run</div>` : ""}
        ${p.admin_override ? `<div class="sub override">⚑ Admin override by ${esc(p.admin_override.by)}${p.admin_override.reason ? `: ${esc(p.admin_override.reason)}` : ""}.
          The review itself scored ${esc(p.admin_override.original_verdict || "–")}${p.admin_override.original_score != null ? ` ${esc(p.admin_override.original_score)}` : ""}.</div>` : ""}
      </div>
      ${p.fingerprint ? "" : `<div class="codebox"><div class="k">Verification code</div><div class="c">${esc(p.code)}</div></div>`}
    </div>
    ${staleBand(p)}
    ${p.fingerprint ? `<div class="fpband"><div class="k">🔏 Verification code · the ${esc(stage)} fingerprint, as your Outlier task shows it</div>
      <div class="fp" title="${esc(p.fingerprint)}">${esc(shortFp(p.fingerprint))}</div>
      ${spelledCode(shortFp(p.fingerprint)).map(l => `<div class="spell">${esc(l)}</div>`).join("")}</div>` : ""}
    <div class="grid ids">
      <div><div class="k">Attempt ID</div><div class="v">${esc(p.attempt_id)}</div></div>
      <div><div class="k">Outlier ID</div><div class="v">${esc(p.user_id)}</div></div>
      <div><div class="k">User</div><div class="v">${esc(p.user_email)}</div></div>
      <div><div class="k">Completed</div><div class="v">${esc(fmtUTC(p.finished_at))}</div></div>
    </div>
    ${laterBand(p)}
    <details class="more"${opts.open ? " open" : ""}><summary>More details</summary>
    ${rubricWeights(p.fingerprint) ? `<div class="wband"><div class="k">Rubric weights, one per row · not part of the verification code</div>
      <div class="w">${esc(rubricWeights(p.fingerprint))}</div></div>` : ""}
    <div class="grid">
      ${p.task_id ? `<div><div class="k">Task ID</div><div class="v">${esc(p.task_id)}</div></div>` : ""}
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
    </details>
    ${opts.foot === false ? "" : `<div class="foot${risky ? " risky" : ""}"><div class="grow">${risky
        ? `⚠ <b>Don't screenshot this one.</b> Outlier's reader often misreads the brackets or quotes in this code.
      Click <b>Download image</b> and upload that file in your Outlier task.`
        : `📸 <b>Screenshot this card</b> and upload it in your Outlier task.
      Or download the image, which is checked with Outlier's reader first.`}</div>
      ${COPYABLE.has(p.stage) && p.fingerprint ? `<button data-copypass>Copy what passed</button>` : ""}
      <button data-dlpass${risky ? ` class="primary"` : ""}>⬇ Download image</button><button data-close>Close</button></div>
      <div class="dlnote muted" data-dlnote hidden></div>`}
  </div>`;
}

// The pass screen, always in two steps, every time it opens and for everyone: first a reminder they must tick
// (the pass is for the exact version they sent, so that latest fixed version has to go into the taxonomy),
// then the card. The reminder sits outside the pass card (a cropped screenshot shows only the pass) and uses its
// own orange style, never a verdict colour.
function showProof(p) {
  if (p.locked) return showLockedPass(p);
  // An unacknowledged env data warning holds the /hardness pass (server/env_check.py, chat page only).
  if (p.stage === "hardness" && typeof showEnvAlert === "function" && state.me && !state.me.admin && state.me.env_alert)
    return showEnvAlert();
  const cmd = esc("/" + (p.stage || ""));
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  // Later stages outdated (Evan, 2026-11-05): before the pass, a warning they must tick, every time.
  const laterStep = () => {
    const later = p.warn_later, list = stageList(later, true);
    wrap.innerHTML = `<div class="lock-dialog warnlater" role="alertdialog" aria-labelledby="later-title">
      <div class="lock-head"><span class="lock-big" aria-hidden="true">⚠️</span><div>
        <div id="later-title" class="lock-title">Don't forget ${esc(stageList(later))}</div>
        <div class="lock-sub">Your <b class="mono">${cmd}</b> ${esc(p.verdict)} is ready. But your ${list} ${later.length > 1 ? "passes are" : "pass is"} outdated:
          you changed your inputs after ${later.length > 1 ? "they" : "it"} passed.</div></div></div>
      ${lockChecklistHtml(later, { buttons: typeof send === "function" })}
      <div class="lock-note penalty">🚫 <b>Send ${list} again before you submit.</b> If you submit with your older ${list} screenshots,
        your task gets sent back and you'll be penalized. Submits are checked automatically and the team is notified.</div>
      <label class="taxo-check later-check"><input type="checkbox"> I'll send ${later.length > 1 ? "them" : "it"} again before I submit</label>
      <div class="lock-foot"><button data-close>Close</button><button class="primary" data-show disabled>Show my ${cmd} pass →</button></div></div>`;
    const tick = wrap.querySelector(".later-check input"), show = wrap.querySelector("[data-show]");
    tick.onchange = () => { show.disabled = !tick.checked; };
    show.onclick = step;
  };
  const card = () => {
    wrap.innerHTML = `<div class="proof-stack">${proofHtml(p)}
      <div class="taxo-strip" role="note"><span class="taxo-icon" aria-hidden="true">⚠</span><div><b>Reminder:</b> your Outlier task must have
        this exact <b class="mono">${cmd}</b> version. Update it before you submit; a mismatch gets your task flagged.</div></div></div>`;
  };
  const step = () => {
    wrap.innerHTML = `<div class="taxo-step" role="alertdialog" aria-labelledby="taxo-title">
      <div class="taxo-head"><span class="taxo-icon" aria-hidden="true">⚠</span><span id="taxo-title">Before you screenshot</span></div>
      <div class="taxo-body">
        ${p.outdated ? `<p class="taxo-warn">⚠ This pass is outdated. ${esc(p.outdated.message)}</p>` : ""}
        <p>This pass is for the exact <b class="mono">${cmd}</b> you sent in this review.</p>
        <p>If you fixed it here to pass, put that <b>latest fixed version into your Outlier task</b>. Don't forget to update it.</p>
        ${p.prompt_fingerprint && (p.stage === "oe" || p.stage === "rubric") ? `<div class="taxo-warn prompt-bound"><b>This pass is only for this prompt:</b>
          <div class="mono" style="margin:4px 0">${esc(p.prompt_fingerprint)}</div>
          Your Outlier task's prompt must be <b>exactly this version</b>, together with the SQL and OE this review read. If you
          submit a different prompt (an older one with its old /prompt pass, for example), the task gets sent back.</div>` : ""}
        <p class="taxo-warn">If your Outlier task doesn't match the version that passed here, we will know, and your task will be flagged.</p>
        <p class="taxo-note">If you change it again, run <b class="mono">${cmd}</b> again.</p>
        <label class="taxo-check"><input type="checkbox"> ${p.prompt_fingerprint && (p.stage === "oe" || p.stage === "rubric")
          ? "I've updated my Outlier task, and its prompt is this exact version" : "I've updated my Outlier task"}</label>
      </div>
      <div class="taxo-foot"><button data-close>Close</button><button class="primary" data-show disabled>Show my pass →</button></div></div>`;
    const tick = wrap.querySelector(".taxo-check input"), show = wrap.querySelector("[data-show]");
    tick.onchange = () => { show.disabled = !tick.checked; };
    show.onclick = INJECT_REMINDER_STAGES.has(p.stage) && (p.stage === "prompt" || p.stage === "hardness" || hasSavedSql()) ? injectStep : card;
  };
  // After a /prompt or /hardness pass the next step happens in sandboxes (Universe Explorer, Agent Runner), and CBs kept
  // forgetting to run their injection SQL there first (Evan, 2026-11-03). One more step, every such pass.
  const injectStep = () => {
    const sql = hasSavedSql();
    wrap.innerHTML = injectGuideHtml({ lead: sql ? OUTLIER_SQL_LEAD : "", ok: "Show my pass →",
      check: sql ? "I'll redeploy from the base universe, run only my final SQL, and answer Yes on Outlier with this same SQL"
                 : "I'll run my injection SQL in every sandbox I open" });
    const tick = wrap.querySelector(".taxo-check input"), show = wrap.querySelector("[data-show]");
    tick.onchange = () => { show.disabled = !tick.checked; };
    show.onclick = card;
  };
  (p.warn_later || []).length ? laterStep() : step();
  wrap.addEventListener("click", e => {
    if (e.target === wrap || e.target.closest("[data-close]") || e.target.closest("[data-sendcmd]")) wrap.remove();
    else if (e.target.closest("[data-dlpass]")) downloadPassImage(p, wrap);
    else if (e.target.closest("[data-copypass]")) copyPassInput(p, wrap);
  });
  document.body.appendChild(wrap);
}

// ---- The pass image ----------------------------------------------------------------------------------------
// What the contributor uploads in the taxonomy instead of their own screenshot. A screenshot's size depends on the
// screen and the crop, and small text is where the taxonomy's GPT-4o reader misread the code (a zoomed-in
// screenshot of the same pass went through, 2026-10-27). So the portal draws the pass itself: the green header the
// reader looks for (verdict badge, "/stage · Stage review · COMPLETE"), then the 🔏 verification code large and in
// plain characters with its first and last characters spelled out (spelledCode), then the details. Before the download it's read with the taxonomy's own reader and prompt
// (server/taxo_reader.py); if that misreads, it's drawn again with a bigger code, up to 3 times.
const COPYABLE = new Set(["injection", "prompt", "oe"]);
// /oe and /rubric too (Evan, 2026-11-06): contributors who redo a task after passing /rubric go back into sandboxes.
// Those two show it only when the task has injection SQL saved.
const INJECT_REMINDER_STAGES = new Set(["prompt", "hardness", "oe", "rubric"]);
function hasSavedSql() {
  return typeof state !== "undefined" && !!state.me && (state.me.inputs || []).some(i => i.key === "injection" && i.state === "saved");
}
// The platform side (QC 6aec82c2, 2026-11-06: the SQL was in the portal, but Outlier said "did you inject: no").
const OUTLIER_SQL_LEAD = `<p class="taxo-warn"><b>On Outlier, use this exact SQL.</b> Paste your <b>final</b> injection SQL (the version that passed
  <b class="mono">/injection</b>) and answer <b>Yes</b> to “did you inject”. Before any run on the platform, redeploy the sandbox from the
  <b>base universe</b> (never a snapshot) and run only that SQL, once. Data you add any other way (by hand in the explorer, or left
  from an older draft) gets the task failed in QC.</p>`;

// "Inject your SQL in every sandbox" (2026-11-03): on /prompt and /hardness pass screens, before every /hardness run,
// and after a /hardness report that found the run had no injection. lead = an extra first paragraph.
// Redeploy from the base universe, never a snapshot (Evan, 2026-11-06): a snapshot can be gone ("Artifact
// snapshot-hg4-with-gsuite-bundle-1174-e51786a0 version=16 not found") and it brings back old rows (QC 6aecc5e4).
const REDEPLOY_BASE = `<p class="taxo-warn"><b>Always redeploy from the base universe, never from a snapshot.</b> Under <b>Redeploy from</b>,
  look at the name under <b>Universe this environment was deployed with</b>. If it's <b class="mono">hg4-synth-sep30cut</b> (task date
  Oct 1, 2025) or <b class="mono">hg4-2026-08-31</b> (Feb 14, 2026), keep it. If it starts with <b class="mono">snapshot-</b>, pick
  <b>A specific universe or snapshot id</b> instead and paste the base universe for your task date. A snapshot can be deleted
  (the "Artifact snapshot-… not found" error) and it still has the old rows in it.</p>`;
function injectGuideHtml({ lead = "", check, ok, cancel = "Close" }) {
  const shot = (file, text) => `<li><p>${text}</p><img src="/static/img/${file}" alt="${esc(text.replace(/<[^>]+>/g, ""))}" loading="lazy"></li>`;
  return `<div class="taxo-step inject-step" role="alertdialog" aria-labelledby="inject-title">
    <div class="taxo-head"><span class="taxo-icon" aria-hidden="true">💉</span><span id="inject-title">Inject your SQL in every sandbox</span></div>
    <div class="taxo-body">
      ${lead}
      <p><b>Every time you open a sandbox</b> (the <b>Universe Explorer</b> or the <b>Agent Runner</b>), run your injection SQL in it
        <b>before you do anything else</b>. Each sandbox starts from the plain universe and the portal does not load your SQL for you,
        so without it your injected records aren't there. Not needed in the Rubric Verifier.</p>
      <ol class="inject-steps">
        ${shot("inject_1_query_tab.png", "Open the <b>Database</b> tab (1), then the <b>Query</b> tab (2).")}
        ${shot("inject_2_run_query.png", "Paste your whole injection SQL, the version that passed <b class=\"mono\">/injection</b> (3), and press <b>Run Query</b> (4).")}
        ${shot("inject_3_no_records.png", "<b>No records found</b> (5) is normal: an insert or update returns no rows. It is not an error.")}
        ${shot("inject_4_changelog.png", "To check it worked, open <b>Tables → _changelog</b> (6): one row for each change you made (7).")}
      </ol>
      <div class="inject-fresh"><p><b>Changed your SQL? Redeploy the sandbox first.</b> If you edited your injection SQL since you
        ran it in this sandbox (or since your last Opus run), don't run the new version on top of the old one, and don't try to
        delete the old rows by hand: inserts with the same ids fail and updates apply twice, so the run still won't match your task.</p>
        <p>Open the <b>Settings</b> tab (1), choose the base universe under <b>Redeploy from</b> (below), and press
        <b>Redeploy Environment</b> (2). It can take about 30 minutes and gives you a clean sandbox from the plain universe. Then run
        your <b>current</b> SQL once (steps 1–4 above), check <b>_changelog</b>, and make your Opus run there.</p>
        ${REDEPLOY_BASE}
        <img src="/static/img/inject_5_redeploy.png" alt="Settings tab (1), then Redeploy Environment (2)" loading="lazy"></div>
      ${check ? `<label class="taxo-check"><input type="checkbox"> ${esc(check)}</label>` : ""}
    </div>
    <div class="taxo-foot"><button data-close>${esc(cancel)}</button>${ok ? `<button class="primary" data-show${check ? " disabled" : ""}>${esc(ok)}</button>` : ""}</div></div>`;
}

// The guide on its own: resolves true when they press the main button (after ticking the box, if there is one).
function showInjectGuide(opts) {
  return new Promise(resolve => {
    const wrap = document.createElement("div");
    wrap.className = "overlay";
    wrap.innerHTML = injectGuideHtml(opts);
    const tick = wrap.querySelector(".taxo-check input"), show = wrap.querySelector("[data-show]");
    if (tick && show) tick.onchange = () => { show.disabled = !tick.checked; };
    const done = v => { wrap.remove(); resolve(v); };
    wrap.addEventListener("click", e => {
      if (e.target === wrap || e.target.closest("[data-close]")) done(false);
      else if (e.target.closest("[data-show]") && !e.target.closest("[data-show]").disabled) done(true);
    });
    document.body.appendChild(wrap);
  });
}
const INJECT_AFTER_LEAD = {
  missing: `<p class="taxo-warn">This /hardness review found that your Opus run was made <b>without your injection</b>,
    so it can't score it. Make a new run in a sandbox where you ran your SQL first, upload it, and send /hardness again.</p>`,
  stale: `<p class="taxo-warn">This /hardness review found that your Opus run was made on an <b>older version of your SQL</b>
    than the one saved now, so it doesn't match your task. <b>Redeploy</b> the sandbox (Settings → Redeploy Environment, see the
    blue box below), run your current SQL there, make a new Opus run, upload it, and send /hardness again.</p>`,
};
function showInjectGuideAfterHardness(kind = "missing") {
  if (kind === "steps") return showInjectGuide({ ok: "Got it", cancel: "Close" });
  return showInjectGuide({ lead: INJECT_AFTER_LEAD[kind] || INJECT_AFTER_LEAD.missing, ok: "Got it", cancel: "Close" });
}
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", e => {
    const b = e.target.closest?.("[data-injectguide]");
    if (b) { e.preventDefault(); showInjectGuideAfterHardness(b.dataset.injectguide || "missing"); }
  });
}  // their pass screen reminds them to inject in every sandbox
// "Sandbox not working?" (Evan, 2026-11-04): Snapshot failed (502), a run stuck at Loading data, 500s, an expired
// environment. Same fix almost every time: hard refresh, then Settings → Redeploy Environment, then the SQL again.
// Opens on the reply to an /admin request that reads like one (server/env_help.py) and from its button.
function envGuideHtml() {
  const keys = (...k) => k.map(x => `<kbd>${x}</kbd>`).join(" + ");
  return `<div class="taxo-step inject-step" role="alertdialog" aria-labelledby="env-title">
    <div class="taxo-head"><span class="taxo-icon" aria-hidden="true">🛠️</span><span id="env-title">Sandbox not working? Do these in order</span></div>
    <div class="taxo-body">
      <p>For <b>Snapshot failed (502)</b>, a run stuck at <b>Loading data</b> or <b>Waiting for the agent's first steps</b>,
        <b>500 / 502 errors</b> in the Universe Explorer, or an <b>expired</b> environment. Try the next step only if the one before didn't fix it.</p>
      <ol class="inject-steps env-steps">
        <li><p><b>Hard refresh the page</b> (the Universe Explorer or Agent Runner tab), then try again:</p>
          <ul class="env-keys">
            <li><b>Mac:</b> ${keys("⌘ Cmd", "⇧ Shift", "R")}</li>
            <li><b>Windows / Linux:</b> ${keys("Ctrl", "Shift", "R")} &nbsp;or&nbsp; ${keys("Ctrl", "F5")}</li>
          </ul>
          <p class="taxo-note">If it says <i>your browser can't reach the environment's host</i>, turn off your VPN or ad-blocker for the site, then refresh.</p></li>
        <li><p><b>Redeploy the sandbox.</b> Open the <b>Settings</b> tab (1), choose the base universe under <b>Redeploy from</b>,
          and press <b>Redeploy Environment</b> (2). It can take about <b>30 minutes</b>: wait until it finishes and don't press it again meanwhile.</p>
          ${REDEPLOY_BASE}
          <img src="/static/img/inject_5_redeploy.png" alt="Settings tab (1), then Redeploy Environment (2)" loading="lazy"></li>
        <li><p><b>Run your injection SQL again</b> in the new sandbox: it starts from the plain universe, so your records are gone.
          Paste your <b>current</b> SQL in <b>Database → Query</b>, press <b>Run Query</b> once, and check <b>Tables → _changelog</b>.
          Then start your run.</p>
          <p><button class="small" data-injectguide="steps">Show the injection steps</button></p></li>
        <li><p><b>Still broken?</b> Type <b class="mono">/admin</b> with the exact error text, which button you pressed, and the
          <b>Sandbox ID</b> from the Settings tab. Tell us what you already tried (steps 1–3).</p></li>
      </ol>
    </div>
    <div class="taxo-foot"><button class="primary" data-close>Got it</button></div></div>`;
}
function showEnvGuide() {
  if (typeof document === "undefined" || document.querySelector(".env-steps")) return;  // one at a time
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  wrap.innerHTML = envGuideHtml();
  wrap.addEventListener("click", e => { if (e.target === wrap || e.target.closest("[data-close]")) wrap.remove(); });
  document.body.appendChild(wrap);
}
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", e => {
    const b = e.target.closest?.("[data-envguide]");
    if (b) { e.preventDefault(); showEnvGuide(); }
  });
}
const CHECKABLE = new Set(["injection", "prompt", "rubric", "stumping", "all-fail-validation", "reference-notes"]);
const PASS_IMAGE_SIZES = [44, 56, 68];  // code font sizes, px, tried in order
const STAGE_NAMES = {
  injection: "Injection",
  prompt: "Prompt",
  rubric: "Rubric",
  stumping: "Stumping",
  "all-fail-validation": "All-Fail Validation",
  "reference-notes": "Reference Notes",
};

function wrapCanvasText(ctx, text, maxWidth) {
  // Break at spaces (a long head/tail without spaces is broken anywhere); prefer the code's own " | " and " - ".
  const lines = [];
  let line = "";
  for (const word of text.split(/(?<= )/)) {
    if (ctx.measureText(line + word).width <= maxWidth || !line) { line += word; continue; }
    lines.push(line.trimEnd());
    line = word;
  }
  if (line) lines.push(line.trimEnd());
  return lines.flatMap(l => {
    if (ctx.measureText(l).width <= maxWidth) return [l];
    const out = []; let cur = "";
    for (const ch of Array.from(l)) { if (ctx.measureText(cur + ch).width > maxWidth && cur) { out.push(cur); cur = ""; } cur += ch; }
    return cur ? [...out, cur] : out;
  });
}

async function passImage(p, codeSize) {
  const mono = '"Aeonik Mono", ui-monospace, Menlo, Consolas, monospace', sans = 'Aeonik, Arial, sans-serif';
  const codeFont = `${codeSize}px ${mono}`;
  try { await Promise.all([document.fonts.load(codeFont), document.fonts.load('22px "Aeonik Mono"'), document.fonts.load('500 40px Aeonik')]); } catch (e) {}
  const W = 1400, pad = 56, inner = W - 2 * pad;
  const stage = STAGE_NAMES[p.stage] || p.stage;
  const code = p.fingerprint ? shortFp(p.fingerprint) : p.code;
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = codeFont;
  const codeLines = wrapCanvasText(measure, code, inner);
  measure.font = `24px ${sans}`;
  const staleLines = p.outdated ? wrapCanvasText(measure, p.outdated.message || "", inner) : [];
  const spellLines = spelledCode(code).flatMap(l => wrapCanvasText(measure, l, inner));
  const details = [["Attempt ID", p.attempt_id], ["Outlier ID", p.user_id], ["User", p.user_email],
    ["Completed", fmtUTC(p.finished_at)], ["Report SHA-256", p.report_sha256]].filter(d => d[1]);
  const headH = 250 + (p.checked_during ? 34 : 0) + (p.admin_override ? 34 : 0);
  const staleH = staleLines.length ? 70 + staleLines.length * 32 : 0;
  // Later stages outdated (staleness.later_outdated): drawn at the bottom, away from the verdict and code the reader reads.
  const laterLines = (p.warn_later || []).length ? wrapCanvasText(measure, laterNote(p.warn_later), inner) : [];
  const laterH = laterLines.length ? 70 + laterLines.length * 32 : 0;
  const spellAt = 96 + codeLines.length * Math.round(codeSize * 1.3);
  const codeH = spellAt + (spellLines.length ? spellLines.length * 34 + 8 : 0);
  const H = headH + staleH + codeH + 60 + details.length * 40 + laterH + 70;
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#FFFFFF"; ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = "alphabetic";
  // green header
  ctx.fillStyle = "#193A29"; ctx.fillRect(0, 0, W, headH);
  ctx.fillStyle = "rgba(255,255,255,0.8)"; ctx.font = `22px ${mono}`;
  ctx.fillText("ORIGINAL CONFERENCE EVAL PORTAL", pad, 70);
  ctx.font = `500 46px ${sans}`;
  const badgeW = ctx.measureText(p.verdict).width + 56;
  ctx.fillStyle = p.verdict === "NON-FAIL" ? "#EDE272" : "#79C96E";
  ctx.beginPath(); ctx.roundRect(pad, 100, badgeW, 72, 36); ctx.fill();
  ctx.fillStyle = "#000000"; ctx.fillText(p.verdict, pad + 28, 152);
  if (p.score != null) { ctx.fillStyle = "#FFFFFF"; ctx.font = `46px ${sans}`; ctx.fillText(`Score ${p.score}`, pad + badgeW + 24, 152); }
  ctx.fillStyle = "#FFFFFF"; ctx.font = `28px ${sans}`;
  let y = 214;
  ctx.fillText(`/${p.stage} · ${stage} review · ${p.execution_status}`, pad, y);
  ctx.font = `22px ${sans}`;
  if (p.checked_during) { y += 34; ctx.fillText(`Checked inside the /${p.checked_during} run`, pad, y); }
  if (p.admin_override) { y += 34; ctx.fillText(`Admin override by ${p.admin_override.by}`, pad, y); }
  y = headH;
  if (staleLines.length) {
    ctx.fillStyle = "#FDECEC"; ctx.fillRect(0, y, W, staleH);
    ctx.fillStyle = "#8A1C22"; ctx.font = `500 24px ${sans}`; ctx.fillText("⚠ OUTDATED PASS", pad, y + 44);
    ctx.font = `24px ${sans}`; staleLines.forEach((l, i) => ctx.fillText(l, pad, y + 80 + i * 32));
    y += staleH;
  }
  // the verification code
  ctx.fillStyle = "#F6FAF7"; ctx.fillRect(0, y, W, codeH);
  ctx.fillStyle = "#575757"; ctx.font = `22px ${mono}`;
  ctx.fillText(p.fingerprint ? `🔏 VERIFICATION CODE · THE ${stage.toUpperCase()} FINGERPRINT, AS YOUR OUTLIER TASK SHOWS IT` : "VERIFICATION CODE", pad, y + 52);
  ctx.fillStyle = "#000000"; ctx.font = codeFont;
  codeLines.forEach((l, i) => ctx.fillText(l, pad, y + 72 + codeSize + i * Math.round(codeSize * 1.3)));
  ctx.fillStyle = "#333333"; ctx.font = `24px ${sans}`;
  spellLines.forEach((l, i) => ctx.fillText(l, pad, y + spellAt + 4 + i * 34));
  y += codeH;
  ctx.fillStyle = "#EAEAEA"; ctx.fillRect(0, y, W, 2);
  // details
  y += 60;
  details.forEach(([k, v]) => {
    ctx.fillStyle = "#575757"; ctx.font = `20px ${mono}`; ctx.fillText(k.toUpperCase(), pad, y);
    ctx.fillStyle = "#000000"; ctx.font = `24px ${mono}`; ctx.fillText(String(v), pad + 280, y);
    y += 40;
  });
  ctx.fillStyle = "#575757"; ctx.font = `20px ${sans}`;
  if (laterLines.length) {
    const top = H - 70 - laterH;
    ctx.fillStyle = "#FFF1E4"; ctx.fillRect(0, top, W, laterH);
    ctx.fillStyle = "#9A4A06"; ctx.font = `500 24px ${sans}`;
    ctx.fillText(`⚠ ALSO SEND ${p.warn_later.map(s => "/" + s.toUpperCase()).join(", ")} AGAIN BEFORE YOU SUBMIT`, pad, top + 44);
    ctx.fillStyle = "#000000"; ctx.font = `24px ${sans}`; laterLines.forEach((l, i) => ctx.fillText(l, pad, top + 80 + i * 32));
  }
  ctx.fillText(`Pass image from the Geranium Mambo Eval Portal · issued ${fmtUTC(p.issued_at || p.finished_at)}`, pad, H - 34);
  return canvas;
}

const PASS_IMAGE_OK = {};  // pass code -> data URL of the image the reader read correctly (this page load)

async function downloadPassImage(p, root) {
  const button = root.querySelector("[data-dlpass]"), note = root.querySelector("[data-dlnote]");
  const say = (html, cls = "") => { note.hidden = false; note.className = `dlnote ${cls}`; note.innerHTML = html; };
  const name = `pass-${p.stage}-${p.attempt_id}.png`;
  const save = url => { const a = document.createElement("a"); a.href = url; a.download = name; a.click(); };
  if (PASS_IMAGE_OK[p.code]) { save(PASS_IMAGE_OK[p.code]); return say("✓ Downloaded. Upload this image in your Outlier task.", "ok"); }
  button.disabled = true;
  let url = null, last = null;
  try {
    for (const size of PASS_IMAGE_SIZES) {
      say(`Checking the image with Outlier's reader…`);
      url = (await passImage(p, size)).toDataURL("image/png");
      last = await api(`/api/proofs/${encodeURIComponent(p.code)}/reader-check`, { json: { image: url } });
      if (last.ok !== false) break;  // read correctly, or the check couldn't run
    }
  } catch (e) {
    last = { ok: null, error: e.message };
    if (!url) { button.disabled = false; return say(`Couldn't make the image: ${esc(e.message)}`, "bad"); }
  }
  button.disabled = false;
  save(url);
  if (last.ok) {
    PASS_IMAGE_OK[p.code] = url;
    say("✓ Downloaded. Outlier's reader reads this image correctly. Upload it in your Outlier task.", "ok");
  } else if (last.ok === null) {
    say("Downloaded. The reader check couldn't run right now, but this image is made for it. Upload it in your Outlier task.");
  } else {
    say(`Downloaded, but in our check the reader read <span class="mono">${esc(last.read || "nothing")}</span> instead of
      <span class="mono">${esc(last.expected)}</span>. If Outlier blocks it, type <b class="mono">/admin</b> with your attempt ID.`, "bad");
  }
}

async function copyPassInput(p, root) {
  const note = root.querySelector("[data-dlnote]");
  const say = (html, cls = "") => { note.hidden = false; note.className = `dlnote ${cls}`; note.innerHTML = html; };
  try {
    const text = await api(`/api/proofs/${encodeURIComponent(p.code)}/input`);
    await navigator.clipboard.writeText(text);
    say(`✓ Copied the exact ${esc(STAGE_NAMES[p.stage] || p.stage)} this pass graded (${fmtInt(text.length)} characters).
      In that field in your Outlier task press <b>Ctrl/⌘+A</b>, then paste, so nothing old is left.`, "ok");
  } catch (e) {
    say(esc(e.message), "bad");
  }
}

// ---- "Check my taxonomy": paste the taxonomy field, see if it matches this pass -------------------------------
// The linter's own fingerprint (server/taxo_check.py), so "it's identical" can be checked before pressing Next.
// It sits under the card, outside it, so a screenshot of the card never shows it.
function showTaxoCheck(p) {
  const wrap = document.createElement("div");
  wrap.className = "overlay";
  wrap.innerHTML = `<div class="check-panel" data-checkpanel></div>`;
  wrap.addEventListener("click", e => {
    if (e.target === wrap || e.target.closest("[data-close]")) wrap.remove();
    else if (e.target.closest("[data-runcheck]")) runTaxoCheck(p, wrap);
  });
  document.body.appendChild(wrap);
  openTaxoCheck(p, wrap);
}

function openTaxoCheck(p, root) {
  const panel = root.querySelector("[data-checkpanel]");
  const what = p.stage === "rubric" ? "the rubric's whole <b>JSON view</b> from your Outlier task"
    : `the whole <b>${esc(STAGE_NAMES[p.stage] || p.stage)}</b> field from your Outlier task (Ctrl/⌘+A, copy)`;
  panel.hidden = false;
  panel.innerHTML = `<div class="k">Check my task in Outlier</div>
    <p>Paste ${what}. This checks it the same way Outlier's check does.</p>
    <textarea data-checktext rows="6" spellcheck="false" placeholder="Paste here"></textarea>
    <div class="row"><button class="primary" data-runcheck>Check</button><button data-close>Close</button><span class="muted" data-checkresult></span></div>
    <p class="muted">/${esc(p.stage)} pass · <span class="mono">${esc(shortFp(p.fingerprint))}</span></p>`;
  panel.querySelector("textarea").focus();
}

// Which rubric rows differ (server taxo_check.rubric_rows): formatting, escapes and links already ignored.
function rubricRowsHtml(d) {
  const t = s => `<span class="mono">“${esc(s || "")}”</span>`;
  const line = r => r.kind === "changed" ? `<li><b>Row ${r.taxonomy_row}</b> in your Outlier task differs from row ${r.passed_row} that passed:
        <div>Outlier task: ${t(r.taxonomy_title)}</div><div>Passed: ${t(r.passed_title)}</div></li>`
    : r.kind === "extra" ? `<li><b>Row ${r.taxonomy_row}</b> is in your Outlier task but not in what passed: ${t(r.taxonomy_title)}</li>`
    : r.kind === "missing" ? `<li>Row ${r.passed_row} that passed is <b>missing from your Outlier task</b>: ${t(r.passed_title)}</li>`
    : `<li><b>Row ${r.taxonomy_row}</b>: weight ${esc(r.taxonomy_weight)} in your Outlier task, ${esc(r.passed_weight)} in what passed: ${t(r.taxonomy_title)}</li>`;
  return `<br>Rows: ${fmtInt(d.taxonomy_count)} in your Outlier task, ${fmtInt(d.passed_count)} passed.
    ${d.total ? `<ul class="rows">${d.rows.map(line).join("")}</ul>${d.total > d.rows.length ? `<div>…and ${fmtInt(d.total - d.rows.length)} more.</div>` : ""}`
      : "<br>Every row's title matches; the difference is in the weights."}`;
}

async function runTaxoCheck(p, root) {
  const panel = root.querySelector("[data-checkpanel]");
  const text = panel.querySelector("[data-checktext]").value, out = panel.querySelector("[data-checkresult]");
  if (!text.trim()) { out.textContent = "Paste the text from your Outlier task first."; return; }
  out.className = "muted"; out.textContent = "Checking…";
  try {
    const r = await api(`/api/proofs/${encodeURIComponent(p.code)}/check-input`, { json: { text } });
    if (r.match) {
      out.className = "ok";
      out.innerHTML = r.links_ignored
        ? `✓ Matches this pass: same rows, titles and weights. Links aren't compared, because Outlier's JSON view shortens them.`
        : `✓ Matches this pass. Your Outlier task's code will be <span class="mono">${esc(r.taxonomy)}</span>.`;
      return;
    }
    const d = r.difference;
    const mark = (s, at) => `${esc(s.slice(0, at))}<mark>${esc(s.slice(at, at + 1) || "∅")}</mark>${esc(s.slice(at + 1))}`;
    out.className = "bad";
    out.innerHTML = `✗ Doesn't match. Your Outlier task: <span class="mono">${esc(r.taxonomy)}</span> · this pass: <span class="mono">${esc(r.passed)}</span>.
      ${d ? `<br>First difference at character ${fmtInt(d.at)} (Outlier task ${fmtInt(d.taxonomy_chars)} chars, passed ${fmtInt(d.passed_chars)}):
        <div class="diff"><div><b>Outlier task</b> <span class="mono">…${mark(d.taxonomy_text, d.offset)}…</span></div>
        <div><b>Passed</b> <span class="mono">…${mark(d.passed_text, d.offset)}…</span></div></div>` : ""}
      ${r.rubric_rows ? rubricRowsHtml(r.rubric_rows) : p.stage === "rubric" ? "<br>Your saved rubric here has changed since this pass, so the rows can't be listed." : ""}
      ${COPYABLE.has(p.stage) ? "<br>Use <b>Copy what passed</b> and paste it over the whole field in your Outlier task." : "<br>Fix those rows in your Outlier task so they match what passed, or run the review again on what's there."}`;
  } catch (e) {
    out.className = "bad"; out.textContent = e.message;
  }
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

// ---- guide links (the chat page's Guide panel: app.js openGuide) ----
// A link to /?guide=<tab>#<heading-slug> opens that Guide tab scrolled to the heading; the chat page catches the click,
// and a fresh load of that URL opens it too. Slugs are GitHub-style ("Part 8. If the run comes back too easy" ->
// "part-8-if-the-run-comes-back-too-easy"), and a link may use just the start of one ("#part-8").
function guideSlug(text) {
  return String(text ?? "").toLowerCase().trim().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s+/g, "-");
}
const TOO_EASY_LINK = "/?guide=hardness#part-8-if-the-run-comes-back-too-easy";
// A /hardness report that came back too easy: a FAIL, or score 1 (Too easy) or 2 (Easy).
function hardnessTooEasy(meta) {
  return !!meta && meta.stage === "hardness" && (meta.verdict === "FAIL" || meta.score === 1 || meta.score === 2);
}
function tooEasyHint() {
  return `<div class="guide-hint">Too easy? See the <a href="${TOO_EASY_LINK}">hardness guide, Part 8. If the run comes back too easy</a></div>`;
}
// The two /hardness checks with their own guide (2026-11-04), read from the report text so older reports get them too.
// Tool calls: the FAIL's "Why Easy (tool calls)" paragraph. Failure pattern: the ⚠ warning or a "Failure-pattern risk: YES"
// line, however the report bolds it ("Failure-pattern risk: **YES**", "**Failure-pattern risk: YES (advice only).**").
const TOOLCALLS_LINK = "/?guide=toolcalls#part-4-what-adds-real-calls-do-these";
const PATTERNS_LINK = "/?guide=patterns#4-build-at-least-three-traps-that-split-the-rollouts";
function hardnessToolCallFail(m) {
  return !!m && (m.meta || {}).stage === "hardness" && /Why Easy \(tool calls\)/.test(m.content || "");
}
function hardnessPatternRisk(m) {
  return !!m && (m.meta || {}).stage === "hardness"
    && /⚠\s*\**\s*Failure-pattern risk|Failure-pattern risk[\s*:]*YES\b/.test(m.content || "");
}
function toolCallHint() {
  return `<div class="guide-hint">Too few tool calls? See the <a href="${TOOLCALLS_LINK}">tool calls guide, Part 4. What adds real calls</a></div>`;
}
function patternRiskHint() {
  return `<div class="guide-hint">Failure-pattern risk? See the <a href="${PATTERNS_LINK}">failure patterns guide, 4. Build at least three traps that split the rollouts</a></div>`;
}
// ---- end guide links ----

// One chat message -> HTML. Used by the user chat and the admin view.
function messageHtml(m, opts = {}) {
  const meta = m.meta || {};
  const ts = msgTime(m.created_at);
  if (m.role === "user") {
    const text = m.content || "";
    const long = text.length > 1800;
    const files = (meta.files || []).length ? `<div class="attach-list">📎 ${meta.files.map(esc).join(", ")}</div>` : "";
    const adm = meta.command === "admin";  // an /admin request: marked so admins can find it (admin.js markAdminMessages)
    return `<div class="msg user${adm ? " adminmsg" : ""}"${adm ? ` data-adminmsg="${m.id}" data-at="${m.created_at}"` : ""}><div class="bubble">${
      opts.admin ? `<div class="who">${adm ? '<span class="adm-tag">🚨 /admin request</span> · ' : ""}User</div>` : ""}${
      long ? `<span class="clip">${esc(text.slice(0, 1800))}…</span><span class="full hidden">${esc(text)}</span>
      <div class="more" data-expand>Show all (${fmtInt(text.length)} characters)</div>` : esc(text)}${files}<div class="ts-row">${ts}</div></div></div>`;
  }
  if (m.role === "assistant") return `<div class="msg assistant"><div class="bubble"><div class="who">Eval assistant ${ts}</div>${md(m.content)}</div></div>`;
  if (m.role === "admin") return `<div class="msg admin"><div class="bubble"><div class="who">Admin${opts.admin && meta.admin ? ` · ${esc(meta.admin)}` : ""} ${ts}</div>${md(m.content)}</div></div>`;
  if (m.role === "report") {
    const label = STAGE_NAMES[meta.stage] || meta.stage;
    return `<div class="msg system"><div class="bubble"><div class="rcard"><div class="grow">📋 <b>${esc(label)} report</b> ${pill(meta.verdict)}
      ${meta.score != null ? `<span class="muted">score ${esc(meta.score)}</span>` : ""}
      ${meta.exec_status && meta.exec_status !== "COMPLETE" ? `<span class="muted">· ${esc(meta.exec_status)}</span>` : ""}</div>
      ${ts}<button data-report="${m.id}">View</button><button data-download="${m.id}">Download</button></div>
      ${meta.fingerprint ? `<div class="rfp" title="${esc(meta.fingerprint)}">🔏 ${esc(shortFp(meta.fingerprint))}</div>` : ""}
      ${!opts.admin && !meta.injection_missing && !meta.injection_stale
        ? (hardnessToolCallFail(m) ? toolCallHint() : "") + (hardnessTooEasy(meta) ? tooEasyHint() : "")
          + (hardnessPatternRisk(m) ? patternRiskHint() : "") : ""}
      ${["stumping", "hardness"].includes(meta.stage) && meta.injection_missing ? `<div class="stale-line">💉 Your runs were made without your injection.
        <button class="small" data-injectguide="missing">How to inject</button></div>`
      : ["stumping", "hardness"].includes(meta.stage) && meta.injection_stale ? `<div class="stale-line">💉 Your runs were made on an older version of your SQL.
        <button class="small" data-injectguide="stale">How to re-inject</button></div>` : ""}</div></div>`;
  }
  if (m.role === "proof" && meta.locked) {
    return `<div class="msg system"><div class="bubble proofcard lockedcard"><div class="rcard"><div class="grow">🔒 <b>/${esc(meta.stage)} ${esc(meta.verdict)}</b>
      <span class="pill LOCKED">LOCKED</span><span class="muted"> no code or image until ${(meta.locked.todo || []).map(s => `<b class="mono">/${esc(s)}</b>`).join(", ")} ${(meta.locked.todo || []).length > 1 ? "are" : "is"} sent again</span></div>${ts}
      <button class="primary" data-proof="${m.id}">What do I do?</button></div></div></div>`;
  }
  if (m.role === "proof") {
    const stale = (meta.outdated ? `<div class="stale-line">⚠ Outdated: ${esc(meta.outdated.message)}</div>` : "") +
      ((meta.warn_later || []).length ? `<div class="later-line">⚠ Also send ${meta.warn_later.map(s => `<b class="mono">/${esc(s)}</b>`).join(", ")} again before you submit: ${meta.warn_later.length > 1 ? "those passes are" : "that pass is"} outdated.</div>` : "");
    return `<div class="msg system"><div class="bubble proofcard${meta.outdated ? " stale" : ""}"><div class="rcard"><div class="grow">🏆 <b>/${esc(meta.stage)} ${esc(meta.verdict)}${meta.admin_override ? " (admin override)" : ""}</b>${meta.outdated ? ` <span class="pill OUTDATED">OUTDATED</span>` : ""}
      <span class="mono fp-inline" title="${esc(meta.fingerprint || meta.code)}">${meta.fingerprint ? "🔏 " + esc(shortFp(meta.fingerprint)) : esc(meta.code)}</span></div>${ts}${CHECKABLE.has(meta.stage) && meta.fingerprint ? `<button data-taxocheck="${m.id}">Check my task in Outlier</button>` : ""}<button class="primary" data-proof="${m.id}">Show pass screen</button></div>${stale}</div></div>`;
  }
  const kickoff = meta.kickoff ? " kickoff" : "";  // the plan-your-task card (server/plan.py): solid, not a dashed note
  const warn = m.meta && (m.meta.spend_flag || m.meta.warn) ? " warnnote" : "";
  const envBtn = meta.env_guide ? `<p><button class="small" data-envguide>Sandbox fix steps</button></p>` : "";
  return `<div class="msg system"><div class="bubble${warn}${kickoff}">${md(m.content)}${envBtn}<div class="ts-row">${ts}</div></div></div>`;
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
      const shown = shownVerdict(m.meta.stage, m.meta.verdict);
      const label = `${m.meta.stage} report — ${shown || "no verdict"}`;
      const w = openModal(label, `<div class="bubble" style="border:0;padding:0">${md(m.content)}</div>`,
        `<button data-dl>Download</button>`);
      w.querySelector("[data-dl]").onclick = () => download(reportName(m), m.content);
    } else if (t.dataset.download) {
      const m = getMessage(+t.dataset.download);
      download(reportName(m), m.content);
    } else if (t.dataset.taxocheck) {
      showTaxoCheck(getMessage(+t.dataset.taxocheck).meta);
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
    b.innerHTML = theme === "dark" ? '☀<span class="tlabel"> Light</span>' : '☾<span class="tlabel"> Dark</span>';
    b.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  });
}
document.querySelectorAll("[data-theme-toggle]").forEach(b =>
  b.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")));
applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");

// An image in a message (an admin's screenshot): click to open it full size in a new tab.
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("click", e => {
    const img = e.target.closest?.(".bubble img, .inject-step img");  // chat images and the inject guide's screenshots
    if (img && img.src && new URL(img.src, location.href).origin === location.origin) window.open(img.src, "_blank", "noopener");
  });
}
