// Admin dashboard: every chat, live; reply as Admin, resolve /admin requests, inspect runs.

const S = { chats: [], selected: null, detail: null, es: null, steps: null, funnelPick: null };
const REPORT_FILE = { injection: "0_injection.md", prompt: "1_prompt.md", oe: "2_oe.md", rubric: "3_rubric.md", hardness: "1a_hardness.md" };

const INVITE = location.pathname.startsWith("/admin/invite/") ? location.pathname.split("/").pop() : null;

async function boot() {
  if (INVITE) return showInvite();
  try { await loadChats(); } catch (e) { return showLogin(); }
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  connect();
  loadBudget();
  const hash = location.hash.slice(1);
  if (hash) select(hash);
}

function showLogin(msg) {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#login-error").classList.toggle("hidden", !msg);
  $("#login-error").textContent = msg || "";
}

$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  try {
    await api("/api/admin/login", { json: { email: $("#email").value, password: $("#password").value } });
    boot();
  } catch (err) { showLogin(err.message); }
});
$("#logout").onclick = async () => { await api("/api/admin/logout", { method: "POST" }); location.reload(); };

function showInvite(msg) {
  $("#invite").classList.remove("hidden");
  $("#invite-error").classList.toggle("hidden", !msg);
  $("#invite-error").textContent = msg || "";
}
$("#invite-form").addEventListener("submit", async e => {
  e.preventDefault();
  if ($("#pw1").value !== $("#pw2").value) return showInvite("The passwords don't match.");
  try {
    await api("/api/admin/accept", { json: { token: INVITE, password: $("#pw1").value } });
    location.href = "/admin";
  } catch (err) { showInvite(err.message); }
});

// ---- switch to the chat page (the selected chat, else the latest), signed in as this admin ----
$("#view-switch").addEventListener("click", async e => {
  if (e.target.closest("[data-view]")?.dataset.view === "cost") { location.href = "/admin/cost"; return; }
  if (e.target.closest("[data-view]")?.dataset.view === "cbs") { location.href = "/admin/cbs"; return; }
  if (e.target.closest("[data-view]")?.dataset.view !== "chat") return;
  try {
    await api("/api/admin/open-chat", { json: { attempt_id: S.selected || "" } });
    location.href = "/";
  } catch (err) { uiAlert("Couldn't open the chat view", err.message); }
});

