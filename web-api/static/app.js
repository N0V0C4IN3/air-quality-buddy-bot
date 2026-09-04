/* The page.
 *
 * Three pieces of state: which time frame, which resolution, which panels. The
 * time frame and resolution live in the URL so a view can be linked and
 * survives a refresh; the panel layout lives in localStorage because it is a
 * preference of this browser, not of this link. */

import { api, ApiError, telegram } from "./api.js";
import { heatmap, pollutantChart, rampColour } from "./charts.js";

const PRESETS = [
  ["1h", "1h"], ["12h", "12h"], ["today", "Today"], ["24h", "24h"],
  ["7d", "7d"], ["30d", "30d"], ["90d", "90d"],
];

const LAYOUT_KEY = "aq.panels.v1";
const DEFAULT_PANELS = ["live", "pm25", "pm10", "stats", "levels", "profile", "heatmap"];

const state = {
  range: { preset: "24h", custom: false, from: null, to: null },
  bucket: "",
  meta: null,
  panels: loadLayout(),
  charts: [],
};

const el = {
  presets: document.getElementById("presets"),
  from: document.getElementById("from"),
  to: document.getElementById("to"),
  apply: document.getElementById("apply-custom"),
  bucket: document.getElementById("bucket"),
  panels: document.getElementById("panels"),
  toggles: document.getElementById("panel-toggles"),
  customize: document.getElementById("customize"),
  customizeBtn: document.getElementById("customize-btn"),
  themeBtn: document.getElementById("theme-btn"),
  status: document.getElementById("status-line"),
  dot: document.getElementById("live-dot"),
  foot: document.getElementById("foot-meta"),
};

// ---------- panel registry ----------
// A panel declares what it needs; the loader fetches only those endpoints, so
// hiding the heatmap really does stop the server computing one.

const PANELS = [
  { id: "live", title: "Now", needs: ["latest"], render: renderLive },
  { id: "pm25", title: "PM2.5", wide: true, needs: ["series"],
    render: (b, d, note) => renderPollutant(b, d, note, "pm25") },
  { id: "pm10", title: "PM10", wide: true, needs: ["series"],
    render: (b, d, note) => renderPollutant(b, d, note, "pm10") },
  { id: "stats", title: "Statistics", needs: ["summary"], render: renderStats },
  { id: "levels", title: "Time by level", needs: ["summary"], render: renderLevels },
  { id: "profile", title: "Hour of day", needs: ["patterns"], render: renderProfile },
  { id: "heatmap", title: "Weekday by hour", wide: true, needs: ["patterns"],
    render: renderHeatmap },
];

const byId = Object.fromEntries(PANELS.map((p) => [p.id, p]));

// ---------- boot ----------

async function boot() {
  if (telegram) {
    telegram.ready();
    telegram.expand();
  }
  buildPresets();
  buildToggles();
  wire();
  readUrl();

  try {
    state.meta = await api.meta();
  } catch (err) {
    return fail(err);
  }
  applyTheme(preferredTheme());
  el.foot.textContent =
    `Times in ${state.meta.timezone}. Readings every ${state.meta.reading_interval_seconds}s, ` +
    `kept for ${state.meta.retention_days} days.`;

  await refresh();
  // The dashboard is only ever as fresh as the reader; polling faster than it
  // writes just burns the Pi.
  setInterval(refresh, Math.max(30, state.meta.reading_interval_seconds) * 1000);
}

