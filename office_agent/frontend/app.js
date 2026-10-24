"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  data: null,
  csrf: null,
  active: null,
  expanded: false,
  drawerFocus: null,
};
const names = {
  vacancy_rate: "Vacancy / availability (all grades)",
  grade_a_vacancy_rate: "Grade A vacancy rate",
  prime_rent_psf: "Prime headline rent",
  grade_a_rent_psf: "Average achieved Grade A rent",
  take_up_sqft: "Leasing take-up in period",
  take_up_ytd_sqft: "Year-to-date take-up",
  investment_volume_gbp: "Office investment volume",
  prime_rent_growth_yoy: "Prime rent growth (year on year)",
  prime_rent_growth_qoq: "Prime rent growth (quarter on quarter)",
  under_offer_sqft: "Space under offer",
  active_demand_change_qoq: "Active demand change (quarter on quarter)",
  active_demand_change_mom: "Active demand change (month on month)",
  under_construction_spec_sqft: "Speculative space under construction",
  new_starts_sqft: "New construction starts",
  refurb_share_of_starts: "Refurbishment share of starts",
  completions_sqft: "Completions",
  pre_let_share_of_completions: "Pre-let share of completions",
  ai_sector_take_up_sqft: "AI-sector take-up",
};
const markets = {
  "Central London": "Central London",
  City: "City",
  "West End": "West End",
  "Canary Wharf": "Canary Wharf",
  Midtown: "Midtown",
  "King's Cross": "King's Cross",
  Southbank: "Southbank",
  "Fringe / Tech Belt": "Fringe / Tech Belt",
};
const macroNames = {
  bank_rate: "UK Bank Rate",
  sonia: "SONIA overnight rate",
  gilt_10y: "10-year gilt yield",
  cpi: "CPI inflation (annual)",
  unemployment: "Unemployment rate",
  gdp_qoq: "GDP growth (quarterly)",
};
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  chat: '<path d="M21 11a8 8 0 0 1-8 8H6l-4 3V11a9 9 0 0 1 19 0Z"/><path d="M7 9h10M7 13h6"/>',
  chart: '<path d="M4 3v17h17M8 15V9m5 6V5m5 10v-4"/>',
  document: '<path d="M14 2H5v20h14V7ZM14 2v6h5M8 12h8M8 16h8"/>',
  database:
    '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  refresh: '<path d="M20 8a9 9 0 1 0 1 7M20 3v6h-6"/>',
  spark: '<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3ZM20 2v4m-2-2h4"/>',
  shield: '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6ZM8 12l3 3 5-6"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  close: '<path d="m5 5 14 14M19 5 5 19"/>',
};
document.querySelectorAll("[data-icon]").forEach((el) => {
  el.innerHTML = `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[el.dataset.icon] || ""}</svg>`;
});
function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function clear(id) {
  const el = typeof id === "string" ? $(id) : id;
  el.replaceChildren();
  el.classList.remove("skeleton-block");
  return el;
}
function hide(id, hidden = true) {
  $(id).classList.toggle("hidden", hidden);
}
function toast(message) {
  $("toast").textContent = message;
  hide("toast", false);
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => hide("toast"), 3500);
}
function date(value) {
  if (!value) return "Date not stated";
  const parsed = new Date(value);
  return Number.isNaN(+parsed)
    ? value
    : new Intl.DateTimeFormat("en-GB", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        timeZone: "Asia/Hong_Kong",
      }).format(parsed);
}
function value(row) {
  const n = row.value ?? row.latest;
  if (!Number.isFinite(n)) return "—";
  const f = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 }).format(
    n,
  );
  if (row.unit === "%") return `${f}%`;
  if (row.unit?.startsWith("GBP/sq")) return `£${f} / ft² / year`;
  if (row.unit === "GBP") return `£${f}`;
  if (row.unit === "sq ft") return `${f} ft²`;
  return `${f} ${row.unit || ""}`;
}
function safeLink(url, label) {
  try {
    const u = new URL(url);
    if (u.protocol !== "https:" || u.username || u.password)
      return node("span", label);
    const a = node("a", label);
    a.href = u.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  } catch {
    return node("span", label);
  }
}
async function api(path, payload) {
  const options = {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  };
  if (payload !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.headers["X-CSRF-Token"] = state.csrf;
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || "Unable to connect. Please try again.");
  return data;
}
function route() {
  if (location.hash === "#main") return;
  window.scrollTo(0, 0);
  const view = ["overview", "ask", "metrics", "brief"].includes(
    location.hash.slice(1),
  )
    ? location.hash.slice(1)
    : "overview";
  const labels = {
    overview: "Overview",
    ask: "Ask the market",
    metrics: "Market evidence",
    brief: "Research brief",
  };
  document
    .querySelectorAll(".view")
    .forEach((el) => el.classList.toggle("hidden", el.id !== `view-${view}`));
  document.querySelectorAll("[data-view]").forEach((el) => {
    const active = el.dataset.view === view;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  $("page-name").textContent = labels[view];
  document.title = `${labels[view]} · Office Intelligence`;
}
window.addEventListener("hashchange", route);
document
  .querySelectorAll("[data-view]")
  .forEach((link) =>
    link.addEventListener("click", () => window.scrollTo(0, 0)),
  );
route();
$("today").textContent = date(new Date());

function openSources(sources, reading = null) {
  state.drawerFocus = document.activeElement;
  const root = clear("source-content");
  $("source-title").textContent = reading
    ? `${reading.publisher || reading.source} · Source details`
    : `Source register · ${sources.length} ${sources.length === 1 ? "entry" : "entries"}`;
  if (reading) {
    const dl = node("dl", null, "source-detail");
    for (const [label, text] of [
      [
        "Metric / submarket",
        `${names[reading.metric] || reading.label || ""} · ${markets[reading.submarket] || reading.submarket || "UK"}`,
      ],
      ["Reading", value(reading)],
      ["Period", reading.period_label || reading.as_of || reading.period],
      ["Original definition", reading.definition],
      ["Report", reading.report],
      [
        "Retrieved from source",
        reading.retrieved_at ? timeLabel(reading.retrieved_at) : null,
      ],
      ["Evidence & notes", reading.note],
    ]) {
      if (text) {
        dl.append(node("dt", label), node("dd", text));
      }
    }
    root.append(dl);
  }
  for (const source of sources) {
    const card = node("div", null, "source-card");
    card.append(
      node(
        "strong",
        `${source.id || ""} · ${source.publisher || source.source}`,
      ),
      node(
        "p",
        source.period ||
          source.period_label ||
          source.published ||
          source.as_of ||
          "Period not stated",
      ),
    );
    if (source.origins)
      card.append(node("p", `Source mode: ${source.origins.join(" / ")}`));
    if (source.warnings?.length)
      card.append(
        node("p", `Note: ${source.warnings.join("; ")}`, "source-flags"),
      );
    const link = safeLink(source.url, "Open original source ↗");
    link.className = "source-link";
    card.append(link);
    root.append(card);
  }
  if (!sources.length)
    root.append(node("p", "No traceable sources available."));
  hide("source-overlay", false);
  hide("source-drawer", false);
  document.body.style.overflow = "hidden";
  $("close-source").focus();
}
function closeSources() {
  hide("source-overlay");
  hide("source-drawer");
  document.body.style.overflow = "";
  state.drawerFocus?.focus();
}
$("close-source").addEventListener("click", closeSources);
$("source-overlay").addEventListener("click", closeSources);
document.addEventListener("keydown", (event) => {
  if ($("source-drawer").classList.contains("hidden")) return;
  if (event.key === "Escape") closeSources();
  if (event.key === "Tab") {
    const items = $("source-drawer").querySelectorAll("button,a[href]");
    const first = items[0],
      last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
function rowSources(row) {
  const found = state.data.sources.find((s) => s.id === row.source_id);
  openSources(
    found
      ? [found]
      : [
          {
            publisher: row.publisher || row.source,
            period: row.period_label || row.as_of,
            url: row.url,
          },
        ],
    row,
  );
}
$("all-sources").addEventListener(
  "click",
  () => state.data && openSources(state.data.sources),
);
function sourceButton(row, label = "View source ↗") {
  const b = node("button", label, "text-button");
  b.type = "button";
  b.addEventListener("click", () => rowSources(row));
  return b;
}
function kpiValue(row) {
  const el = node("div", row ? value(row) : "No data", "kpi-value");
  if (row?.unit?.startsWith("GBP/sq")) {
    el.textContent = `£${row.value}`;
    el.append(node("small", "/ ft² / year"));
  }
  return el;
}
function sparkline(points = []) {
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 100 45");
  svg.setAttribute("class", "sparkline");
  svg.setAttribute("aria-hidden", "true");
  const values = points.map((p) => p[1]).filter(Number.isFinite);
  if (values.length < 2) return svg;
  const min = Math.min(...values),
    max = Math.max(...values),
    span = max - min || 1;
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute(
    "points",
    values
      .map(
        (v, i) =>
          `${(i * 96) / (values.length - 1) + 2},${38 - ((v - min) / span) * 30}`,
      )
      .join(" "),
  );
  line.setAttribute("stroke", "#7b9b87");
  line.setAttribute("stroke-width", "1.8");
  line.setAttribute("fill", "none");
  svg.append(line);
  return svg;
}
function bars(target, rows) {
  const root = clear(target);
  if (!rows.length) {
    root.append(
      node(
        "p",
        "No readings available for this selection.",
        "empty-state compact",
      ),
    );
    return;
  }
  const max = Math.max(...rows.map((r) => Math.abs(r.value)), 1);
  const signed = rows.some((r) => r.value < 0);
  for (const row of rows) {
    const line = node("div", null, "bar-row");
    line.append(node("span", row.publisher, "bar-label"));
    const track = node("div", null, "bar-track"),
      fill = node("div", null, "bar-fill");
    const width = (Math.abs(row.value) / max) * (signed ? 50 : 100);
    fill.style.width = `${width}%`;
    if (row.value === 0) fill.style.minWidth = "0";
    if (signed) {
      track.classList.add("signed");
      fill.style.position = "absolute";
      fill.style.left = `${row.value < 0 ? 50 - width : 50}%`;
    }
    track.append(fill);
    line.append(track, node("span", value(row), "bar-value"));
    root.append(line);
  }
}
function periodEndKey(period) {
  const year = Number(period.slice(0, 4));
  const quarter = period.match(/-Q([1-4])$/);
  const half = period.match(/-H([12])$/);
  const month = period.match(/-(\d{2})$/);
  return (
    year * 12 +
    (quarter
      ? Number(quarter[1]) * 3
      : half
        ? Number(half[1]) * 6
        : month
          ? Number(month[1])
          : 12)
  );
}
function bestPeriod(rows) {
  const counts = new Map();
  for (const r of rows) {
    if (!counts.has(r.period)) counts.set(r.period, new Set());
    counts.get(r.period).add(r.publisher);
  }
  return (
    [...counts.keys()].sort(
      (a, b) =>
        counts.get(b).size - counts.get(a).size ||
        periodEndKey(b) - periodEndKey(a) ||
        b.localeCompare(a),
    )[0] || ""
  );
}
function renderOverview() {
  const d = state.data;
  const grid = clear("kpi-grid");
  grid.classList.remove("loading");
  d.cards.forEach((card) => {
    const el = node("article", null, "kpi-card");
    el.append(node("div", card.label, "kpi-label"), kpiValue(card.reading));
    if (card.reading) {
      el.append(
        node(
          "p",
          `${card.reading.publisher} · ${card.reading.period_label || card.reading.period}`,
          "kpi-source",
        ),
        sourceButton(card.reading),
      );
    }
    if (card.coverage_note)
      el.append(node("p", card.coverage_note, "kpi-coverage"));
    grid.append(el);
  });
  const rows = d.rows.filter(
      (r) => r.metric === "vacancy_rate" && r.submarket === "Central London",
    ),
    period = bestPeriod(rows);
  $("mini-chart-context").textContent =
    `Central London · Vacancy / availability (all grades) · ${period || "No period"}`;
  const comparable = rows.filter((r) => r.period === period);
  const publishers = new Set(comparable.map((r) => r.publisher)).size;
  $("mini-chart-title").textContent =
    publishers > 1
      ? "One period. Different perspectives."
      : publishers === 1
        ? "One broker reading for this period"
        : "Vacancy coverage unavailable";
  $("mini-chart-note").textContent =
    publishers > 1
      ? "Broker definitions can differ even within the same period. Readings are shown independently, never averaged."
      : publishers === 1
        ? "Only one broker has a reading for this period. Other reporting periods are kept separate in Market evidence."
        : "No verified vacancy readings are available in the retrieved sources.";
  const brokerStatus = d.source_status?.find((s) => s.id === "brokers");
  $("market-coverage").textContent =
    brokerStatus && brokerStatus.status !== "done"
      ? "Some broker reports could not be refreshed. Each reading keeps its original source and reporting period; open a source to check when it was retrieved."
      : "Cards show the available measures exactly as labelled. Grade A and all-grade vacancy, and average and prime rents, are different measures.";
  bars("mini-chart", comparable);
  renderNews();
  const macro = clear("macro-grid");
  if (!d.macro.indicators.length)
    macro.append(
      node(
        "p",
        "Macro readings are unavailable. Review the live retrieval status above.",
        "empty-state whole-grid",
      ),
    );
  d.macro.indicators.forEach((row) => {
    const card = node("article", null, "macro-card");
    const copy = node("div");
    copy.append(
      node("h3", macroNames[row.indicator] || row.label),
      node("strong", value(row)),
    );
    card.append(copy);
    const delta = row.change;
    copy.append(
      node(
        "p",
        Number.isFinite(delta)
          ? `${delta > 0 ? "+" : ""}${delta} percentage points vs ${row.previous_as_of}`
          : "No prior observation",
        "small muted",
      ),
    );
    copy.append(
      node("p", `${row.source} · ${row.as_of}`, "small muted"),
      sourceButton(row),
    );
    card.append(sparkline(row.recent));
    macro.append(card);
  });
}
function renderNews() {
  const items = state.data.news.items || [],
    root = clear("news-list");
  $("news-count").textContent = `${items.length} stories`;
  for (const row of items.slice(0, state.expanded ? 12 : 3)) {
    const item = node("article", null, "news-item");
    const meta = node("div", null, "news-meta");
    meta.append(node("span", row.source), node("span", date(row.published)));
    item.append(meta, safeLink(row.url, row.title));
    const topics = {
      development: "Development",
      investment: "Investment",
      leasing: "Leasing",
      macro_policy: "Macro policy",
      vacancy_supply: "Supply",
      esg: "ESG",
      general: "Market news",
    };
    if (row.topics?.length)
      item.append(
        node(
          "span",
          row.topics.map((t) => topics[t] || t).join(" / "),
          "topic",
        ),
      );
    root.append(item);
  }
  if (!items.length)
    root.append(
      node(
        "p",
        "No matching stories in this date range.",
        "empty-state compact",
      ),
    );
  hide("more-news", items.length <= 3);
  $("more-news").textContent = state.expanded
    ? "Show fewer stories ↑"
    : "More market stories ↓";
}
$("more-news").addEventListener("click", () => {
  state.expanded = !state.expanded;
  renderNews();
});
function options(id, values, selected) {
  const select = clear(id);
  values.forEach(([key, label]) => {
    const option = node("option", label);
    option.value = key;
    select.append(option);
  });
  select.disabled = values.length === 0;
  if (!values.length) {
    const option = node("option", "No data available");
    option.value = "";
    select.append(option);
  }
  select.value = values.some(([k]) => k === selected)
    ? selected
    : values[0]?.[0] || "";
}
function setupFilters() {
  const rows = state.data.rows;
  options(
    "metric-filter",
    [...new Set(rows.map((r) => r.metric))].map((k) => [
      k,
      names[k] || state.data.metrics[k] || k,
    ]),
    "vacancy_rate",
  );
  updateMarkets();
}
function updateMarkets() {
  const rows = state.data.rows.filter(
    (r) => r.metric === $("metric-filter").value,
  );
  options(
    "market-filter",
    [...new Set(rows.map((r) => r.submarket))].map((k) => [k, markets[k] || k]),
    $("market-filter").value || "Central London",
  );
  updatePeriods();
}
function selectedRows() {
  return state.data.rows.filter(
    (r) =>
      r.metric === $("metric-filter").value &&
      r.submarket === $("market-filter").value,
  );
}
function updatePeriods() {
  const rows = selectedRows();
  options(
    "period-filter",
    evidencePeriods(rows).map((group) => [group.period, group.label]),
    bestPeriod(rows),
  );
  renderMetrics();
}
function evidencePeriods(rows) {
  const groups = new Map();
  for (const row of rows) {
    if (!groups.has(row.period))
      groups.set(row.period, {
        period: row.period,
        label: row.period_label || row.period,
        publishers: new Set(),
      });
    groups.get(row.period).publishers.add(row.publisher);
  }
  return [...groups.values()].sort(
    (a, b) =>
      periodEndKey(b.period) - periodEndKey(a.period) ||
      b.period.localeCompare(a.period),
  );
}
function changesForSelection(changes, rows) {
  return changes.filter((change) =>
    rows.some(
      (row) =>
        change.metric === row.metric &&
        change.submarket === row.submarket &&
        change.publisher === row.publisher &&
        (change.to_period
          ? change.to_period === row.period
          : typeof change.to === "string" &&
            change.to.endsWith(`(${row.period_label || row.period})`)),
    ),
  );
}
function renderMetrics() {
  const available = selectedRows();
  const rows = available.filter((r) => r.period === $("period-filter").value);
  const publishers = [...new Set(rows.map((row) => row.publisher))];
  const periods = clear("evidence-periods");
  for (const group of evidencePeriods(available)) {
    const button = node(
      "button",
      `${group.label} · ${[...group.publishers].join(", ")}`,
      "evidence-period",
    );
    button.type = "button";
    button.setAttribute(
      "aria-pressed",
      String(group.period === $("period-filter").value),
    );
    button.addEventListener("click", () => {
      $("period-filter").value = group.period;
      renderMetrics();
    });
    periods.append(button);
  }
  if (!available.length)
    periods.append(node("p", "No reporting periods retrieved yet.", "muted"));
  $("comparison-title").textContent =
    names[$("metric-filter").value] || "Broker comparison";
  $("comparison-subtitle").textContent = rows.length
    ? `${markets[$("market-filter").value] || $("market-filter").value} · ${rows[0].period_label || rows[0].period} · ${rows.length} ${rows.length === 1 ? "reading" : "readings"}`
    : "No retrieved broker readings for this selection. Run a live update from the overview.";
  $("comparison-status").textContent =
    publishers.length > 1
      ? `${publishers.length} sources · Check definitions`
      : publishers.length
        ? "Single-source reading"
        : "Awaiting source data";
  const otherPublishers = [
    ...new Set(available.map((row) => row.publisher)),
  ].filter((publisher) => !publishers.includes(publisher));
  $("comparison-context").textContent = !rows.length
    ? "A reading will appear after a source is retrieved for this selection."
    : publishers.length > 1
      ? "These sources cover the same reporting period. Check their definitions before interpreting any difference."
      : `Only ${publishers[0]} has a retrieved reading for this period. ${otherPublishers.length ? `${otherPublishers.join(", ")} report in other periods; select one above to view it. These are not a period-over-period comparison.` : "A comparison needs another publisher covering the same metric, market and period."}`;
  bars("comparison-chart", rows);
  hide("comparison-chart", publishers.length < 2);
  const body = clear("metrics-table-body");
  for (const row of rows) {
    const tr = node("tr");
    for (const text of [
      row.publisher,
      value(row),
      row.period_label || row.period,
      row.stale
        ? "Older period · Review"
        : row.note?.includes("Review flag") || row.note?.includes("table row")
          ? "Needs review"
          : "Source identified",
    ]) {
      tr.append(node("td", text));
    }
    const td = node("td");
    td.append(sourceButton(row, "Definition & source ↗"));
    tr.append(td);
    body.append(tr);
  }
  hide("metrics-empty", rows.length > 0);
  const changes = clear("changes-list");
  const list = changesForSelection(state.data.changes || [], rows);
  $("changes-panel").open = list.length > 0;
  $("changes-title").textContent = list.length
    ? "Period changes within each source"
    : "Why no period change is shown";
  for (const change of list) {
    const el = node("div", null, "change-item");
    el.append(
      node("strong", change.publisher),
      node("p", `${change.from || ""} → ${change.to || ""}`),
      node(
        "p",
        `${change.change ?? change.delta ?? "—"} ${change.unit || ""}`,
        "change-value",
      ),
    );
    changes.append(el);
  }
  if (!list.length)
    changes.append(
      node(
        "p",
        "The retrieved evidence has no compatible earlier reading from the same publisher for the selected period. A reading from another publisher or a different reporting frequency cannot establish a change.",
        "evidence-context",
      ),
    );
}
$("metric-filter").addEventListener("change", updateMarkets);
$("market-filter").addEventListener("change", updatePeriods);
$("period-filter").addEventListener("change", renderMetrics);
$("reset-filters").addEventListener(
  "click",
  () => state.data && setupFilters(),
);
async function loadDashboard() {
  hide("global-error");
  try {
    state.data = await api("/api/dashboard");
    renderOverview();
    setupFilters();
    renderWorkspace();
    $("data-badge").textContent = state.data.workspace.has_data
      ? "● Retrieved source data"
      : "● Awaiting live data";
    $("data-note").textContent = state.data.retrieved_at
      ? `Data updated ${timeLabel(state.data.retrieved_at)}${state.data.workspace.analysis?.completed_at ? ` · AI updated ${timeLabel(state.data.workspace.analysis.completed_at)}` : ""}`
      : "The first live update starts automatically.";
  } catch (error) {
    $("error-message").textContent = error.message;
    hide("global-error", false);
    throw error;
  }
}
$("retry-data").addEventListener("click", () => init());

function timeLabel(value) {
  if (!value) return "Not yet run";
  const d = new Date(value);
  if (Number.isNaN(+d)) return "Time unavailable";
  return (
    new Intl.DateTimeFormat("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: "Asia/Hong_Kong",
    }).format(d) + " HKT"
  );
}
function renderWorkspace() {
  const latest =
    state.data.workspace.activity || state.data.workspace.history?.[0];
  if (latest) renderActivity(latest);
  if (!state.active) setPipelineBusy(false);
}
function renderActivity(job) {
  const running = !["done", "failed", "cancelled"].includes(job.status);
  hide("pipeline-progress", !running);
  $("pipeline-label").textContent =
    job.events?.at(-1) || "Getting live sources";
  const elapsed =
    running && job.started_at
      ? Math.max(
          0,
          Math.floor((Date.now() - new Date(job.started_at).getTime()) / 1000),
        )
      : job.seconds;
  $("pipeline-elapsed").textContent = `${elapsed || 0}s elapsed`;
  hide("pipeline-error", !job.error);
  $("pipeline-error").textContent = job.error || "";
}
function showAnalysis() {
  const analysis = state.data.workspace.analysis;
  if (!analysis) return;
  openSources(analysis.sources || []);
  $("source-title").textContent = "AI market analysis";
  const content = node("section", null, "analysis-drawer-content");
  content.append(
    node(
      "p",
      `Analysis updated ${timeLabel(analysis.completed_at)}`,
      "small muted",
    ),
  );
  const answer = node("div", null, "markdown");
  markdown(answer, analysis.answer);
  content.append(answer);
  const checks = node("div", null, "quality-box");
  checks.id = "drawer-analysis-quality";
  content.append(checks);
  $("source-content").prepend(content);
  quality("drawer-analysis-quality", analysis);
}
function setPipelineBusy(active) {
  for (const id of [
    "refresh-market",
    "ask-submit",
    "generate-brief",
    "reset-chat",
  ])
    $(id).disabled = active;
  $("refresh-market-label").textContent = active
    ? "Update in progress…"
    : state.ai
      ? "Run AI analysis"
      : "Get latest data";
  hide("pipeline-progress", !active);
}
async function pollPipeline(id, owner) {
  state.active = { id, kind: "pipeline", owner };
  setPipelineBusy(true);
  $("cancel-pipeline").disabled = !owner;
  const deadline = Date.now() + 195000;
  let job;
  try {
    while (Date.now() < deadline) {
      const activity = await api("/api/activity");
      job = activity.activity;
      if (!job || job.id !== id) break;
      renderActivity(job);
      if (
        activity.retrieved_at &&
        activity.retrieved_at !== state.data?.retrieved_at
      )
        await loadDashboard();
      if (["done", "failed", "cancelled"].includes(job.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    await loadDashboard();
    return job;
  } catch (error) {
    $("pipeline-error").textContent = error.message;
    hide("pipeline-error", false);
  } finally {
    state.active = null;
    setPipelineBusy(false);
  }
}
async function runPipeline(automatic = false) {
  if (state.active) return;
  const previousAnalysis = state.data?.workspace.analysis?.completed_at;
  state.active = { kind: "pipeline", id: null, owner: true };
  setPipelineBusy(true);
  hide("pipeline-error");
  try {
    const started = await api("/api/refresh", {
      automatic,
    });
    if (!started.skipped) {
      const job = await pollPipeline(started.id, started.owner);
      if (!automatic && job?.status === "done") {
        if (
          state.data.workspace.analysis?.completed_at !== previousAnalysis &&
          state.data.workspace.analysis
        )
          showAnalysis();
        else if (state.ai) {
          $("pipeline-error").textContent =
            "No new AI analysis was produced. Some sources or the AI service may be unavailable. Please try again.";
          hide("pipeline-error", false);
        } else toast("Live source data updated.");
      }
    }
  } catch (error) {
    $("pipeline-error").textContent = error.message;
    hide("pipeline-error", false);
  } finally {
    state.active = null;
    setPipelineBusy(false);
  }
}
$("refresh-market").addEventListener("click", () => runPipeline());
$("cancel-pipeline").addEventListener("click", async () => {
  if (state.active?.id && state.active.owner) {
    try {
      await api(`/api/jobs/${state.active.id}/cancel`, {});
      $("pipeline-label").textContent = "Stopping the current run…";
    } catch (error) {
      toast(error.message);
    }
  }
});

// Render a deliberately small Markdown subset using DOM nodes. Model text is never HTML.
function inline(parent, text) {
  const re =
    /\[([^\]\n]+)\]\((https:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*|`([^`\n]+)`|(https:\/\/[^\s<>]+)|_([^_\n]+)_/g;
  let last = 0;
  for (const match of text.matchAll(re)) {
    parent.append(document.createTextNode(text.slice(last, match.index)));
    if (match[1]) parent.append(safeLink(match[2], match[1]));
    else if (match[5]) parent.append(safeLink(match[5], match[5]));
    else
      parent.append(
        node(
          match[3] ? "strong" : match[4] ? "code" : "em",
          match[3] || match[4] || match[6],
        ),
      );
    last = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(last)));
}
function markdown(target, text) {
  const root = clear(target);
  root.classList.add("markdown");
  let list = null;
  for (const line of String(text || "").split("\n")) {
    if (!line.trim()) {
      list = null;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)/),
      bullet = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)/);
    if (heading) {
      list = null;
      const h = node(`h${Math.min(heading[1].length + 1, 4)}`);
      inline(h, heading[2]);
      root.append(h);
    } else if (bullet) {
      if (!list) {
        list = node("ul");
        root.append(list);
      }
      const li = node("li");
      inline(li, bullet[1]);
      list.append(li);
    } else {
      list = null;
      const p = node("p");
      inline(p, line);
      root.append(p);
    }
  }
}
function quality(id, result) {
  const box = clear(id);
  hide(id, false);
  const check = result.check;
  const warnings = [
    ...(check?.not_found || []).map(
      (x) => `Figure not directly matched in sources: ${x}`,
    ),
    ...(check?.wrong_source || []).map(
      (x) => `Publisher attribution needs checking: ${x}`,
    ),
    ...(check?.unverified || []),
  ];
  const warning =
    check &&
    (check.status === "warn" ||
      check.status === "failed" ||
      check.passed === false ||
      warnings.length > 0);
  box.classList.toggle("warning", !!warning);
  box.append(
    node(
      "strong",
      !result.used_llm
        ? "Source summary"
        : warning
          ? "Check flagged figures before sharing"
          : "AI-generated briefing",
    ),
  );
  box.append(
    node(
      "p",
      !result.used_llm
        ? "Readings are presented directly from the sources."
        : warning
          ? "Some figures could not be fully linked to their source context. This does not necessarily mean they are wrong."
          : "Review the linked sources before quoting. Automated checks do not verify every conclusion.",
    ),
  );
  if (!check) return;
  const details = node("details", null, "quality-details");
  details.append(
    node(
      "summary",
      warning
        ? `View source checks${warnings.length ? ` (${warnings.length})` : ""}`
        : "About the source checks",
    ),
  );
  details.append(
    node(
      "p",
      `${check.traced ?? 0} figures found in the source data. A numeric match alone does not confirm the meaning, date or attribution.`,
    ),
  );
  for (const issue of warnings.slice(0, 6))
    details.append(node("p", sourceCheckLabel(issue)));
  if (warning && !warnings.length)
    details.append(
      node(
        "p",
        "The source check did not confirm this briefing. Review the original sources.",
      ),
    );
  box.append(details);
}
function sourceCheckLabel(issue) {
  const text =
    typeof issue === "string"
      ? issue
      : issue.reason || issue.message || JSON.stringify(issue);
  const reasons = {
    "metric context does not match":
      "The figure was found, but the checker could not confirm it refers to this measure.",
    "submarket context does not match":
      "The figure was found, but its location could not be confirmed.",
    "cited period does not match or is missing":
      "The reporting date needs checking against the original source.",
    "missing adjacent citation":
      "A source citation is missing beside this figure.",
    "unknown cited publisher":
      "The named publisher could not be linked to the supplied sources.",
    "unit does not match":
      "The unit needs checking against the original source.",
    "currency does not match":
      "The currency needs checking against the original source.",
    "area unit does not match":
      "The area unit needs checking against the original source.",
  };
  for (const [reason, explanation] of Object.entries(reasons))
    if (text.endsWith(`: ${reason}`))
      return `${text.slice(0, -reason.length - 2)} — ${explanation}`;
  if (text.startsWith("Figure not directly matched in sources: "))
    return `${text.slice("Figure not directly matched in sources: ".length)} — This figure was not found directly in the supplied data.`;
  return text;
}
function answerSources(sources) {
  const root = clear("answer-sources");
  for (const source of sources) {
    const card = node("div", null, "source-card");
    card.append(
      node("strong", `${source.id} · ${source.publisher}`),
      node("p", source.period),
    );
    const b = node("button", "Source details ↗", "text-button");
    b.addEventListener("click", () => openSources([source]));
    card.append(b);
    root.append(card);
  }
  if (!sources.length)
    root.append(
      node(
        "p",
        "This answer has no traceable sources. Verify it before quoting.",
        "empty-state compact",
      ),
    );
}
function message(role, text, result = null) {
  const log = $("chat-log");
  log.querySelector(".chat-welcome")?.remove();
  const el = node("article", null, `message ${role}`);
  el.append(
    node(
      "div",
      role === "user"
        ? "Your question"
        : result?.used_llm
          ? "OFFICE INTELLIGENCE · AI analysis"
          : "OFFICE INTELLIGENCE · Source data",
      "message-label",
    ),
  );
  const content = node("div", null, "message-content");
  markdown(content, text);
  el.append(content);
  if (result) {
    const footer = node("div", null, "message-footer");
    footer.append(node("span", `Retrieved source data · ${result.seconds} s`));
    const copy = node("button", "Copy answer", "text-button");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        toast("Answer copied");
      } catch {
        toast("Copy is unavailable. Select and copy the answer text.");
      }
    });
    footer.append(copy);
    el.append(footer);
  }
  log.append(el);
  log.scrollTop = log.scrollHeight;
}
function busy(active, kind) {
  for (const id of [
    "ask-submit",
    "generate-brief",
    "reset-chat",
    "ask-ai",
    "brief-ai",
    "refresh-market",
  ]) {
    $(id).disabled =
      active ||
      (id === "ask-ai" && !state.ai) ||
      (id === "brief-ai" && !state.briefAI);
  }
  document
    .querySelectorAll("[data-question]")
    .forEach((el) => (el.disabled = active));
  hide("ask-progress", !active || kind !== "ask");
  hide("brief-progress", !active || kind !== "brief");
}
async function runJob(kind, payload) {
  if (state.active) return;
  state.active = { id: null, kind };
  busy(true, kind);
  if (kind === "ask") {
    clear("answer-sources");
    clear("answer-quality").append(
      node("p", "Gathering the evidence for this answer…"),
    );
  } else {
    clear("brief-preview").append(
      node("p", "Gathering the evidence for your brief…", "empty-state"),
    );
    hide("brief-quality");
  }
  try {
    const started = await api(`/api/${kind}`, {
      ...payload,
    });
    state.active.id = started.id;
    let job;
    const deadline = Date.now() + 105000;
    while (Date.now() < deadline) {
      job = await api(`/api/jobs/${started.id}`);
      document.querySelector(`#${kind}-progress .progress-label`).textContent =
        job.events.at(-1) || "Preparing the evidence";
      if (["done", "failed", "cancelled"].includes(job.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    if (job?.status !== "done")
      throw new Error(
        job?.status === "cancelled"
          ? "Task cancelled."
          : job?.error ||
            "The task is still running. Cancel it before trying again.",
      );
    const result = job.result;
    if (kind === "ask") {
      message(
        "assistant",
        result.answer.replace(/\n\n_Check:[\s\S]*_$/, ""),
        result,
      );
      quality("answer-quality", result);
      answerSources(result.sources);
    } else {
      markdown("brief-preview", result.brief);
      quality("brief-quality", result);
      $("brief-state-label").textContent = result.used_llm
        ? "AI analysis · Complete"
        : "Factual digest · Complete";
      $("download-brief").href = `/api/jobs/${started.id}/download`;
      hide("download-brief", false);
      toast("Your brief is ready to preview or download.");
    }
  } catch (error) {
    toast(error.message);
    if (kind === "ask") message("assistant", error.message);
    else $("brief-state-label").textContent = error.message;
  } finally {
    state.active = null;
    busy(false, kind);
  }
}
$("ask-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const question = $("question").value.trim();
  if (!question || state.active) return;
  message("user", question);
  $("question").value = "";
  runJob("ask", { question, use_llm: $("ask-ai").checked });
});
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey))
    $("ask-form").requestSubmit();
});
document.querySelectorAll("[data-question]").forEach((el) =>
  el.addEventListener("click", () => {
    $("question").value = el.dataset.question;
    $("ask-form").requestSubmit();
  }),
);
$("generate-brief").addEventListener("click", () => {
  hide("download-brief");
  runJob("brief", { use_llm: $("brief-ai").checked });
});
document.querySelectorAll(".cancel-job").forEach((el) =>
  el.addEventListener("click", async () => {
    if (!state.active?.id) return;
    try {
      await api(`/api/jobs/${state.active.id}/cancel`, {});
      toast("Cancellation requested. Finishing the task.");
    } catch (error) {
      toast(error.message);
    }
  }),
);
async function resetChat() {
  await api("/api/reset", {});
  clear("chat-log").append(
    node(
      "p",
      "A fresh conversation is ready. Start with a specific market question.",
      "empty-state compact",
    ),
  );
  clear("answer-sources");
  clear("answer-quality").append(
    node("p", "Evidence for the next answer will appear here."),
  );
}
$("reset-chat").addEventListener("click", () =>
  resetChat().catch((error) => toast(error.message)),
);
$("ask-ai").addEventListener("change", () => {
  resetChat().catch((error) => toast(error.message));
  $("ai-hint").textContent = $("ask-ai").checked
    ? "Uses the server-configured model"
    : "Source data only";
});
$("brief-ai").addEventListener("change", () => {
  $("brief-mode-note").textContent = $("brief-ai").checked
    ? "Uses the server-configured model. Review the generated analysis."
    : "A factual digest is generated by default, with no model calls.";
});
async function init() {
  try {
    const bootstrap = await api("/api/bootstrap");
    state.csrf = bootstrap.csrf;
    state.ai = bootstrap.ai_available;
    state.briefAI = bootstrap.brief_ai_available;
    $("ask-ai").disabled = !state.ai;
    $("brief-ai").disabled = !state.briefAI;
    $("refresh-market-label").textContent = state.ai
      ? "Run AI analysis"
      : "Get latest data";
    $("ai-hint").textContent = state.ai
      ? "Optional · Uses the configured model"
      : "Source data only";
    await loadDashboard();
    const activity = state.data.workspace.activity;
    if (
      activity &&
      !["done", "failed", "cancelled"].includes(activity.status)
    ) {
      await pollPipeline(activity.id, false);
    } else if (!state.data.workspace.has_data) {
      await runPipeline(true);
    }
  } catch (error) {
    $("error-message").textContent = error.message;
    hide("global-error", false);
  }
}
init();
