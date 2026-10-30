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
// For /oe only a PASS counts (no pass card, can't submit), so an OE NON-FAIL is shown as FAIL everywhere a verdict
// is displayed. The stored report, its score and the run's verdict stay as the eval graded them.
const shownVerdict = (stage, v) => (stage === "oe" && v === "NON-FAIL" ? "FAIL" : v);
const OE_NONFAIL_NOTE = "Shown as FAIL: for the oracle events only a PASS counts, so fix the items in this report and run /oe again. (The report itself says NON-FAIL.)";

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
    : c.last_verdict ? (v => `<span class="pill ${esc(v)}" title="Last review: /${esc(c.last_cmd)} ${esc(v)}">/${esc(c.last_cmd)} ${esc(v)}</span>`)(shownVerdict(c.last_cmd, c.last_verdict))
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

function proofHtml(p, opts = {}) {
  const t = p.totals || {};
  const total = (t.input || 0) + (t.output || 0) + (t.cache_read || 0) + (t.cache_creation || 0);
  const models = Object.entries(p.models || {}).map(([m, u]) => `${m} (${fmtInt(u.calls)} calls)`).join(", ") || "–";
  const hashes = Object.entries(p.report_hashes || {}).slice(0, 4)
    .map(([k, v]) => `<div class="wide"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
  const verifyUrl = `${location.origin}/verify/${encodeURIComponent(p.code)}`;
  const stage = { injection: "Injection", prompt: "Prompt", hardness: "Hardness", oe: "Oracle Events", rubric: "Rubric" }[p.stage] || p.stage;
  // Screenshot-safe (2026-10-27): the taxonomy reads the code off a screenshot with GPT-4o, and it misread small
  // text in tall cards. So the card is short (header, the code large, four IDs) and everything else is folded
  // under "More details", which a screenshot doesn't need.
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
    ${staleBand(p)}
    ${p.fingerprint ? `<div class="fpband"><div class="k">🔏 Verification code · the ${esc(stage)} fingerprint, as the taxonomy shows it</div>
      <div class="fp" title="${esc(p.fingerprint)}">${esc(shortFp(p.fingerprint))}</div></div>` : ""}
    <div class="grid ids">
      <div><div class="k">Attempt ID</div><div class="v">${esc(p.attempt_id)}</div></div>
      <div><div class="k">Outlier ID</div><div class="v">${esc(p.user_id)}</div></div>
      <div><div class="k">User</div><div class="v">${esc(p.user_email)}</div></div>
      <div><div class="k">Completed</div><div class="v">${esc(fmtUTC(p.finished_at))}</div></div>
    </div>
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
    ${opts.foot === false ? "" : `<div class="foot"><div class="grow">📸 <b>Screenshot this card</b> and upload it in the taxonomy.
      Or download the image, which is checked with the taxonomy's reader first.</div>
      ${COPYABLE.has(p.stage) && p.fingerprint ? `<button data-copypass>Copy what passed</button>` : ""}
      <button data-dlpass>⬇ Download image</button><button data-close>Close</button></div>
      <div class="dlnote muted" data-dlnote hidden></div>`}
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
        ${p.outdated ? `<p class="taxo-warn">⚠ This pass is outdated. ${esc(p.outdated.message)}</p>` : ""}
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
  wrap.addEventListener("click", e => {
    if (e.target === wrap || e.target.closest("[data-close]")) wrap.remove();
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
// plain characters, then the details. Before the download it's read with the taxonomy's own reader and prompt
// (server/taxo_reader.py); if that misreads, it's drawn again with a bigger code, up to 3 times.
const COPYABLE = new Set(["injection", "prompt", "oe"]);
const CHECKABLE = new Set(["injection", "prompt", "oe", "rubric"]);
const PASS_IMAGE_SIZES = [44, 56, 68];  // code font sizes, px, tried in order
const STAGE_NAMES = { injection: "Injection", prompt: "Prompt", hardness: "Hardness", oe: "Oracle Events", rubric: "Rubric" };

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
  const details = [["Attempt ID", p.attempt_id], ["Outlier ID", p.user_id], ["User", p.user_email],
    ["Completed", fmtUTC(p.finished_at)], ["Report SHA-256", p.report_sha256]].filter(d => d[1]);
  const headH = 250 + (p.checked_during ? 34 : 0) + (p.admin_override ? 34 : 0);
  const staleH = staleLines.length ? 70 + staleLines.length * 32 : 0;
  const codeH = 96 + codeLines.length * Math.round(codeSize * 1.3);
  const H = headH + staleH + codeH + 60 + details.length * 40 + 70;
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
  ctx.fillText(p.fingerprint ? `🔏 VERIFICATION CODE · THE ${stage.toUpperCase()} FINGERPRINT, AS THE TAXONOMY SHOWS IT` : "VERIFICATION CODE", pad, y + 52);
  ctx.fillStyle = "#000000"; ctx.font = codeFont;
  codeLines.forEach((l, i) => ctx.fillText(l, pad, y + 72 + codeSize + i * Math.round(codeSize * 1.3)));
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
  ctx.fillText(`Pass image from the Original Conference Eval Portal · issued ${fmtUTC(p.issued_at || p.finished_at)}`, pad, H - 34);
  return canvas;
}

const PASS_IMAGE_OK = {};  // pass code -> data URL of the image the reader read correctly (this page load)

async function downloadPassImage(p, root) {
  const button = root.querySelector("[data-dlpass]"), note = root.querySelector("[data-dlnote]");
  const say = (html, cls = "") => { note.hidden = false; note.className = `dlnote ${cls}`; note.innerHTML = html; };
  const name = `pass-${p.stage}-${p.attempt_id}.png`;
  const save = url => { const a = document.createElement("a"); a.href = url; a.download = name; a.click(); };
  if (PASS_IMAGE_OK[p.code]) { save(PASS_IMAGE_OK[p.code]); return say("✓ Downloaded. Upload this image in the taxonomy.", "ok"); }
  button.disabled = true;
  let url = null, last = null;
  try {
    for (const size of PASS_IMAGE_SIZES) {
      say(`Checking the image with the taxonomy's reader…`);
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
    say("✓ Downloaded. The taxonomy's reader reads this image correctly. Upload it in the taxonomy.", "ok");
  } else if (last.ok === null) {
    say("Downloaded. The reader check couldn't run right now, but this image is made for it. Upload it in the taxonomy.");
  } else {
    say(`Downloaded, but in our check the reader read <span class="mono">${esc(last.read || "nothing")}</span> instead of
      <span class="mono">${esc(last.expected)}</span>. If the taxonomy blocks it, type <b class="mono">/admin</b> with your attempt ID.`, "bad");
  }
}

