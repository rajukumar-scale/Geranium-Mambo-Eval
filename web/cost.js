// Admin Cost page: what Atlas Commercial spends on models (every key) per day, per submitted task and per contributor.
// Charts follow the dataviz rules: columns <= 24px with a 4px rounded top and a 2px gap between stacked parts,
// hairline grid, one axis, a legend for two series (none for one), values only on the peak and today,
// a tooltip on hover and keyboard focus (textContent only), and a table view. Colours are validated per mode.

// The range is the last 7 / 14 / 30 days or a custom start..end (UTC days, from Aug 1), from the link or the last visit.
const URLQ = new URLSearchParams(location.search);
const C = { days: +(URLQ.get("days") || localStorage.getItem("evp_cost_days") || 14), view: localStorage.getItem("evp_cost_view") || "both", data: null, sort: {},
            start: URLQ.get("start") || "", end: URLQ.get("end") || "",
            period: localStorage.getItem("evp_cost_period") || "day", chart: localStorage.getItem("evp_cost_chart") || "bar" };
if (!URLQ.get("days") && !C.start) [C.start, C.end] = (localStorage.getItem("evp_cost_custom") || "").split("..").concat("").slice(0, 2);
if (!(C.start && C.end)) C.start = C.end = "";
const FIRST_DAY = "2026-08-01";  // the cost data starts here
// Charts show the range by UTC day, week (Monday start) or month; a week or month cut by the range is marked.
const PERIODS = { day: "day", week: "week", month: "month" };
// What the page counts: evals (portal reviews + the old in-task sandbox, where the evals ran before the portal), the platform's
// task runs (Agent Runner / pre-check / verifier runs + the hint pipeline), or both. Every tile, chart and table follows it.
const VIEWS = { evals: { label: "Evals", parts: ["sandbox", "portal"] }, runs: { label: "Task runs", parts: ["runs", "hint"] },
                both: { label: "Evals + task runs", parts: ["runs", "sandbox", "hint", "portal"] } };
const SERIES = [{ key: "runs", label: "Task runs", cls: "s-runs" }, { key: "hint", label: "Hint pipeline", cls: "s-hint" },
                { key: "sandbox", label: "In-task sandbox (evals)", cls: "s-sandbox" }, { key: "portal", label: "Portal reviews (evals)", cls: "s-untagged" }];
const inView = k => VIEWS[C.view].parts.includes(k);
const median = v => { const s = v.slice().sort((a, b) => a - b), m = s.length >> 1; return s.length ? (s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2) : null; };
// The report re-totalled for the chosen view.
function viewOf(c) {
  const rowTotal = r => (inView("runs") ? r.agent_runs || 0 : 0) + (inView("sandbox") ? r.sandbox || 0 : 0) + (inView("portal") ? r.portal || 0 : 0);
  const daily = c.daily.map(d => ({ ...d, total: SERIES.reduce((t, x) => t + (inView(x.key) ? d[x.key] || 0 : 0), 0) }));
  const split = (r, prefix) => lostOf({ agent_runs: r[`${prefix}_agent_runs`], evals: r[`${prefix}_evals`] });
  const tasks = c.tasks.map(t => ({ ...t, total: rowTotal(t), lost_before: split(t, "lost_before") }));
  const people = c.people.map(p => ({ ...p, total: rowTotal(p), lost: split(p, "lost"), per_task: p.tasks_submitted ? rowTotal(p) / p.tasks_submitted : null }));
  // the five parts of the total; when they're there, the total is their sum, so the page adds up to the cent
  const breakdown = c.breakdown && { ...c.breakdown, parts: PARTS.map(p => ({ ...p, amount: lostOf(c.breakdown[p.key]), detail: c.breakdown[p.key] })) };
  const totals = tasks.map(t => t.total), cbDays = daily.reduce((n, d) => n + d.active_cbs, 0);
  const total = breakdown ? breakdown.parts.reduce((n, p) => n + p.amount, 0) : daily.reduce((n, d) => n + d.total, 0);
  const lost = c.lost && { ...c.lost, total: lostOf(c.lost), claims: c.lost.claims.map(t => ({ ...t, total: rowTotal(t) })).filter(t => t.total > 0),
                           reasons: c.lost.reasons.map(r => ({ ...r, total: lostOf(r) })).filter(r => r.total > 0),
                           in_progress: { ...c.lost.in_progress, total: lostOf(c.lost.in_progress) } };
  const pendingTasks = c.pending_multi && c.pending_multi.tasks.map(t => ({ ...t, total: rowTotal(t), lost: split(t, "lost"), holder_spend: split(t, "holder") }))
    .filter(t => t.total > 0);
  const pending_multi = c.pending_multi && { ...c.pending_multi, tasks: pendingTasks, total: pendingTasks.reduce((n, t) => n + t.total, 0) };
  return { ...c, daily, tasks, people, lost, pending_multi, breakdown, summary: { ...c.summary, total, per_task_median: median(totals),
    per_task_mean: totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : null, per_cb_day: cbDays ? total / cbDays : null,
    per_task_all_in: c.summary.tasks_submitted ? total / c.summary.tasks_submitted : null } };
}
// A {agent_runs, evals} pair in the chosen view: task runs, evals (in-task sandbox + portal), or both.
const lostOf = x => (C.view !== "evals" ? x.agent_runs || 0 : 0) + (C.view !== "runs" ? x.evals || 0 : 0);
// Where the range's money went: five parts that add up to the total (server/cost.py, breakdown).
const PARTS = [
  { key: "submitted", label: "Spent by people who submitted", what: () => "each person's own spend on tasks they submitted" },
  { key: "lost", label: "Lost", what: () => "spent by people whose claim ended without them submitting (released, expired, cancelled)" },
  { key: "in_progress", label: "In progress", what: () => "spent by people who still hold the task" },
  { key: "internal", label: "Internal tests", what: () => "@scale.com and internal accounts, admin runs in the portal" },
  { key: "untied", label: "Can't be tied to a task", what: d => [
      inView("runs") && `task runs never in a submission${d.hint ? `, ${usd(d.hint)} hint pipeline` : ""}`,
      inView("portal") && `sandbox calls with no task${d.portal_no_task ? `, ${usd(d.portal_no_task)} portal runs with no task` : ""}`].filter(Boolean).join("; ") },
];
// Rounds each value to a whole number so they still add up to the rounded total (largest remainder).
function roundedParts(values, total) {
  const floors = values.map(Math.floor), want = Math.round(total);
  let left = want - floors.reduce((a, b) => a + b, 0);
  const order = values.map((v, i) => [v - floors[i], i]).sort((a, b) => b[0] - a[0]);
  for (const [, i] of order) { if (left <= 0) break; floors[i] += 1; left -= 1; }
  return floors;
}
const LOST_SERIES = () => [{ key: "lost_runs", label: "Task runs", cls: "s-runs" }, { key: "lost_evals", label: "Evals (sandbox + portal reviews)", cls: "s-untagged" }]
  .filter(x => x.key === "lost_runs" ? C.view !== "evals" : C.view !== "runs");