// ---- LiteLLM key budget (any admin) ----
// The key is shared with the platform's task traffic and stops at its cap, so this sits in the top bar.
const usd = v => "$" + Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
const when = t => new Date(t * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const hoursText = h => (h < 1.5 ? `${Math.round(h * 60)} min` : h < 48 ? `${Math.round(h)} h` : `${(h / 24).toFixed(1)} days`);
async function loadBudget(refresh = false) {
  try { S.budget = await api(`/api/admin/budget${refresh ? "?refresh=1" : ""}`); } catch (e) { return; }
  const b = S.budget, chip = $("#budget-btn");
  chip.classList.toggle("hidden", !!b.off);
  if (b.off) return;
  const soon = t => new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  const meter = k => `<span class="bmeter"><span style="width:${Math.min(100, k.pct || 0)}%"></span></span>`;
  const out = k => (k.pace_24h && k.pace_24h.before_reset ? ` · runs out ~${soon(k.pace_24h.at)}` : "");
  if (b.active === "backup") {  // the main key is at its cap and the meter carries every call on the backup key
    const k = b.backup;
    chip.className = "budget-chip " + (k.blocked ? "blocked" : k.level === "ok" ? "warn" : k.level);
    chip.innerHTML = k.blocked ? "⛔ Both keys used up" : `${meter(k)}On backup key ${k.pct}%${out(k)}`;
    chip.title = `The main key is at its cap, so reviews use the backup key ${k.key_alias}: ${usd(k.spend)} of ${usd(k.max_budget)} this ${k.cycle} cycle. Click for details.`;
    return;
  }
  chip.className = "budget-chip " + (b.error ? "warn" : b.level);
  chip.innerHTML = b.error ? "Key budget: unknown"
    : b.blocked ? "⛔ Key budget used up"
    : `${meter(b)}Key budget ${b.pct}%${out(b)}${b.log_gap ? " ⚠ undercounted" : ""}`;
  chip.title = b.error || `${b.key_alias}: ${usd(b.spend)} of ${usd(b.max_budget)} this ${b.cycle} cycle.`
    + (b.log_gap ? " May be too low: neither LiteLLM nor SMIG has recorded the key lately." : "") + " Click for details.";
}
// Spend per UTC day (the key's 7-day cycle resets at 00:00 UTC): one series, so no legend; the title names it.
// Columns <= 24px with a 4px rounded top, hairline grid, a reference line at the cap's daily pace, a marker at the
// cycle reset, values on the peak and today only, a tooltip per column (also on keyboard focus) and a table view.
const kUsd = v => (v >= 1000 ? `$${+(v / 1000).toFixed(1)}K` : `$${Math.round(v)}`);
const utcDay = (t, opts) => new Date(t * 1000).toLocaleDateString("en-US", { timeZone: "UTC", ...opts });
function spendChart(b) {
  const days = b.daily || [];
  if (!days.length) return "";
  const W = 860, H = 230, L = 52, R = 12, T = 26, B = 40, pw = W - L - R, ph = H - T - B;
  const cycleDays = parseInt(b.cycle, 10) || 7, pace = b.max_budget ? b.max_budget / cycleDays : 0;
  const top = Math.max(pace, ...days.map(d => d.spend), 1) * 1.12;
  const step = [500, 1000, 2000, 2500, 5000, 10000, 20000, 50000].find(s => top / s <= 5) || 100000;
  const yMax = Math.ceil(top / step) * step, y = v => T + ph - (v / yMax) * ph;
  const slot = pw / days.length, bw = Math.min(24, slot * 0.6);
  const peak = days.reduce((m, d, i) => (d.spend > days[m].spend ? i : m), 0);
  let svg = "";
  for (let v = 0; v <= yMax; v += step) {
    svg += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="tick" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${kUsd(v)}</text>`;
  }
  const resetAt = days.findIndex(d => b.cycle_start && d.day === b.cycle_start);
  if (resetAt > 0) {
    const x = L + resetAt * slot;
    svg += `<line class="ref" x1="${x}" x2="${x}" y1="${T - 14}" y2="${T + ph}"/><text class="note" x="${x + 4}" y="${T - 6}">cycle reset</text>`;
  }
  days.forEach((d, i) => {
    const cx = L + (i + 0.5) * slot, x = cx - bw / 2, y0 = T + ph, y1 = y(d.spend), h = y0 - y1, r = Math.min(4, h);
    const shape = h > 0 ? `<path class="bar" d="M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + bw - r} Q${x + bw},${y1} ${x + bw},${y1 + r} V${y0} Z"/>` : "";
    const label = (i === peak || d.today) && d.spend > 0 ? `<text class="val" x="${cx}" y="${y1 - 6}" text-anchor="middle">${kUsd(d.spend)}</text>` : "";
    const name = i === 0 || utcDay(d.day, { day: "numeric" }) === "1" ? utcDay(d.day, { month: "short", day: "numeric" }) : utcDay(d.day, { day: "numeric" });
    svg += `<g class="sbar${d.today ? " today" : ""}" data-i="${i}" tabindex="0" role="img" aria-label="${esc(utcDay(d.day, { weekday: "short", month: "short", day: "numeric" }))}: ${esc(usd(d.spend))}">
      <rect class="hit" x="${L + i * slot}" y="${T}" width="${slot}" height="${ph}"/>${shape}${label}
      <text class="tick" x="${cx}" y="${y0 + 16}" text-anchor="middle">${esc(name)}</text>${d.today ? `<text class="note" x="${cx}" y="${y0 + 30}" text-anchor="middle">today</text>` : ""}</g>`;
  });
  if (pace) svg += `<line class="pace" x1="${L}" x2="${W - R}" y1="${y(pace)}" y2="${y(pace)}"/>`;  // keyed in the title row
  svg += `<line class="axis" x1="${L}" x2="${W - R}" y1="${T + ph}" y2="${T + ph}"/>`;
  const rows = days.slice().reverse().map(d => `<tr><td>${esc(utcDay(d.day, { weekday: "short", month: "short", day: "numeric" }))}${d.today ? " (so far)" : ""}</td>
    <td>${esc(usd(d.spend))}</td><td>${fmtInt(d.requests)}</td><td>${esc(usd(d.portal))}</td></tr>`).join("");
  const gap = b.log_gap ? `<div class="error" style="margin:0 0 8px">⚠ ${esc(`Neither LiteLLM nor SMIG has recorded any usage on this key since ${when(b.log_gap.since)}, although the key still works (the portal has made ${fmtInt(b.log_gap.portal_calls)} successful calls with it since). From its own records the portal has used about ${usd(b.log_gap.portal_usd)} since then; the figures here are missing it.`)}</div>` : "";
  const key = pace ? `<span class="sc-key"><span class="sc-line"></span>${kUsd(pace)}/day = cap ÷ ${cycleDays} days</span>` : "";
  return `<div class="spend-chart"><div class="sc-title"><span>Spend per day <span class="muted">· UTC days · LiteLLM + SMIG</span></span>${key}</div>${gap}
    <div class="sc-plot"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${svg}</svg><div class="sc-tip hidden" role="tooltip"></div></div>
    <details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>Day (UTC)</th><th>Key spend</th><th>Requests</th><th>This portal (est.)</th></tr>${rows}</table></details></div>`;
}
function wireSpendChart(w, b) {
  const plot = w.querySelector(".sc-plot");
  if (!plot) return;
  const tip = plot.querySelector(".sc-tip");
  const show = g => {
    const d = b.daily[+g.dataset.i];
    tip.replaceChildren();
    const line = (strong, rest) => { const div = document.createElement("div"); const s = document.createElement("b"); s.textContent = strong; div.append(s, document.createTextNode(rest)); tip.append(div); };
    const head = document.createElement("div"); head.className = "muted";
    head.textContent = utcDay(d.day, { weekday: "short", month: "short", day: "numeric" }) + (d.today ? " · today so far" : "") + " (UTC)";
    tip.append(head);
    line(usd(d.spend), ` key spend · ${fmtInt(d.requests)} requests`);
    line(usd(d.portal), " this portal (est.)");
    tip.classList.remove("hidden");
    const box = plot.getBoundingClientRect(), r = g.querySelector(".hit").getBoundingClientRect();
    tip.style.left = Math.min(Math.max(r.left - box.left + r.width / 2 - tip.offsetWidth / 2, 0), box.width - tip.offsetWidth) + "px";
    tip.style.top = Math.max(0, (g.querySelector(".bar") || g.querySelector(".hit")).getBoundingClientRect().top - box.top - tip.offsetHeight - 8) + "px";
  };
  plot.querySelectorAll(".sbar").forEach(g => {
    g.addEventListener("pointerenter", () => show(g));
    g.addEventListener("focus", () => show(g));
    g.addEventListener("pointerleave", () => tip.classList.add("hidden"));
    g.addEventListener("blur", () => tip.classList.add("hidden"));
  });
}

function openBudget() {
  const b = S.budget;
  if (!b) return;
  const pace = (p, label) => !p ? `<div class="muted">${label}: no spend</div>`
    : `<div>${label} (${usd(p.per_hour)}/h): runs out in ~${hoursText(p.hours)}, ${esc(when(p.at))}
        ${p.before_reset ? `<span class="chip flag">before the reset</span>` : `<span class="chip">after the reset</span>`}</div>`;
  const ago = t => t ? `${esc(when(t))} (${fmtAgo(t)})` : "–";
  const k = b.backup;
  const inUse = !k ? "" : b.active === "backup"
    ? `<div class="error" style="margin:0 0 10px">The main key is at its cap, so every review call now goes through the backup key (below). The meter tries the main key again every 10 minutes and moves back once it works.</div>`
    : `<p class="muted" style="margin-top:0">In use: the main key. The backup key (below) takes over if the main key reaches its cap.</p>`;
  const backupSection = !k ? "" : k.error ? `<h3>Backup key</h3><div class="error">${esc(k.error)}</div>` : `
    <h3>Backup key${b.active === "backup" ? ` <span class="chip flag">in use</span>` : ""}</h3>
    <p style="margin-top:0"><b style="font-size:18px">${usd(k.spend)}</b> <span class="muted">of ${usd(k.max_budget)} · ${k.pct}% used · ${usd(k.left)} left</span></p>
    <div class="spend-meter budget-meter ${esc(k.level)}"><span style="width:${Math.min(100, k.pct || 0)}%"></span></div>
    <div class="rc-row"><div class="k">Key</div><div class="mono">${esc(k.key_alias)} · ${esc(k.cycle || "")} cycle${k.blocked ? ` <span class="chip flag">used up</span>` : ""}</div></div>
    <div class="rc-row"><div class="k">Resets</div><div>${k.resets_at ? `${esc(when(k.resets_at))} (in ${hoursText((k.resets_at - k.checked_at) / 3600)})` : "–"}</div></div>
    <div class="rc-row"><div class="k">Burn</div><div>${usd(k.spend_1h)} in the last hour · ${usd(k.spend_3h)} in 3 h · ${usd(k.spend_24h)} in 24 h</div></div>
    <div class="rc-row"><div class="k">Runs out</div><div>${k.blocked ? "Already at the cap." : pace(k.pace_24h, "At the 24 h pace") + pace(k.pace_3h, "At the 3 h pace")}</div></div>
    <div class="rc-row"><div class="k">Data as of</div><div>${ago(k.synced_at)} · last request ${ago(k.last_request_at)}</div></div>
    ${k.query_url ? `<p style="margin-bottom:0"><a href="${esc(k.query_url)}" target="_blank" rel="noopener">Backup key query in Redash</a></p>` : ""}`;
  const body = b.error ? `<div class="error">${esc(b.error)}</div>${backupSection}` : `
    ${b.stale ? `<div class="error">Couldn't refresh just now (${esc(b.stale)}); showing the last figures.</div>` : ""}
    ${inUse}${k ? "<h3 style=\"margin-top:0\">Main key</h3>" : ""}
    <p style="margin-top:0"><b style="font-size:22px">${usd(b.spend)}</b> <span class="muted">of ${usd(b.max_budget)} · ${b.pct}% used · ${usd(b.left)} left</span></p>
    <div class="spend-meter budget-meter ${esc(b.level)}"><span style="width:${Math.min(100, b.pct || 0)}%"></span></div>
    ${spendChart(b)}
    <div class="rc-row"><div class="k">Key</div><div class="mono">${esc(b.key_alias)} · ${esc(b.cycle || "")} cycle${b.blocked ? ` <span class="chip flag">used up</span>` : ""}</div></div>
    ${b.smig_spend != null ? `<div class="rc-row"><div class="k">Sources</div><div>${usd(b.smig_spend)} through SMIG · ${usd(b.litellm_spend)} through LiteLLM, this cycle</div></div>` : ""}
    <div class="rc-row"><div class="k">Resets</div><div>${b.resets_at ? `${esc(when(b.resets_at))} (in ${hoursText((b.resets_at - b.checked_at) / 3600)})` : "–"}</div></div>
    <div class="rc-row"><div class="k">Burn</div><div>${usd(b.spend_1h)} in the last hour · ${usd(b.spend_3h)} in 3 h · ${usd(b.spend_24h)} in 24 h</div></div>
    <div class="rc-row"><div class="k">Runs out</div><div>${b.blocked ? "Already at the cap." : pace(b.pace_24h, "At the 24 h pace") + pace(b.pace_3h, "At the 3 h pace")}</div></div>
    <div class="rc-row"><div class="k">This portal</div><div>${usd(b.portal_cycle)} this cycle${b.spend ? ` (${Math.round(100 * b.portal_cycle / b.spend)}% of the key's spend)` : ""} · ${usd(b.portal_24h)} in the last 24 h</div></div>
    <div class="rc-row"><div class="k">Data as of</div><div>${ago(b.synced_at)} · last request ${ago(b.last_request_at)}</div></div>
    <p class="muted" style="margin-bottom:0">The key is shared with the platform's own task traffic. The cap is set on the LiteLLM key, and LiteLLM
      stopped the key at the cap on Oct 11 (reviews here fail until the cycle resets or the cap is raised); since Oct 16 most calls go through SMIG.
      Figures are LiteLLM's tables plus SMIG's usage records in Snowflake (a few minutes behind).</p>
    ${backupSection}`;
  const w = openModal("LiteLLM key budget", body, `${S.owner ? `<a class="button" href="/admin/cost">Cost page</a>` : ""}<button data-refresh>Refresh now</button>${b.query_url ? `<a class="button" href="${esc(b.query_url)}" target="_blank" rel="noopener">Open in Redash</a>` : ""}`);
  wireSpendChart(w, b);
  const btn = w.querySelector("[data-refresh]");
  btn.onclick = async () => { btn.disabled = true; btn.textContent = "Refreshing…"; await loadBudget(true); w.remove(); openBudget(); };
}
$("#budget-btn").onclick = openBudget;
setInterval(() => { if (S.chats.length) loadBudget(); }, 300000);

// ---- spend (any admin) ----
$("#spend-btn").onclick = () => openSpend("user", 30);

async function openSpend(group, days) {
  const res = await api(`/api/admin/spend?group=${group}&days=${days}`);
  const money = v => "$" + Number(v || 0).toFixed(2);
  const short = id => (id && id.length > 12 ? id.slice(0, 8) + "…" : id || "–");
  const list = (items, n = 2) => esc(items.slice(0, n).join(", ")) + (items.length > n ? ` +${items.length - n}` : "");
  const head = {
    user: "<th>User (Outlier ID)</th><th>Email</th><th>Tasks</th><th>Attempts</th>",  // + top attempt vs limit, after Spend
    task: "<th>Task</th><th>Users</th><th>Attempts</th><th></th>",
    attempt: "<th>Attempt</th><th>Email</th><th>Task</th><th></th>",
    day: "<th>Day (UTC)</th><th>Users</th><th>Tasks</th><th>Attempts</th>",
  }[group];
  const lead = g => ({
    user: `<td class="mono" title="${esc(g.key)}">${esc(short(g.key))}</td><td>${list(g.emails, 1)}</td><td>${g.tasks.length}</td><td>${g.attempts.length}</td>`,
    task: `<td class="mono">${esc(g.key)}</td><td>${list(g.emails)}</td><td>${g.attempts.length}</td><td></td>`,
    attempt: `<td class="mono"><a href="#${esc(g.key)}" data-open="${esc(g.key)}">${esc(g.key)}</a></td><td>${list(g.emails, 1)}</td><td class="mono">${esc(short(g.tasks[0]))}</td><td></td>`,
    day: `<td>${esc(g.key)}</td><td>${g.users.length + g.emails.filter(e => !g.users.length).length}</td><td>${g.tasks.length}</td><td>${g.attempts.length}</td>`,
  }[group]);
  const limitCol = { user: "Top attempt", attempt: "Attempt total" }[group];  // what the per-attempt limit measures, all time
  const rows = res.rows.map(g => `<tr>${lead(g)}<td>${g.reviews}</td><td>${g.followups}</td>
    <td>${fmtInt(g.calls)}</td><td>${fmtInt(g.output_tokens)}</td><td><b>${money(g.spend_usd)}</b></td>
    ${limitCol ? `<td>${g.limit_spend_usd === "" ? "–" : money(g.limit_spend_usd)}${g.flagged
        ? ` <span class="chip flag">⚑ ${group === "user" && +g.flagged > 1 ? g.flagged + " flagged" : "flagged"}</span>`
      : g.limit_spend_usd !== "" && res.limit && g.limit_spend_usd >= 0.8 * res.limit ? ` <span class="chip flag">near limit</span>` : ""}${group !== "user" || g.limit_spend_usd === "" ? ""
      : g.disabled ? ` <span class="chip flag">⛔ disabled</span>` : ` <a href="#" data-disable="${esc(g.key)}" data-who="${esc(g.emails[0] || g.key)}">disable</a>`}</td>` : ""}
    <td>${esc(fmtTime(g.last_run))}${g.admin_runs ? ` <span class="chip" title="runs started by an admin">admin ${g.admin_runs}</span>` : ""}</td></tr>`).join("");
  const tab = (g, label) => `<button class="${g === group ? "primary" : ""}" data-group="${g}">${label}</button>`;
  const span = (d, label) => `<option value="${d}"${d === days ? " selected" : ""}>${label}</option>`;
  const w = openModal("Spend", `
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
      ${tab("user", "By user")}${tab("task", "By task")}${tab("attempt", "By attempt")}${tab("day", "By day")}
      <select id="spend-days" style="width:auto">${span(1, "Last 24 hours")}${span(7, "Last 7 days")}${span(30, "Last 30 days")}${span(0, "All time")}</select>
      <div style="flex:1"></div>
      <a class="button" href="/api/admin/spend?group=${group}&days=${days}&format=csv">Download CSV</a>
    </div>
    <p class="muted" style="margin-top:0">${res.total.runs} runs · ${fmtInt(res.total.calls)} model calls · <b>${money(res.total.spend_usd)}</b> total.
      Each run is billed to whoever started it. Runs an admin starts on someone's attempt count under the admin.
      A task's spend adds up every attempt of that task.</p>
    ${limitCol && res.limit ? `<p class="muted" style="margin-top:-6px">Limit: $${res.limit} per attempt; each new attempt starts at $0.
      "${limitCol}" is ${group === "user" ? "their highest-spending attempt" : "the attempt's whole spend"}, whatever period is shown.
      Reaching the limit flags the attempt, warns them in that chat and posts a Slack alert. Runs aren't blocked.</p>` : ""}
    <table class="runs"><tr>${head}<th>Reviews</th><th>Follow-ups</th><th>Calls</th><th>Output tokens</th><th>Spend</th>${limitCol ? `<th>${limitCol}</th>` : ""}<th>Last run</th></tr>
      ${rows || `<tr><td colspan="10" class="muted">No runs in this period.</td></tr>`}</table>`);
  w.querySelector("#spend-days").onchange = e => { w.remove(); openSpend(group, +e.target.value); };
  w.addEventListener("click", e => {
    const g = e.target.closest("[data-group]");
    if (g) { w.remove(); openSpend(g.dataset.group, days); }
    const open = e.target.closest("[data-open]");
    if (open) { e.preventDefault(); w.remove(); select(open.dataset.open); }
    const dis = e.target.closest("[data-disable]");
    if (dis) {
      e.preventDefault();
      askBlock("person", { identifier: dis.dataset.disable, who: dis.dataset.who }).then(done => { if (done) { w.remove(); openSpend(group, days); } });
    }
  });
}

// ---- block a chat / disable a contributor (any admin) ----
const blockTitle = b => `by ${b.by} on ${fmtTime(b.at)}${b.reason ? " · " + b.reason : ""}`;

// target: { attempt_id } (a chat; for "person", its owner) or { identifier } (an Outlier ID or email); who = label
async function askBlock(kind, target) {
  const chatOnly = kind === "attempt";
  const values = { reason: "", stop: true };
  const asked = uiDialog({
    title: chatOnly ? "Block this chat?" : `Disable ${target.who || "this contributor"}?`,
    html: `<p style="margin-top:0">${chatOnly
        ? "The owner can still read this chat, but can't send messages, upload files or start reviews in it. They can type /admin to reach you. Their other chats aren't affected."
        : "They're signed out of every chat and can't sign in again, with any attempt, until you re-enable them. Their chats, reports and pass codes are kept."}</p>
      <div class="field"><label for="blk-reason">Reason (they see it)</label>
        <input id="blk-reason" maxlength="500" placeholder="${chatOnly ? "e.g. wrong task in this chat" : "e.g. over the $500 spend limit"}"></div>
      <label style="display:flex;gap:8px;align-items:center;font-size:14px"><input type="checkbox" id="blk-stop" checked style="width:auto">
        Also stop ${chatOnly ? "the review running in this chat" : "their running reviews"}</label>
      <p class="muted" style="margin-bottom:0;font-size:13px">You can undo this any time${chatOnly ? " with Unblock chat" : " from Blocked in the top bar"}.</p>`,
    ok: chatOnly ? "Block chat" : "Disable", cancel: "Cancel", danger: true });
  const box = [...document.querySelectorAll(".overlay")].pop();
  box.addEventListener("input", () => {
    values.reason = box.querySelector("#blk-reason").value;
    values.stop = box.querySelector("#blk-stop").checked;
  });
  box.addEventListener("change", () => { values.stop = box.querySelector("#blk-stop").checked; });
  box.querySelector("#blk-reason").focus();
  if (!(await asked)) return false;
  try {
    const res = await api("/api/admin/block", { json: { kind, attempt_id: target.attempt_id || "", identifier: target.identifier || "",
                                                          reason: values.reason, stop_runs: values.stop } });
    if (res.stopped) uiAlert(chatOnly ? "Chat blocked" : "Contributor disabled", `Stopped ${res.stopped} running review${res.stopped > 1 ? "s" : ""}.`);
  } catch (err) { uiAlert("Couldn't do that", err.message); return false; }
  if (S.selected) await select(S.selected);
  loadChats();
  return true;
}

async function askUnblock(kind, key, who) {
  const chatOnly = kind === "attempt";
  if (!(await uiConfirm(chatOnly ? "Unblock this chat?" : `Re-enable ${who || "this contributor"}?`,
    `<p style="margin:0">${chatOnly ? "The owner can use this chat again." : "They can sign in and use their chats again."}</p>`,
    chatOnly ? "Unblock" : "Re-enable"))) return false;
  try { await api("/api/admin/unblock", { json: { kind, key, attempt_id: S.selected || "" } }); }
  catch (err) { uiAlert("Couldn't do that", err.message); return false; }
  if (S.selected) await select(S.selected);
  loadChats();
  return true;
}

$("#blocks-btn").onclick = () => openBlocks();
async function openBlocks() {
  let res;
  try { res = await api("/api/admin/blocks"); } catch (err) { return uiAlert("Couldn't load blocks", err.message); }
  const rows = res.blocks.map(b => `<tr>
      <td>${b.kind === "person" ? "⛔ contributor disabled" : "⛔ chat blocked"}</td>
      <td>${esc(b.label || b.key)}<div class="muted mono" style="font-size:12px">${b.kind === "person"
        ? `${esc(b.key.startsWith("email:") ? "by email" : b.key)} · ${b.attempts.length} chat${b.attempts.length === 1 ? "" : "s"}`
        : `<a href="#${esc(b.key)}" data-open="${esc(b.key)}">${esc(b.key)}</a>`}</div></td>
      <td>${esc(b.reason || "–")}</td><td>${esc(b.by)}<div class="muted" style="font-size:12px">${esc(fmtTime(b.at))}</div></td>
      <td><button data-unblock="${esc(b.kind)}" data-key="${esc(b.key)}" data-who="${esc(b.label || b.key)}">${b.kind === "person" ? "Re-enable" : "Unblock"}</button></td></tr>`).join("");
  const w = openModal("Blocked", `
    <p class="muted" style="margin-top:0">A disabled contributor can't sign in or use any chat. A blocked chat is read-only for its owner,
      who can still type /admin. Admins are never blocked. Block a chat or disable someone from the chat's header, from Spend → By user, or here.</p>
    <table class="runs"><tr><th>What</th><th>Who</th><th>Reason</th><th>By</th><th></th></tr>
      ${rows || `<tr><td colspan="5" class="muted">Nobody is blocked.</td></tr>`}</table>
    <form id="blk-form" style="display:flex;gap:8px;margin-top:14px">
      <input id="blk-id" placeholder="Disable a contributor by Outlier ID or email" required>
      <button class="danger">Disable…</button></form>`);
  w.querySelector("#blk-form").addEventListener("submit", async e => {
    e.preventDefault();
    const id = w.querySelector("#blk-id").value.trim();
    if (await askBlock("person", { identifier: id, who: id })) { w.remove(); openBlocks(); }
  });
  w.addEventListener("click", async e => {
    const open = e.target.closest("[data-open]");
    if (open) { e.preventDefault(); w.remove(); return select(open.dataset.open); }
    const un = e.target.closest("[data-unblock]");
    if (un && await askUnblock(un.dataset.unblock, un.dataset.key, un.dataset.who)) { w.remove(); openBlocks(); }
  });
}

// ---- broadcasts: a message that pops up over contributors' chats until they press Acknowledge ----
$("#broadcast-btn").onclick = () => openBroadcasts();

async function openBroadcasts() {
  let res;
  try { res = await api("/api/admin/broadcasts"); } catch (err) { return uiAlert("Couldn't load broadcasts", err.message); }
  const rows = res.broadcasts.map(b => `<tr>
      <td><b>${esc(b.title)}</b><div class="muted" style="font-size:12px;white-space:pre-wrap;max-height:4.5em;overflow:hidden">${esc(b.body)}</div></td>
      <td>${b.audience === "everyone" ? "Everyone with an active chat" : "Chosen emails"}</td>
      <td>${esc(b.created_by || "")}<div class="muted" style="font-size:12px">${esc(fmtTime(b.created_at))}</div></td>
      <td style="min-width:260px"><b>${b.acked}</b> of ${b.people}
        <details style="margin-top:4px"><summary class="muted">✅ ${b.acked} acknowledged</summary>
          <div class="mono" style="font-size:12px;max-height:14em;overflow:auto">${(b.acked_by || []).map(a =>
            `${esc(a.email)} <span class="muted">· ${esc(fmtTime(a.at))}</span>`).join("<br>") || `<span class="muted">nobody yet</span>`}</div></details>
        <details style="margin-top:4px"><summary class="muted">⏳ ${b.waiting.length} not yet</summary>
          <div class="mono" style="font-size:12px;max-height:14em;overflow:auto">${b.waiting.map(esc).join("<br>") || `<span class="muted">everyone has</span>`}</div></details></td>
      <td>${b.ended_at ? `<span class="muted">stopped ${esc(fmtTime(b.ended_at))}</span>`
        : `<button data-end="${b.id}" data-title="${esc(b.title)}">Stop showing</button>`}</td></tr>`).join("");
  const w = openModal("Broadcast", `
    <p class="muted" style="margin-top:0">The message pops up over the contributor's chat until they press Acknowledge. "Everyone" means people
      who have a chat that isn't archived when you send it; specific emails reach those people even if their chats are archived. Anyone who
      signs in for the first time later won't see it. Markdown and line breaks work.</p>
    <form id="bc-form" style="display:grid;gap:8px">
      <input id="bc-title" placeholder="Title" maxlength="200" required>
      <textarea id="bc-body" rows="6" placeholder="Message" required></textarea>
      <div class="row" style="gap:14px;flex-wrap:wrap">
        <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="bc-aud" value="everyone" checked style="width:auto"> Everyone with an active chat</label>
        <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="bc-aud" value="emails" style="width:auto"> Specific emails</label>
      </div>
      <textarea id="bc-emails" rows="3" class="hidden" placeholder="Emails, one per line or comma-separated"></textarea>
      <div class="row" style="gap:8px"><span class="spacer"></span><button type="button" id="bc-preview">Preview</button><button class="primary">Send…</button></div>
    </form>
    <h3 style="margin:18px 0 6px">Sent</h3>
    <table class="runs"><tr><th>Message</th><th>To</th><th>By</th><th>Acknowledged</th><th></th></tr>
      ${rows || `<tr><td colspan="5" class="muted">No broadcasts yet.</td></tr>`}</table>`);
  const form = w.querySelector("#bc-form"), emailsBox = w.querySelector("#bc-emails");
  const audience = () => form.querySelector('input[name="bc-aud"]:checked').value;
  form.addEventListener("change", () => emailsBox.classList.toggle("hidden", audience() !== "emails"));
  const body = () => ({
    title: w.querySelector("#bc-title").value.trim(), body: w.querySelector("#bc-body").value.trim(), audience: audience(),
    emails: emailsBox.value.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean),
  });
  w.querySelector("#bc-preview").onclick = () => {
    const b = body();
    uiDialog({ title: "📣 " + (b.title || "(no title)"), html: `<div class="bubble broadcast-body" style="border:0;padding:0">${broadcastPreview(b.body)}</div>`,
               ok: "Acknowledge" });
  };
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const b = body();
    let check;
    try { check = await api("/api/admin/broadcasts", { json: { ...b, dry_run: true } }); } catch (err) { return uiAlert("Can't send that", err.message); }
    const who = b.audience === "everyone" ? `everyone with an active (not archived) chat now (<b>${check.people}</b> people)` : `<b>${check.people}</b> of the emails you listed`;
    const miss = check.unmatched.length ? `<p class="muted">These have no chat yet, so they won't get it: <span class="mono">${check.unmatched.map(esc).join(", ")}</span></p>` : "";
    if (!check.people) return uiAlert("Nobody to send it to", "None of those emails has a chat yet.");
    if (!await uiDialog({ title: "Send this broadcast?", html: `<p style="margin-top:0">It will pop up for ${who} until they press Acknowledge.</p>${miss}`,
                          ok: "Send", cancel: "Cancel" })) return;
    try { await api("/api/admin/broadcasts", { json: b }); } catch (err) { return uiAlert("Couldn't send it", err.message); }
    w.remove(); openBroadcasts();
  });
  w.addEventListener("click", async e => {
    const btn = e.target.closest("[data-end]");
    if (!btn) return;
    if (!await uiDialog({ title: "Stop showing this broadcast?", html: `<p style="margin:0">“${esc(btn.dataset.title)}” stops popping up for people who
      haven't acknowledged it yet. The acknowledgements so far are kept.</p>`, ok: "Stop showing", cancel: "Cancel", danger: true })) return;
    try { await api(`/api/admin/broadcasts/${btn.dataset.end}/end`, { method: "POST" }); } catch (err) { return uiAlert("Couldn't stop it", err.message); }
    w.remove(); openBroadcasts();
  });
}

