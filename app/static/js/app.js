"use strict";
/* API Testing Dashboard front end. No innerHTML with data: everything is built with h()/textContent. */

const CSRF = { "X-Requested-With": "api-dashboard" };
const state = { meta: null, envs: [], charts: [], timers: [], user: null };
const $ = (sel, el = document) => el.querySelector(sel);

/* ---------------------------------------------------------------- helpers */
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function errMsg(data, res) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (d && d.message) return d.message;
  if (Array.isArray(d)) return d.map((e) => `${(e.loc || []).slice(1).join(".") || "input"}: ${e.msg}`).join("; ");
  return `Request failed (${res.status}).`;
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: { ...CSRF }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error("Cannot reach the server. Check that it is still running.");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    showLogin();
    throw new Error("Your session ended. Sign in again.");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (!res.ok) throw new Error(errMsg(data, res));
  return data;
}

function toast(message, kind = "info") {
  const t = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status", text: message });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 3500);
}

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString() : "n/a");
const fmtDur = (s) => (s === null || s === undefined ? "n/a" : s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`);
const fmtMs = (v) => (v === null || v === undefined ? "n/a" : `${v.toFixed(v < 10 ? 2 : 1)} ms`);
const fmtPct = (v) => (v === null || v === undefined ? "n/a" : `${v}%`);
const badge = (status) => h("span", { class: `badge ${status}`, text: status.replace("_", " ") });
const isActive = (r) => r.status === "queued" || r.status === "running";

function empty(text, action) { return h("div", { class: "empty" }, h("p", { text }), action || null); }
function loading() { return h("div", { class: "loading", text: "Loading…" }); }
function errorBox(e, retry) {
  return h("div", { class: "errorbox", role: "alert" }, h("p", { text: e.message || String(e) }),
    retry ? h("button", { class: "btn small", onclick: retry, text: "Try again" }) : null);
}
function stat(label, value, note) {
  return h("div", { class: "stat" }, h("b", { text: value }), h("span", { text: label }), note ? h("small", { text: note }) : null);
}
function table(headers, rows, opts = {}) {
  const head = h("tr", {}, headers.map((x) => h("th", { class: x.num ? "num" : "", text: x.label })));
  const body = h("tbody", {}, rows);
  return h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, head), body));
}
function cell(content, num) { return h("td", { class: num ? "num" : "" }, content); }

/* ------------------------------------------------------------------ charts */
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src; s.onload = resolve; s.onerror = () => reject(new Error("load failed"));
    document.head.append(s);
  });
}
const chartReady = (async () => {
  try { await loadScript("/static/js/vendor/chart.umd.min.js"); }
  catch (e) {
    try { await loadScript("https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"); } catch (e2) { /* offline */ }
  }
  return !!window.Chart;
})();

async function drawChart(box, config) {
  const ok = await chartReady;
  await new Promise((r) => setTimeout(r, 0)); // let the router attach the view first
  if (!box.isConnected) return;
  if (!ok) {
    box.replaceChildren(empty("Charts need Chart.js, which could not be loaded (offline?). The numbers above and the tables below still work. See README: Offline charts."));
    return;
  }
  const canvas = h("canvas");
  box.replaceChildren(canvas);
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--line");
  Chart.defaults.font.family = css("--font");
  state.charts.push(new Chart(canvas, { ...config, options: { responsive: true, maintainAspectRatio: false, ...(config.options || {}) } }));
}
function resetView() {
  state.charts.forEach((c) => c.destroy());
  state.charts = [];
  state.timers.forEach((t) => clearTimeout(t));
  state.timers = [];
}

/* ------------------------------------------------------------ run listing */
function runsTable(root, opts = {}) {
  const f = { q: "", status: "", suite: "", date_from: "", date_to: "", sort: "created_at", order: "desc", offset: 0 };
  const limit = opts.limit || 10;
  const body = h("div", {});
  let alive = true;

  const sortSel = h("select", { "aria-label": "Sort runs", onchange: (e) => { [f.sort, f.order] = e.target.value.split(":"); f.offset = 0; load(); } },
    [["created_at:desc", "Newest first"], ["created_at:asc", "Oldest first"], ["duration:desc", "Longest first"], ["failed:desc", "Most failures"]]
      .map(([v, l]) => h("option", { value: v, text: l })));
  const field = (label, input) => h("label", {}, label, input);
  const filters = h("div", { class: "row" },
    field("Search", h("input", { type: "search", class: "grow", placeholder: "Suite, target or message", oninput: (e) => { f.q = e.target.value; f.offset = 0; debounced(); } })),
    field("Status", h("select", { onchange: (e) => { f.status = e.target.value; f.offset = 0; load(); } },
      [["", "Any"], ["passed", "Passed"], ["failed", "Failed"], ["error", "Error"], ["timeout", "Timeout"], ["running", "Running"], ["queued", "Queued"]].map(([v, l]) => h("option", { value: v, text: l })))),
    field("Suite", h("select", { onchange: (e) => { f.suite = e.target.value; f.offset = 0; load(); } },
      [h("option", { value: "", text: "Any" }), ...state.meta.suites.map((s) => h("option", { value: s.key, text: s.label }))])),
    field("From", h("input", { type: "date", onchange: (e) => { f.date_from = e.target.value; f.offset = 0; load(); } })),
    field("To", h("input", { type: "date", onchange: (e) => { f.date_to = e.target.value; f.offset = 0; load(); } })),
    field("Sort", sortSel));
  let t;
  const debounced = () => { clearTimeout(t); t = setTimeout(load, 300); };

  async function load() {
    body.replaceChildren(loading());
    const qs = new URLSearchParams({ limit, offset: f.offset, sort: f.sort, order: f.order });
    for (const k of ["q", "status", "suite", "date_from", "date_to"]) if (f[k]) qs.set(k, f[k]);
    try {
      const data = await api(`/api/runs?${qs}`);
      if (!alive || !body.isConnected) return;
      if (!data.items.length) {
        const filtered = f.q || f.status || f.suite || f.date_from || f.date_to;
        body.replaceChildren(empty(filtered ? "No runs match these filters. Clear a filter to see more." : "No runs yet. Pick a suite on the Test runs page and start one."));
        return;
      }
      const rows = data.items.map((r) => {
        const tr = h("tr", { class: "click", tabindex: "0", "aria-label": `Open run ${r.id}`, onclick: () => openRun(r.id), onkeydown: (e) => { if (e.key === "Enter") openRun(r.id); } },
          cell(`#${r.id}`), cell(r.suite), cell(badge(r.status)),
          cell(`${r.passed}/${r.total}`, true), cell(String(r.failed + r.errors), true),
          cell(fmtDur(r.duration_s), true), cell(fmtDate(r.created_at)), cell(h("span", { class: "mono small", text: r.target_url })));
        return tr;
      });
      const pages = Math.ceil(data.total / limit), page = Math.floor(f.offset / limit) + 1;
      body.replaceChildren(
        table([{ label: "Run" }, { label: "Suite" }, { label: "Status" }, { label: "Passed", num: true }, { label: "Failed", num: true }, { label: "Duration", num: true }, { label: "Started" }, { label: "Target" }], rows),
        h("div", { class: "row small" },
          h("span", { class: "muted", text: `${data.total} run${data.total === 1 ? "" : "s"} · page ${page} of ${pages}` }),
          h("button", { class: "btn small", disabled: f.offset === 0, onclick: () => { f.offset = Math.max(0, f.offset - limit); load(); }, text: "Previous" }),
          h("button", { class: "btn small", disabled: f.offset + limit >= data.total, onclick: () => { f.offset += limit; load(); }, text: "Next" })));
      if (opts.autoRefresh && data.items.some(isActive)) state.timers.push(setTimeout(() => { if (alive && body.isConnected) load(); }, 2500));
    } catch (e) {
      if (body.isConnected) body.replaceChildren(errorBox(e, load));
    }
  }
  root.append(filters, h("div", { class: "spacer" }), body);
  load();
  return { reload: load, stop: () => { alive = false; } };
}