const usd = (v, d = 0) => "$" + Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const kUsd = v => (v >= 1000 ? `$${+(v / 1000).toFixed(1)}K` : `$${Math.round(v)}`);
const utcDay = (t, opts) => new Date(t * 1000).toLocaleDateString("en-US", { timeZone: "UTC", ...opts });
const longDay = t => utcDay(t, { weekday: "short", month: "short", day: "numeric" });
const when = t => new Date(t * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const shortDay = t => utcDay(t, { month: "short", day: "numeric" });
const rangeOf = c => (c.start === c.end ? longDay(c.start) : `${shortDay(c.start)} – ${shortDay(c.end)}`);
const rangeName = c => (c.days ? `last ${c.days} days` : rangeOf(c));
// A chart column's full name: the day, the week (from its Monday) or the month, with where the range cuts it.
const cutNote = (d, c) => d.partial_start ? `from ${shortDay(c.start)}` : d.partial_end && !d.today ? `to ${shortDay(c.end)}` : "";
const periodName = (d, period) => {
  const cut = period === "day" ? "" : cutNote(d, C.shown);
  return (period === "week" ? `Week of ${shortDay(d.day)}` : period === "month" ? utcDay(d.day, { month: "long", year: "numeric" }) : longDay(d.day))
    + (cut ? ` (${cut})` : "");
};
const periodRow = (d, period) => esc(periodName(d, period)) + (d.today ? " (so far)" : "");

// The chart rows for the chosen period: the range's days, weeks or months, re-totalled for the view.
function chartRows(c) {
  if (C.period === "day") return c.daily;
  return (C.period === "week" ? c.weeks : c.months).map(d => ({ ...d, total: SERIES.reduce((t, x) => t + (inView(x.key) ? d[x.key] || 0 : 0), 0) }));
}
const chartSince = () => C.period === "day" ? "" : `${rangeOf(C.shown)}, by UTC ${C.period === "week" ? "week (Monday start)" : "month"} · `;

const rangeQuery = () => (C.start ? `start=${C.start}&end=${C.end}` : `days=${C.days}`);
async function load(refresh = false, quiet = false) {
  document.querySelectorAll("#range [data-days]").forEach(b => b.classList.toggle("on", !C.start && +b.dataset.days === C.days));
  $("#custom").classList.toggle("on", !!C.start);
  history.replaceState(null, "", `${location.pathname}?${rangeQuery()}`);
  clearTimeout(C.poll);
  if (!quiet) { $("#app").classList.add("refetching"); $("#asof").textContent = "Loading…"; }  // keep the last render, dimmed
  try {
    const data = await api(`/api/admin/cost?${rangeQuery()}${refresh ? "&refresh=1" : ""}`);
    // The server keeps every figure since Aug 1 and refreshes them in the background, so this answers at once. Only
    // the very first lookup (about a minute) makes the page wait: ask again until it's there, and after a refresh.
    const c = data.cost || {};
    if (c.loading || c.refreshing) C.poll = setTimeout(() => load(false, true), c.loading ? 5000 : 15000);
    if (c.loading) {
      $("#asof").textContent = "Fetching the cost data for the first time (about a minute)…";
      return;
    }
    C.data = data;
  } catch (err) {
    if (err.status === 401 || err.status === 403) { location.href = "/admin"; return; }
    $("#asof").textContent = "";
    $("#notes").innerHTML = `<div class="error">Couldn't load the cost data: ${esc(err.message)}</div>`;
    return;
  } finally {
    $("#app").classList.remove("refetching");
  }
  render();
}

function render() {
  const { budget: b = {} } = C.data;
  let c = C.data.cost || {};
  $("#asof").textContent = c.fetched_at ? `Data as of ${when(c.fetched_at)} (${fmtAgo(c.fetched_at)}) · `
    + (c.refreshing ? "updating in the background" : "refreshes every 15 min") : "";
  const notes = [];
  if (c.error) notes.push(c.error);
  if (c.stale) notes.push(`Couldn't refresh just now (${c.stale}); showing the last figures.`);
  const cv = c.coverage || {}, runsShown = C.view !== "evals";  // the task-run logging gap doesn't affect eval figures
  if (runsShown && cv.runs && cv.pct != null && cv.pct < 80) notes.push(`Task-run figures may be too low: of the ${fmtInt(cv.runs)} task runs in the range's last ${cv.days} days' submissions, only ${fmtInt(cv.logged)} (${cv.pct}%) have any spend in LiteLLM's log or SMIG's usage records. The runs happened (their IDs are in the submissions), so the rest of their spend is missing from both.`);
  if (runsShown && b.log_gap) notes.push(`Neither LiteLLM nor SMIG has recorded this portal's key since ${when(b.log_gap.since)}, although the portal has made ${fmtInt(b.log_gap.portal_calls)} calls since (about ${usd(b.log_gap.portal_usd)} by its own records). Portal reviews on this page come from the portal's own records, so they're unaffected; the key's budget figure may be under-counting.`);
  // Portal runs before the proxy tagged its calls (2026-10-26 20:00 UTC) are priced at list, not at SMIG's billed cost.
  const est = (c.summary || {}).portal_estimated;
  const info = est > 0 && C.view !== "runs" ? `Portal reviews before Oct 26, 20:00 UTC (${usd(est)} in this range) are the portal's list-price estimate, about 40% above what's billed; later reviews are SMIG's billed cost.` : "";
  $("#notes").innerHTML = notes.map(n => `<div class="error">${esc(n)}</div>`).join("") + (info ? `<p class="muted">${esc(info)}</p>` : "");
  if (c.error) return;
  document.querySelectorAll("#view [data-view-pick]").forEach(x => x.classList.toggle("on", x.dataset.viewPick === C.view));
  document.querySelectorAll("#period [data-period]").forEach(x => x.classList.toggle("on", x.dataset.period === C.period));
  document.querySelectorAll("#chart-type [data-chart-type]").forEach(x => x.classList.toggle("on", x.dataset.chartType === C.chart));
  C.shown = viewOf(c);
  c = C.shown;
  renderTiles(c, b);
  $("#split-card").innerHTML = c.breakdown ? splitCard(c) : "";
  $("#split-card").classList.toggle("hidden", !c.breakdown);
  $("#allin-card").innerHTML = c.breakdown ? allInCard(c) : "";
  $("#allin-card").classList.toggle("hidden", !c.breakdown);
  $("#daily-card").innerHTML = dailyCard(c, b);
  $("#pertask-card").innerHTML = perTaskCard(c);
  $("#percb-card").innerHTML = perCbCard(c);
  $("#lost-card").innerHTML = c.lost ? lostCard(c) : "";
  $("#lost-card").classList.toggle("hidden", !c.lost);
  $("#losttasks-card").classList.toggle("hidden", !c.lost);
  $("#losttaskrows-card").classList.toggle("hidden", !c.lost);
  if (c.lost) { renderTable("losttasks"); renderTable("losttaskrows"); }
  $("#pending-card").classList.toggle("hidden", !c.pending_multi);
  if (c.pending_multi) renderTable("pending");
  renderTable("tasks");
  renderTable("people");
  wireCharts();
  const q = c.queries || {};
  $("#foot").innerHTML = `<b>How it's counted.</b> UTC days from Aug 1, 2026; weekly and monthly charts add up the range's days, with the current week or month so far. <b>Task runs</b> are the platform's Agent Runner, pre-check and verifier runs on
    the HG4 env, from LiteLLM's request log and SMIG's usage records (most calls go through SMIG since Oct 16) on every key tagged with the Atlas Commercial project (mostly <span class="mono">pub-mcp-commercial</span>);
    a run counts for a task and contributor when its run ID is in one of their submitted attempts, or when it was launched from the task itself
    (run named after the task's pipeline node); earlier sandbox-viewer runs can't be tied to a task and only show in the daily totals. <b>In-task sandbox</b> is the old in-task chat
    (<span class="mono">${esc(b.key_alias || "")}</span>), <b>hint pipeline</b> the autohint runs. Other teams' work that carries the project tag
    (CoopBench, checkpoint evals) is left out. <b>Portal reviews</b> come from this portal's own records: SMIG's billed cost per run from Oct 26, 20:00 UTC, the list-price estimate before that. A <b>submitted task</b> is a contributor's
    submission at L-1 or L0 (the bot and @scale.com accounts excluded); its cost is its task runs from when it started to the range's end, plus the portal reviews run on it.
    Contributors count only what falls inside the range. <b>Lost spend</b> is counted per person and task: when someone's claim ends (released, expired, taken away, task cancelled)
    without them submitting that task, what they spent on it is lost at once, even if someone else submits it later; if they claim it again and submit, it isn't.
    Someone who holds a live or paused claim now is <b>in progress</b>. Claims come from the CB tracker (L-1 claims since Oct 1); for older work, the task's
    platform state stands in (live claim or paused, and they're its last worker). A submitted task's cost is only what the people who submitted it spent;
    what earlier holders spent shows as "lost before". <b>Internal tests</b> are @scale.com and internal accounts and admin runs in the portal. Task runs that never
    reached a submission, the hint pipeline, untagged sandbox calls and portal runs with no task <b>can't be tied to a task</b>.
    ${q.states ? `Task states: <a href="${esc(q.states)}" target="_blank" rel="noopener">platform state per task</a>. ` : ""}Source: <a href="${esc(q.facts || "#")}" target="_blank" rel="noopener">every figure since Aug 1</a>,
    refreshed in the background every 15 minutes.`;
}

function tile(label, value, sub) {
  return `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}</div><div class="tile-sub">${sub}</div></div>`;
}
// Per submitted task. Test runs can't be tied to a task, so task runs use all run spend ÷ tasks submitted (an average,
// test runs included); the median of the runs tied to each task is the smaller, final-batch-only figure.
function perTaskTile(c) {
  const s = c.summary, n = s.tasks_submitted || 0;
  if (C.view === "evals" || !n) return tile("Per submitted task", s.per_task_median == null ? "–" : esc(usd(s.per_task_median)),
    s.per_task_median == null ? "no submissions yet" : `median · average ${esc(usd(s.per_task_mean))} · ${esc(VIEWS[C.view].label.toLowerCase())}`);
  const runsAvg = (s.runs || 0) / n, tied = median(c.tasks.map(t => t.agent_runs || 0));
  const tiedNote = `${esc(usd(tied))} median of the runs tied to each task (final batch)`;
  if (C.view === "runs") return tile("Task runs per submitted task", esc(usd(runsAvg)),
    `average: all run spend ÷ ${fmtInt(n)} tasks, test runs included · ${tiedNote}`);
  const evalsAvg = c.tasks.reduce((t, x) => t + (x.sandbox || 0) + (x.portal || 0), 0) / n;
  return tile("Per submitted task", esc(usd(runsAvg + evalsAvg)),
    `average: ${esc(usd(runsAvg))} task runs (test runs included) + ${esc(usd(evalsAvg))} evals · ${tiedNote}`);
}

function renderTiles(c, b) {
  const s = c.summary;
  $("#tiles").innerHTML = [
    tile(`${VIEWS[C.view].label}, ${rangeName(c)}`, esc(usd(s.total)),
      SERIES.filter(x => inView(x.key) && s[x.key]).map(x => `${esc(usd(s[x.key]))} ${esc(x.label.toLowerCase())}`).join(" · ") || "nothing"),
    inView("runs") && tile("Task runs tied to a submission", s.runs ? `${Math.round(100 * s.runs_linked / s.runs)}%` : "–",
      `${esc(usd(s.runs_linked))} of ${esc(usd(s.runs))} · the rest are re-runs or unsubmitted work`),
    c.lost ? tile(`Lost spend, ${rangeName(c)}`, esc(usd(c.lost.total)),
      `${fmtInt(c.lost.claims.length)} ${c.lost.claims.length === 1 ? "claim" : "claims"} ended without that person submitting`
      + (c.summary.total ? ` · ${Math.round(100 * c.lost.total / c.summary.total)}% of ${esc(VIEWS[C.view].label.toLowerCase())}` : "")
      + (c.lost.in_progress.total ? ` · ${esc(usd(c.lost.in_progress.total))} in progress (still held)` : "")) : null,
    tile("Cost per submitted task, all-in", s.per_task_all_in == null ? "–" : esc(usd(s.per_task_all_in)),
      s.tasks_submitted ? `${esc(usd(s.total))} ÷ ${fmtInt(s.tasks_submitted)} tasks submitted · lost, tests and untied spend included` : "no submissions yet"),
    perTaskTile(c),
    tile("Per active contributor, per day", s.per_cb_day == null ? "–" : esc(usd(s.per_cb_day)), `${esc(VIEWS[C.view].label.toLowerCase())} ÷ contributor-days`),
    tile("Tasks submitted", esc(fmtInt(s.tasks_submitted)), `by ${fmtInt(s.submitters)} contributors · L-1/L0`),
    tile("Key budget this cycle", b.pct == null ? "–" : `${esc(b.pct)}%`,
      b.max_budget ? `${esc(usd(b.spend))} of ${esc(usd(b.max_budget))}${b.resets_at ? ` · resets ${esc(when(b.resets_at))}` : ""}` : esc(b.error || "")),
  ].filter(Boolean).join("");
}

// ---- column charts ----
// series: [{key, label, cls}] stacked bottom-up; value(d, key) gives each part. In line mode (the Bars / Lines toggle)
// each series is its own 2px line with 8px markers instead, on the same single axis. period names the columns.
function columns({ id, title, sub, days, series, value, pace, paceLabel, cycleStart, tip, fmt = kUsd, period = "day", bars = false }) {
  // bars: always columns, whatever the Bars / Lines toggle says (the all-in chart stacks its parts, Evan 2026-11-04)
  const W = 1100, H = 250, L = 56, R = 12, T = 26, B = 42, pw = W - L - R, ph = H - T - B, GAP = 2, line = !bars && C.chart === "line";
  const totals = days.map(d => series.reduce((t, s) => t + (value(d, s.key) || 0), 0));
  const highs = line ? days.map(d => Math.max(0, ...series.map(s => value(d, s.key) || 0))) : totals;
  const top = Math.max(pace || 0, ...highs, 1) * 1.12;
  const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 50000, 100000];
  const step = steps.find(s => top / s <= 5) || 200000;
  const yMax = Math.ceil(top / step) * step, y = v => T + ph - (v / yMax) * ph;
  const slot = pw / days.length, bw = Math.min(24, slot * 0.6);
  const peak = highs.reduce((m, v, i) => (v > highs[m] ? i : m), 0), cx = i => L + (i + 0.5) * slot;
  let svg = "";
  for (let v = 0; v <= yMax + 1e-9; v += step) {
    svg += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="tick" x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(v))}</text>`;
  }
  const resetAt = cycleStart ? days.findIndex(d => d.day === cycleStart) : -1;
  if (resetAt > 0) {
    const x = L + resetAt * slot;
    svg += `<line class="ref" x1="${x}" x2="${x}" y1="${T - 14}" y2="${T + ph}"/><text class="note" x="${x + 4}" y="${T - 6}">cycle reset</text>`;
  }
  const every = days.length > 16 ? 2 : 1;
  if (line) series.forEach(s => {
    svg += `<polyline class="line ${s.cls}" points="${days.map((d, i) => `${cx(i).toFixed(1)},${y(value(d, s.key) || 0).toFixed(1)}`).join(" ")}"/>`;
  });
  days.forEach((d, i) => {
    const x = cx(i) - bw / 2, base = T + ph;
    let cum = 0, parts = "";
    const drawn = line ? [] : series.map(s => ({ s, v: value(d, s.key) || 0 })).filter(p => p.v > 0);
    if (line) parts = `<line class="guide" x1="${cx(i)}" x2="${cx(i)}" y1="${T}" y2="${base}"/>`
      + series.map(s => `<circle class="dot ${s.cls}" cx="${cx(i)}" cy="${y(value(d, s.key) || 0)}" r="4"/>`).join("");
    drawn.forEach((p, j) => {
      let y1 = y(cum + p.v), y0 = y(cum);
      if (j > 0) y0 -= GAP;  // the surface gap between stacked parts
      cum += p.v;
      const h = y0 - y1;
      if (h <= 0.5) return;
      const r = j === drawn.length - 1 ? Math.min(4, h) : 0;  // only the top part gets the rounded end
      parts += r ? `<path class="bar ${p.s.cls}" d="M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + bw - r} Q${x + bw},${y1} ${x + bw},${y1 + r} V${y0} Z"/>`
        : `<rect class="bar ${p.s.cls}" x="${x}" y="${y1}" width="${bw}" height="${h}"/>`;
    });
    // values only on the peak and the current period; with several lines the legend and tooltip carry them
    const shown = line && series.length > 1 ? 0 : highs[i];
    const label = (i === peak || d.today) && shown > 0 ? `<text class="val" x="${cx(i)}" y="${y(shown) - (line ? 10 : 6)}" text-anchor="middle">${esc(fmt(shown))}</text>` : "";
    const first = i === 0 || utcDay(d.day, { day: "numeric" }) === "1";
    const name = period === "week" ? shortDay(d.day) : period === "month" ? utcDay(d.day, { month: "short" })
      : i % every && !first && !d.today ? "" : first ? shortDay(d.day) : utcDay(d.day, { day: "numeric" });
    const note = d.today ? (period === "day" ? "today" : "so far") : period === "day" ? "" : cutNote(d, C.shown);
    svg += `<g class="sbar" data-chart="${id}" data-i="${i}" tabindex="0" role="img" aria-label="${esc(periodName(d, period))}: ${esc(fmt(totals[i]))}">
      <rect class="hit" x="${L + i * slot}" y="${T}" width="${slot}" height="${ph}"/>${parts}${label}
      <text class="tick" x="${cx(i)}" y="${base + 16}" text-anchor="middle">${esc(name)}</text>${note ? `<text class="note" x="${cx(i)}" y="${base + 30}" text-anchor="middle">${esc(note)}</text>` : ""}</g>`;
  });
  if (pace) svg += `<line class="pace" x1="${L}" x2="${W - R}" y1="${y(pace)}" y2="${y(pace)}"/>`;
  svg += `<line class="axis" x1="${L}" x2="${W - R}" y1="${T + ph}" y2="${T + ph}"/>`;
  const legend = (series.length > 1 ? series.map(s => `<span class="lg"><span class="sw ${line ? "swl " : ""}${s.cls}"></span>${esc(s.label)}</span>`).join("") : "")
    + (pace ? `<span class="lg"><span class="ln"></span>${esc(paceLabel)}</span>` : "");
  CHARTS[id] = { days, tip, period };
  return `<div class="cc-head"><div><div class="cc-title">${esc(title)}</div><div class="cc-sub">${sub}</div></div><div class="legend">${legend}</div></div>
    <div class="sc-plot"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${svg}</svg><div class="sc-tip hidden" role="tooltip"></div></div>`;
}
const CHARTS = {};
function wireCharts() {
  document.querySelectorAll(".sc-plot").forEach(plot => {
    const tip = plot.querySelector(".sc-tip");
    const show = g => {
      const chart = CHARTS[g.dataset.chart], d = chart.days[+g.dataset.i];
      tip.replaceChildren();
      const head = document.createElement("div");
      head.className = "muted";
      head.textContent = periodName(d, chart.period) + (d.today ? (chart.period === "day" ? " · today so far" : " · so far") : "") + " (UTC)";
      tip.append(head);
      chart.tip(d).forEach(([strong, rest, cls]) => {
        const row = document.createElement("div"), b = document.createElement("b");
        if (cls) { const k = document.createElement("span"); k.className = `tk ${cls}`; row.append(k); }
        b.textContent = strong;
        row.append(b, document.createTextNode(rest));
        tip.append(row);
      });
      tip.classList.remove("hidden");
      const box = plot.getBoundingClientRect(), hit = g.querySelector(".hit").getBoundingClientRect();
      const topMark = [...g.querySelectorAll(".bar, .dot")].reduce((m, e) => Math.min(m, e.getBoundingClientRect().top), hit.bottom - 20);
      tip.style.left = Math.min(Math.max(hit.left - box.left + hit.width / 2 - tip.offsetWidth / 2, 0), box.width - tip.offsetWidth) + "px";
      tip.style.top = Math.max(0, topMark - box.top - tip.offsetHeight - 8) + "px";
    };
    plot.querySelectorAll(".sbar").forEach(g => {
      g.addEventListener("pointerenter", () => show(g));
      g.addEventListener("focus", () => show(g));
      g.addEventListener("pointerleave", () => tip.classList.add("hidden"));
      g.addEventListener("blur", () => tip.classList.add("hidden"));
    });
  });
}

function dailyCard(c, b) {
  const title = `Spend per ${PERIODS[C.period]}`, days = chartRows(c), period = C.period;
  const chart = columns({
    id: "daily", title, sub: `${chartSince() || "UTC days · "}task runs from LiteLLM + SMIG (all keys), portal reviews from the portal's own records`, days, period,
    series: SERIES.filter(x => inView(x.key)),
    value: (d, k) => d[k],
    tip: d => [[usd(d.total), ` ${VIEWS[C.view].label.toLowerCase()}`],
      ...(inView("runs") ? [[usd(d.runs), d.runs_linked == null ? " task runs" : ` task runs (${usd(d.runs_linked)} tied to a submission)`, "s-runs"]] : []),
      ...(inView("hint") && d.hint ? [[usd(d.hint), " hint pipeline", "s-hint"]] : []),
      ...(inView("sandbox") && d.sandbox ? [[usd(d.sandbox), " in-task sandbox", "s-sandbox"]] : []),
      ...(inView("portal") ? [[usd(d.portal), " portal reviews", "s-untagged"]] : []),
      [fmtInt(d.active_cbs), ` active contributors · ${fmtInt(d.tasks_submitted)} tasks submitted`],
      ...(inView("runs") && d.sub_runs ? [[`${fmtInt(d.sub_runs_logged)} of ${fmtInt(d.sub_runs)}`, " runs in that day's submissions have logged spend"]] : [])],
  });
  if (period !== "day") {
    const rows = days.slice().reverse().map(d => `<tr><td>${periodRow(d, period)}</td><td>${esc(usd(d.total))}</td><td>${esc(usd(d.runs))}</td>
      <td>${esc(usd(d.sandbox))}</td><td>${esc(usd(d.hint))}</td><td>${esc(usd(d.portal))}</td><td>${fmtInt(d.active_cbs)}</td><td>${fmtInt(d.tasks_submitted)}</td></tr>`).join("");
    return chart + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>${period === "week" ? "Week" : "Month"} (UTC)</th><th>Total</th>
      <th>Task runs</th><th>In-task sandbox</th><th>Hint pipeline</th><th>Portal reviews</th><th>Active contributors</th><th>Tasks submitted</th></tr>${rows}</table></details>`;
  }
  const rows = c.daily.slice().reverse().map(d => `<tr><td>${esc(longDay(d.day))}${d.today ? " (so far)" : ""}</td><td>${esc(usd(d.total))}</td>
    <td>${esc(usd(d.runs))}</td><td>${esc(usd(d.runs_linked))}</td><td>${esc(usd(d.sandbox))}</td><td>${esc(usd(d.hint))}</td><td>${esc(usd(d.portal))}</td>
    <td>${fmtInt(d.active_cbs)}</td><td>${fmtInt(d.tasks_submitted)}</td><td>${d.sub_runs ? `${fmtInt(d.sub_runs_logged)} / ${fmtInt(d.sub_runs)}` : "–"}</td></tr>`).join("");
  return chart + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>Day (UTC)</th><th>Total</th>
    <th>Task runs</th><th>…tied to a submission</th><th>In-task sandbox</th><th>Hint pipeline</th><th>Portal reviews</th><th>Active contributors</th>
    <th>Tasks submitted</th><th>Runs logged / in submissions</th></tr>${rows}</table></details>`;
}