function broadcastPreview(text) {  // the same rendering as the contributor's pop-up (line breaks kept)
  const html = window.marked ? marked.parse(String(text ?? ""), { breaks: true, gfm: true }) : esc(text);
  return window.DOMPurify ? DOMPurify.sanitize(html) : esc(text);
}

// ---- status page incidents (the public /status page; automatic incidents need nothing here) ----
$("#status-btn").onclick = () => openStatus();

async function openStatus() {
  let res, live;
  try { [res, live] = await Promise.all([api("/api/admin/status/incidents"), api("/api/status")]); }
  catch (err) { return uiAlert("Couldn't load the status page data", err.message); }
  const comps = res.components, names = Object.fromEntries(comps.map(c => [c[0], c[1]]));
  const impactSel = (v = "degraded") => ["degraded", "partial", "major"].map(i =>
    `<option value="${i}" ${i === v ? "selected" : ""}>${{ degraded: "Degraded performance", partial: "Partial outage", major: "Major outage" }[i]}</option>`).join("");
  const open = res.incidents.filter(i => !i.end), done = res.incidents.filter(i => i.end);
  const incHtml = i => `<div class="st-inc" style="border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0">
      <b>${esc(i.title)}</b> <span class="muted">· ${esc(i.components.map(c => names[c] || c).join(", "))} · ${esc(i.impact)}</span>
      ${i.updates.map(u => `<div style="font-size:13px;margin-top:4px"><b>${esc(u.status)}</b> – ${esc(u.body)} <span class="muted">${esc(fmtTime(u.created_at))}</span></div>`).join("")}
      ${i.end ? "" : `<form data-update="${i.id}" style="display:grid;gap:6px;margin-top:8px">
        <div class="row" style="gap:8px"><select name="status">${res.statuses.map(s => `<option>${esc(s)}</option>`).join("")}</select>
          <select name="impact">${impactSel(i.impact)}</select></div>
        <textarea name="body" rows="2" placeholder="Update message (shown publicly)" required></textarea>
        <div class="row"><span class="spacer"></span><button class="primary">Post update</button></div></form>`}</div>`;
  const autoNow = live.incidents.filter(i => !i.manual && !i.end);
  const w = openModal("Status page", `
    <p class="muted" style="margin-top:0">Public page: <a href="/status" target="_blank" rel="noopener">${esc(location.origin)}/status</a>.
      Right now: <b>${esc(live.overall)}</b>. Automatic incidents open and resolve by themselves (slow or failed review starts,
      failing reviews, model errors, portal down). Post one here to explain something in your own words. Everything you write is public.</p>
    ${autoNow.length ? `<p><b>Automatic, ongoing:</b> ${autoNow.map(i => esc(i.title) + " (" + esc(i.components.join(", ")) + ")").join("; ")}</p>` : ""}
    <h3 style="margin:14px 0 6px">Post an incident</h3>
    <form id="st-form" style="display:grid;gap:8px">
      <input name="title" maxlength="200" placeholder="Title, e.g. Reviews starting slowly" required>
      <div class="row" style="gap:14px;flex-wrap:wrap">${comps.map(c => `<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" name="comp" value="${esc(c[0])}" style="width:auto"> ${esc(c[1])}</label>`).join("")}</div>
      <div class="row" style="gap:8px"><select name="status">${res.statuses.slice(0, -1).map(s => `<option>${esc(s)}</option>`).join("")}</select>
        <select name="impact">${impactSel()}</select></div>
      <textarea name="body" rows="3" placeholder="What's happening (shown publicly)" required></textarea>
      <div class="row"><span class="spacer"></span><button class="primary">Post incident…</button></div>
    </form>
    <h3 style="margin:18px 0 6px">Open (${open.length})</h3>${open.map(incHtml).join("") || `<p class="muted">None.</p>`}
    <h3 style="margin:18px 0 6px">Resolved, last 30 days (${done.length})</h3>${done.map(incHtml).join("") || `<p class="muted">None.</p>`}`);
  w.querySelector("#st-form").addEventListener("submit", async e => {
    e.preventDefault();
    const f = e.target, comps = [...f.querySelectorAll('input[name="comp"]:checked')].map(x => x.value);
    if (!comps.length) return uiAlert("Pick a component", "Choose at least one part of the portal this is about.");
    if (!await uiDialog({ title: "Post this incident publicly?", html: `<p style="margin:0">It appears on /status right away${""}.</p>`, ok: "Post", cancel: "Cancel" })) return;
    try { await api("/api/admin/status/incidents", { json: { title: f.title.value, components: comps, impact: f.impact.value, status: f.status.value, body: f.body.value } }); }
    catch (err) { return uiAlert("Couldn't post it", err.message); }
    w.remove(); openStatus();
  });
  w.addEventListener("submit", async e => {
    const f = e.target.closest("[data-update]");
    if (!f) return;
    e.preventDefault();
    try { await api(`/api/admin/status/incidents/${f.dataset.update}/update`, { json: { status: f.status.value, impact: f.impact.value, body: f.body.value } }); }
    catch (err) { return uiAlert("Couldn't post the update", err.message); }
    w.remove(); openStatus();
  });
}

// ---- eval files: what's in use, what changed at the links, update the chosen ones ----
$("#evalfiles-btn").onclick = () => openEvalFiles();

async function openEvalFiles() {
  let data;
  try { data = await api("/api/admin/evalfiles"); } catch (err) { return uiAlert("Couldn't load eval files", err.message); }
  const size = b => (b == null ? "–" : b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : b >= 1e6 ? (b / 1e6).toFixed(1) + " MB" : Math.round(b / 1e3) + " KB");
  const day = s => { const d = s ? new Date(s) : null; return d && !isNaN(d) ? d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–"; };
  const running = data.job.active;
  const rows = data.files.map(f => {
    const status = !f.remote.ok ? `<span class="pill FAIL">link unreachable</span>`
      : !f.installed ? `<span class="pill none">not recorded</span>`
      : f.changed ? `<span class="pill NON-FAIL">new version at link</span>` : `<span class="pill PASS">up to date</span>`;
    return `<tr><td><input type="checkbox" data-file="${esc(f.name)}" ${f.changed ? "checked" : ""} ${running || !data.can_update ? "disabled" : ""} style="width:auto"></td>
      <td class="mono">${esc(f.zip)}${f.universe ? ` <span class="chip">universe</span>` : ""}</td>
      <td>${size(f.remote.bytes)}</td><td>${esc(day(f.remote.last_modified))}</td>
      <td>${f.installed ? esc(day(f.installed.last_modified)) : "–"}</td><td>${status}</td></tr>`;
  }).join("");
  const job = data.job;
  const jobHtml = job.state ? `<div style="margin-top:16px">
      <div style="display:flex;gap:10px;align-items:center;margin-bottom:6px"><b>Last update</b>
        <span class="pill ${job.state === "done" ? "PASS" : job.state === "failed" ? "FAIL" : "none"}">${esc(job.state)}</span>
        <span class="muted" style="font-size:13px">${esc(job.files || "")} · started ${esc(fmtTime(+job.started_at))}${job.finished_at ? " · finished " + esc(fmtTime(+job.finished_at)) : ""}</span></div>
      ${job.active ? `<div style="display:flex;gap:8px;align-items:center"><div class="spinner"></div><span>${esc(job.step || "starting…")}</span></div>` : ""}
      ${job.state === "failed" ? `<div class="error" style="margin-top:8px">${esc(job.error || "failed")}. The portal is still using the previous version.</div>` : ""}
      <pre class="input-view" style="max-height:220px;margin-top:8px">${esc(job.log || "")}</pre></div>` : "";
  const w = openModal("Eval files", `
    <p class="muted" style="margin-top:0">In use: release <span class="mono">${esc(data.release)}</span> · review box image <span class="mono">${esc(data.snapshot)}</span>.
      Files are pulled from the same links each time; replace a file at its link, then update it here.</p>
    <table class="runs"><tr><th></th><th>File</th><th>Size</th><th>At the link</th><th>In use</th><th></th></tr>${rows}</table>
    <p class="muted" style="font-size:13px">Evals and claude take about a minute. A universe also rebuilds the image every review box starts from,
      about 15–40 minutes. Reviews keep running during an update; new reviews use the new files once it finishes, and if the new
      version fails the eval's own check, nothing switches.</p>
    ${data.can_update ? `<div style="display:flex;gap:8px;justify-content:flex-end"><button class="primary" id="ef-go" ${running ? "disabled" : ""}>
      ${running ? "Update running…" : "Update selected"}</button></div>` : `<p class="muted">Only the owner can start an update.</p>`}
    ${jobHtml}`);
  const go = w.querySelector("#ef-go");
  if (go) go.onclick = async () => {
    const files = [...w.querySelectorAll("[data-file]:checked")].map(c => c.dataset.file);
    if (!files.length) return uiAlert("Nothing selected", "Tick the files you replaced at their links.");
    const slow = files.some(f => f.includes("HarmonyGames_"));
    const ok = await uiConfirm(`Update ${files.length} file${files.length > 1 ? "s" : ""}?`,
      `<p style="margin-top:0">${files.map(f => `<span class="mono">${esc(f)}.zip</span>`).join(", ")}</p>
       <p class="muted" style="margin-bottom:0">${slow ? "Includes a universe, so the review box image is rebuilt too: about 15–40 minutes."
         : "About a minute."} You can close this window; progress shows here when you reopen it.</p>`, "Start update");
    if (!ok) return;
    try { await api("/api/admin/evalfiles/update", { json: { files } }); }
    catch (err) { return uiAlert("Couldn't start the update", err.message); }
    w.remove(); openEvalFiles();
  };
  if (running) {  // refresh the progress while the job runs
    const timer = setInterval(async () => {
      if (!document.body.contains(w)) return clearInterval(timer);
      try {
        const j = (await api("/api/admin/evalfiles")).job;
        if (!j.active) { clearInterval(timer); w.remove(); openEvalFiles(); return; }
        const pre = w.querySelector("pre.input-view"); if (pre) { pre.textContent = j.log || ""; pre.scrollTop = pre.scrollHeight; }
        const step = w.querySelector(".spinner + span"); if (step) step.textContent = j.step || "working…";
      } catch (e) { /* keep polling */ }
    }, 3000);
  }
}

// ---- admin management (owner only) ----
$("#admins-btn").onclick = () => openAdmins();
async function openAdmins() {
  const res = await api("/api/admin/admins");
  const rows = res.admins.map(a => `<tr><td>${esc(a.email)}</td><td>${esc(a.role)}</td>
    <td>${a.role === "owner" ? "owner (from settings)" : a.active ? `active · last sign-in ${fmtTime(a.last_login)}` : `invited · link expires ${fmtTime(a.invite_expires)}`}</td>
    <td>${a.role === "owner" ? "" : `<a href="#" data-link="${esc(a.email)}">${a.active ? "reset password link" : "new link"}</a> ·
      <a href="#" data-rm="${esc(a.email)}" style="color:var(--fail)">remove</a>`}</td></tr>`).join("");
  const w = openModal("Admins", `
    <p class="muted" style="margin-top:0">Admins can see every chat, reply as Admin, resolve /admin requests and cancel runs.
      Only owners (set in the server settings) can add or remove admins.</p>
    <table class="runs"><tr><th>Email</th><th>Role</th><th>Status</th><th></th></tr>${rows}</table>
    <form id="add-admin" style="display:flex;gap:8px;margin-top:14px">
      <input id="new-admin" type="email" placeholder="teammate@scale.com" required><button class="primary">Add admin</button></form>
    <div id="invite-out" class="hidden" style="margin-top:12px"></div>`);
  const showLink = r => {
    const out = w.querySelector("#invite-out");
    out.classList.remove("hidden");
    out.innerHTML = `<div class="muted" style="font-size:13px">Send this link to them yourself (it works once, for 72 hours). It's not shown again.</div>
      <div style="display:flex;gap:8px;margin-top:6px"><input class="mono" readonly value="${esc(r.invite_url)}"><button data-copy>Copy</button></div>`;
    out.querySelector("[data-copy]").onclick = () => navigator.clipboard.writeText(r.invite_url);
  };
  w.querySelector("#add-admin").addEventListener("submit", async e => {
    e.preventDefault();
    try { showLink(await api("/api/admin/admins", { json: { email: w.querySelector("#new-admin").value } })); }
    catch (err) { uiAlert("Couldn't add that admin", err.message); }
  });
  w.addEventListener("click", async e => {
    if (e.target.dataset.link) { e.preventDefault(); showLink(await api("/api/admin/admins", { json: { email: e.target.dataset.link } })); }
    if (e.target.dataset.rm) {
      e.preventDefault();
      const rm = e.target.dataset.rm;
      if (!(await uiDialog({ title: `Remove ${rm}?`, html: `<p style="margin:0">They lose admin access immediately.</p>`,
                             ok: "Remove", cancel: "Cancel", danger: true }))) return;
      await api(`/api/admin/admins/${encodeURIComponent(rm)}`, { method: "DELETE" });
      w.remove(); openAdmins();
    }
  });
}

async function loadChats() {
  const res = await api("/api/admin/chats");
  $("#whoami").innerHTML = `<b>${esc(res.me.email)}</b> · ${esc(res.me.role)}`;
  $("#admins-btn").classList.toggle("hidden", res.me.role !== "owner");
  S.owner = res.me.role === "owner";  // only owners see the Cost page
  document.querySelector('#view-switch [data-view="cost"]').classList.toggle("hidden", !S.owner);
  S.chats = res.chats;
  const st = res.stats_24h;
  $("#stats").innerHTML = `<span class="sb-label">Last 24 h</span><span>${st.runs} runs · ${fmtInt(st.calls)} model calls · ~$${Number(st.cost).toFixed(2)}</span>
    <span class="sb-label">Now</span><span>${res.running} running · ${res.queued} queued</span>`
    + (res.spend_limit && res.flagged ? `<span class="chip flag">⚑ ${res.flagged} attempt${res.flagged === 1 ? "" : "s"} over the $${res.spend_limit} limit</span>` : "");
  $("#slack").textContent = res.slack ? "Slack alerts on" : "Slack alerts off";
  $("#slack").className = "slack-status " + (res.slack ? "on" : "off");
  renderFunnel(res.steps);
  renderChats();
}

// "Where they are now": each person once, at their furthest review step.
// Clicking a row (or one of its parts, like "2 running") lists only those chats; clicking it again shows all.
const STEP_NAMES = { injection: "Injection", prompt: "Prompt", hardness: "Hardness", oe: "Oracle Events", rubric: "Rubric", archived: "Archived" };
const stepKey = r => r.step || "none";
// The Idle row's parts are the steps people stopped at.
const partLabel = (step, state) => (step === "rubric" && state === "passed" ? "done, not submitted"
  : step === "idle" ? (state === "none" ? "no review yet" : `at ${STEP_NAMES[state] || state}`) : state);
const ARCHIVED_PARTS = ["submitted", "released", "with someone else"];
const rowName = r => (r.step === "idle" ? `Idle ${S.steps && S.steps.idle_days || 2}d+` : r.step ? STEP_NAMES[r.step] || r.step : "No review yet");
function renderFunnel(f) {
  if (!f) return;
  S.steps = f;
  const pick = S.funnelPick;
  $("#funnel-title").textContent = `Where they are now · ${f.people} ${f.people === 1 ? "person" : "people"}`;
  $("#funnel-rows").innerHTML = f.rows.map(r => {
    const key = stepKey(r), on = !!pick && pick.step === key && !pick.state;
    const part = (state, n) => `<button type="button" class="fpart${pick && pick.step === key && pick.state === state ? " on" : ""}"
      data-state="${state}" title="Show only these chats">${n} ${partLabel(r.step, state)}</button>`;
    const parts = r.step === "archived" ? ARCHIVED_PARTS.filter(s => r[s]).map(s => part(s, r[s])).join(" · ")
      : r.step === "idle" ? ["none", ...Object.keys(STEP_NAMES)].filter(s => r[s]).map(s => part(s, r[s])).join(" · ")
      : [r["on it"] && part("on it", r["on it"]), r.running && part("running", r.running),
         r.passed && part("passed", r.passed) + (r.step === "rubric" ? ` <button type="button" class="fwhy" data-why title="Why haven't they submitted?">why?</button>` : "")]
        .filter(Boolean).join(" · ");
    const set = r.step === "idle" || r.step === "archived";  // the sections below the steps
    return `<div class="funnel-row${r.total ? " pick" : " empty"}${set ? ` side-set ${r.step}` : ""}${on ? " on" : ""}" data-step="${key}" title="${esc(r.emails.join("\n") || "nobody")}"
      ${r.total ? `role="button" tabindex="0" aria-pressed="${on}"` : ""}>
      <span class="fname">${esc(rowName(r))}</span><b>${r.total}</b>
      <span class="fparts">${parts}</span></div>`;
  }).join("");
  $("#funnel-note").textContent = `Each person counted once, at where they are now: their latest review or edit on a task they still hold. `
    + `Idle: they still hold a task but haven't reviewed or edited anything for ${f.idle_days || 2}+ days. `
    + `Archived: all their tasks are finished (submitted, released, or now with someone else; platform checked `
    + `${f.submissions_checked_at ? fmtAgo(f.submissions_checked_at) : "not yet"}). Hover a row for names; click it to list only those chats.`;
}
// The chats the picked row or part stands for (null = no pick). Re-read on every refresh, so people move with the data.
function funnelAttempts() {
  const p = S.funnelPick, row = p && S.steps && S.steps.rows.find(r => stepKey(r) === p.step);
  if (!p) return null;
  return new Set((row ? row.attempts || [] : []).filter(a => !p.state || a.state === p.state).map(a => a.attempt));
}
function pickFunnel(step, state) {
  const p = S.funnelPick, active = document.activeElement;
  const refocus = active && $("#funnel-rows").contains(active)  // the redraw replaces the rows: keep keyboard focus where it was
    ? `.funnel-row[data-step="${step}"]${active.dataset.state ? ` [data-state="${active.dataset.state}"]` : ""}` : null;
  S.funnelPick = p && p.step === step && p.state === state ? null : { step, state };
  if (S.funnelPick) {  // show exactly that group: drop the other filters so the list matches the number
    saveChatListPrefs({ chip: "all" });
    const search = $("#list-tools .list-search");
    if (search) search.value = "";
  }
  renderFunnel(S.steps);
  renderChats();
  if (refocus && $(refocus)) $(refocus).focus();
}
// "done, not submitted" debugger: what happened since each of them passed /rubric.
const DD_ICON = { gone: "⇄", rework: "✎", blocked: "⛔", quiet: "⏸", fresh: "✓" };
async function openDoneDebug(refresh = false) {
  const w = openModal("Done, not submitted: why?", `<p class="muted" style="margin-top:0">Loading what happened since each of them passed /rubric…</p>`);
  let d;
  try { d = await api(`/api/admin/rubric-done${refresh ? "?refresh=1" : ""}`); } catch (err) { w.querySelector(".content").innerHTML = `<div class="error">${esc(err.message)}</div>`; return; }
  const h = x => (x == null ? "?" : x < 1 ? `${Math.round(x * 60)} min` : x < 48 ? `${Math.round(x)} h` : `${(x / 24).toFixed(1)} days`);
  const claim = c => c.state === "theirs" ? `Still theirs on the platform · claimed ${h((d.checked_at - c.since) / 3600)} ago · task last touched ${fmtAgo(c.touched)}`
    : c.state === "reassigned" ? `Reassigned to <b>${esc(c.to)}</b> (${fmtAgo(c.since)})`
    : c.state === "cancelled" ? `Their claim was cancelled ${fmtAgo(c.at)}` : c.state === "none" ? "Nobody holds this task now" : "Couldn't check the platform";
  const reruns = p => p.reruns.length ? p.reruns.map(r => r.status === "queued" || r.status === "running" ? `/${esc(r.command)} running`
      : `/${esc(r.command)} ${pill(r.verdict)}${r.score != null ? ` ${esc(r.score)}` : ""}`).join(" · ") : "none";
  const body = (d.claims_error ? `<div class="error">${esc(d.claims_error)}</div>` : "") + (d.people.length ? d.people.map(p => `
    <div class="dd-card">
      <div class="dd-head"><b>${esc(p.email || "unknown")}</b><span class="muted">passed /rubric ${h(p.hours_since_pass)} ago</span>
        <span class="spacer"></span><button data-open-chat="${esc(p.attempt_id)}">Open chat</button></div>
      <div class="dd-signals">${p.signals.map(s => `<span class="dd-sig ${esc(s.kind)}">${DD_ICON[s.kind] || "•"} ${esc(s.text)}</span>`).join("") || `<span class="dd-sig">No clear signal</span>`}</div>
      <div class="rc-row"><div class="k">Platform</div><div>${claim(p.claim)}</div></div>
      <div class="rc-row"><div class="k">Since the pass</div><div>${p.messages_since} message${p.messages_since === 1 ? "" : "s"} from them${p.admin_replies_since ? ` · ${p.admin_replies_since} admin repl${p.admin_replies_since === 1 ? "y" : "ies"}` : ""} · reruns: ${reruns(p)}</div></div>
      ${p.last_message ? `<div class="rc-row"><div class="k">Last message</div><div>“${esc(p.last_message.text)}” <span class="muted">${fmtAgo(p.last_message.at)}</span></div></div>` : ""}
    </div>`).join("") : `<p>Nobody is done and waiting to submit right now.</p>`)
    + `<p class="muted" style="margin-bottom:0">Platform claims come from Snowflake (a few minutes behind). Signals are hints from their activity, not verdicts.</p>`;
  w.remove();
  const m = openModal(`Done, not submitted: why? (${d.people.length})`, body, `<button data-refresh>Refresh</button>`);
  m.querySelector("[data-refresh]").onclick = () => { m.remove(); openDoneDebug(true); };
  m.addEventListener("click", e => {
    const b = e.target.closest("[data-open-chat]");
    if (b) { m.remove(); select(b.dataset.openChat); }
  });
}
$("#funnel-rows").addEventListener("click", e => {
  if (e.target.closest("[data-why]")) return openDoneDebug();
  const row = e.target.closest(".funnel-row.pick"), part = e.target.closest("[data-state]");
  if (row) pickFunnel(row.dataset.step, part ? part.dataset.state : null);
});
$("#funnel-rows").addEventListener("keydown", e => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches(".funnel-row.pick")) { e.preventDefault(); pickFunnel(e.target.dataset.step, null); }
});
$("#funnel-pick").addEventListener("click", e => { if (e.target.closest("[data-clear]")) { S.funnelPick = null; renderFunnel(S.steps); renderChats(); } });