/* -------------------------------------------------------------- run detail */
function analysisView(out) {
  const wrap = h("div", { class: "card" });
  wrap.append(h("h3", { text: out.llm_used ? `AI analysis (${out.provider})` : "Rules-based analysis" }));
  if (out.summary) wrap.append(h("p", { text: out.summary }));
  if (out.facts && out.facts.length) wrap.append(h("h3", { text: "Facts from the recorded evidence" }), h("ul", { class: "facts" }, out.facts.map((x) => h("li", { text: x }))));
  if (out.hypotheses && out.hypotheses.length) {
    wrap.append(h("h3", { text: "Hypotheses (unverified)" }));
    out.hypotheses.forEach((x) => wrap.append(h("div", { class: "hyp" }, h("span", { class: "badge", text: `${x.confidence} confidence · ${x.source}` }), " ", x.text)));
  }
  if (out.next_steps && out.next_steps.length) wrap.append(h("h3", { text: "Suggested next steps" }), h("ol", { class: "steps" }, out.next_steps.map((x) => h("li", { text: x }))));
  if (out.notice) wrap.append(h("p", { class: "notice", text: out.notice }));
  return wrap;
}

async function openRun(id) {
  const dlg = $("#detail");
  dlg.replaceChildren(h("div", { class: "detail-body" }, loading()));
  if (!dlg.open) dlg.showModal();
  let filter = "all";
  async function render() {
    let run;
    try { run = await api(`/api/runs/${id}`); } catch (e) { dlg.replaceChildren(h("div", { class: "detail-body" }, errorBox(e, render))); return; }
    if (!dlg.open) return;
    const list = h("div", {});
    const results = run.results.filter((r) => filter === "all" || (filter === "failed" ? r.outcome === "failed" || r.outcome === "error" : r.outcome === filter));
    if (!run.results.length) list.append(empty(isActive(run) ? "Results appear when the run finishes." : "No individual results were recorded for this run."));
    else if (!results.length) list.append(empty("No results match this filter."));
    results.forEach((r) => {
      const bad = r.outcome === "failed" || r.outcome === "error";
      const aiBox = h("div", {});
      list.append(h("details", { class: "result", open: bad && results.length <= 6 },
        h("summary", {}, badge(r.outcome), h("span", { class: "mono", text: r.name }), h("span", { class: "muted small", text: fmtDur(r.duration_s) })),
        h("div", {},
          h("p", { class: "mono small muted", text: r.nodeid }),
          r.message ? h("pre", { text: r.message }) : null,
          r.details ? h("pre", { text: r.details }) : null,
          bad ? h("button", { class: "btn small", text: "Explain this failure", onclick: async (e) => {
            e.target.disabled = true; e.target.textContent = "Analysing…";
            try { const a = await api("/api/ai/explain-failure", { method: "POST", body: { result_id: r.id } }); aiBox.replaceChildren(analysisView(a.output)); e.target.remove(); }
            catch (err) { toast(err.message, "error"); e.target.disabled = false; e.target.textContent = "Explain this failure"; }
          } }) : null, aiBox)));
    });
    const filterSel = h("select", { "aria-label": "Filter results", onchange: (e) => { filter = e.target.value; render(); } },
      [["all", "All results"], ["failed", "Failures and errors"], ["passed", "Passed"], ["skipped", "Skipped"]].map(([v, l]) => h("option", { value: v, selected: v === filter, text: l })));
    const dl = (fmt) => h("a", { class: "btn small", href: `/api/reports/runs/${id}?format=${fmt}`, download: "", text: fmt.toUpperCase() });
    dlg.replaceChildren(
      h("div", { class: "detail-head" },
        h("div", {}, h("h2", { text: `Run #${run.id} · ${run.suite}` }), h("div", { class: "row" }, badge(run.status),
          h("span", { class: "muted small", text: `${run.passed} passed · ${run.failed + run.errors} failed · ${run.skipped} skipped · ${fmtDur(run.duration_s)}` }))),
        h("button", { class: "btn", onclick: () => dlg.close(), text: "Close" })),
      h("div", { class: "detail-body" },
        h("p", { class: "muted small" }, `Started ${fmtDate(run.started_at)} · target `, h("span", { class: "mono", text: run.target_url }), run.retries_used ? ` · ${run.retries_used} preflight retr${run.retries_used === 1 ? "y" : "ies"}` : ""),
        run.message ? h("div", { class: "errorbox", text: run.message }) : null,
        h("div", { class: "row" }, h("span", { class: "muted small", text: "Download report:" }), dl("html"), dl("json"), dl("csv"), h("span", { class: "grow" }), filterSel,
          isActive(run) ? null : h("button", { class: "btn small danger", text: "Delete run", onclick: async () => {
            if (!confirm(`Delete run #${id} and its results?`)) return;
            try { await api(`/api/runs/${id}`, { method: "DELETE" }); dlg.close(); toast("Run deleted", "ok"); route(); } catch (e) { toast(e.message, "error"); }
          } })),
        h("div", { class: "spacer" }), list,
        run.output_tail ? h("details", {}, h("summary", { class: "muted small", text: "Raw PyTest output (redacted)" }), h("pre", { text: run.output_tail })) : null));
    if (isActive(run)) state.timers.push(setTimeout(() => { if (dlg.open) render(); }, 2000));
  }
  render();
}