// Where the range's money went: five parts, in whole dollars that add up to the total shown, with each one's share.
function splitCard(c) {
  const B = c.breakdown, total = c.summary.total;
  const dollars = roundedParts(B.parts.map(p => Math.max(p.amount, 0)), total);
  const pct = roundedParts(B.parts.map(p => total > 0 ? 100 * Math.max(p.amount, 0) / total : 0), total > 0 ? 100 : 0);
  const rows = B.parts.map((p, i) => `<tr><td class="split-label">${esc(p.label)}</td><td class="muted">${esc(p.what(p.detail))}</td>
    <td class="num">${esc(usd(dollars[i]))}</td><td class="num">${total > 0 ? `${pct[i]}%` : "–"}</td></tr>`).join("");
  return `<div class="cc-head"><div><div class="cc-title">Where the money went</div>
    <div class="cc-sub">${esc(VIEWS[C.view].label)}, ${esc(rangeName(c))}. Each person's spend on a task is counted once, by where their claim stands now.
    ${B.partial ? " Claims data is still loading, so who holds a task comes from the task state for now." : ""}</div></div></div>
    <div class="table-wrap"><table class="runs cost-table split-table"><tbody>${rows}
    <tr class="split-total"><td class="split-label">Total</td><td></td><td class="num">${esc(usd(Math.round(total)))}</td><td class="num">${total > 0 ? "100%" : "–"}</td></tr></tbody></table></div>`;
}