function wire() {
  el.apply.addEventListener("click", () => {
    if (!el.from.value || !el.to.value) {
      return note("Pick both a start and an end.");
    }
    state.range = { custom: true, from: el.from.value, to: el.to.value, preset: null };
    markPresets();
    writeUrl();
    refresh();
  });

  el.bucket.addEventListener("change", () => {
    state.bucket = el.bucket.value;
    writeUrl();
    refresh();
  });

  el.customizeBtn.addEventListener("click", () => {
    const open = el.customize.hidden;
    el.customize.hidden = !open;
    el.customizeBtn.setAttribute("aria-expanded", String(open));
  });

  el.themeBtn.addEventListener("click", async () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("aq.theme", next);
    render();                       // charts bake their colours in at draw time
    try {
      await api.setTheme(next);     // shared with the bot's PNGs when we know the chat
    } catch (_) { /* browser-only visitor: the local choice is enough */ }
  });

  window.addEventListener("popstate", () => { readUrl(); refresh(); });
  window.addEventListener("resize", debounce(render, 150));
}

// ---------- data ----------

let lastData = {};

async function refresh() {
  const needed = new Set(activePanels().flatMap((p) => p.needs));
  note("Loading...");
  try {
    const [latest, series, summary, patterns] = await Promise.all([
      needed.has("latest") ? api.latest().catch(nullOn404) : null,
      needed.has("series") ? api.series(state.range, state.bucket) : null,
      needed.has("summary") ? api.summary(state.range) : null,
      needed.has("patterns") ? api.patterns(state.range) : null,
    ]);
    lastData = { latest, series, summary, patterns };
    note("");
    render();
  } catch (err) {
    fail(err);
  }
}

function nullOn404(err) {
  if (err instanceof ApiError && err.status === 404) return null;
  throw err;
}

// ---------- render ----------

function render() {
  state.charts.forEach((c) => c.destroy());
  state.charts = [];
  el.panels.innerHTML = "";

  for (const panel of activePanels()) {
    const node = document.getElementById("tpl-panel").content.firstElementChild.cloneNode(true);
    if (panel.wide) node.classList.add("wide");
    node.querySelector("h2").textContent = panel.title;
    const body = node.querySelector(".panel-body");
    const noteEl = node.querySelector(".panel-note");
    el.panels.append(node);
    try {
      panel.render(body, lastData, noteEl);
    } catch (err) {
      // One panel failing is not a reason to show a blank dashboard.
      empty(body, `Could not draw this panel: ${err.message}`);
    }
  }
  paintLiveDot();
}

function renderLive(body, data) {
  const d = data.latest;
  if (!d) return empty(body, "No readings yet.");

  const t = state.meta.thresholds;
  body.innerHTML = "";
  const wrap = div("live");
  wrap.append(figure("PM2.5", d.pm25, t.pm25, "--pm25"));
  wrap.append(figure("PM10", d.pm10, t.pm10, "--pm10"));

  const side = div("live-figure");
  const level = document.createElement("span");
  level.className = "live-level";
  level.innerHTML = `<span class="dot" data-level="${d.level}"></span>${d.level_label}`;
  const when = document.createElement("span");
  when.className = d.stale ? "trend stale" : "trend";
  when.textContent = d.stale
    ? `Sensor quiet - last reading ${ago(d.age_seconds)} ago`
    : `Updated ${ago(d.age_seconds)} ago`;
  side.append(level, when);
  wrap.append(side);
  body.append(wrap);
}

/* Each pollutant metered against its own limits, warn marked - the same
 * construction as the status card the bot sends. */
function figure(label, value, limits, colourVar) {
  const f = div("live-figure");
  const v = div("live-value");
  v.textContent = value.toFixed(1);
  const l = div("live-label");
  l.textContent = `${label} µg/m³`;

  const meter = div("meter");
  const fill = document.createElement("i");
  const top = limits.err * 1.25;
  fill.style.width = `${Math.min(100, (value / top) * 100)}%`;
  fill.style.background = `var(${colourVar})`;
  const mark = document.createElement("b");
  mark.style.left = `${(limits.warn / top) * 100}%`;
  mark.title = `warns at ${limits.warn}`;
  meter.append(fill, mark);

  f.append(v, l, meter);
  return f;
}