(() => {  // the panel folds like the chat page's sidebar sections (remembered)
  const sec = $("#funnel"), toggle = sec.querySelector(".sec-toggle");
  const set = folded => { sec.classList.toggle("folded", folded); toggle.setAttribute("aria-expanded", String(!folded)); };
  set(localStorage.getItem("evp_funnel_folded") === "1");
  toggle.onclick = () => { const folded = !sec.classList.contains("folded"); set(folded); localStorage.setItem("evp_funnel_folded", folded ? "1" : "0"); };
})();

const listTools = chatListTools($("#list-tools"), renderChats);
// The top bar's "/admin requests (N)" button: the "/admin open" filter is easy to miss under the funnel panel.
const openRequests = () => S.chats.filter(c => c.admin_open && !c.archived);
function renderAdminRequests() {
  const n = openRequests().length;
  $("#adminreq-n").textContent = n;
  $("#adminreq-btn").classList.toggle("alert", n > 0);
}
$("#adminreq-btn").onclick = () => {
  S.funnelPick = null;
  saveChatListPrefs({ chip: "admin" });
  const search = $("#list-tools .list-search");
  if (search) search.value = "";
  if (S.steps) renderFunnel(S.steps);
  renderChats();
  $("#chats").scrollTop = 0;
  const first = $("#chats [data-a]");
  if (first && !openRequests().some(c => c.attempt_id === S.selected)) select(first.dataset.a);
};
function renderChats() {
  renderAdminRequests();
  const list = S.chats.map(c => {
    const [cmd, verdict] = (c.last_review || "").split(" ");
    return { ...c, running: c.active_runs > 0, over: !!c.spend_flag, last_cmd: cmd, last_verdict: verdict };
  });
  const only = funnelAttempts(), p = S.funnelPick;
  const shown = listTools.apply(only ? list.filter(c => only.has(c.attempt_id)) : list, { pick: !!only });
  $("#funnel-pick").classList.toggle("hidden", !p);
  if (p) $("#funnel-pick").innerHTML = `<span>Only <b>${esc(rowName({ step: p.step === "none" ? null : p.step }))}${p.state
      ? ` · ${esc(partLabel(p.step, p.state))}` : ""}</b> · ${shown.length} chat${shown.length === 1 ? "" : "s"}</span>
    <button type="button" class="ghost" data-clear>✕ Show all</button>`;
  $("#chats").innerHTML = shown.map(c => chatRowHtml(c, { attr: "data-a", current: c.attempt_id === S.selected, god: true, preview: c.last_text }))
    .join("") || `<div class="muted">${S.chats.length ? "No matching chats." : "No chats yet."}</div>`;
}
setInterval(renderChats, 60000);  // keep "5m ago" current between updates
$("#chats").addEventListener("click", e => { const row = e.target.closest("[data-a]"); if (row) select(row.dataset.a); });