// All-in cost per submitted task, per day / week / month (Evan, 2026-11-04): everything spent that period ÷ tasks
// submitted that period, in four parts: what went into submitted tasks (task runs, evals) and the overhead on each
// side (lost, internal tests, and what can't be tied to a task: test runs, the hint pipeline, untagged calls).
// Spend by people still holding a task (in progress) is left out: it may still become a task. Periods with fewer than
// MIN_TASKS submissions are left out, like the other per-task charts.
const ALLIN_SERIES = [
  { key: "direct_runs", label: "Task runs, submitted tasks", cls: "s-runs", side: "runs", parts: ["submitted"] },
  { key: "direct_evals", label: "Evals, submitted tasks", cls: "s-untagged", side: "evals", parts: ["submitted"] },
  { key: "over_runs", label: "Task-run overhead", cls: "s-hint", side: "runs", parts: ["lost", "internal", "untied"] },
  { key: "over_evals", label: "Eval overhead", cls: "s-lost", side: "evals", parts: ["lost", "internal", "untied"] }];
const allInSeries = () => ALLIN_SERIES.filter(x => x.side === "runs" ? C.view !== "evals" : C.view !== "runs");
const allInOf = (d, x) => x.parts.reduce((t, k) => t + (d[`p_${k}_${x.side}`] || 0), 0);
const allInWip = d => (C.view !== "evals" ? d.p_in_progress_runs || 0 : 0) + (C.view !== "runs" ? d.p_in_progress_evals || 0 : 0);
function allInCard(c) {
  const period = C.period, series = allInSeries();
  const rows = chartRows(c).map(d => {
    const ok = d.tasks_submitted >= MIN_TASKS, r = { ...d, ok, wip: allInWip(d) };
    series.forEach(x => { r[x.key] = allInOf(d, x); r[`${x.key}_per`] = ok ? r[x.key] / d.tasks_submitted : 0; });
    r.all = series.reduce((t, x) => t + r[x.key], 0);
    return r;
  });
  const used = rows.filter(r => r.ok), subs = used.reduce((n, r) => n + r.tasks_submitted, 0);
  const sumOf = k => used.reduce((n, r) => n + r[k], 0), perTask = subs ? sumOf("all") / subs : 0;
  const chart = columns({
    id: "allin", title: `All-in cost per submitted task, by ${period}`, period, days: rows, bars: true,
    series: series.map(x => ({ ...x, key: `${x.key}_per` })), value: (d, k) => Math.max(0, d[k]),
    pace: perTask, paceLabel: perTask ? `${kUsd(perTask)} all-in per task over ${rangeName(c)}` : "",
    sub: `${chartSince() || "UTC days · "}${VIEWS[C.view].label.toLowerCase()}: everything spent that ${period} (except people still holding a task) ÷ tasks submitted that ${period} · ${period}s with fewer than ${MIN_TASKS} submitted tasks left out`,
    tip: d => d.ok
      ? [[usd(d.all / d.tasks_submitted), " all-in per submitted task"],
         ...series.map(x => [usd(d[`${x.key}_per`]), ` ${x.label.toLowerCase()}`, x.cls]),
         [usd(d.all), ` spent · ${fmtInt(d.tasks_submitted)} tasks submitted`],
         ...(d.wip >= 1 ? [[usd(d.wip), " in progress, not counted"]] : [])]
      : [[usd(d.all), " spent"], [d.tasks_submitted ? `Only ${fmtInt(d.tasks_submitted)}` : "No tasks", ` submitted this ${period}, so it's left out`]],
  });
  const direct = series.filter(x => x.parts[0] === "submitted").reduce((n, x) => n + sumOf(x.key), 0);
  const head = subs ? `<div class="cc-sub">Over the ${used.length} ${period}${used.length === 1 ? "" : "s"} shown: <b>${esc(usd(perTask))}</b> all-in per submitted task
    (${fmtInt(subs)} tasks) = ${series.map(x => `${esc(x.label.toLowerCase())} <b>${esc(usd(sumOf(x.key) / subs))}</b>`).join(" + ")}.
    Overhead is <b>${sumOf("all") > 0 ? Math.round(100 * (sumOf("all") - direct) / sumOf("all")) : 0}%</b> of it.</div>` : "";
  const table = rows.slice().reverse().map(d => `<tr><td>${periodRow(d, period)}</td>${series.map(x => `<td>${esc(usd(d[x.key]))}</td>`).join("")}
    <td>${esc(usd(d.all))}</td><td>${fmtInt(d.tasks_submitted)}</td><td>${d.ok ? esc(usd(d.all / d.tasks_submitted)) : "–"}</td><td>${esc(usd(d.wip))}</td></tr>`).join("");
  return chart + head + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>${period === "day" ? "Day" : period === "week" ? "Week" : "Month"} (UTC)</th>
    ${series.map(x => `<th>${esc(x.label)}</th>`).join("")}<th>All-in</th><th>Tasks submitted</th><th>All-in per submitted task</th><th>In progress (not counted)</th></tr>${table}</table></details>`;
}

// Lost spend over time, by the day it was spent, split into task runs and evals, plus why the tasks were lost.
function lostCard(c) {
  const days = chartRows(c), period = C.period, series = LOST_SERIES(), L = c.lost;
  const chart = columns({
    id: "lost", title: `Lost spend per ${PERIODS[period]}`, period, days, series, value: (d, k) => d[k],
    sub: `${chartSince() || "UTC days · "}spent by people whose claim ended without them submitting, by the day it was spent`,
    tip: d => {
      const lost = series.reduce((t, x) => t + (d[x.key] || 0), 0);
      return [[usd(lost), " lost"], ...series.filter(x => d[x.key]).map(x => [usd(d[x.key]), ` ${x.label.toLowerCase()}`, x.cls]),
              ...(d.total ? [[`${Math.round(100 * lost / d.total)}%`, ` of ${VIEWS[C.view].label.toLowerCase()} that ${period}`]] : [])];
    },
  });
  const why = L.reasons.length ? `<div class="cc-sub">Why: ${L.reasons.map(r => `<b>${esc(usd(r.total))}</b> ${esc(r.label.toLowerCase())} (${fmtInt(r.claims)} claim${r.claims === 1 ? "" : "s"})`).join(" · ")}`
    + `${L.partial ? " · Claims data is still loading, so this uses the task state only for now." : ""}</div>` : "";
  const rows = days.slice().reverse().filter(d => d.lost_runs || d.lost_evals).map(d => `<tr><td>${periodRow(d, period)}</td>
    ${series.map(x => `<td>${esc(usd(d[x.key]))}</td>`).join("")}<td>${esc(usd(series.reduce((t, x) => t + (d[x.key] || 0), 0)))}</td></tr>`).join("");
  return chart + why + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>${period === "day" ? "Day" : period === "week" ? "Week" : "Month"} (UTC)</th>
    ${series.map(x => `<th>${esc(x.label)}</th>`).join("")}<th>Lost</th></tr>${rows}</table></details>`;
}