function renderPollutant(body, data, noteEl, key) {
  const s = data.series;
  if (!s || !s.t.length) return empty(body, "No readings in this time frame.");

  noteEl.textContent = `${s.count} readings, ${bucketLabel(s.bucket)}`;
  const spread = s.bucket.seconds > state.meta.reading_interval_seconds;
  const chart = pollutantChart(body, {
    title: key === "pm25" ? "PM2.5" : "PM10",
    unitColour: key === "pm25" ? "--pm25" : "--pm10",
    t: s.t, avg: s[key].avg, min: s[key].min, max: s[key].max,
    warn: state.meta.thresholds[key].warn,
    err: state.meta.thresholds[key].err,
    tzName: state.meta.timezone,
    width: Math.max(280, body.clientWidth || 600),
    showSpread: spread,
  });
  if (spread) noteEl.textContent += " - shaded band is each bucket's min to max";
  state.charts.push(chart);
}

function renderStats(body, data) {
  const s = data.summary;
  if (!s || s.empty) return empty(body, "No readings in this time frame.");

  const rows = [
    ["PM2.5", s.pm25], ["PM10", s.pm10],
  ].map(([name, v]) => `<tr><td>${name}</td><td>${v.avg}</td><td>${v.min}</td>` +
                        `<td>${v.max}</td></tr>`).join("");

  const worst = s.worst_hour
    ? `<p class="trend">Worst hour: <b>${s.worst_hour.local}</b> at ${s.worst_hour.pm25} µg/m³ PM2.5</p>` +
      `<p class="trend">Cleanest hour: <b>${s.best_hour.local}</b> at ${s.best_hour.pm25} µg/m³ PM2.5</p>`
    : "";

  body.innerHTML =
    `<table><thead><tr><th>Pollutant</th><th>Avg</th><th>Min</th><th>Max</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>${worst}` +
    `<p class="trend">${s.count} readings.</p>`;
}

function renderLevels(body, data) {
  const s = data.summary;
  if (!s || s.empty) return empty(body, "No readings in this time frame.");

  const parts = [["ok", "Good", "--ok"], ["warn", "Elevated", "--warn"], ["err", "High", "--err"]];
  const bar = parts.map(([k, , v]) =>
    `<span style="width:${s.level_share[k]}%;background:var(${v})" title="${s.level_share[k]}%"></span>`
  ).join("");
  const legend = parts.map(([k, label, v]) =>
    `<span><i style="background:var(${v})"></i>${label} ${s.level_share[k]}% ` +
    `<span class="trend">(${s.levels[k]})</span></span>`
  ).join("");

  body.innerHTML = `<div class="levelbar">${bar}</div><div class="legend">${legend}</div>`;
}

function renderProfile(body, data) {
  const p = data.patterns;
  if (!p) return empty(body, "No readings in this time frame.");
  const values = p.pm25.by_hour;
  if (!values.some((v) => v != null)) return empty(body, "Not enough data yet.");

  const lo = Math.min(...values.filter((v) => v != null));
  const hi = Math.max(...values.filter((v) => v != null));
  const rows = values.map((v, h) =>
    `<tr><td>${String(h).padStart(2, "0")}:00</td>` +
    `<td><span style="display:inline-block;height:9px;border-radius:3px;` +
    `width:${v == null ? 0 : Math.max(3, (v / (hi || 1)) * 100)}%;` +
    `background:${v == null ? "transparent" : rampColour(v, lo, hi)}"></span></td>` +
    `<td>${v == null ? "--" : v.toFixed(1)}</td></tr>`
  ).join("");

  body.innerHTML =
    `<table><thead><tr><th>Local hour</th><th></th><th>PM2.5</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`;
}

function renderHeatmap(body, data, noteEl) {
  const p = data.patterns;
  if (!p || !p.pm25.grid.flat().some((v) => v != null)) {
    return empty(body, "Not enough data yet.");
  }
  noteEl.textContent = "Mean PM2.5, local time";
  heatmap(body, { days: p.days, grid: p.pm25.grid, unit: "µg/m³" });
}