/* ------------------------------------------------------------------ views */
async function viewDashboard(root) {
  const [summary, recent] = await Promise.all([api("/api/analytics/summary?days=14"), api("/api/runs?limit=60")]);
  const t = summary.totals, lat = summary.latency_all_tests;
  root.append(h("div", { class: "stats" },
    stat("Runs in the last 14 days", String(t.runs)),
    stat("Test pass rate", fmtPct(t.test_pass_rate), `${t.tests_executed} tests executed`),
    stat("Fully green runs", fmtPct(t.run_pass_rate), `${t.green_runs} of ${t.runs}`),
    stat("Average run time", t.avg_duration_s === null ? "n/a" : `${t.avg_duration_s}s`),
    stat("Test duration p95", lat.p95 === null ? "n/a" : fmtDur(lat.p95), lat.p95 === null ? `needs 20+ samples (have ${lat.n})` : `${lat.n} samples`)));
  root.append(h("div", { class: "spacer" }), h("h2", { text: "Recent runs" }));
  const tape = h("div", { class: "tape", role: "list", "aria-label": "Most recent runs, oldest on the left" });
  const items = [...recent.items].reverse();
  if (!items.length) tape.append(h("span", { class: "tape-empty", text: "Each run will appear here as one tick: green passed, red failed, height shows duration." }));
  const maxDur = Math.max(1, ...items.map((r) => r.duration_s || 0));
  items.forEach((r) => {
    const tick = h("button", { class: `tick ${r.status}`, role: "listitem", title: `#${r.id} ${r.suite}: ${r.status}, ${fmtDur(r.duration_s)}`, "aria-label": `Run ${r.id}, ${r.suite}, ${r.status}`, onclick: () => openRun(r.id) });
    tick.style.height = `${10 + 40 * ((r.duration_s || 0) / maxDur)}px`;
    tape.append(tick);
  });
  root.append(tape, h("div", { class: "spacer" }));

  if (!summary.has_data) {
    root.append(empty("No finished runs in this period, so there is nothing to chart yet.", h("a", { class: "btn primary", href: "#/runs", text: "Start a test run" })));
  } else {
    const a = h("div", { class: "chart-box" }), b = h("div", { class: "chart-box" });
    root.append(h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h2", { text: "Tests per day" }), a),
      h("div", { class: "card" }, h("h2", { text: "Test duration per run (seconds)" }), b)));
    drawChart(a, dailyConfig(summary.daily));
    drawChart(b, latencyConfig(summary.per_run_latency));
    root.append(h("div", { class: "spacer" }));
  }
  root.append(h("h2", { text: "Run history" }));
  runsTable(root, { limit: 10 });
}

function dailyConfig(daily) {
  return { type: "bar", data: { labels: daily.map((d) => d.date.slice(5)), datasets: [
    { label: "Passed", data: daily.map((d) => d.passed), backgroundColor: css("--pass"), stack: "s" },
    { label: "Failed", data: daily.map((d) => d.failed), backgroundColor: css("--fail"), stack: "s" }] },
    options: { scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } } } } };
}
function latencyConfig(per) {
  const ds = [{ label: "Mean", data: per.map((r) => r.mean), borderColor: css("--info"), backgroundColor: css("--info"), tension: .25 }];
  if (per.some((r) => r.p95 !== null)) ds.push({ label: "p95", data: per.map((r) => r.p95), borderColor: css("--accent"), backgroundColor: css("--accent"), tension: .25, spanGaps: false });
  return { type: "line", data: { labels: per.map((r) => `#${r.run_id}`), datasets: ds }, options: { scales: { y: { beginAtZero: true } } } };
}

async function viewRuns(root) {
  const suiteSel = h("select", { id: "suite-sel" }, state.meta.suites.map((s) => h("option", { value: s.key, text: s.label })));
  const desc = h("p", { class: "muted small" });
  const envSel = h("select", {}, [h("option", { value: "", text: state.meta.demo_api_enabled ? "Built-in demo API (this server)" : "Choose an environment" }), ...state.envs.map((e) => h("option", { value: e.id, text: e.name }))]);
  const ext = h("input", { type: "checkbox" });
  const extLabel = h("label", { class: "inline", hidden: true }, ext, "I am authorised to test this external target");
  const sync = () => {
    desc.textContent = (state.meta.suites.find((s) => s.key === suiteSel.value) || {}).description || "";
    const env = state.envs.find((e) => String(e.id) === envSel.value);
    extLabel.hidden = !(env && env.allow_external);
  };
  suiteSel.addEventListener("change", sync); envSel.addEventListener("change", sync); sync();
  let table_;
  const start = h("button", { class: "btn primary", text: "Run suite", onclick: async (e) => {
    e.target.disabled = true;
    try {
      const run = await api("/api/runs", { method: "POST", body: { suite: suiteSel.value, environment_id: envSel.value ? Number(envSel.value) : null, authorized_external: ext.checked } });
      toast(`Run #${run.id} queued`, "ok"); table_.reload();
    } catch (err) { toast(err.message, "error"); } finally { e.target.disabled = false; }
  } });
  root.append(h("div", { class: "card" }, h("h2", { text: "Start a test run" }),
    h("div", { class: "row" }, h("label", {}, "Suite", suiteSel), h("label", {}, "Target", envSel), extLabel, start), desc,
    h("p", { class: "muted small", text: "Only the listed suites can run. Nothing typed here is ever executed as a command." })),
    h("div", { class: "spacer" }));
  table_ = runsTable(root, { limit: 10, autoRefresh: true });
}

/* key/value rows */
function kvEditor(initial = {}) {
  const box = h("div", {});
  const add = (k = "", v = "") => {
    const row = h("div", { class: "kv" }, h("input", { placeholder: "Name", value: k, "aria-label": "Name" }), h("input", { placeholder: "Value", value: v, "aria-label": "Value" }),
      h("button", { class: "btn ghost small", type: "button", "aria-label": "Remove row", text: "×", onclick: () => row.remove() }));
    box.append(row);
  };
  Object.entries(initial).forEach(([k, v]) => add(k, v));
  const el = h("div", {}, box, h("button", { class: "btn small", type: "button", text: "Add row", onclick: () => add() }));
  return { el, get: () => Object.fromEntries([...box.children].map((r) => [r.children[0].value.trim(), r.children[1].value]).filter(([k]) => k)) };
}