// Each task's full cost (task runs since it started + portal reviews), grouped by the UTC day it was submitted:
// the column is that day's median, so one expensive task doesn't swing it; the line is the range's median.
// In the "both" view it's split into an evals chart and a task-runs chart, so each part reads on its own.
function perTaskCard(c) {
  if (C.period !== "day") return periodPerTaskCard(c);
  if (C.view === "runs") return runsPerTaskChart(c, "pertask");
  if (C.view !== "both") return perTaskChart(c, { id: "pertask", title: "Cost per submitted task, by day submitted",
    what: VIEWS[C.view].label.toLowerCase(), cls: "s-runs", totalOf: t => t.total });
  return perTaskChart(c, { id: "pertask-evals", title: "Eval cost per submitted task, by day submitted",
      what: "evals: in-task sandbox + portal reviews", cls: "s-untagged", totalOf: t => (t.sandbox || 0) + (t.portal || 0) })
    + runsPerTaskChart(c, "pertask-runs");
}

// Task-run cost per submitted task. Test runs (before the final batch) can't be tied to a task, so each day's column is
// all task-run spend that day ÷ tasks submitted that day, test runs included; the line is the same over the range (the
// tile's figure). The median of the runs that do tie to each task (final batch) is in the tooltip and the table.
function runsPerTaskChart(c, id) {
  const tied = {};
  c.tasks.forEach(t => {
    if (t.last_submitted) (tied[Math.floor(t.last_submitted / 86400) * 86400] ||= []).push(t.agent_runs || 0);
  });
  const days = c.daily.map(d => ({ ...d, avg: d.tasks_submitted ? (d.runs || 0) / d.tasks_submitted : 0,
                                    tied: tied[d.day] ? median(tied[d.day]) : null }));
  const subs = c.summary.tasks_submitted || 0, overall = subs ? (c.summary.runs || 0) / subs : 0;
  const chart = columns({
    id, title: "Task-run cost per submitted task, by day",
    sub: "all task-run spend that day ÷ tasks submitted that day, test runs included (test runs can't be tied to a task, so this is an average)",
    days, series: [{ key: "avg", label: "Per submitted task", cls: "s-runs" }], value: (d, k) => d[k],
    pace: overall, paceLabel: overall ? `${kUsd(overall)} average over ${rangeName(c)}` : "",
    tip: d => d.tasks_submitted
      ? [[usd(d.avg), " per submitted task (average)"], [usd(d.runs), ` task-run spend · ${fmtInt(d.tasks_submitted)} task${d.tasks_submitted === 1 ? "" : "s"} submitted`],
         ...(d.tied != null ? [[usd(d.tied), " median of the runs tied to each task (final batch)"]] : [])]
      : [[usd(d.runs), " task-run spend"], ["No tasks", " submitted this day"]],
  });
  const rows = days.slice().reverse().map(d => `<tr><td>${esc(longDay(d.day))}${d.today ? " (so far)" : ""}</td><td>${esc(usd(d.runs))}</td>
    <td>${fmtInt(d.tasks_submitted)}</td><td>${d.tasks_submitted ? esc(usd(d.avg)) : "–"}</td><td>${d.tied != null ? esc(usd(d.tied)) : "–"}</td></tr>`).join("");
  return chart + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>Day (UTC)</th><th>Task-run spend</th>
    <th>Tasks submitted</th><th>Per submitted task</th><th>Median tied to each task</th></tr>${rows}</table></details>`;
}

function perTaskChart(c, { id, title, what, cls, totalOf }) {
  const byDay = {}, all = [];
  c.tasks.forEach(t => {
    if (!t.last_submitted) return;
    const d = Math.floor(t.last_submitted / 86400) * 86400;
    (byDay[d] = byDay[d] || []).push(totalOf(t));
    all.push(totalOf(t));
  });
  const days = c.daily.map(d => {
    const v = byDay[d.day] || [];
    return { ...d, n: v.length, median: v.length ? median(v) : 0, mean: v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0,
             min: v.length ? Math.min(...v) : 0, max: v.length ? Math.max(...v) : 0 };
  });
  const overall = median(all);
  const chart = columns({
    id, title,
    sub: `median cost of the tasks submitted that day (${esc(what)}), since each task started`,
    days, series: [{ key: "median", label: "Median per task", cls }], value: (d, k) => d[k],
    pace: overall || 0, paceLabel: overall ? `${kUsd(overall)} median over ${rangeName(c)}` : "",
    tip: d => d.n ? [[usd(d.median), " median per task"], [fmtInt(d.n), ` task${d.n === 1 ? "" : "s"} submitted · average ${usd(d.mean)}`],
                     [`${usd(d.min)} – ${usd(d.max)}`, " cheapest – most expensive"]]
                  : [["No tasks", " submitted this day"]],
  });
  const rows = days.slice().reverse().filter(d => d.n).map(d => `<tr><td>${esc(longDay(d.day))}${d.today ? " (so far)" : ""}</td><td>${fmtInt(d.n)}</td>
    <td>${esc(usd(d.median))}</td><td>${esc(usd(d.mean))}</td><td>${esc(usd(d.min))}</td><td>${esc(usd(d.max))}</td></tr>`).join("");
  return chart + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>Day submitted (UTC)</th><th>Tasks</th>
    <th>Median</th><th>Average</th><th>Cheapest</th><th>Most expensive</th></tr>${rows}</table></details>`;
}