// Open a chat: the list only moves its highlight (no redraw, no scroll jump) and the chat fades in.
// Re-selecting the open chat (a run finished, a block changed) refreshes its header and leaves the messages alone.
let selectSeq = 0;
async function select(attempt) {
  if (attempt !== S.selected) clearReplyImages();  // images attached for another chat aren't sent here
  const seq = ++selectSeq;
  const fresh = attempt !== S.selected || !S.detail;
  S.selected = attempt;
  if (location.hash.slice(1) !== attempt) history.replaceState(null, "", "#" + attempt);
  document.querySelectorAll("#chats [data-a]").forEach(r => r.classList.toggle("current", r.dataset.a === attempt));
  if (fresh) $("#detail").classList.add("switching");
  let detail;
  try {
    detail = await api(`/api/admin/chat/${attempt}`);
  } catch (err) {
    if (seq === selectSeq) { $("#detail").classList.remove("switching"); uiAlert("Couldn't open that chat", err.message); }
    return;
  }
  if (seq !== selectSeq) return;  // a later click won: don't show this chat under it
  const shown = new Set(fresh ? [] : S.detail.messages.map(m => m.id));
  S.detail = detail;
  renderDetail(fresh);
  if (!fresh) {  // a refresh: add anything that arrived before its live event did
    const box = $("#messages");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 200;
    detail.messages.filter(m => !shown.has(m.id)).forEach(m => box.insertAdjacentHTML("beforeend", messageHtml(m, { admin: true })));
    if (nearBottom) box.scrollTop = box.scrollHeight;
    markAdminMessages();
  }
  requestAnimationFrame(() => $("#detail").classList.remove("switching"));
}