async function viewWorkbench(root) {
  let saved = await api("/api/requests");
  let current = null;
  const listBox = h("div", { class: "list" }), formBox = h("div", {}), respBox = h("div", {});
  const split = h("div", { class: "split" }, h("div", { class: "card" }, h("h2", { text: "Saved requests" }), h("button", { class: "btn small", text: "New request", onclick: () => edit(null) }), h("div", { class: "spacer" }), listBox), h("div", {}, formBox, h("div", { class: "spacer" }), respBox));
  root.append(split);

  function renderList() {
    if (!saved.length) { listBox.replaceChildren(h("p", { class: "muted small", text: "Nothing saved yet. Build a request and choose Save." })); return; }
    listBox.replaceChildren(...saved.map((r) => h("button", { type: "button", "aria-current": current && current.id === r.id ? "true" : "false", onclick: () => edit(r) },
      h("span", { class: "badge method", text: r.method }), h("span", { text: r.name }))));
  }

  function edit(r) {
    current = r; renderList(); respBox.replaceChildren();
    const params = kvEditor(r ? r.params : {}), headers = kvEditor(r ? r.headers : {});
    const name = h("input", { value: r ? r.name : "", placeholder: "Name to save under", class: "grow", maxlength: 120 });
    const method = h("select", {}, ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].map((m) => h("option", { value: m, selected: r && r.method === m, text: m })));
    const url = h("input", { class: "grow", value: r ? r.url : `${location.origin}/demo/health`, placeholder: "https://… or /path with an environment", "aria-label": "URL" });
    const env = h("select", {}, [h("option", { value: "", text: "No environment" }), ...state.envs.map((e) => h("option", { value: e.id, selected: r && r.environment_id === e.id, text: e.name }))]);
    const body = h("textarea", { rows: 7, placeholder: '{"key": "value"}', "aria-label": "JSON body", spellcheck: "false" });
    if (r && r.body !== null && r.body !== undefined) body.value = JSON.stringify(r.body, null, 2);
    const timeout = h("input", { type: "number", min: 0.5, max: 30, step: 0.5, value: r ? r.timeout : 10, "aria-label": "Timeout in seconds" });
    const authType = h("select", {}, [["none", "No authentication"], ["bearer", "Bearer token"], ["basic", "Basic auth"], ["api_key", "API key header"]].map(([v, l]) => h("option", { value: v, selected: r && r.auth.type === v, text: l })));
    const authUser = h("input", { placeholder: "Username", value: r ? r.auth.username : "", autocomplete: "off" });
    const authHeader = h("input", { placeholder: "Header name", value: r ? r.auth.header_name : "X-API-Key" });
    const authSecret = h("input", { type: "password", autocomplete: "new-password", placeholder: r && r.auth.has_secret ? "Saved. Leave blank to keep it" : "Token or password" });
    const ext = h("input", { type: "checkbox" });
    const authFields = h("div", { class: "row" }, authUser, authHeader, authSecret);
    const syncAuth = () => { authFields.hidden = authType.value === "none"; authUser.hidden = authType.value !== "basic"; authHeader.hidden = authType.value !== "api_key"; };
    authType.addEventListener("change", syncAuth); syncAuth();

    const payload = () => {
      let parsed = null;
      if (body.value.trim()) { try { parsed = JSON.parse(body.value); } catch (e) { throw new Error("The body is not valid JSON. Fix it or clear it to send no body."); } }
      return { method: method.value, url: url.value.trim(), params: params.get(), headers: headers.get(), body: parsed, timeout: Number(timeout.value) || 10,
        environment_id: env.value ? Number(env.value) : null,
        auth: { type: authType.value, username: authUser.value, header_name: authHeader.value || "X-API-Key", secret: authSecret.value || null } };
    };
    const send = h("button", { class: "btn primary", type: "button", text: "Send", onclick: async (e) => {
      let p; try { p = payload(); } catch (err) { toast(err.message, "error"); return; }
      e.target.disabled = true; e.target.textContent = "Sending…"; respBox.replaceChildren(loading());
      try { showResponse(await api("/api/workbench/execute", { method: "POST", body: { ...p, authorized_external: ext.checked } })); }
      catch (err) { respBox.replaceChildren(errorBox(err)); }
      finally { e.target.disabled = false; e.target.textContent = "Send"; }
    } });
    const save = h("button", { class: "btn", type: "button", text: r ? "Save changes" : "Save", onclick: async () => {
      let p; try { p = payload(); } catch (err) { toast(err.message, "error"); return; }
      if (!name.value.trim()) { toast("Enter a name before saving.", "error"); name.focus(); return; }
      try {
        const out = await api(r ? `/api/requests/${r.id}` : "/api/requests", { method: r ? "PUT" : "POST", body: { ...p, name: name.value.trim() } });
        saved = await api("/api/requests"); toast("Request saved", "ok"); edit(saved.find((x) => x.id === out.id));
      } catch (err) { toast(err.message, "error"); }
    } });
    const del = r ? h("button", { class: "btn danger", type: "button", text: "Delete", onclick: async () => {
      if (!confirm(`Delete saved request "${r.name}"?`)) return;
      try { await api(`/api/requests/${r.id}`, { method: "DELETE" }); saved = await api("/api/requests"); toast("Request deleted", "ok"); edit(null); } catch (err) { toast(err.message, "error"); }
    } }) : null;

    formBox.replaceChildren(h("div", { class: "card" },
      h("div", { class: "row" }, h("label", { class: "grow" }, "Name", name), h("label", {}, "Environment", env)),
      h("div", { class: "spacer" }),
      h("div", { class: "row" }, h("label", {}, "Method", method), h("label", { class: "grow" }, "URL", url), send),
      h("div", { class: "spacer" }),
      h("div", { class: "grid cols-2" }, h("div", {}, h("h3", { text: "Query parameters" }), params.el), h("div", {}, h("h3", { text: "Headers" }), headers.el)),
      h("div", { class: "spacer" }),
      h("div", { class: "grid cols-2" },
        h("div", {}, h("h3", { text: "Authentication" }), authType, h("div", { class: "spacer" }), authFields,
          h("p", { class: "muted small", text: "Secrets are encrypted when saved and are never shown again." })),
        h("div", {}, h("h3", { text: "JSON body" }), body)),
      h("div", { class: "spacer" }),
      h("div", { class: "row" }, h("label", {}, "Timeout (s)", timeout), h("label", { class: "inline" }, ext, "I am authorised to test an external host"), h("span", { class: "grow" }), save, del)));
  }

  function showResponse(res) {
    const klass = res.status_code < 400 ? "passed" : "failed";
    const text = res.body_json !== null && res.body_json !== undefined ? JSON.stringify(res.body_json, null, 2) : res.body_text;
    const hdr = Object.entries(res.headers).map(([k, v]) => h("tr", {}, h("td", { class: "mono", text: k }), h("td", { class: "mono", text: String(v) })));
    respBox.replaceChildren(h("div", { class: "card" },
      h("div", { class: "row" }, h("span", { class: `badge ${klass}`, text: `${res.status_code} ${res.reason || ""}` }),
        h("span", { class: "muted", text: `${fmtMs(res.duration_ms)} · ${res.size_bytes} bytes${res.truncated ? " (truncated)" : ""}` }),
        res.redirects.length ? h("span", { class: "muted small", text: `${res.redirects.length} redirect(s) followed` }) : null),
      h("h3", { text: "Body" }), h("pre", { text: text || "(empty body)" }),
      h("details", {}, h("summary", { class: "muted", text: `Headers (${hdr.length}). Sensitive values are redacted.` }), h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, hdr))))));
  }
  renderList(); edit(null);
}

