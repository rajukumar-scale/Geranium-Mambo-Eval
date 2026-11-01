// Admin CB tracker (server/cbs.py): Atlas Commercial contributors at L-1 since Oct 13. Benchmarks per group against
// fixed bars, weekly trends, learning curves, trusted conversion, and each contributor's status, with "Mark trained"
// so offboarding only follows lagging after training.
// Charts follow the dataviz rules: 2px lines with 8px dots, hairline grid, one axis, a legend above the small
// multiples plus direct labels at each line's end, a tooltip on hover and keyboard focus (textContent only), and the
// same numbers in the tables. Group colours are validated in both modes (style.css, .cbs-app).

const S = { data: null, status: "", group: "", search: "", open: new Set(), sort: null };  // sort: {key, dir}
const GROUPS = [{ key: "QMA", label: "QMA", cls: "g-qma" }, { key: "trusted", label: "Trusted", cls: "g-trusted" },
                { key: "non-trusted", label: "Non-trusted", cls: "g-non" }];
const STATUSES = {
  offboard: { label: "Offboard", icon: "⛔", cls: "st-offboard", tip: "Trained, and the last 2 claims since then went badly" },
  needs_training: { label: "Needs training", icon: "⚠", cls: "st-training", tip: "Past the first 2 tasks: the last 2 closed claims had no submit, or the last 2 submitted tasks were over the Flag bar" },
  trained: { label: "Trained", icon: "🎓", cls: "st-trained", tip: "Trained; watching their next 2 claims" },
  grace: { label: "Grace", icon: "🌱", cls: "st-grace", tip: "Their first 2 tasks: no flags yet" },
  efficient: { label: "Efficient", icon: "✓", cls: "st-efficient", tip: "Submitted a task within the Acceptable bar (hours and $)" },
  ok: { label: "No flag", icon: "·", cls: "st-ok", tip: "Past grace, no flag, no task within the Acceptable bar yet" },
  offboarded: { label: "Offboarded", icon: "🚫", cls: "st-ok", tip: "Tagged mcp_ac_temp_block (already offboarded); the tag syncs daily" },
};
const usd = v => (v == null ? "–" : "$" + Math.round(v).toLocaleString("en-US"));
const hrs = v => (v == null ? "–" : `${(+v).toFixed(1)} h`);
const num = v => (v == null ? "–" : (+v % 1 ? (+v).toFixed(1) : String(v)));
const pct = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : "–");
const day = t => new Date(t * 1000).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
const weekName = w => `Week of ${new Date(w + "T00:00:00Z").toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" })}`;
const when = t => new Date(t * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

async function load(refresh = false) {
  clearTimeout(S.poll);
  $("#asof").textContent = "Loading…";
  try {
    const d = await api(`/api/admin/cbs${refresh ? "?refresh=1" : ""}`);
    document.querySelector('#view-switch [data-view="cost"]').classList.toggle("hidden", d.me?.role !== "owner");
    if (d.loading || (refresh && d.refreshing)) { S.poll = setTimeout(() => load(), 4000); }
    if (d.loading) { $("#asof").textContent = "Reading the claims data… (about a minute the first time)"; return; }
    if (d.error) { $("#asof").textContent = ""; $("#notes").innerHTML = `<div class="error">${esc(d.error)}</div>`; return; }
    S.data = d;
    render();
  } catch (e) {
    $("#asof").textContent = "";
    $("#notes").innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

function render() {
  const d = S.data;
  $("#asof").textContent = `Claims data from ${when(d.fetched_at)}${d.refreshing ? " · refreshing…" : ""} · portal figures live`;
  $("#notes").innerHTML = d.stale ? `<div class="error">${esc(d.stale)} Showing the last figures.</div>` : "";
  renderTiles(d);
  renderBench(d);
  renderTrend(d);
  renderCurve(d);
  renderConv(d);
  renderPeople(d);
  $("#foot").innerHTML = `A row is one task × contributor at L-1, counted from the first time they opened it (claim, incomplete
    attempt, submit or portal chat). Only claims they worked on count: at least one review, or a submit (the analysis also counted
    30+ active minutes, which the portal can't see); untouched claims are shown per contributor. <b>Hours</b> = from their first
    portal activity on the task until /injection, /prompt, /oe and /rubric had all passed. <b>$</b> = their own portal runs
    (list-price estimate, as in the analysis). <b>Groups</b> on the day the task was opened: QMA (qm_auditor), trusted
    (mcp_ac_trusted that day), non-trusted; internal accounts are left out. The bars are fixed at the P1.1 analysis
    (p50 / p75 / p90 of QMA + trusted submitted tasks, Oct 13–29). A claim <b>went badly</b> = no submit, or submitted over the
    Flag bar. Claims data refreshes every 30 minutes.`;
}

function renderTiles(d) {
  const counts = Object.fromEntries(Object.keys(STATUSES).map(k => [k, 0]));
  d.people.forEach(p => counts[p.status]++);
  $("#tiles").innerHTML = Object.entries(STATUSES).map(([k, s]) => {
    const st = d.statuses[k] || {};
    const split = GROUPS.map(g => st[g.key] ? `${st[g.key]} ${g.label.toLowerCase()}` : "").filter(Boolean).join(" · ") || "none";
    return `<button type="button" class="tile cbs-tile ${S.status === k ? "on" : ""}" data-status="${k}" title="${esc(s.tip)}">
      <div class="tile-label"><span class="st ${s.cls}">${s.icon} ${esc(s.label)}</span></div>
      <div class="tile-value">${fmtInt(counts[k])}</div>
      <div class="tile-sub">${esc(split)}${k === "offboard" || k === "needs_training" ? `<br>${usd(st.spend || 0)} portal spend` : ""}</div></button>`;
  }).join("");
}

function barLine(name, b) {
  return `<b>${name}</b> ≤${b.hours} h, ≤${usd(b.cost)}, ≤${b.reviews} reviews`;
}

function renderBench(d) {
  const row = g => {
    const s = d.groups[g.key];
    return `<tr><th scope="row"><span class="sw ${g.cls}"></span>${g.label}</th><td class="num">${fmtInt(s.cbs)}</td><td class="num">${fmtInt(s.claims)}</td>
      <td class="num">${fmtInt(s.submitted)}</td><td class="num">${hrs(s.hours_p50)}</td><td class="num">${hrs(s.hours_p90)}</td>
      <td class="num">${usd(s.cost_p50)}</td><td class="num">${num(s.reviews_p50)}</td><td class="num">${num(s.prompt_tries_p50)}</td><td class="num">${num(s.rubric_tries_p50)}</td></tr>`;
  };
  const b = d.bars, r = d.recent_bars;
  $("#bench-card").innerHTML = `<div class="cc-head"><h2>1 · Benchmarks by group</h2>
    <p class="muted">Medians; "slowest tenth" = the 90th percentile. $, reviews and tries are per submitted task.</p></div>
    <div class="table-wrap"><table class="cost-table"><thead><tr><th>Group</th><th class="num">CBs</th><th class="num">Claims worked</th>
      <th class="num">Submitted</th><th class="num">Hours</th><th class="num">Slowest tenth</th><th class="num">$ per task</th>
      <th class="num">Reviews</th><th class="num">/prompt tries</th><th class="num">/rubric tries</th></tr></thead>
      <tbody>${GROUPS.map(row).join("")}</tbody></table></div>
    <div class="cbs-bars">
      <div><span class="st st-efficient">✓ Efficient (target)</span> ${barLine("", b.efficient)}</div>
      <div><span class="st st-ok">Acceptable</span> ${barLine("", b.acceptable)}</div>
      <div><span class="st st-offboard">⚑ Flag</span> over ${b.flag.hours} h, over ${usd(b.flag.cost)}, or over ${b.flag.reviews} reviews</div>
      <p class="muted">These bars are fixed. The same cut on the last ${d.recent_days} days (${fmtInt(d.recent_n)} QMA + trusted submitted tasks)
        would give: efficient ${hrs(r.efficient.hours)} / ${usd(r.efficient.cost)} / ${num(r.efficient.reviews)}, acceptable ${hrs(r.acceptable.hours)} /
        ${usd(r.acceptable.cost)} / ${num(r.acceptable.reviews)}, flag ${hrs(r.flag.hours)} / ${usd(r.flag.cost)} / ${num(r.flag.reviews)}.</p>
    </div>`;
}

// One small chart per measure, by week, one line per group, with the Acceptable bar as a dashed reference line.
function lineChart({ id, title, key, fmt, bar, weeks }) {
  const W = 340, H = 190, L = 44, R = 78, T = 14, B = 26, w = W - L - R, h = H - T - B;
  const vals = weeks.flatMap(wk => GROUPS.map(g => wk[g.key][key])).filter(v => v != null);
  const top = Math.max(bar || 0, ...vals, 1) * 1.1;
  const raw = top / 3, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);  // round gridlines: 10, 20, 50, 100...
  const max = step * Math.ceil(top / step);
  const x = i => L + (weeks.length === 1 ? w / 2 : (i * w) / (weeks.length - 1));
  const y = v => T + h - (v / max) * h;
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  let svg = ticks.map(t => `<line class="grid" x1="${L}" x2="${L + w}" y1="${y(t)}" y2="${y(t)}"/><text class="axis" x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${esc(fmt(t))}</text>`).join("");
  if (bar) svg += `<line class="refline" x1="${L}" x2="${L + w}" y1="${y(bar)}" y2="${y(bar)}"/><text class="axis" x="${L + w + 4}" y="${y(bar) + 4}">Acceptable</text>`;
  weeks.forEach((wk, i) => { svg += `<text class="axis" x="${x(i)}" y="${H - 6}" text-anchor="middle">${esc(day(Date.parse(wk.week + "T00:00:00Z") / 1000))}</text>`; });
  const ends = [];
  GROUPS.forEach(g => {
    let path = "", pen = false;
    weeks.forEach((wk, i) => {
      const v = wk[g.key][key];
      if (v == null) { pen = false; return; }
      path += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    if (path) svg += `<path class="line ${g.cls}" d="${path}"/>`;
    weeks.forEach((wk, i) => { const v = wk[g.key][key]; if (v != null) svg += `<circle class="dot ${g.cls}" cx="${x(i)}" cy="${y(v)}" r="4"/>`; });
    const last = weeks.map((wk, i) => [i, wk[g.key][key]]).filter(([, v]) => v != null).pop();
    if (last) ends.push({ g, y: y(last[1]), x: x(last[0]) });
  });
  ends.sort((a, b) => a.y - b.y).forEach((e, i, all) => { if (i && e.y - all[i - 1].y < 13) e.y = all[i - 1].y + 13; });
  ends.forEach(e => { svg += `<text class="endlabel" x="${e.x + 8}" y="${e.y + 4}">${esc(e.g.label)}</text>`; });
  weeks.forEach((wk, i) => {
    const half = weeks.length === 1 ? w / 2 : w / (weeks.length - 1) / 2;
    svg += `<rect class="hit" tabindex="0" data-chart="${id}" data-i="${i}" x="${x(i) - half}" y="${T}" width="${half * 2}" height="${h}"/>`;
  });
  CHARTS[id] = { title, key, fmt, weeks };
  return `<figure class="cbs-chart"><figcaption>${esc(title)}</figcaption>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)} by week and group">${svg}</svg></figure>`;
}
const CHARTS = {};

function renderTrend(d) {
  const weeks = d.weeks;
  $("#trend-card").innerHTML = `<div class="cc-head"><h2>Weekly trend</h2>
    <p class="muted">By the week (Monday, UTC) the task was opened. The current week is still filling in.</p>
    <div class="legend">${GROUPS.map(g => `<span><span class="sw ${g.cls}"></span>${g.label}</span>`).join("")}<span><span class="sw refsw"></span>Acceptable bar</span></div></div>
    <div class="cbs-charts">
      ${lineChart({ id: "hours", title: "Median hours per task", key: "hours_p50", fmt: v => `${v} h`, bar: d.bars.acceptable.hours, weeks })}
      ${lineChart({ id: "cost", title: "Median $ per submitted task", key: "cost_p50", fmt: v => usd(v), bar: d.bars.acceptable.cost, weeks })}
      ${lineChart({ id: "reviews", title: "Median reviews per submitted task", key: "reviews_p50", fmt: v => String(v), bar: d.bars.acceptable.reviews, weeks })}
    </div>
    <details class="sc-table"><summary>Show as a table</summary><div class="table-wrap"><table class="cost-table">
      <thead><tr><th>Week</th>${GROUPS.map(g => `<th class="num">${g.label} hours</th><th class="num">${g.label} $</th><th class="num">${g.label} reviews</th><th class="num">${g.label} submitted</th>`).join("")}</tr></thead>
      <tbody>${weeks.map(wk => `<tr><th scope="row">${esc(weekName(wk.week))}</th>${GROUPS.map(g => `<td class="num">${hrs(wk[g.key].hours_p50)}</td><td class="num">${usd(wk[g.key].cost_p50)}</td><td class="num">${num(wk[g.key].reviews_p50)}</td><td class="num">${fmtInt(wk[g.key].submitted)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div></details>`;
}

function showTip(target) {
  const c = CHARTS[target.dataset.chart], wk = c.weeks[+target.dataset.i], tip = $("#tip");
  tip.textContent = "";
  const head = document.createElement("div"); head.className = "tip-head"; head.textContent = `${weekName(wk.week)} · ${c.title}`; tip.appendChild(head);
  GROUPS.forEach(g => {
    const line = document.createElement("div");
    const sw = document.createElement("span"); sw.className = `sw ${g.cls}`; line.appendChild(sw);
    const s = wk[g.key], v = s[c.key];
    line.appendChild(document.createTextNode(`${g.label}: ${v == null ? "no data" : c.fmt(Math.round(v * 10) / 10)} (${s.claims} claims, ${s.submitted} submitted)`));
    tip.appendChild(line);
  });
  const r = target.getBoundingClientRect();
  tip.classList.remove("hidden");
  tip.style.left = `${Math.min(window.innerWidth - tip.offsetWidth - 8, r.left + r.width / 2 + 10)}px`;
  tip.style.top = `${r.top + window.scrollY + 8}px`;
}
document.addEventListener("mouseover", e => { const t = e.target.closest?.(".hit"); if (t) showTip(t); });
document.addEventListener("focusin", e => { const t = e.target.closest?.(".hit"); if (t) showTip(t); });
document.addEventListener("mouseout", e => { if (e.target.closest?.(".hit")) $("#tip").classList.add("hidden"); });
document.addEventListener("focusout", e => { if (e.target.closest?.(".hit")) $("#tip").classList.add("hidden"); });

function renderCurve(d) {
  const p = d.paired;
  $("#curve-card").innerHTML = `<div class="cc-head"><h2>2 · Learning curves</h2>
    <p class="muted">External contributors (trusted + non-trusted), by the Nth task they opened.</p></div>
    <p class="cbs-callout">Same person, 1st vs 2nd measured task: <b>${hrs(p.first_p50)} → ${hrs(p.second_p50)}</b>.
      <b>${fmtInt(p.faster)} of ${fmtInt(p.people)}</b> got faster.</p>
    <div class="table-wrap"><table class="cost-table"><thead><tr><th>Task</th><th class="num">Claims worked</th><th class="num">Submitted</th>
      <th class="num">Hours</th><th class="num">$ per task</th><th class="num">Reviews</th><th class="num">/prompt tries</th><th class="num">/rubric tries</th></tr></thead>
      <tbody>${d.curve.map(c => `<tr><th scope="row">${esc(c.task)}</th><td class="num">${fmtInt(c.claims)}</td><td class="num">${fmtInt(c.submitted)}</td>
        <td class="num">${hrs(c.hours_p50)}</td><td class="num">${usd(c.cost_p50)}</td><td class="num">${num(c.reviews_p50)}</td>
        <td class="num">${num(c.prompt_tries_p50)}</td><td class="num">${num(c.rubric_tries_p50)}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderConv(d) {
  const c = d.conversion;
  const step = (label, n, of, note) => `<div class="funnel-row"><div class="funnel-label">${label}</div>
    <div class="funnel-track"><div class="funnel-bar" style="width:${of ? Math.max(1, (100 * n) / c.claimed) : 0}%"></div></div>
    <div class="funnel-num"><b>${fmtInt(n)}</b> ${of ? `· ${pct(n, of)}${note ? ` ${note}` : ""}` : ""}</div></div>`;
  $("#conv-card").innerHTML = `<div class="cc-head"><h2>3 · Trusted conversion</h2>
    <p class="muted">External contributors (not QMA) who worked on a task since Oct 13. Efficient = submitted a task within the
      Acceptable bar (≤${d.bars.acceptable.hours} h and ≤${usd(d.bars.acceptable.cost)}).</p></div>
    <div class="funnel">
      ${step("Claimed", c.claimed, c.claimed)}
      ${step("Submitted a task", c.submitted, c.claimed, "of claimed")}
      ${step("Efficient", c.efficient, c.submitted, `of submitters · ${pct(c.efficient, c.claimed)} overall`)}
    </div>
    <p class="muted">${fmtInt(c.measured)} of the ${fmtInt(c.submitted)} submitters have full portal timing. For comparison,
      <b>${fmtInt(c.qma_efficient)} of ${fmtInt(c.qma)}</b> QMAs meet the same bar.</p>
    <div class="table-wrap"><table class="cost-table"><thead><tr><th>First claim</th><th class="num">Claimed</th><th class="num">Submitted</th><th class="num">Efficient</th></tr></thead>
      <tbody>${c.cohorts.map(w => `<tr><th scope="row">${esc(weekName(w.week))}</th><td class="num">${fmtInt(w.claimed)}</td>
        <td class="num">${fmtInt(w.submitted)} · ${pct(w.submitted, w.claimed)}</td><td class="num">${fmtInt(w.efficient)} · ${pct(w.efficient, w.claimed)}</td></tr>`).join("")}</tbody></table></div>`;
}

const OUTCOME = { submitted: "Submitted", working: "Working", dropped: "No submit" };
function taskRows(p) {
  return `<tr class="cbs-detail"><td colspan="13"><table class="cost-table"><thead><tr><th>#</th><th>Task</th><th>Opened</th><th>Outcome</th>
    <th class="num">Hours</th><th class="num">$</th><th class="num">Reviews</th><th>Tries to pass</th></tr></thead><tbody>
    ${p.tasks.map(t => `<tr><td>${t.n}</td><td class="mono">${esc(t.task)}</td><td>${esc(day(t.first_open))}</td>
      <td>${esc(OUTCOME[t.outcome])}${t.over_flag ? ` <span class="st st-offboard">⚑ over Flag bar</span>` : t.acceptable ? ` <span class="st st-efficient">✓ acceptable</span>` : ""}</td>
      <td class="num">${hrs(t.hours)}</td><td class="num">${usd(t.cost)}</td><td class="num">${fmtInt(t.reviews)}</td>
      <td>${esc(Object.entries(t.tries).map(([k, v]) => `/${k} ${v}`).join(", ") || "–")}</td></tr>`).join("")}
    </tbody></table></td></tr>`;
}

// The contributor columns: click a header to sort highest first, again for lowest first. Unsorted = the server's
// order (needs action first). Text columns sort A to Z first; empty values always go last.
const PEOPLE_COLS = [
  { key: "email", label: "Contributor", text: true }, { key: "group", label: "Group", text: true },
  { key: "status", label: "Status", text: true, value: p => Object.keys(STATUSES).indexOf(p.status) },
  { key: "claims", label: "Claims", num: true, tip: "Claims they worked on (a review or a submit), plus live claims" },
  { key: "untouched", label: "Untouched", num: true, tip: "Closed claims with no review and no submit" },
  { key: "submitted", label: "Submitted", num: true }, { key: "working", label: "Live", num: true },
  { key: "hours_p50", label: "Hours", num: true }, { key: "cost_p50", label: "$ per task", num: true },
  { key: "reviews_p50", label: "Reviews", num: true }, { key: "spend", label: "Portal spend", num: true },
  { key: "last", label: "Last claim" }, { key: "trained", label: "Training", value: p => p.trained?.at ?? null },
];
function sortPeople(list) {
  if (!S.sort) return list;
  const col = PEOPLE_COLS.find(c => c.key === S.sort.key);
  const val = col.value || (p => p[col.key]);
  const sign = S.sort.dir === "asc" ? 1 : -1;
  return list.slice().sort((a, b) => {
    const x = val(a), y = val(b);
    if (x == null || x === "") return y == null || y === "" ? 0 : 1;
    if (y == null || y === "") return -1;
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * sign;
  });
}

function renderPeople(d) {
  const q = S.search.trim().toLowerCase();
  const list = sortPeople(d.people.filter(p => (!S.status || p.status === S.status) && (!S.group || p.group === S.group)
    && (!q || p.email.toLowerCase().includes(q) || p.user.includes(q))));
  const head = PEOPLE_COLS.map(c => {
    const on = S.sort?.key === c.key, arrow = on ? (S.sort.dir === "desc" ? " ↓" : " ↑") : "";
    return `<th class="${c.num ? "num " : ""}sortable" scope="col" aria-sort="${on ? (S.sort.dir === "desc" ? "descending" : "ascending") : "none"}"
      ${c.tip ? `title="${esc(c.tip)}"` : ""}><button type="button" class="th-sort" data-sort="${c.key}">${esc(c.label)}${arrow}</button></th>`;
  }).join("");
  const g = Object.fromEntries(GROUPS.map(x => [x.key, x]));
  $("#people-card").innerHTML = `<div class="cc-head"><h2>4 · Contributors: training and offboarding</h2>
    <p class="muted">Needs action first; click a column name to sort by it (highest first, click again for lowest). Click a row for their tasks. Grace = their first 2 tasks; Needs training after that when the
      last 2 closed claims had no submit or the last 2 submitted tasks were over the Flag bar; Offboard only after training. Offboarded = tagged mcp_ac_temp_block (synced daily), listed last.</p></div>
    <div class="cost-filters">
      <div class="seg" id="status-pick" role="group" aria-label="Status"><button type="button" data-status="" class="${S.status ? "" : "on"}">All</button>${
        Object.entries(STATUSES).map(([k, s]) => `<button type="button" data-status="${k}" class="${S.status === k ? "on" : ""}">${esc(s.label)}</button>`).join("")}</div>
      <div class="seg" id="group-pick" role="group" aria-label="Group"><button type="button" data-group="" class="${S.group ? "" : "on"}">All groups</button>${
        GROUPS.map(x => `<button type="button" data-group="${x.key}" class="${S.group === x.key ? "on" : ""}">${x.label}</button>`).join("")}</div>
      <input type="search" id="search" placeholder="Search email or user ID" value="${esc(S.search)}">
      <span class="muted">${fmtInt(list.length)} of ${fmtInt(d.people.length)}</span>
    </div>
    <div class="table-wrap"><table class="cost-table cbs-people"><thead><tr>${head}</tr></thead>
      <tbody>${list.map(p => {
        const s = STATUSES[p.status];
        const training = p.trained
          ? `<span title="${esc(p.trained.note || "")}">${esc(day(p.trained.at))} · ${esc((p.trained.by || "").split("@")[0])}</span> <button class="ghost small" data-untrain="${p.user}">Undo</button>`
          : `<button class="small" data-train="${p.user}">Mark trained</button>`;
        return `<tr class="cbs-row" data-user="${p.user}" tabindex="0" aria-expanded="${S.open.has(p.user)}">
          <td><div>${esc(p.email || p.user)}</div><div class="muted mono small">${esc(p.user)}</div></td>
          <td><span class="sw ${g[p.group].cls}"></span>${g[p.group].label}</td>
          <td><span class="st ${s.cls}" title="${esc(s.tip)}">${s.icon} ${esc(s.label)}</span>${p.reason ? `<div class="muted small">${esc(p.reason)}</div>` : ""}</td>
          <td class="num">${fmtInt(p.claims)}</td><td class="num">${fmtInt(p.untouched)}</td><td class="num">${fmtInt(p.submitted)}</td><td class="num">${fmtInt(p.working)}</td>
          <td class="num">${hrs(p.hours_p50)}</td><td class="num">${usd(p.cost_p50)}</td><td class="num">${num(p.reviews_p50)}</td>
          <td class="num">${usd(p.spend)}</td><td>${esc(day(p.last))}</td><td class="nowrap">${training}</td></tr>${S.open.has(p.user) ? taskRows(p) : ""}`;
      }).join("") || `<tr><td colspan="13" class="muted">Nobody matches.</td></tr>`}</tbody></table></div>`;
}

async function markTrained(uid) {
  const p = S.data.people.find(x => x.user === uid);
  let note = "";
  const asked = uiConfirm(`Mark ${p.email || uid} as trained`, `<p style="margin:0 0 10px">From now on, their next 2 closed claims decide:
    if both go badly (no submit, or over the Flag bar) they show as <b>Offboard</b>.</p>
    <label class="muted small" for="train-note">Note (optional): what was covered, by whom</label>
    <textarea id="train-note" rows="3" style="width:100%"></textarea>`, "Mark trained");
  $("#train-note").addEventListener("input", e => { note = e.target.value; });
  if (!(await asked)) return;
  try { await api("/api/admin/cbs/trained", { json: { user_id: uid, email: p.email, note } }); await load(); }
  catch (e) { uiAlert("Couldn't save", e.message); }
}

async function unmarkTrained(uid) {
  const p = S.data.people.find(x => x.user === uid);
  if (!(await uiConfirm("Undo training", `<p style="margin:0">Remove the training record for ${esc(p.email || uid)}?</p>`, "Undo", { danger: true }))) return;
  try { await api("/api/admin/cbs/trained", { json: { user_id: uid, undo: true } }); await load(); }
  catch (e) { uiAlert("Couldn't save", e.message); }
}

document.addEventListener("click", e => {
  const sortBtn = e.target.closest("[data-sort]");
  if (sortBtn) {  // highest first, then lowest first (text: A to Z, then Z to A), then back to the default order
    const key = sortBtn.dataset.sort, text = PEOPLE_COLS.find(c => c.key === key).text;
    const [first, second] = text ? ["asc", "desc"] : ["desc", "asc"];
    S.sort = S.sort?.key !== key ? { key, dir: first } : S.sort.dir === first ? { key, dir: second } : null;
    renderPeople(S.data);
    return;
  }
  const train = e.target.closest("[data-train]"), untrain = e.target.closest("[data-untrain]");
  if (train) return markTrained(train.dataset.train);
  if (untrain) return unmarkTrained(untrain.dataset.untrain);
  const st = e.target.closest("[data-status]");
  if (st) { S.status = S.status === st.dataset.status && st.classList.contains("cbs-tile") ? "" : st.dataset.status; render(); return; }
  const gr = e.target.closest("[data-group]");
  if (gr) { S.group = gr.dataset.group; renderPeople(S.data); return; }
  const row = e.target.closest(".cbs-row");
  if (row && !e.target.closest("button, a")) { const u = row.dataset.user; S.open.has(u) ? S.open.delete(u) : S.open.add(u); renderPeople(S.data); }
});
document.addEventListener("keydown", e => {
  const row = e.target.closest?.(".cbs-row");
  if (row && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); row.click(); }
});
document.addEventListener("input", e => {
  if (e.target.id !== "search") return;
  S.search = e.target.value;
  renderPeople(S.data);
  const box = $("#search"); box.focus(); box.setSelectionRange(box.value.length, box.value.length);
});
$("#refresh").onclick = async () => { $("#refresh").disabled = true; await load(true); $("#refresh").disabled = false; };
$("#logout").onclick = async () => { await api("/api/admin/logout", { method: "POST" }); location.href = "/admin"; };
$("#view-switch").addEventListener("click", async e => {
  const view = e.target.closest("[data-view]")?.dataset.view;
  if (view === "admin") location.href = "/admin";
  if (view === "cost") location.href = "/admin/cost";
  if (view === "chat") {
    try { await api("/api/admin/open-chat", { json: {} }); location.href = "/"; }
    catch (err) { uiAlert("Couldn't open the chat view", err.message); }
  }
});
setInterval(() => { if (!document.hidden && S.data) load(); }, 300000);
load();