// /admin requests inside a chat: the user's /admin messages are outlined (red while the request is open) and the
// header's "Jump to /admin" button and each request line scroll to them, so they're easy to find in a long chat.
function markAdminMessages() {
  const open = (S.detail ? S.detail.admin_requests : []).filter(r => r.status === "open").map(r => r.created_at);
  document.querySelectorAll("#messages [data-adminmsg]").forEach(el =>
    el.classList.toggle("open", open.some(t => Math.abs(t - Number(el.dataset.at)) < 5)));
  const all = document.querySelectorAll("#messages [data-adminmsg]").length;
  const nOpen = document.querySelectorAll("#messages [data-adminmsg].open").length;
  const btn = $("#detail-head [data-jumpadmin]");
  if (btn) {
    btn.classList.toggle("hidden", !all);
    btn.classList.toggle("alert", nOpen > 0);
    btn.textContent = nOpen ? `↓ Jump to /admin (${nOpen} open)` : `↓ Jump to /admin (${all})`;
  }
}
let jumpIndex = 0;
function jumpToAdmin(at = null) {
  const els = [...document.querySelectorAll("#messages [data-adminmsg]")];
  if (!els.length) return;
  let el;
  if (at != null) el = els.reduce((best, e) => Math.abs(Number(e.dataset.at) - at) < Math.abs(Number(best.dataset.at) - at) ? e : best);
  else {  // the open ones first, newest first; each click goes to the next one
    const open = els.filter(e => e.classList.contains("open")), pool = (open.length ? open : els).reverse();
    el = pool[jumpIndex++ % pool.length];
  }
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}
$("#detail-head").addEventListener("click", e => {
  if (e.target.closest("[data-jumpadmin]")) { jumpToAdmin(); return; }
  const req = e.target.closest("[data-jumpreq]");
  if (req && !e.target.closest("summary .muted")) jumpToAdmin(Number(req.dataset.jumpreq));
});