async function viewPerf(root) {
  const [limits, history] = await Promise.all([api("/api/perf/limits"), api("/api/perf/runs")]);
  if (!limits.enabled) { root.append(empty("Performance testing is turned off (PERF_TESTING_ENABLED=false).")); return; }
  const out = h("div", {}), hist = h("div", {});
  const url = h("input", { class: "grow", value: `${location.origin}/demo/health`, "aria-label": "Target URL" });
  const method = h("select", {}, ["GET", "HEAD", "POST"].map((m) => h("option", { value: m, text: m })));
  const total = h("input", { type: "number", min: 1, max: limits.max_requests, value: Math.min(100, limits.max_requests) });
  const conc = h("input", { type: "number", min: 1, max: limits.max_concurrency, value: Math.min(5, limits.max_concurrency) });
  const timeout = h("input", { type: "number", min: 0.5, max: 30, step: 0.5, value: 5 });
  const env = h("select", {}, [h("option", { value: "", text: "No environment" }), ...state.envs.map((e) => h("option", { value: e.id, text: e.name }))]);
  const ext = h("input", { type: "checkbox" });
  const run = h("button", { class: "btn primary", text: "Start load test", onclick: async (e) => {
    e.target.disabled = true; e.target.textContent = "Running…"; out.replaceChildren(loading());
    try {
      const r = await api("/api/perf/run", { method: "POST", body: { url: url.value.trim(), method: method.value, total_requests: Number(total.value), concurrency: Number(conc.value), timeout: Number(timeout.value), environment_id: env.value ? Number(env.value) : null, authorized_external: ext.checked } });
      showPerf(r); loadHist();
    } catch (err) { out.replaceChildren(errorBox(err)); } finally { e.target.disabled = false; e.target.textContent = "Start load test"; }
  } });
  root.append(h("div", { class: "card" }, h("h2", { text: "Load test" }),
    h("div", { class: "row" }, h("label", {}, "Method", method), h("label", { class: "grow" }, "URL", url)),
    h("div", { class: "spacer" }),
    h("div", { class: "row" }, h("label", {}, `Requests (max ${limits.max_requests})`, total), h("label", {}, `Concurrency (max ${limits.max_concurrency})`, conc), h("label", {}, "Timeout (s)", timeout), h("label", {}, "Environment", env),
      h("label", { class: "inline" }, ext, "I am authorised to load-test an external host"), run),
    h("p", { class: "muted small", text: `Local targets are the default. Runs stop after ${limits.max_duration_s}s. External hosts are ${limits.external_enabled ? "enabled but still need the authorisation box" : "disabled by server configuration"}.` })),
    h("div", { class: "spacer" }), out, h("div", { class: "spacer" }), h("h2", { text: "Previous load tests" }), hist);

  function showPerf(p) {
    const l = p.metrics.latency_ms || {};
    const chartBox = h("div", { class: "chart-box" });
    out.replaceChildren(h("div", { class: "card" }, h("h2", { text: `Result #${p.id}` }),
      h("div", { class: "stats" }, stat("Throughput", `${p.throughput_rps} req/s`), stat("Error rate", `${p.error_rate}%`, `${p.failed} of ${p.completed} failed`),
        stat("Completed", `${p.completed}/${p.total_requests}`, p.status === "stopped_early" ? "stopped at the safety limit" : null), stat("Elapsed", `${p.duration_s}s`)),
      h("div", { class: "spacer" }),
      table([{ label: "Latency" }, { label: "n", num: true }, { label: "min", num: true }, { label: "mean", num: true }, { label: "p50", num: true }, { label: "p95", num: true }, { label: "p99", num: true }, { label: "max", num: true }],
        [h("tr", {}, cell("ms"), cell(String(l.n || 0), true), ...["min", "mean", "p50", "p95", "p99", "max"].map((k) => cell(fmtMs(l[k]), true)))]),
      (l.notes || []).length ? h("p", { class: "notice", text: (l.notes || []).join(" ") }) : null,
      h("p", { class: "small" }, "Status codes: ", Object.entries(p.metrics.status_counts || {}).map(([k, v]) => `${k} × ${v}`).join(", ") || "none"),
      Object.keys(p.metrics.error_kinds || {}).length ? h("p", { class: "small error", text: `Network errors: ${Object.entries(p.metrics.error_kinds).map(([k, v]) => `${k} × ${v}`).join(", ")}` }) : null,
      p.note ? h("p", { class: "notice", text: p.note }) : null,
      l.n ? chartBox : null));
    if (l.n) drawChart(chartBox, { type: "bar", data: { labels: ["min", "mean", "p50", "p95", "p99", "max"].filter((k) => l[k] !== null && l[k] !== undefined), datasets: [{ label: "Latency (ms)", data: ["min", "mean", "p50", "p95", "p99", "max"].filter((k) => l[k] !== null && l[k] !== undefined).map((k) => l[k]), backgroundColor: css("--info") }] }, options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } } });
  }
  async function loadHist() {
    try {
      const items = await api("/api/perf/runs");
      if (!hist.isConnected) return;
      if (!items.length) { hist.replaceChildren(empty("No load tests yet.")); return; }
      hist.replaceChildren(table([{ label: "Run" }, { label: "Target" }, { label: "Requests", num: true }, { label: "Concurrency", num: true }, { label: "req/s", num: true }, { label: "Errors", num: true }, { label: "p95", num: true }, { label: "When" }],
        items.map((p) => h("tr", { class: "click", tabindex: "0", onclick: () => { showPerf(p); window.scrollTo({ top: 0, behavior: "smooth" }); }, onkeydown: (e) => { if (e.key === "Enter") showPerf(p); } },
          cell(`#${p.id}`), cell(h("span", { class: "mono small", text: `${p.method} ${p.target_url}` })), cell(String(p.completed), true), cell(String(p.concurrency), true), cell(String(p.throughput_rps), true),
          cell(`${p.error_rate}%`, true), cell(fmtMs(((p.metrics || {}).latency_ms || {}).p95), true), cell(fmtDate(p.created_at))))));
    } catch (e) { hist.replaceChildren(errorBox(e, loadHist)); }
  }
  loadHist(); void history;
}