async function copyPassInput(p, root) {
  const note = root.querySelector("[data-dlnote]");
  const say = (html, cls = "") => { note.hidden = false; note.className = `dlnote ${cls}`; note.innerHTML = html; };
  try {
    const text = await api(`/api/proofs/${encodeURIComponent(p.code)}/input`);
    await navigator.clipboard.writeText(text);
    say(`✓ Copied the exact ${esc(STAGE_NAMES[p.stage] || p.stage)} this pass graded (${fmtInt(text.length)} characters).
      In the taxonomy field press <b>Ctrl/⌘+A</b>, then paste, so nothing old is left.`, "ok");
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
  const what = p.stage === "rubric" ? "the rubric's whole <b>JSON view</b> from the taxonomy"
    : `the whole <b>${esc(STAGE_NAMES[p.stage] || p.stage)}</b> field from the taxonomy (Ctrl/⌘+A, copy)`;
  panel.hidden = false;
  panel.innerHTML = `<div class="k">Check my taxonomy</div>
    <p>Paste ${what}. This checks it the same way the taxonomy's linter does.</p>
    <textarea data-checktext rows="6" spellcheck="false" placeholder="Paste here"></textarea>
    <div class="row"><button class="primary" data-runcheck>Check</button><button data-close>Close</button><span class="muted" data-checkresult></span></div>
    <p class="muted">/${esc(p.stage)} pass · <span class="mono">${esc(shortFp(p.fingerprint))}</span></p>`;
  panel.querySelector("textarea").focus();
}

async function runTaxoCheck(p, root) {
  const panel = root.querySelector("[data-checkpanel]");
  const text = panel.querySelector("[data-checktext]").value, out = panel.querySelector("[data-checkresult]");
  if (!text.trim()) { out.textContent = "Paste the taxonomy text first."; return; }
  out.className = "muted"; out.textContent = "Checking…";
  try {
    const r = await api(`/api/proofs/${encodeURIComponent(p.code)}/check-input`, { json: { text } });
    if (r.match) {
      out.className = "ok";
      out.innerHTML = `✓ Matches this pass. The taxonomy's code will be <span class="mono">${esc(r.taxonomy)}</span>.`;
      return;
    }
    const d = r.difference;
    const mark = (s, at) => `${esc(s.slice(0, at))}<mark>${esc(s.slice(at, at + 1) || "∅")}</mark>${esc(s.slice(at + 1))}`;
    out.className = "bad";
    out.innerHTML = `✗ Doesn't match. Taxonomy: <span class="mono">${esc(r.taxonomy)}</span> · this pass: <span class="mono">${esc(r.passed)}</span>.
      ${d ? `<br>First difference at character ${fmtInt(d.at)} (taxonomy ${fmtInt(d.taxonomy_chars)} chars, passed ${fmtInt(d.passed_chars)}):
        <div class="diff"><div><b>Taxonomy</b> <span class="mono">…${mark(d.taxonomy_text, d.offset)}…</span></div>
        <div><b>Passed</b> <span class="mono">…${mark(d.passed_text, d.offset)}…</span></div></div>` : ""}
      ${COPYABLE.has(p.stage) ? "<br>Use <b>Copy what passed</b> and paste it over the whole taxonomy field." : "<br>Put the version that passed into the taxonomy, or run the review again on what's there."}`;
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
    const oeNonfail = shownVerdict(meta.stage, meta.verdict) !== meta.verdict;
    return `<div class="msg system"><div class="bubble"><div class="rcard"><div class="grow">📋 <b>${esc(label)} report</b> ${oeNonfail ? `<span class="pill FAIL" title="${esc(OE_NONFAIL_NOTE)}">FAIL</span>` : pill(meta.verdict)}
      ${meta.score != null ? `<span class="muted">score ${esc(meta.score)}</span>` : ""}
      ${meta.exec_status && meta.exec_status !== "COMPLETE" ? `<span class="muted">· ${esc(meta.exec_status)}</span>` : ""}</div>
      ${ts}<button data-report="${m.id}">View</button><button data-download="${m.id}">Download</button></div>
      ${meta.fingerprint ? `<div class="rfp" title="${esc(meta.fingerprint)}">🔏 ${esc(shortFp(meta.fingerprint))}</div>` : ""}</div></div>`;
  }
  if (m.role === "proof") {
    const stale = meta.outdated ? `<div class="stale-line">⚠ Outdated: ${esc(meta.outdated.message)}</div>` : "";
    return `<div class="msg system"><div class="bubble proofcard${meta.outdated ? " stale" : ""}"><div class="rcard"><div class="grow">🏆 <b>/${esc(meta.stage)} ${esc(meta.verdict)}${meta.admin_override ? " (admin override)" : ""}</b>${meta.outdated ? ` <span class="pill OUTDATED">OUTDATED</span>` : ""}
      <span class="mono fp-inline" title="${esc(meta.fingerprint || meta.code)}">${meta.fingerprint ? "🔏 " + esc(shortFp(meta.fingerprint)) : esc(meta.code)}</span></div>${ts}${CHECKABLE.has(meta.stage) && meta.fingerprint ? `<button data-taxocheck="${m.id}">Check my taxonomy</button>` : ""}<button class="primary" data-proof="${m.id}">Show pass screen</button></div>${stale}</div></div>`;
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
      const shown = shownVerdict(m.meta.stage, m.meta.verdict);
      const label = `${m.meta.stage} report — ${shown || "no verdict"}`;
      const banner = shown !== m.meta.verdict ? `<div class="bubble warnnote" style="margin-bottom:10px">${esc(OE_NONFAIL_NOTE)}</div>` : "";
      const w = openModal(label, `${banner}<div class="bubble" style="border:0;padding:0">${md(m.content)}</div>`,
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
    b.textContent = theme === "dark" ? "☀ Light" : "☾ Dark";
    b.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  });
}
document.querySelectorAll("[data-theme-toggle]").forEach(b =>
  b.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")));
applyTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