function renderDetail(fresh = true) {
  const d = S.detail;
  const active = d.active_run;
  const stopping = r => ["queued", "running"].includes(r.status) && r.error === "cancelled by user";
  const runs = d.runs.slice(0, 12).map(r => `<tr><td>${fmtTime(r.created_at)}</td><td>${esc(r.command ? "/" + r.command : "follow-up")}</td>
    <td>${stopping(r) ? "stopping…" : esc(r.status)}${r.error && !stopping(r) ? ` <span class="muted">(${esc(r.error)})</span>` : ""}</td><td>${r.verdict ? pill(r.verdict) : ""}</td>
    <td>${fmtInt(r.calls)}</td><td>${fmtInt((r.input_tokens || 0) + (r.output_tokens || 0) + (r.cache_read_tokens || 0) + (r.cache_creation_tokens || 0))}</td>
    <td>$${Number(r.cost_estimate || 0).toFixed(2)}</td>
    <td><a href="/api/admin/runs/${r.id}/log">log</a>${["queued", "running"].includes(r.status) && !stopping(r) ? ` · <a href="#" data-cancel="${r.id}">cancel</a>` : ""}${
      r.kind === "review" && r.status === "done" && !r.verdict ? ` · <a href="#" data-recover="${r.id}" title="It finished but no report card was posted: post its report (and pass card) from the saved report file">recover report</a>` : ""}</td></tr>`).join("");
  // A request can be a whole pasted SQL file: show one line, the full text in its own scroll box on demand.
  const openReqs = fresh ? new Set() : new Set([...document.querySelectorAll("#detail-head details[data-req][open]")].map(e => e.dataset.req));
  const requests = d.admin_requests.filter(r => r.status === "open").map(r => {
    const text = r.message || "", short = text.length > 160;
    const line = `<a href="#" class="adm-jump" data-jumpreq="${r.created_at}" title="Show it in the chat">🚨 ${fmtTime(r.created_at)}</a>: ${esc(short ? text.slice(0, 160).replace(/\s+/g, " ") + "…" : text)}`;
    return short ? `<details class="adm-req" data-req="${esc(String(r.id))}"${openReqs.has(String(r.id)) ? " open" : ""}>
        <summary>${line} <span class="muted">show full (${fmtInt(text.length)} chars)</span></summary><div class="adm-req-full">${esc(text)}</div></details>`
      : `<div class="adm-req">${line}</div>`;
  }).join("");
  const wasOpen = !fresh && !!($("#detail-head details.run-details") || {}).open;  // keep "Runs, reports and inputs" open on a refresh
  $("#detail-head").innerHTML = `
    <div class="row" style="flex-wrap:wrap;gap:8px"><b class="mono">${esc(d.attempt_id)}</b><span class="chip">task ${esc(d.task_id)}</span>
      ${d.archived_from ? `<span class="chip" title="The platform re-served this task to someone else; this earlier holder's chat was moved aside and is read-only">🗄 archived · earlier holder of <span class="mono">${esc(d.archived_from)}</span></span>` : ""}
      <span class="chip">${esc(d.email)}</span>${d.admin_open ? '<span class="pill FAIL">admin request open</span>' : ""}
      ${active ? `<span class="pill NON-FAIL">${stopping(active) ? "stopping" : esc(active.status)} ${esc(active.command ? "/" + active.command : "follow-up")}</span>` : ""}
      ${d.limits.reviews_left == null ? "" : `<span class="muted">${d.limits.reviews_left} reviews left today</span>`}
      ${d.spend && d.spend.limit ? `<span class="chip${d.spend.over ? " flag" : ""}" title="The contributor's own runs on this attempt (admin runs not counted); the limit is per attempt">
        ${d.spend.over ? "⚑ " : ""}spend $${Number(d.spend.usd).toFixed(2)} of $${d.spend.limit}${d.spend.flagged_at ? " · flagged " + esc(fmtTime(d.spend.flagged_at)) : ""}</span>` : ""}
      ${d.disabled ? `<span class="chip flag" title="${esc(blockTitle(d.disabled))}">⛔ contributor disabled</span>` : ""}
      ${(d.broadcasts_waiting || []).length ? `<span class="chip" title="${esc("Waiting for this contributor to press Acknowledge:\n"
        + d.broadcasts_waiting.map(b => `• ${b.title} (${fmtTime(b.created_at)})`).join("\n"))}">📣 ${d.broadcasts_waiting.length} broadcast${d.broadcasts_waiting.length === 1 ? "" : "s"} not yet acknowledged</span>` : ""}
      ${d.blocked ? `<span class="chip flag" title="${esc(blockTitle(d.blocked))}">⛔ chat blocked</span>` : ""}
      <span class="spacer"></span>
      <button class="hidden adm-jumpbtn" data-jumpadmin title="Scroll to the user's /admin message in this chat (click again for the next one)">↓ Jump to /admin</button>
      ${d.spend && d.spend.limit ? `<button data-budget title="Let this attempt spend more: its runs stop at the budget until an admin approves more">Approve more budget…</button>` : ""}
      ${(d.override_options || []).length ? `<button data-override title="Issue a pass card for a review that got none (it didn't pass, or passed but PARTIAL). It's marked as an admin override, with your name and reason, on the pass screen and on /verify.">Issue pass (override)…</button>` : ""}
      ${d.blocked ? `<button data-unblock="attempt" data-key="${esc(d.attempt_id)}">Unblock chat</button>`
        : `<button class="danger" data-block="attempt">Block chat</button>`}
      ${d.disabled ? `<button data-unblock="person" data-key="${esc(d.disabled.key)}">Re-enable contributor</button>`
        : `<button class="danger" data-block="person">Disable contributor</button>`}</div>
    ${requests ? `<div class="adm-reqs">${requests}</div>` : ""}
    <details class="run-details" style="margin-top:6px"${wasOpen ? " open" : ""}><summary class="muted">Runs, reports and inputs</summary>
      <table class="runs"><tr><th>When</th><th>Run</th><th>Status</th><th>Verdict</th><th>Calls</th><th>Tokens</th><th>Est. cost</th><th></th></tr>${runs}</table>
      <div style="margin-top:6px">Reports: ${d.reports.map(r => `<a href="#" data-rep="${esc(r.file)}">${esc(r.stage)}</a> ${pill(r.verdict)}`).join(" · ") || "none"}</div>
      <div class="muted" style="margin-top:4px">Inputs: ${d.inputs.map(i => `${esc(i.key)} r${esc(i.revision)}`).join(", ") || "none"}</div>
    </details>`;
  if (fresh) {
    const box = $("#messages");
    box.innerHTML = d.messages.map(m => messageHtml(m, { admin: true })).join("");
    box.scrollTop = box.scrollHeight;
    jumpIndex = 0;
  }
  markAdminMessages();
  if (fresh && d.admin_open) requestAnimationFrame(() => jumpToAdmin());  // opening a chat with an open request: show it
  $("#composer").classList.remove("hidden");
  $("#detail-hint").textContent = "The user sees your reply live, labelled Admin.";
}

// ---- issue a pass over a review that didn't pass (marked as an admin override everywhere it shows) ----
async function askOverride() {
  const d = S.detail, opts = d.override_options || [];
  const first = opts.map(o => o.same_input).lastIndexOf(true);  // pre-pick the latest stage a card can match
  if (!opts.length) return uiAlert("Nothing to pass", "Every stage's newest review already passed or has a pass card.");
  const items = opts.map((o, i) => `<label style="display:flex;gap:8px;align-items:flex-start;margin:4px 0">
      <input type="radio" name="ov-pick" value="${i}" ${i === first ? "checked" : ""} ${o.same_input ? "" : "disabled"} style="width:auto;margin-top:3px">
      <span><b>/${esc(o.stage)}</b> · ${pill(o.verdict)}${o.score != null ? ` score ${esc(o.score)}` : ""} · ${esc(fmtTime(o.finished_at))}
        ${o.same_input ? "" : `<br><span class="muted" style="font-size:12px">The saved input changed after this review, so a card wouldn't match. Run it again first.</span>`}</span></label>`).join("");
  const box = document.createElement("div");
  box.innerHTML = `<p style="margin-top:0">Gives <b>${esc(d.email)}</b> a pass card for the newest review of the stage you pick, covering exactly the
      version that review graded. The card says <b>admin override</b>, with your name, your reason and the review's own verdict, on the pass screen and on /verify.</p>
    ${items}
    <textarea id="ov-reason" rows="3" placeholder="Reason (shown on the card)" style="margin-top:8px"></textarea>`;
  S.lastOverride = { index: first, reason: "" };  // the pre-checked choice; updated as the admin edits
  const ok = await uiDialog({ title: "Issue pass (admin override)", html: box.outerHTML, ok: "Issue pass", cancel: "Cancel" });
  if (!ok) return;
  // uiDialog removes its DOM on close, so read the values from the last open copy before it closed
  const pick = S.lastOverride || {};
  if (!pick.reason || pick.reason.length < 5) return uiAlert("Add a reason", "The reason is shown on the pass card. Write at least a few words.");
  const o = opts[pick.index];
  if (!o || !o.same_input) return uiAlert("Can't issue that one", "Pick a review whose input hasn't changed since.");
  try {
    const res = await api(`/api/admin/chat/${encodeURIComponent(d.attempt_id)}/override-pass`, { json: { run_id: o.run_id, stage: o.stage, reason: pick.reason } });
    uiAlert("Pass issued", `Code ${res.code}. It's in their chat now, marked as an admin override.`);
    select(d.attempt_id);
  } catch (err) { uiAlert("Couldn't issue the pass", err.message); }
}
document.addEventListener("input", e => { if (e.target.id === "ov-reason") (S.lastOverride = S.lastOverride || {}).reason = e.target.value.trim(); });
document.addEventListener("change", e => { if (e.target.name === "ov-pick") (S.lastOverride = S.lastOverride || {}).index = +e.target.value; });