async function viewAnalytics(root) {
  const body = h("div", {});
  let days = 14;
  const sel = h("select", { "aria-label": "Period", onchange: (e) => { days = Number(e.target.value); load(); } }, [7, 14, 30, 90].map((d) => h("option", { value: d, selected: d === days, text: `Last ${d} days` })));
  root.append(h("div", { class: "row" }, h("label", {}, "Period", sel)), h("div", { class: "spacer" }), body);

  async function load() {
    state.charts.forEach((c) => c.destroy()); state.charts = [];
    body.replaceChildren(loading());
    try {
      const [s, flaky, runs] = await Promise.all([api(`/api/analytics/summary?days=${days}`), api("/api/analytics/flaky"), api("/api/runs?limit=50")]);
      if (!body.isConnected) return;
      const l = s.latency_all_tests;
      body.replaceChildren(h("div", { class: "stats" },
        stat("Test pass rate", fmtPct(s.totals.test_pass_rate), `${s.totals.tests_executed} tests`), stat("Run pass rate", fmtPct(s.totals.run_pass_rate), `${s.totals.runs} runs`),
        stat("p50 test duration", l.p50 === null ? "n/a" : fmtDur(l.p50)), stat("p95 test duration", l.p95 === null ? "n/a" : fmtDur(l.p95)), stat("p99 test duration", l.p99 === null ? "n/a" : fmtDur(l.p99))));
      if (l.notes.length) body.append(h("p", { class: "notice", text: l.notes.join(" ") }));
      if (!s.has_data) body.append(h("div", { class: "spacer" }), empty("No finished runs in this period."));
      else {
        const a = h("div", { class: "chart-box" }), b = h("div", { class: "chart-box" });
        body.append(h("div", { class: "spacer" }), h("div", { class: "grid cols-2" }, h("div", { class: "card" }, h("h2", { text: "Pass rate per day (%)" }), a), h("div", { class: "card" }, h("h2", { text: "Test duration per run (seconds)" }), b)));
        drawChart(a, { type: "line", data: { labels: s.daily.map((d) => d.date.slice(5)), datasets: [{ label: "Pass rate", data: s.daily.map((d) => d.pass_rate), borderColor: css("--pass"), backgroundColor: css("--pass"), spanGaps: true, tension: .2 }] }, options: { scales: { y: { min: 0, max: 100 } } } });
        drawChart(b, latencyConfig(s.per_run_latency));
      }
      if (s.perf_trend.length) {
        const c = h("div", { class: "chart-box" });
        body.append(h("div", { class: "spacer" }), h("div", { class: "card" }, h("h2", { text: "Load test latency trend (ms)" }), c));
        drawChart(c, { type: "line", data: { labels: s.perf_trend.map((p) => `#${p.id}`), datasets: [{ label: "p50", data: s.perf_trend.map((p) => p.p50), borderColor: css("--info") }, { label: "p95", data: s.perf_trend.map((p) => p.p95), borderColor: css("--accent") }] }, options: { scales: { y: { beginAtZero: true } } } });
      }
      body.append(h("div", { class: "spacer" }), compareCard(runs.items), h("div", { class: "spacer" }), flakyCard(flaky));
    } catch (e) { body.replaceChildren(errorBox(e, load)); }
  }
  load();
}

function compareCard(runs) {
  const result = h("div", {});
  if (runs.length < 2) return h("div", { class: "card" }, h("h2", { text: "Compare runs" }), empty("You need at least two finished runs to compare."));
  const opts = (sel) => runs.map((r, i) => h("option", { value: r.id, selected: i === sel, text: `#${r.id} ${r.suite} · ${r.status} · ${fmtDate(r.created_at)}` }));
  const a = h("select", {}, opts(1)), b = h("select", {}, opts(0));
  const list = (title, items, fmt) => items.length ? h("div", {}, h("h3", { text: `${title} (${items.length})` }), h("ul", {}, items.map((x) => h("li", { class: "mono small", text: fmt ? fmt(x) : x })))) : null;
  return h("div", { class: "card" }, h("h2", { text: "Compare two runs" }),
    h("div", { class: "row" }, h("label", { class: "grow" }, "Baseline", a), h("label", { class: "grow" }, "Candidate", b),
      h("button", { class: "btn", text: "Compare", onclick: async () => {
        result.replaceChildren(loading());
        try {
          const c = await api(`/api/analytics/compare?a=${a.value}&b=${b.value}`), d = c.diff;
          result.replaceChildren(h("p", {}, `Passed ${c.delta.passed >= 0 ? "+" : ""}${c.delta.passed}, failed ${c.delta.failed >= 0 ? "+" : ""}${c.delta.failed}, duration ${c.delta.duration_s === null ? "n/a" : `${c.delta.duration_s >= 0 ? "+" : ""}${c.delta.duration_s}s`} · ${d.common_tests} tests in common`),
            list("Newly failing", d.newly_failing), list("Newly passing", d.newly_passing), list("Still failing", d.still_failing),
            list("Slower", d.slower, (x) => `${x.nodeid}: ${fmtDur(x.before_s)} → ${fmtDur(x.after_s)} (×${x.factor})`), list("Only in baseline", d.only_in_a), list("Only in candidate", d.only_in_b),
            !d.newly_failing.length && !d.newly_passing.length && !d.slower.length ? h("p", { class: "muted", text: "No outcome or timing differences found." }) : null);
        } catch (e) { result.replaceChildren(errorBox(e)); }
      } })), h("div", { class: "spacer" }), result);
}

function flakyCard(f) {
  const card = h("div", { class: "card" }, h("h2", { text: "Potentially flaky tests" }));
  if (!f.flaky_tests.length) { card.append(empty(`No flaky tests found among ${f.tests_tracked} tracked tests. A test needs at least 4 runs with both passes and failures (switching at least twice).`)); return card; }
  card.append(table([{ label: "Test" }, { label: "Runs", num: true }, { label: "Failures", num: true }, { label: "Flip rate", num: true }, { label: "Recent" }],
    f.flaky_tests.map((t) => h("tr", {}, cell(h("span", { class: "mono small", text: t.nodeid })), cell(String(t.runs), true), cell(String(t.failures), true), cell(`${Math.round(t.flip_rate * 100)}%`, true),
      cell(h("span", {}, t.recent.map((o) => h("span", { class: `badge ${o === "passed" ? "passed" : "failed"}`, text: o === "passed" ? "P" : "F", title: o }), " ")))))));
  return card;
}

async function viewAI(root) {
  const status = state.meta.ai;
  const panel = h("div", {});
  const tabs = [["explain", "Explain a failure"], ["generate", "Generate test cases"], ["missing", "Find missing cases"], ["flaky", "Flaky tests"], ["history", "History"]];
  let active = "explain";
  const bar = h("div", { class: "tabs", role: "tablist" });
  const draw = () => {
    bar.replaceChildren(...tabs.map(([k, l]) => h("button", { role: "tab", "aria-selected": String(k === active), text: l, onclick: () => { active = k; draw(); show(); } })));
  };
  root.append(h("p", { class: "muted" }, status.mode === "llm" ? `AI provider connected: ${status.model}. Sensitive data is stripped before anything is sent.` : "Running in rules-based mode (no AI key configured). Everything below works offline; see the README to add an AI provider."), bar, panel);
  draw(); show();

  async function show() {
    panel.replaceChildren(loading());
    try { await ({ explain: tabExplain, generate: tabGenerate, missing: tabMissing, flaky: tabFlaky, history: tabHistory })[active](panel); }
    catch (e) { panel.replaceChildren(errorBox(e, show)); }
  }
}