// ---------- chrome ----------

function buildPresets() {
  el.presets.innerHTML = "";
  for (const [value, label] of PRESETS) {
    const b = document.createElement("button");
    b.textContent = label;
    b.dataset.preset = value;
    b.addEventListener("click", () => {
      state.range = { preset: value, custom: false, from: null, to: null };
      markPresets();
      writeUrl();
      refresh();
    });
    el.presets.append(b);
  }
  markPresets();
}

function markPresets() {
  for (const b of el.presets.children) {
    b.setAttribute("aria-pressed",
      String(!state.range.custom && b.dataset.preset === state.range.preset));
  }
}

function buildToggles() {
  el.toggles.innerHTML = "";
  for (const p of PANELS) {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = state.panels.includes(p.id);
    box.addEventListener("change", () => {
      state.panels = PANELS.filter(
        (q) => (q.id === p.id ? box.checked : state.panels.includes(q.id))
      ).map((q) => q.id);
      saveLayout();
      refresh();
    });
    label.append(box, document.createTextNode(p.title));
    el.toggles.append(label);
  }
}

function activePanels() {
  return state.panels.map((id) => byId[id]).filter(Boolean);
}

function loadLayout() {
  try {
    const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY));
    if (Array.isArray(saved) && saved.length) return saved;
  } catch (_) { /* corrupt or unavailable: the default layout is fine */ }
  return [...DEFAULT_PANELS];
}

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(state.panels));
  } catch (_) { /* private window: the layout just will not persist */ }
}

function preferredTheme() {
  const stored = localStorage.getItem("aq.theme");
  if (stored) return stored;
  if (telegram && telegram.colorScheme) return telegram.colorScheme;
  return (state.meta && state.meta.theme) || "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
}

function paintLiveDot() {
  const d = lastData.latest;
  el.dot.dataset.level = d && !d.stale ? d.level : "";
}

// ---------- url state ----------

function readUrl() {
  const q = new URLSearchParams(location.search);
  const from = q.get("from"), to = q.get("to");
  if (from && to) {
    state.range = { custom: true, from, to, preset: null };
    el.from.value = from.slice(0, 16);
    el.to.value = to.slice(0, 16);
  } else {
    state.range = { preset: q.get("range") || "24h", custom: false, from: null, to: null };
  }
  state.bucket = q.get("bucket") || "";
  el.bucket.value = state.bucket;
  markPresets();
}

function writeUrl() {
  const q = new URLSearchParams(location.search);
  ["range", "from", "to", "bucket"].forEach((k) => q.delete(k));
  if (state.range.custom) {
    q.set("from", state.range.from);
    q.set("to", state.range.to);
  } else {
    q.set("range", state.range.preset);
  }
  if (state.bucket) q.set("bucket", state.bucket);
  history.pushState(null, "", `${location.pathname}?${q}`);
}

// ---------- small helpers ----------

function div(cls) {
  const d = document.createElement("div");
  d.className = cls;
  return d;
}

function empty(body, text) {
  body.innerHTML = `<p class="empty">${text}</p>`;
}

function note(text) {
  el.status.textContent = text;
}

function fail(err) {
  const message = err instanceof ApiError && err.status === 401
    ? "This dashboard opens from inside Telegram. Use the bot's menu button."
    : `Could not load: ${err.message}`;
  note(message);
}

function bucketLabel(bucket) {
  const names = { raw: "every reading", "5m": "5-minute means", "15m": "15-minute means",
                  "1h": "hourly means", "6h": "6-hour means", "1d": "daily means" };
  return names[bucket.name] || `${bucket.seconds}s buckets`;
}

function ago(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 172800) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

function debounce(fn, ms) {
  let id;
  return (...args) => {
    clearTimeout(id);
    id = setTimeout(() => fn(...args), ms);
  };
}

boot();
