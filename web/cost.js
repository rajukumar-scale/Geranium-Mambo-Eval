// Admin Cost page: what Atlas Commercial spends on models (every key) per day, per submitted task and per contributor.
// Charts follow the dataviz rules: columns <= 24px with a 4px rounded top and a 2px gap between stacked parts,
// hairline grid, one axis, a legend for two series (none for one), values only on the peak and today,
// a tooltip on hover and keyboard focus (textContent only), and a table view. Colours are validated per mode.

// The range is the last 7 / 14 / 30 days or a custom start..end (UTC days, from Aug 1), from the link or the last visit.
const URLQ = new URLSearchParams(location.search);
const C = { days: +(URLQ.get("days") || localStorage.getItem("evp_cost_days") || 14), view: localStorage.getItem("evp_cost_view") || "both", data: null, sort: {},
            start: URLQ.get("start") || "", end: URLQ.get("end") || "",
            period: localStorage.getItem("evp_cost_period") || "day", chart: localStorage.getItem("evp_cost_chart") || "bar",
            layer: URLQ.get("layer") || localStorage.getItem("evp_cost_layer") || "all" };
if (!URLQ.get("days") && !C.start) [C.start, C.end] = (localStorage.getItem("evp_cost_custom") || "").split("..").concat("").slice(0, 2);
if (!(C.start && C.end)) C.start = C.end = "";
const FIRST_DAY = "2026-08-01";  // the cost data starts here
// The pipeline layer switch (Evan, 2026-11-09): every figure for all layers, or only what was spent at L-1 / L0 and the
// tasks submitted there (the server cuts it, server/cost.py). In the all-layers view a chart and the task table split
// the spend by layer: L-1, L0, L10 and later, and no layer known (the hint pipeline, older runs never tied to a submission).
const LAYERS = { all: "All layers", l1: "L-1", l0: "L0" };
if (!LAYERS[C.layer]) C.layer = "all";
const LAYER_SERIES = [{ key: "l1", label: "L-1", cls: "s-l1" }, { key: "l0", label: "L0", cls: "s-l0" },
                      { key: "later", label: "L10 and later", cls: "s-later" }, { key: "unknown", label: "No layer known", cls: "s-nolayer" }];