async function tabExplain(p) {
  const runs = (await api("/api/runs?limit=50&status=failed")).items;
  if (!runs.length) { p.replaceChildren(empty("No failed runs yet. Run the “Demo: failures and flakiness” suite to try this.", h("a", { class: "btn primary", href: "#/runs", text: "Go to test runs" }))); return; }
  const out = h("div", {}), testSel = h("select", { class: "grow" });
  const runSel = h("select", { onchange: loadTests }, runs.map((r) => h("option", { value: r.id, text: `#${r.id} ${r.suite} · ${fmtDate(r.created_at)}` })));
  async function loadTests() {
    const run = await api(`/api/runs/${runSel.value}`);
    const bad = run.results.filter((r) => r.outcome === "failed" || r.outcome === "error");
    testSel.replaceChildren(...bad.map((r) => h("option", { value: r.id, text: r.name })));
  }
  p.replaceChildren(h("div", { class: "card" }, h("div", { class: "row" }, h("label", {}, "Run", runSel), h("label", { class: "grow" }, "Failed test", testSel),
    h("button", { class: "btn primary", text: "Explain", onclick: async (e) => {
      if (!testSel.value) return;
      e.target.disabled = true;
      try { out.replaceChildren(analysisView((await api("/api/ai/explain-failure", { method: "POST", body: { result_id: Number(testSel.value) } })).output)); } catch (err) { out.replaceChildren(errorBox(err)); }
      e.target.disabled = false;
    } }))), h("div", { class: "spacer" }), out);
  await loadTests();
}

async function tabGenerate(p) {
  const out = h("div", {});
  const req = h("textarea", { rows: 6, "aria-label": "Requirement", placeholder: "Example: POST /users creates a user. name is required and must be a string at most 50 characters. email is required and must be an email. age must be between 0 and 120. Returns 201 on success." });
  p.replaceChildren(h("div", { class: "card" }, h("label", {}, "Describe the API behaviour in plain English", req), h("div", { class: "spacer" }),
    h("button", { class: "btn primary", text: "Generate draft cases", onclick: async (e) => {
      e.target.disabled = true; out.replaceChildren(loading());
      try {
        const r = (await api("/api/ai/generate-tests", { method: "POST", body: { requirement: req.value } })).output;
        out.replaceChildren(h("div", { class: "card" }, h("h2", { text: `${r.cases.length} draft cases (${r.llm_used ? r.provider : "rules"})` }),
          r.assumptions.length ? h("ul", {}, r.assumptions.map((a) => h("li", { class: "muted", text: a }))) : null,
          table([{ label: "Category" }, { label: "Case" }, { label: "Request" }, { label: "Expect" }], r.cases.map((c) => h("tr", {}, cell(badge2(c.category)), cell(c.title), cell(h("span", { class: "mono small", text: `${c.method} ${c.path}` })), cell(c.expected_status.join(" / "))))),
          h("h3", { text: "Draft pytest module (text only, never executed by the dashboard)" }), h("pre", { text: r.pytest_draft }),
          h("button", { class: "btn small", text: "Copy draft", onclick: () => navigator.clipboard.writeText(r.pytest_draft).then(() => toast("Copied", "ok"), () => toast("Copy failed. Select the text manually.", "error")) }),
          h("p", { class: "notice", text: r.notice })));
      } catch (err) { out.replaceChildren(errorBox(err)); }
      e.target.disabled = false;
    } })), h("div", { class: "spacer" }), out);
}
const badge2 = (t) => h("span", { class: "badge", text: t });

async function tabMissing(p) {
  const runs = (await api("/api/runs?limit=50")).items;
  const out = h("div", {});
  const sel = h("select", {}, [h("option", { value: "saved", text: "Saved workbench requests" }), ...runs.map((r) => h("option", { value: r.id, text: `Run #${r.id} ${r.suite}` }))]);
  p.replaceChildren(h("div", { class: "card" }, h("div", { class: "row" }, h("label", { class: "grow" }, "Analyse the test names from", sel),
    h("button", { class: "btn primary", text: "Find gaps", onclick: async (e) => {
      e.target.disabled = true;
      try {
        const body = sel.value === "saved" ? { saved_requests: true } : { run_id: Number(sel.value) };
        const r = (await api("/api/ai/suggest-missing", { method: "POST", body })).output;
        out.replaceChildren(h("div", { class: "card" }, h("h2", { text: `${r.tests_examined} tests examined` }),
          h("p", { class: "small" }, "Already covered: ", r.covered_categories.join(", ") || "nothing detected"),
          r.missing.length ? table([{ label: "Missing area" }, { label: "Why add it" }, { label: "Example name" }], r.missing.map((m) => h("tr", {}, cell(m.category), cell(m.suggestion), cell(h("span", { class: "mono small", text: m.example_test_name }))))) : empty("All areas look covered."),
          (r.extra_suggestions || []).length ? h("ul", {}, r.extra_suggestions.map((x) => h("li", { text: x }))) : null, h("p", { class: "notice", text: r.notice })));
      } catch (err) { out.replaceChildren(errorBox(err)); }
      e.target.disabled = false;
    } }))), h("div", { class: "spacer" }), out);
}

async function tabFlaky(p) {
  const out = h("div", {}), n = h("input", { type: "number", min: 4, max: 200, value: 30 });
  p.replaceChildren(h("div", { class: "card" }, h("div", { class: "row" }, h("label", {}, "Runs to consider", n),
    h("button", { class: "btn primary", text: "Analyse history", onclick: async (e) => {
      e.target.disabled = true;
      try {
        const r = (await api("/api/ai/flaky-analysis", { method: "POST", body: { last_runs: Number(n.value) } })).output;
        out.replaceChildren(h("div", { class: "card" }, h("h2", { text: `${r.flaky_tests.length} potentially flaky test(s)` }), r.summary ? h("p", { text: r.summary }) : null,
          r.flaky_tests.map((t) => h("div", { class: "hyp fact" }, h("b", { class: "mono small", text: t.nodeid }), h("p", { class: "small", text: t.facts[0] }), h("ul", { class: "small" }, t.hypotheses.map((x) => h("li", { text: `Hypothesis: ${x.text}` }))))),
          h("p", { class: "notice", text: r.notice })));
      } catch (err) { out.replaceChildren(errorBox(err)); }
      e.target.disabled = false;
    } }))), h("div", { class: "spacer" }), out);
}

async function tabHistory(p) {
  const items = await api("/api/ai/history");
  if (!items.length) { p.replaceChildren(empty("No saved analyses yet. Results from the other tabs are stored here.")); return; }
  p.replaceChildren(...items.map((a) => h("details", { class: "result" },
    h("summary", {}, badge2(a.kind.replace("_", " ")), h("span", { text: a.input_summary }), h("span", { class: "muted small", text: `${a.provider} · ${fmtDate(a.created_at)}` })),
    h("div", {}, a.kind === "explain_failure" ? analysisView(a.output) : h("pre", { text: JSON.stringify(a.output, null, 2).slice(0, 6000) })))));
}