$("#detail-head").addEventListener("click", async e => {
  const rec = e.target.closest("[data-recover]");
  if (rec) {
    e.preventDefault();
    try {
      const out = await api(`/api/admin/runs/${rec.dataset.recover}/recover-report`, { method: "POST" });
      uiAlert("Report recovered", `Posted the report (${esc(out.verdict || "no verdict")}${out.score != null ? ", score " + out.score : ""})${out.pass ? " and its pass card" : ""} in the chat.`);
      await select(S.selected);
    } catch (err) { uiAlert("Couldn't recover it", err.message || "Please try again."); }
    return;
  }
  if (e.target.closest("[data-budget]")) {
    const sp = S.detail.spend || {};
    const shown = uiDialog({ title: "Approve more budget", ok: "Approve", cancel: "Cancel", html: `<p style="margin-top:0">
      Spent <b>$${Number(sp.usd || 0).toFixed(2)}</b> of a <b>$${sp.limit}</b> budget. The contributor sees your approval in the chat.</p>
      <label class="muted small" for="bud-add">Approve this much more ($)</label><input id="bud-add" type="number" min="1" max="5000" step="1" value="100">
      <label class="muted small" for="bud-note" style="display:block;margin-top:8px">Note (optional, kept with the approval)</label>
      <input id="bud-note" type="text" maxlength="300" placeholder="e.g. last /rubric round after the OE fix">` });
    let add = "100", note = "";
    document.querySelector("#bud-add").addEventListener("input", ev => { add = ev.target.value; });
    document.querySelector("#bud-note").addEventListener("input", ev => { note = ev.target.value; });
    if (!(await shown)) return;
    try {
      await api(`/api/admin/chat/${S.selected}/budget`, { json: { add_usd: Number(add), note } });
      await select(S.selected);
    } catch (err) { uiAlert("Couldn't approve", err.message || "Please try again."); }
    return;
  }
  const blk = e.target.closest("[data-block]"), unblk = e.target.closest("[data-unblock]");
  if (e.target.closest("[data-override]")) return askOverride();
  if (blk) return askBlock(blk.dataset.block, { attempt_id: S.selected, who: S.detail.email });
  if (unblk) return askUnblock(unblk.dataset.unblock, unblk.dataset.key, S.detail.email);
  if (e.target.dataset.cancel) {
    e.preventDefault();
    const id = e.target.dataset.cancel;
    if (!(await uiDialog({ title: "Cancel this run?", html: `<p style="margin:0">The user's run stops now. Their saved inputs are kept.</p>`,
                           ok: "Cancel run", cancel: "Keep running", danger: true }))) return;
    try {
      await api(`/api/admin/runs/${id}/cancel`, { method: "POST" });
      if (S.selected) await select(S.selected);
    } catch (err) {
      uiAlert("Couldn't cancel", err.message || "Please try again.");
    }
  } else if (e.target.dataset.rep) {
    e.preventDefault();
    const text = await api(`/api/admin/reports/${S.selected}/${e.target.dataset.rep}`);
    openModal(e.target.dataset.rep, `<div class="bubble" style="border:0;padding:0">${md(text)}</div>`);
  }
});

wireMessageClicks($("#messages"), id => S.detail.messages.find(m => m.id === id),
  m => `${S.selected}_${REPORT_FILE[m.meta.stage] || "report.md"}`);

// Images in a reply (2026-11-03): pasted or attached, uploaded at once, shown as thumbnails, sent as Markdown images
// after the text. The contributor sees them in the message; the assistant only sees "[image attached by the admin]".
const replyImages = [];  // {url, local} for the chat being answered
function renderReplyImages() {
  const box = $("#reply-images");
  box.classList.toggle("hidden", !replyImages.length);
  box.innerHTML = replyImages.map((im, i) => `<span class="reply-image"><img src="${esc(im.local || im.url)}" alt="">${
    im.url ? "" : '<span class="muted small">uploading…</span>'}<button type="button" class="ghost small" data-unattach="${i}" title="Remove">✕</button></span>`).join("");
}
async function attachImages(files) {
  if (!S.selected) return;
  for (const file of files) {
    if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type)) { uiAlert("Can't attach that", "Only PNG, JPEG, GIF or WebP images."); continue; }
    if (file.size > 8 * 1024 * 1024) { uiAlert("Image too big", "Images can be up to 8 MB."); continue; }
    const im = { url: null, local: URL.createObjectURL(file), chat: S.selected };
    replyImages.push(im);
    renderReplyImages();
    try {
      const form = new FormData();
      form.append("file", file, file.name || "pasted.png");
      im.url = (await api(`/api/admin/chat/${im.chat}/image`, { method: "POST", body: form })).url;
    } catch (err) {
      replyImages.splice(replyImages.indexOf(im), 1);
      uiAlert("Couldn't upload the image", err.message || "Please try again.");
    }
    renderReplyImages();
  }
}
function clearReplyImages() {
  replyImages.forEach(im => im.local && URL.revokeObjectURL(im.local));
  replyImages.length = 0;
  renderReplyImages();
}
$("#attach").onclick = () => $("#reply-file").click();
$("#reply-file").addEventListener("change", e => { attachImages([...e.target.files]); e.target.value = ""; });
$("#reply").addEventListener("paste", e => {
  const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
  if (files.length) { e.preventDefault(); attachImages(files); }
});
$("#reply-images").addEventListener("click", e => {
  const b = e.target.closest("[data-unattach]");
  if (b) { const [im] = replyImages.splice(+b.dataset.unattach, 1); if (im?.local) URL.revokeObjectURL(im.local); renderReplyImages(); }
});

async function sendReply() {
  const text = $("#reply").value.trim();
  if (!S.selected || replyImages.some(im => !im.url)) return;  // wait for uploads
  const images = replyImages.filter(im => im.chat === S.selected).map(im => `![image](${im.url})`);
  const body = [text, ...images].filter(Boolean).join("\n\n");
  if (!body) return;
  await api(`/api/admin/chat/${S.selected}/reply`, { json: { text: body } });
  $("#reply").value = "";
  clearReplyImages();
}
$("#send").onclick = sendReply;
$("#reply").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendReply(); } });
$("#resolve").onclick = async () => { if (S.selected) { await api(`/api/admin/chat/${S.selected}/resolve`, { method: "POST" }); await select(S.selected); loadChats(); } };

// The chat list after live events. It used to reload ~800 ms after ANY event in the whole portal; with 30+ reviews
// streaming that was about once a second per open admin tab, ~3 heavy /api/admin/chats a second in all, and a newly
// opened admin page sat on a blank screen for minutes behind them (2026-11-02). Now: at most once per LIST_EVERY_MS
// per tab, one request at a time, none while the tab is in the background (it catches up when shown again); a new
// /admin request refreshes at once.
const LIST_EVERY_MS = 15000;
let refreshTimer = null, listBusy = false, listLast = 0, listPending = false;
async function refreshList(now = false) {
  listPending = true;
  if (document.hidden || listBusy || refreshTimer) return;
  const wait = now ? 0 : Math.max(0, listLast + LIST_EVERY_MS - Date.now());
  refreshTimer = setTimeout(async () => {
    refreshTimer = null;
    if (document.hidden) return;
    listBusy = true; listPending = false; listLast = Date.now();
    try { await loadChats(); } catch (e) { /* the next event or the interval tries again */ }
    listBusy = false;
    if (listPending) refreshList();
  }, wait);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && listPending) refreshList(true); });

function connect() {
  const es = new EventSource("/api/admin/events");
  S.es = es;
  es.onmessage = () => {};
  ["message", "run", "proof", "admin_request"].forEach(type => es.addEventListener(type, ev => {
    const p = JSON.parse(ev.data);
    if (p.attempt_id === S.selected && S.detail) {
      if (type === "message" && !S.detail.messages.some(m => m.id === p.data.id)) {
        S.detail.messages.push(p.data);
        $("#messages").insertAdjacentHTML("beforeend", messageHtml(p.data, { admin: true }));
        $("#messages").scrollTop = $("#messages").scrollHeight;
        markAdminMessages();
      } else if (type === "run") {
        select(S.selected);
      }
    }
    refreshList(type === "admin_request");
    if (type === "admin_request" && window.Notification && Notification.permission === "granted") {
      new Notification("Eval portal /admin request", { body: p.data.message });
    }
  }));
  es.onerror = () => { es.close(); setTimeout(connect, 4000); };
  if (window.Notification && Notification.permission === "default") Notification.requestPermission();
}

boot();