// A {key: [task runs, evals]} layer split, in the chosen view.
const layerOf = (layers, k) => { const v = (layers || {})[k] || [0, 0]; return (C.view !== "evals" ? v[0] : 0) + (C.view !== "runs" ? v[1] : 0); };
const layerName = () => (C.layer === "all" ? "" : ` at ${LAYERS[C.layer]}`);
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
  // each submitted task: everything anyone spent on it from its start (task runs tied to it + every holder's evals); older
  // cached figures without it fall back to the submitters' own
  const tasks = c.tasks.map(t => {
    const has = t.total_all != null;
    const r = { ...t, runs_v: C.view !== "evals" ? (has ? t.runs_all : t.agent_runs) || 0 : 0,
                evals_sub_v: C.view !== "runs" ? (has ? t.evals_submitters : (t.sandbox || 0) + (t.portal || 0)) || 0 : 0,
                evals_oth_v: C.view !== "runs" ? (has ? t.evals_others : 0) || 0 : 0,
                lost_before: split(t, "lost_before"), l1: layerOf(t.layers, "l1"), l0: layerOf(t.layers, "l0") };
    r.total = r.runs_v + r.evals_sub_v + r.evals_oth_v;
    return r;
  });
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
  { key: "untied", label: "Not tied to a person", what: d => [
      inView("runs") && (d.runs_on_tasks != null
        ? `${usd(d.runs_on_tasks)} task runs on a task, but who ran them isn't recorded; ${usd(Math.max(0, (d.agent_runs || 0) - d.runs_on_tasks - (d.hint || 0)))} task runs with no task${d.hint ? `, ${usd(d.hint)} hint pipeline` : ""}`
        : `task runs never in a submission${d.hint ? `, ${usd(d.hint)} hint pipeline` : ""}`),
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

const rangeQuery = () => (C.start ? `start=${C.start}&end=${C.end}` : `days=${C.days}`) + (C.layer === "all" ? "" : `&layer=${C.layer}`);
async function load(refresh = false, quiet = false) {
  document.querySelectorAll("#range [data-days]").forEach(b => b.classList.toggle("on", !C.start && +b.dataset.days === C.days));
  $("#custom").classList.toggle("on", !!C.start);
  document.querySelectorAll("#layer [data-layer]").forEach(b => b.classList.toggle("on", b.dataset.layer === C.layer));
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
  // one layer: say what's left out (spend with no layer known, and the other layers' share is elsewhere)
  const lay = c.layers && C.layer !== "all" ? `Showing ${LAYERS[C.layer]} only: task runs, in-task sandbox and portal reviews spent while the task was at ${LAYERS[C.layer]}, and the tasks submitted at ${LAYERS[C.layer]}. `
    + `Left out in this range: ${usd(layerOf(c.layers, C.layer === "l1" ? "l0" : "l1"))} at ${C.layer === "l1" ? "L0" : "L-1"}, ${usd(layerOf(c.layers, "later"))} at L10 and later, `
    + `and ${usd(layerOf(c.layers, "unknown"))} with no layer known (the hint pipeline, and runs never tied to a submission before calls carried the task id, ~Oct 31).` : "";
  $("#notes").innerHTML = notes.map(n => `<div class="error">${esc(n)}</div>`).join("") + (info ? `<p class="muted">${esc(info)}</p>` : "")
    + (lay ? `<p class="muted">${esc(lay)}</p>` : "");
  if (c.error) return;
  document.querySelectorAll("#view [data-view-pick]").forEach(x => x.classList.toggle("on", x.dataset.viewPick === C.view));
  document.querySelectorAll("#period [data-period]").forEach(x => x.classList.toggle("on", x.dataset.period === C.period));
  document.querySelectorAll("#chart-type [data-chart-type]").forEach(x => x.classList.toggle("on", x.dataset.chartType === C.chart));
  document.querySelectorAll("#layer [data-layer]").forEach(x => x.classList.toggle("on", x.dataset.layer === C.layer));
  C.shown = viewOf(c);
  c = C.shown;
  renderTiles(c, b);
  $("#split-card").innerHTML = c.breakdown ? splitCard(c) : "";
  $("#split-card").classList.toggle("hidden", !c.breakdown);
  $("#allin-card").innerHTML = taskCostCard(c);
  $("#allin-card").classList.remove("hidden");
  $("#daily-card").innerHTML = dailyCard(c, b);
  const byLayer = C.layer === "all" && !!c.layers;
  $("#layer-card").innerHTML = byLayer ? layerCard(c) : "";
  $("#layer-card").classList.toggle("hidden", !byLayer);
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
    the Sonorra env, from LiteLLM's request log and SMIG's usage records (most calls go through SMIG since Oct 16) on every key tagged with the Geranium Mambo project (mostly <span class="mono">pub-mcp-commercial</span>);
    a run counts for a task and contributor when its run ID is in one of their submitted attempts, or when it was launched from the task itself
    (run named after the task's pipeline node); earlier sandbox-viewer runs can't be tied to a task and only show in the daily totals. <b>In-task sandbox</b> is the old in-task chat
    (<span class="mono">${esc(b.key_alias || "")}</span>), <b>hint pipeline</b> the autohint runs. Other teams' work that carries the project tag
    (CoopBench, checkpoint evals) is left out. <b>Portal reviews</b> come from this portal's own records: SMIG's billed cost per run from Oct 26, 20:00 UTC, the list-price estimate before that. A <b>submitted task</b> is a contributor's
    submission at L-1 or L0 (the bot and @scale.com accounts excluded); its cost is everything anyone spent on it from its start to the range's end: every task run tied to it (by the run ID in a submission, the task it was launched from, or, since about Oct 31, the task id the run carries) and every holder's evals.
    Contributors count only what falls inside the range. <b>Lost spend</b> is counted per person and task: when someone's claim ends (released, expired, taken away, task cancelled)
    without them submitting that task, what they spent on it is lost at once, even if someone else submits it later; if they claim it again and submit, it isn't.
    Someone who holds a live or paused claim now is <b>in progress</b>. Claims come from the CB tracker (L-1 claims since Oct 1); for older work, the task's
    platform state stands in (live claim or paused, and they're its last worker). A submitted task's cost is only what the people who submitted it spent;
    what earlier holders spent shows as "lost before". <b>Internal tests</b> are @scale.com and internal accounts and admin runs in the portal. Task runs that never
    reached a submission, the hint pipeline, untagged sandbox calls and portal runs with no task <b>can't be tied to a task</b>.
    <b>Layers</b>: a task-run or sandbox call counts at the layer of the task's pipeline node that was open when it was made (calls carry the task id from about Oct 31),
    else the node it was launched from, else the layer of the first submission that carries its run ID; a portal review counts at its chat's node's layer.
    The hint pipeline and older runs never tied to a submission have no layer. With L-1 or L0 picked, every figure counts only that layer's spend and the tasks submitted there.
    ${q.states ? `Task states: <a href="${esc(q.states)}" target="_blank" rel="noopener">platform state per task</a>. ` : ""}Source: <a href="${esc(q.facts || "#")}" target="_blank" rel="noopener">every figure since Aug 1</a>,
    refreshed in the background every 15 minutes.`;
}

function tile(label, value, sub) {
  return `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${value}</div><div class="tile-sub">${sub}</div></div>`;
}
// What each submitted task cost: everything anyone spent on it (average, with the median and the parts), and the range's
// whole spend ÷ tasks submitted, with how much of it went to these tasks, other tasks, or nothing we can tie to a task.
const splitOf = v => (C.view !== "evals" ? (v || [0, 0])[0] : 0) + (C.view !== "runs" ? (v || [0, 0])[1] : 0);
function taskCostTile(c) {
  const T = c.tasks, n = T.length;
  if (!n) return tile("Spent on each submitted task", "–", "no submissions yet");
  const avg = k => T.reduce((a, t) => a + (t[k] || 0), 0) / n;
  const parts = [C.view !== "evals" && `${esc(usd(avg("runs_v")))} task runs`,
                 C.view !== "runs" && `${esc(usd(avg("evals_sub_v")))} evals by whoever submitted`,
                 C.view !== "runs" && `${esc(usd(avg("evals_oth_v")))} evals by others (earlier holders, tests)`].filter(Boolean);
  return tile("Spent on each submitted task", esc(usd(avg("total"))),
    `average · median ${esc(usd(median(T.map(t => t.total))))} · ${parts.join(" + ")} · everything anyone spent on it since it started`);
}
function allInTile(c) {
  const s = c.summary, n = s.tasks_submitted;
  if (!n) return tile("All-in per submitted task", "–", "no submissions yet");
  const sp = s.split;
  const sub = sp ? ` · of it ${esc(usd(splitOf(sp.submitted_tasks)))} went to these tasks, ${esc(usd(splitOf(sp.other_tasks)))} to tasks not submitted (yet)`
    + ` and ${esc(usd(splitOf(sp.untied)))} can't be tied to a task${C.view !== "evals" && sp.hint ? ` (${esc(usd(sp.hint))} hint pipeline)` : ""}` : "";
  return tile("All-in per submitted task", esc(usd(s.total / n)), `all ${esc(usd(s.total))} spent in the range ÷ ${fmtInt(n)} tasks submitted${sub}`);
}

function renderTiles(c, b) {
  const s = c.summary;
  $("#tiles").innerHTML = [
    tile(`${VIEWS[C.view].label}${layerName()}, ${rangeName(c)}`, esc(usd(s.total)),
      SERIES.filter(x => inView(x.key) && s[x.key]).map(x => `${esc(usd(s[x.key]))} ${esc(x.label.toLowerCase())}`).join(" · ") || "nothing"),
    ...(C.layer === "all" && c.layers ? ["l1", "l0"].map(k => {  // the two layers' shares; the rest is L10+ or no layer known
      const v = layerOf(c.layers, k), all = LAYER_SERIES.reduce((t, x) => t + layerOf(c.layers, x.key), 0);
      return tile(`At ${LAYERS[k]}, ${rangeName(c)}`, esc(usd(v)), `${all ? Math.round(100 * v / all) : 0}% of ${esc(VIEWS[C.view].label.toLowerCase())}`
        + (k === "l0" ? ` · ${esc(usd(layerOf(c.layers, "later")))} at L10+, ${esc(usd(layerOf(c.layers, "unknown")))} no layer known` : ""));
    }) : []),
    inView("runs") && tile("Task runs tied to a submission", s.runs ? `${Math.round(100 * s.runs_linked / s.runs)}%` : "–",
      `${esc(usd(s.runs_linked))} of ${esc(usd(s.runs))} · the rest are re-runs or unsubmitted work`),
    c.lost ? tile(`Lost spend, ${rangeName(c)}`, esc(usd(c.lost.total)),
      `${fmtInt(c.lost.claims.length)} ${c.lost.claims.length === 1 ? "claim" : "claims"} ended without that person submitting`
      + (c.summary.total ? ` · ${Math.round(100 * c.lost.total / c.summary.total)}% of ${esc(VIEWS[C.view].label.toLowerCase())}` : "")
      + (c.lost.in_progress.total ? ` · ${esc(usd(c.lost.in_progress.total))} in progress (still held)` : "")) : null,
    taskCostTile(c),
    allInTile(c),
    tile("Per active contributor, per day", s.per_cb_day == null ? "–" : esc(usd(s.per_cb_day)), `${esc(VIEWS[C.view].label.toLowerCase())} ÷ contributor-days`),
    tile("Tasks submitted", esc(fmtInt(s.tasks_submitted)), `by ${fmtInt(s.submitters)} contributors · ${C.layer === "all" ? "L-1/L0" : `at ${LAYERS[C.layer]}`}`),
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

// Spend per day / week / month split by the pipeline layer it was spent at (all-layers view only).
function layerCard(c) {
  const days = chartRows(c).map(d => {
    const r = { ...d };
    LAYER_SERIES.forEach(x => { r[`ly_${x.key}`] = layerOf(d.layers, x.key); });
    return r;
  });
  const period = C.period, tot = d => LAYER_SERIES.reduce((t, x) => t + d[`ly_${x.key}`], 0);
  const chart = columns({
    id: "layer", title: `Spend per ${PERIODS[period]}, by layer`, period, days,
    sub: `${chartSince() || "UTC days · "}${VIEWS[C.view].label.toLowerCase()} by the layer the task was at when the money was spent · layers are known for nearly all spend from ~Oct 31 (calls carry the task id); before that only for runs tied to a submission`,
    series: LAYER_SERIES.map(x => ({ ...x, key: `ly_${x.key}` })), value: (d, k) => d[k],
    tip: d => [[usd(tot(d)), ` ${VIEWS[C.view].label.toLowerCase()}`],
      ...LAYER_SERIES.filter(x => d[`ly_${x.key}`]).map(x => [usd(d[`ly_${x.key}`]), ` ${x.label}`, x.cls])],
  });
  const rows = days.slice().reverse().map(d => `<tr><td>${periodRow(d, period)}</td>${LAYER_SERIES.map(x => `<td>${esc(usd(d[`ly_${x.key}`]))}</td>`).join("")}
    <td>${esc(usd(tot(d)))}</td></tr>`).join("");
  return chart + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>${period === "day" ? "Day" : period === "week" ? "Week" : "Month"} (UTC)</th>
    ${LAYER_SERIES.map(x => `<th>${esc(x.label)}</th>`).join("")}<th>Total</th></tr>${rows}</table></details>`;
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

// Cost per submitted task by when it was submitted (Evan, 2026-11-10): each column is the average of everything anyone
// spent on the tasks submitted that day / week / month, from each task's start, in its parts; the line is the range's
// average. (It replaces "spend that day ÷ tasks submitted that day", which read low: most of a task's spend falls on
// the days before it's submitted.)
const COST_SERIES = [{ key: "runs_v", label: "Task runs", cls: "s-runs", side: "runs" },
                     { key: "evals_sub_v", label: "Evals by whoever submitted", cls: "s-untagged", side: "evals" },
                     { key: "evals_oth_v", label: "Evals by others (earlier holders, tests)", cls: "s-lost", side: "evals" }];
const periodOf = (t, period) => {
  const day = Math.floor(t / 86400) * 86400, d = new Date(day * 1000);
  if (period === "week") return day - ((d.getUTCDay() + 6) % 7) * 86400;
  if (period === "month") return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000;
  return day;
};
function taskCostCard(c) {
  const period = C.period, series = COST_SERIES.filter(x => x.side === "runs" ? C.view !== "evals" : C.view !== "runs");
  const groups = {};
  c.tasks.forEach(t => { if (t.last_submitted) (groups[periodOf(t.last_submitted, period)] ||= []).push(t); });
  const rows = chartRows(c).map(d => {
    const ts = groups[d.day] || [], r = { ...d, n: ts.length, med: ts.length ? median(ts.map(t => t.total)) : 0 };
    series.forEach(x => { r[x.key] = ts.length ? ts.reduce((a, t) => a + (t[x.key] || 0), 0) / ts.length : 0; });
    r.avg = series.reduce((a, x) => a + r[x.key], 0);
    return r;
  });
  const n = c.tasks.length, overall = n ? c.tasks.reduce((a, t) => a + t.total, 0) / n : 0;
  const chart = columns({
    id: "taskcost", title: `Cost per submitted task, by ${period} submitted`, period, days: rows, bars: true, series, value: (d, k) => d[k],
    pace: overall, paceLabel: overall ? `${kUsd(overall)} average over ${rangeName(c)}` : "",
    sub: `${chartSince() || "UTC days · "}${VIEWS[C.view].label.toLowerCase()}: everything anyone spent on each task from its start, averaged over the tasks submitted that ${period}`,
    tip: d => d.n ? [[usd(d.avg), " per task (average)"], ...series.map(x => [usd(d[x.key]), ` ${x.label.toLowerCase()}`, x.cls]),
                     [fmtInt(d.n), ` task${d.n === 1 ? "" : "s"} submitted · median ${usd(d.med)}`]]
                  : [["No tasks", ` submitted this ${period}`]],
  });
  const table = rows.slice().reverse().filter(d => d.n).map(d => `<tr><td>${periodRow(d, period)}</td><td>${fmtInt(d.n)}</td>
    ${series.map(x => `<td>${esc(usd(d[x.key]))}</td>`).join("")}<td>${esc(usd(d.avg))}</td><td>${esc(usd(d.med))}</td></tr>`).join("");
  return chart + `<details class="sc-table"><summary>Show as table</summary><table class="runs"><tr><th>${period === "day" ? "Day" : period === "week" ? "Week" : "Month"} submitted (UTC)</th>
    <th>Tasks</th>${series.map(x => `<th>${esc(x.label)}</th>`).join("")}<th>Per task (average)</th><th>Median</th></tr>${table}</table></details>`;
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
    sub: "Tasks submitted in the range, with everything anyone spent on each since it started. Task runs = every run tied to the task (its run ID is in a submission, it was launched from the task, or, since about Oct 31, it carries the task's id), whoever ran it. Evals by others = earlier holders whose claim ended without a submit, reviewers and internal tests.",
    rows: () => C.shown.tasks, key: "total",
    // the per-layer columns: the submitters' own spend, by the layer it was spent at (Total - L-1 - L0 = L10+ / no layer known)
    cols: [
      ["Task", "task_id", r => `<span class="mono" title="${esc(r.task_id)}">${esc(r.task_id.slice(-6))}</span>`],
      ["Submitted", "last_submitted", r => r.last_submitted ? esc(longDay(r.last_submitted)) : "–"],
      ["Submitted by", "submitters", r => esc(r.submitters)],
      ["Spent on it", "total", r => `<b>${esc(usd(r.total))}</b>`, true],
      ["Task runs", "runs_v", r => esc(usd(r.runs_v)), true, "runs"],
      ["Evals by whoever submitted", "evals_sub_v", r => esc(usd(r.evals_sub_v)), true, "portal"],
      ["Evals by others", "evals_oth_v", r => (r.evals_oth_v ? esc(usd(r.evals_oth_v)) : "–"), true, "portal"],
      ["…at L-1", "l1", r => esc(usd(r.l1)), true, "layers"],
      ["…at L0", "l0", r => esc(usd(r.l0)), true, "layers"],
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
  // only the columns the chosen view counts; the per-layer split only in the all-layers view
  const cols = t.cols.filter(col => (col[4] === "layers" ? C.layer === "all" : !col[4] || inView(col[4])));
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
$("#layer").addEventListener("click", e => {
  const b = e.target.closest("[data-layer]");
  if (!b || b.dataset.layer === C.layer) return;
  C.layer = b.dataset.layer;
  localStorage.setItem("evp_cost_layer", C.layer);
  C.sort = {};
  load();
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