async function viewEnvs(root) {
  const policy = state.meta.policy;
  const formBox = h("div", {}), listBox = h("div", {});
  root.append(h("p", { class: "muted" }, `Target policy: local targets ${policy.allow_local ? "allowed" : "blocked"}, private networks ${policy.allow_private ? "allowed" : "blocked"}, external hosts ${policy.allow_external ? "allowed with explicit authorisation" : "blocked"}. Change this in .env.`),
    h("div", { class: "grid cols-2" }, h("div", { class: "card" }, h("h2", { text: "Environments" }), listBox), formBox));
  let envs = state.envs;
  function form(e) {
    const name = h("input", { value: e ? e.name : "", maxlength: 60, required: true });
    const url = h("input", { value: e ? e.base_url : "", placeholder: "http://127.0.0.1:8000", required: true });
    const desc = h("input", { value: e ? e.description : "", maxlength: 500 });
    const ext = h("input", { type: "checkbox", checked: e && e.allow_external });
    const hdr = h("textarea", { rows: 4, placeholder: '{"X-Team": "qa"}  (do not put secrets here)', spellcheck: "false" });
    if (e && Object.keys(e.headers).length) hdr.value = JSON.stringify(e.headers, null, 2);
    formBox.replaceChildren(h("div", { class: "card" }, h("h2", { text: e ? `Edit ${e.name}` : "New environment" }),
      h("div", { class: "grid" }, h("label", {}, "Name", name), h("label", {}, "Base URL", url), h("label", {}, "Description", desc),
        h("label", { class: "inline" }, ext, "Allow tests against an external host (still needs per-run authorisation)"), h("label", {}, "Default headers (JSON)", hdr)),
      h("div", { class: "spacer" }),
      h("div", { class: "row" }, h("button", { class: "btn primary", text: e ? "Save changes" : "Create environment", onclick: async () => {
        let headers = {};
        if (hdr.value.trim()) { try { headers = JSON.parse(hdr.value); } catch (x) { toast("Headers must be valid JSON.", "error"); return; } }
        try {
          await api(e ? `/api/environments/${e.id}` : "/api/environments", { method: e ? "PUT" : "POST", body: { name: name.value.trim(), base_url: url.value.trim(), description: desc.value, allow_external: ext.checked, headers } });
          await refresh(); toast("Environment saved", "ok"); form(null);
        } catch (err) { toast(err.message, "error"); }
      } }), e ? h("button", { class: "btn", text: "Cancel", onclick: () => form(null) }) : null)));
  }
  async function refresh() {
    envs = state.envs = await api("/api/environments");
    if (!envs.length) { listBox.replaceChildren(empty("No environments yet. The built-in demo API is used when none is selected.")); return; }
    listBox.replaceChildren(table([{ label: "Name" }, { label: "Base URL" }, { label: "" }], envs.map((e) => h("tr", {}, cell(e.name), cell(h("span", { class: "mono small", text: e.base_url })),
      cell(h("span", { class: "row" }, h("button", { class: "btn small", text: "Edit", onclick: () => form(e) }), h("button", { class: "btn small danger", text: "Delete", onclick: async () => {
        if (!confirm(`Delete environment "${e.name}"?`)) return;
        try { await api(`/api/environments/${e.id}`, { method: "DELETE" }); await refresh(); toast("Environment deleted", "ok"); } catch (err) { toast(err.message, "error"); }
      } })))))));
  }
  await refresh(); form(null);
}

/* ------------------------------------------------------------ router/login */
const ROUTES = [
  { id: "dashboard", title: "Dashboard", render: viewDashboard },
  { id: "runs", title: "Test runs", render: viewRuns },
  { id: "workbench", title: "Request workbench", render: viewWorkbench },
  { id: "perf", title: "Performance", render: viewPerf },
  { id: "analytics", title: "Analytics", render: viewAnalytics },
  { id: "ai", title: "AI assistant", render: viewAI },
  { id: "envs", title: "Environments", render: viewEnvs },
];

async function route() {
  if (!state.user) return;
  const id = (location.hash.replace(/^#\//, "") || "dashboard");
  const r = ROUTES.find((x) => x.id === id) || ROUTES[0];
  resetView();
  $("#sidebar").classList.remove("open"); $("#menu-btn").setAttribute("aria-expanded", "false");
  $("#view-title").textContent = r.title; document.title = `${r.title} · API Testing Dashboard`;
  document.querySelectorAll("#nav a").forEach((a) => a.setAttribute("aria-current", a.dataset.id === r.id ? "page" : "false"));
  const view = $("#view");
  view.replaceChildren(loading());
  const holder = h("div", {});
  try {
    await r.render(holder);
    if (location.hash.replace(/^#\//, "") === r.id || (!location.hash && r.id === "dashboard")) view.replaceChildren(holder);
  } catch (e) { view.replaceChildren(errorBox(e, route)); }
}

function showLogin() {
  state.user = null; resetView();
  $("#app").hidden = true; $("#login").hidden = false; $("#detail").open && $("#detail").close();
  $("#login-form").hidden = false; $("#register-form").hidden = true;
  $("#login-user").focus();
}

async function showApp(username) {
  state.user = username;
  [state.meta, state.envs] = await Promise.all([api("/api/meta"), api("/api/environments")]);
  $("#nav").replaceChildren(...ROUTES.map((r) => h("a", { href: `#/${r.id}`, "data-id": r.id, text: r.title })));
  $("#user-name").textContent = username;
  $("#login").hidden = true; $("#app").hidden = false;
  route();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#login-error"); err.hidden = true;
  try {
    const r = await api("/api/auth/login", { method: "POST", body: { username: $("#login-user").value, password: $("#login-pass").value } });
    $("#login-pass").value = ""; await showApp(r.username);
  } catch (x) { err.textContent = x.message; err.hidden = false; }
});
$("#show-register").addEventListener("click", () => {
  $("#login-form").hidden = true; $("#register-form").hidden = false; $("#register-error").hidden = true; $("#register-user").focus();
});
$("#show-login").addEventListener("click", showLogin);
$("#register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#register-error"); err.hidden = true;
  const username = $("#register-user").value.trim();
  const password = $("#register-pass").value;
  const confirmPassword = $("#register-confirm").value;
  if (password !== confirmPassword) { err.textContent = "Passwords do not match."; err.hidden = false; return; }
  try {
    await api("/api/auth/register", { method: "POST", body: { username, password, confirm_password: confirmPassword } });
    const login = await api("/api/auth/login", { method: "POST", body: { username, password } });
    $("#register-pass").value = ""; $("#register-confirm").value = ""; await showApp(login.username);
    toast("Account created", "ok");
  } catch (x) { err.textContent = x.message; err.hidden = false; }
});
$("#logout").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ } showLogin(); });
$("#menu-btn").addEventListener("click", () => {
  const open = $("#sidebar").classList.toggle("open"); $("#menu-btn").setAttribute("aria-expanded", String(open));
});
window.addEventListener("hashchange", route);
(async () => {
  try { const me = await api("/api/auth/me"); await showApp(me.username); } catch (e) { if (!state.user) showLogin(); }
})();