// Weekly / monthly: each column is the period's spend ÷ tasks submitted in it (an average, test runs included), and the
// line is the same over the periods shown. A period with fewer than MIN_TASKS submissions is left out: one task carrying
// a whole week's runs would dwarf every other column.
const MIN_TASKS = 5;
function periodPerTaskCard(c) {
  const days = chartRows(c), period = C.period;
  const evals = d => (d.sandbox || 0) + (d.portal || 0), runs = d => d.runs || 0;
  const one = (id, title, what, cls, part) => {
    const rows = days.map(d => ({ ...d, part: part(d), avg: d.tasks_submitted >= MIN_TASKS ? part(d) / d.tasks_submitted : 0 }));
    const spent = rows.filter(d => d.part > 0 && d.tasks_submitted >= MIN_TASKS), subs = spent.reduce((n, d) => n + d.tasks_submitted, 0);  // evals only start in Sep
    const overall = subs ? spent.reduce((n, d) => n + d.part, 0) / subs : 0;
    const chart = columns({
      id, title, period, sub: `${chartSince()}${esc(what)} spend that ${period} ÷ tasks submitted that ${period} (an average: test runs can't be tied to a task) · ${period}s with fewer than ${MIN_TASKS} submitted tasks left out`,
      days: rows, series: [{ key: "avg", label: "Per submitted task", cls }], value: (d, k) => d[k],
      pace: overall, paceLabel: overall ? `${kUsd(overall)} average over the ${spent.length} ${period}s with ${what} spend` : "",
      tip: d => d.tasks_submitted >= MIN_TASKS ? [[usd(d.avg), " per submitted task (average)"], [usd(d.part), ` ${what} spend · ${fmtInt(d.tasks_submitted)} tasks submitted`]]
        : [[usd(d.part), ` ${what} spend`], [d.tasks_submitted ? `Only ${fmtInt(d.tasks_submitted)}` : "No tasks", ` submitted this ${period}, so it's left out`]],
    });
    const table = rows.slice().reverse().map(d => `<tr><td>${periodRow(d, period)}</td><td>${esc(usd(d.part))}</td><td>${fmtInt(d.tasks_submitted)}</td>
      <td>${d.tasks_submitted >= MIN_TASKS ? esc(usd(d.avg)) : "–"}</td></tr>`).join("");
    return chart + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>${period === "week" ? "Week" : "Month"} (UTC)</th>
      <th>Spend (${esc(what)})</th><th>Tasks submitted</th><th>Per submitted task</th></tr>${table}</table></details>`;
  };
  const evalsChart = () => one("pertask-evals", `Eval cost per submitted task, by ${period}`, "evals", "s-untagged", evals);
  const runsChart = () => one("pertask-runs", `Task-run cost per submitted task, by ${period}`, "task-run", "s-runs", runs);
  return C.view === "evals" ? evalsChart() : C.view === "runs" ? runsChart() : evalsChart() + runsChart();
}

function perCbCard(c) {
  const rows = chartRows(c), period = C.period;
  if (period !== "day") {  // per contributor-day, so a long month and a partial week compare fairly
    const days = rows.map(d => ({ ...d, per_cb: d.cb_days ? d.total / d.cb_days : 0 }));
    return columns({
      id: "percb", title: `Spend per active contributor-day, by ${period}`, period, days,
      sub: `${chartSince()}${VIEWS[C.view].label.toLowerCase()} ÷ contributor-days (each person counted once per day they were active)`,
      series: [{ key: "per_cb", label: "Per contributor-day", cls: "s-runs" }], value: (d, k) => d[k],
      tip: d => [[d.cb_days ? usd(d.per_cb) : "–", " per active contributor-day"], [fmtInt(d.active_cbs), ` contributors active · ${fmtInt(d.cb_days)} contributor-days`]],
    });
  }
  const days = rows.map(d => ({ ...d, per_cb: d.active_cbs ? d.total / d.active_cbs : 0 }));
  return columns({
    id: "percb", title: "Spend per active contributor, per day", sub: `${VIEWS[C.view].label.toLowerCase()} ÷ contributors active that day · steady means each person costs about the same`,
    days, series: [{ key: "per_cb", label: "Per active contributor", cls: "s-runs" }], value: (d, k) => d[k],
    tip: d => [[d.active_cbs ? usd(d.per_cb) : "–", " per active contributor"], [fmtInt(d.active_cbs), ` active · ${fmtInt(d.tasks_touched)} tasks touched`]],
  });
}

// Lost claims (already in the chosen view) added up per task.
function lostPerTask(claims) {
  const by = {};
  for (const c of claims) {
    const t = by[c.task_id] ||= { task_id: c.task_id, task_now: c.task_now || "", claims: 0, who: new Set(), whys: new Set(), ended: 0,
                                  agent_runs: 0, sandbox: 0, portal: 0, total: 0 };
    t.claims += 1;
    t.who.add(c.email || "unknown");
    t.whys.add(c.reason_label);
    t.ended = Math.max(t.ended, c.ended || 0);
    for (const k of ["agent_runs", "sandbox", "portal", "total"]) t[k] += c[k] || 0;
  }
  return Object.values(by).map(t => ({ ...t, people: [...t.who].sort().join(", "), why: [...t.whys].join(", ") }));
}

// ---- tables (click a header to sort; the first 25 rows, then "show all") ----
const TABLES = {
  losttasks: {
    title: "Lost spend per claim",
    sub: "Each person whose claim on a task ended without them submitting it, and what they spent on it in the range.",
    rows: () => C.shown.lost.claims, key: "total",
    cols: [
      ["Task", "task_id", r => `<span class="mono" title="${esc(r.task_id)}">${esc(r.task_id.slice(-6))}</span>`],
      ["Person", "email", r => esc(r.email || "unknown")],
      ["Why", "reason_label", r => esc(r.reason_label) + (r.cancel_reason ? ` <span class="muted" title="${esc(r.cancel_reason)}">· ${esc(r.cancel_reason.slice(0, 40))}</span>` : "")],
      ["Claim ended", "ended", r => (r.ended ? esc(longDay(r.ended)) : "–") + (r.ended_how === "last seen" ? ` <span class="muted" title="No record of when the claim ended: their last activity">· last seen</span>` : "")],
      ["Task runs", "agent_runs", r => esc(usd(r.agent_runs)), true, "runs"],
      ["In-task sandbox", "sandbox", r => esc(usd(r.sandbox)), true, "sandbox"],
      ["Portal reviews", "portal", r => esc(usd(r.portal)), true, "portal"],
      ["Spend", "total", r => `<b>${esc(usd(r.total))}</b>`, true],
    ],
  },
  // the same lost claims added up per task (Evan, 2026-10-31)
  losttaskrows: {
    title: "Lost spend per task",
    sub: "The lost claims above, added up per task: how many claims ended on it without a submit, by whom, and where the task stands now.",
    rows: () => lostPerTask(C.shown.lost.claims), key: "total",
    cols: [
      ["Task", "task_id", r => `<span class="mono" title="${esc(r.task_id)}">${esc(r.task_id.slice(-6))}</span>`],
      ["Lost claims", "claims", r => fmtInt(r.claims), true],
      ["People", "people", r => esc(r.people)],
      ["Why", "why", r => esc(r.why)],
      ["Last claim ended", "ended", r => (r.ended ? esc(longDay(r.ended)) : "–")],
      ["Task now", "task_now", r => esc(r.task_now)],
      ["Task runs", "agent_runs", r => esc(usd(r.agent_runs)), true, "runs"],
      ["In-task sandbox", "sandbox", r => esc(usd(r.sandbox)), true, "sandbox"],
      ["Portal reviews", "portal", r => esc(usd(r.portal)), true, "portal"],
      ["Lost", "total", r => `<b>${esc(usd(r.total))}</b>`, true],
    ],
  },
  pending: {
    title: "Not submitted yet, claimed by 2+ people",
    sub: () => {
      const p = C.shown.pending_multi;
      return `${usd(p.total)} spent so far on ${fmtInt(p.tasks.length)} ${p.tasks.length === 1 ? "task" : "tasks"}. Tasks nobody has submitted yet, not cancelled, `
        + `that 2 or more people have claimed. Spend so far = spend in the range; lost so far = what people whose claim ended spent; current holder = what the person holding it now spent.`
        + `${p.partial ? " Claims data is still loading, so only portal chats are counted for now." : ""}`;
    },
    rows: () => C.shown.pending_multi.tasks, key: "total",
    cols: [
      ["Task", "task_id", r => `<span class="mono" title="${esc(r.task_id)}">${esc(r.task_id.slice(-6))}</span>`],
      ["Claimed by", "claimers", r => `<span title="${esc(r.claimed_by)}">${fmtInt(r.claimers)}</span>`, true],
      ["Spend so far", "total", r => `<b>${esc(usd(r.total))}</b>`, true],
      ["Lost so far", "lost", r => esc(usd(r.lost)), true],
      ["Current holder spent", "holder_spend", r => (r.live_claim ? esc(usd(r.holder_spend)) : "–"), true],
      ["Current holder", "holder", r => (r.live_claim ? esc(r.holder || "–") : `<span class="muted" title="Held last by ${esc(r.holder || "nobody")}">Nobody</span>`)],
      ["Last activity", "last_activity", r => esc(longDay(r.last_activity))],
    ],
  },
  tasks: {
    title: "Cost per submitted task",
    sub: "Tasks a contributor submitted in the range: what the people who submitted each one spent on it since it started. Task runs = runs whose IDs are in the task's submissions plus runs launched from the task itself; in-task sandbox and portal reviews are the evals. Lost before = what earlier holders spent on it (their claim ended without them submitting).",
    rows: () => C.shown.tasks, key: "total",
    cols: [
      ["Task", "task_id", r => `<span class="mono" title="${esc(r.task_id)}">${esc(r.task_id.slice(-6))}</span>`],
      ["Submitted", "last_submitted", r => r.last_submitted ? esc(longDay(r.last_submitted)) : "–"],
      ["Submitted by", "submitters", r => esc(r.submitters)],
      ["Task runs", "agent_runs", r => esc(usd(r.agent_runs)), true, "runs"],
      ["In-task sandbox", "sandbox", r => esc(usd(r.sandbox)), true, "sandbox"],
      ["Portal reviews", "portal", r => esc(usd(r.portal)), true, "portal"],
      ["Total", "total", r => `<b>${esc(usd(r.total))}</b>`, true],
      ["Lost before", "lost_before", r => (r.lost_before ? esc(usd(r.lost_before)) : "–"), true],
    ],
  },
  people: {
    title: "Cost per contributor",
    sub: "In the range: task runs whose IDs are in each contributor's submissions or that were launched from their task, and the evals they ran (in-task sandbox + portal reviews). Lost = what they spent on tasks whose claim ended without them submitting. Per submitted task divides by their submissions in the range.",
    rows: () => C.shown.people, key: "total",
    cols: [
      ["Contributor", "email", r => esc(r.email)],
      ["Active days", "active_days", r => fmtInt(r.active_days), true],
      ["Tasks touched", "tasks_touched", r => fmtInt(r.tasks_touched), true],
      ["Submitted", "tasks_submitted", r => fmtInt(r.tasks_submitted), true],
      ["Task runs", "agent_runs", r => esc(usd(r.agent_runs)), true, "runs"],
      ["In-task sandbox", "sandbox", r => esc(usd(r.sandbox)), true, "sandbox"],
      ["Portal reviews", "portal", r => esc(usd(r.portal)), true, "portal"],
      ["Total", "total", r => `<b>${esc(usd(r.total))}</b>`, true],
      ["Lost", "lost", r => (r.lost ? esc(usd(r.lost)) : "–"), true],
      ["Per submitted task", "per_task", r => (r.per_task == null ? "–" : esc(usd(r.per_task))), true],
    ],
  },
};
function renderTable(name, all = false) {
  const t = TABLES[name], sort = C.sort[name] || { key: t.key, dir: -1 };
  const rows = t.rows().slice().sort((a, b) => {
    const x = a[sort.key], y = b[sort.key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * sort.dir;
  });
  const shown = all ? rows : rows.slice(0, 25);
  const cols = t.cols.filter(col => !col[4] || inView(col[4]));  // only the columns the chosen view counts
  const head = cols.map(([label, key, , num]) => `<th class="${num ? "num" : ""}"><button type="button" class="th-sort" data-sort="${key}">${esc(label)}${
    sort.key === key ? (sort.dir < 0 ? " ↓" : " ↑") : ""}</button></th>`).join("");
  const body = shown.map(r => `<tr>${cols.map(([, , cell, num]) => `<td class="${num ? "num" : ""}">${cell(r)}</td>`).join("")}</tr>`).join("");
  $(`#${name}-card`).innerHTML = `<div class="cc-head"><div><div class="cc-title">${esc(t.title)} <span class="muted">· ${fmtInt(rows.length)}</span></div>
    <div class="cc-sub">${esc(typeof t.sub === "function" ? t.sub() : t.sub)}</div></div></div>
    <div class="table-wrap"><table class="runs cost-table"><thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${cols.length}" class="muted">Nothing in this range.</td></tr>`}</tbody></table></div>
    ${rows.length > shown.length ? `<button class="ghost" data-all="${name}">Show all ${fmtInt(rows.length)}</button>` : ""}`;
  const card = $(`#${name}-card`);
  card.querySelectorAll("[data-sort]").forEach(btn => btn.onclick = () => {
    const key = btn.dataset.sort;
    C.sort[name] = { key, dir: sort.key === key ? -sort.dir : (typeof (rows[0] || {})[key] === "number" ? -1 : 1) };
    renderTable(name, all);
  });
  const more = card.querySelector("[data-all]");
  if (more) more.onclick = () => renderTable(name, true);
}

// ---- controls ----
$("#range").addEventListener("click", e => {
  const b = e.target.closest("[data-days]");
  if (b && (C.start || +b.dataset.days !== C.days)) {
    C.days = +b.dataset.days;
    C.start = C.end = "";
    localStorage.setItem("evp_cost_days", String(C.days));
    localStorage.removeItem("evp_cost_custom");
    $("#custom-range").classList.add("hidden");
    load();
  }
  if (e.target.closest("#custom")) {  // open the From / To fields, filled with the range on screen
    const box = $("#custom-range"), c = (C.data || {}).cost || {}, iso = t => new Date(t * 1000).toISOString().slice(0, 10);
    const today = new Date().toISOString().slice(0, 10);
    for (const f of [$("#from"), $("#to")]) { f.min = FIRST_DAY; f.max = today; }
    $("#from").value = C.start || (c.start ? iso(c.start) : FIRST_DAY);
    $("#to").value = C.end || (c.end ? iso(c.end) : today);
    box.classList.toggle("hidden");
    $("#custom").setAttribute("aria-expanded", String(!box.classList.contains("hidden")));
    if (!box.classList.contains("hidden")) $("#from").focus();
  }
});
$("#custom-range").addEventListener("submit", e => {
  e.preventDefault();
  let [from, to] = [$("#from").value, $("#to").value];
  if (!from || !to) return;
  if (from > to) [from, to] = [to, from];
  [C.start, C.end] = [from, to];
  localStorage.setItem("evp_cost_custom", `${from}..${to}`);
  $("#custom-range").classList.add("hidden");
  load();
});
$("#view").addEventListener("click", e => {
  const b = e.target.closest("[data-view-pick]");
  if (!b || b.dataset.viewPick === C.view) return;
  C.view = b.dataset.viewPick;
  localStorage.setItem("evp_cost_view", C.view);
  C.sort = {};
  render();
});
$("#period").addEventListener("click", e => {
  const b = e.target.closest("[data-period]");
  if (!b || b.dataset.period === C.period) return;
  C.period = b.dataset.period;
  localStorage.setItem("evp_cost_period", C.period);
  if (C.data) render();
});
$("#chart-type").addEventListener("click", e => {
  const b = e.target.closest("[data-chart-type]");
  if (!b || b.dataset.chartType === C.chart) return;
  C.chart = b.dataset.chartType;
  localStorage.setItem("evp_cost_chart", C.chart);
  if (C.data) render();
});
$("#refresh").onclick = async () => { $("#refresh").disabled = true; await load(true); $("#refresh").disabled = false; };
$("#logout").onclick = async () => { await api("/api/admin/logout", { method: "POST" }); location.href = "/admin"; };
$("#view-switch").addEventListener("click", async e => {
  const view = e.target.closest("[data-view]")?.dataset.view;
  if (view === "admin") location.href = "/admin";
  if (view === "cbs") location.href = "/admin/cbs";
  if (view === "chat") {
    try { await api("/api/admin/open-chat", { json: {} }); location.href = "/"; }
    catch (err) { uiAlert("Couldn't open the chat view", err.message); }
  }
});
setInterval(() => { if (!document.hidden) load(false, true); }, 300000);
load();
