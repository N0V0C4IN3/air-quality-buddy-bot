/* The page.
 *
 * One column: what the air is now, one chart, three numbers and the stretches
 * above a limit. The period is the only filter - 24 hours, 7 days or 30 days -
 * and anything finer is a drag across the chart, which zooms to an explicit
 * pair of instants. Period, zoom, detail and pollutants live in the URL, so a
 * view can be linked and survives a refresh.
 *
 * Presets are resolved by the server against its clock and the sensor's
 * timezone; the page works from the instants it answered with. */

import { api, ApiError, telegram } from "./api.js?v=3";
import { DAY, HOUR, MINUTE, ago, duration, makeClock } from "./clock.js?v=3";
import { LEVEL, NAME, f1, mainChart, ring, sized } from "./charts.js?v=3";

const PERIODS = [["24h", "24 hours", "Last 24 hours"], ["7d", "7 days", "Last 7 days"], ["30d", "30 days", "Last 30 days"]];
// The server knows more presets; a link carrying one still opens.
const OTHER_PRESETS = { "1h": "Last hour", "12h": "Last 12 hours", today: "Today", yesterday: "Yesterday", "90d": "Last 90 days" };
const DETAIL = [["smooth", "Smooth"], ["auto", "Auto"], ["detailed", "Detailed"]];
const BUCKETS = [["raw", null], ["5m", 300], ["15m", 900], ["1h", 3600], ["6h", 21600], ["1d", 86400]];
const BUCKET_TEXT = {
  "5m": "a 5-minute average", "15m": "a 15-minute average", "1h": "an hourly average",
  "6h": "a 6-hour average", "1d": "a daily average",
};

const state = {
  period: "24h",       // the preset the switch shows, and what "Zoom out" returns to
  zoom: null,          // [fromMs, toMs] once the chart has been dragged across
  detail: "auto",
  show: { pm25: true, pm10: true },
  meta: null,
  clock: null,
  latest: null,
  view: null,          // {series, summary, episodes} for the current range
  current: null,       // [fromMs, toMs] the server resolved the range to
};
const $ = (id) => document.getElementById(id);
let stopChart = null, seq = 0;

// ---------- boot ----------

async function boot() {
  if (telegram) { telegram.ready(); telegram.expand(); }
  readUrl();
  buildControls();
  wire();
  try {
    state.meta = await api.meta();
  } catch (err) {
    return fail(err);
  }
  state.clock = makeClock(state.meta.timezone);
  applyTheme(preferredTheme());
  $("foot").textContent = `Times in ${state.meta.timezone}. A reading every ${state.meta.reading_interval_seconds / 60} min, kept for ${state.meta.retention_days} days.`;

  await Promise.all([loadLive(), loadRange()]);
  // The dashboard is only ever as fresh as the reader; polling faster than it
  // writes just burns the Pi.
  setInterval(tick, Math.max(30, state.meta.reading_interval_seconds) * 1000);
}

function tick() {
  loadLive();
  // A preset moves with the clock; a zoom is a fixed piece of the past.
  if (!state.zoom && state.period !== "yesterday") loadRange({ quiet: true });
}

// ---------- data ----------

const range = () => (state.zoom ? { from: state.zoom[0], to: state.zoom[1] } : { preset: state.period });

async function loadLive() {
  try {
    state.latest = await api.latest();
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) return;
    state.latest = null;
  }
  renderNow();
}

async function loadRange({ quiet = false } = {}) {
  const mine = ++seq;
  if (!quiet) busy(true);
  try {
    const r = range();
    const [series, summary, episodes] = await Promise.all([
      api.series(r, chooseBucket()), api.summary(r), api.episodes(r),
    ]);
    if (mine !== seq) return;                 // a newer choice won the race
    state.view = { series, summary, episodes };
    state.current = [Date.parse(series.range.from), Date.parse(series.range.to)];
    render();
  } catch (err) {
    if (mine === seq) fail(err);
  } finally {
    if (mine === seq) busy(false);
  }
}

/* The bucket is chosen here so the chart gets about one point per 2-3 pixels
 * of its own width; the server still enforces its point budget on top. */
function chooseBucket() {
  const interval = state.meta.reading_interval_seconds;
  const ladder = BUCKETS.map(([n, s]) => [n, s || interval]).filter(([, s], i) => i === 0 || s > interval);
  const span = (state.zoom ? state.zoom[1] - state.zoom[0] : presetSpan()) / 1000;
  const target = Math.max(80, Math.min(400, ($("chart").clientWidth || 340) / 2.5));
  let i = ladder.findIndex(([, s]) => span / s <= target);
  if (i < 0) i = ladder.length - 1;
  if (state.detail === "smooth") i = Math.min(ladder.length - 1, i + 1);
  if (state.detail === "detailed") i = Math.max(0, i - 1);
  return ladder[i][0];
}

function presetSpan() {
  const p = state.period;
  if (p === "today") { const n = state.clock.parts(Date.now()); return Math.max(HOUR, n.hour * HOUR + n.minute * MINUTE); }
  return { "1h": HOUR, "12h": 12 * HOUR, yesterday: DAY, "7d": 7 * DAY, "30d": 30 * DAY, "90d": 90 * DAY }[p] || DAY;
}

// ---------- changing what is shown ----------

function setPeriod(period) {
  state.period = period;
  state.zoom = null;
  commit();
}

function zoomTo(a, b) {
  const span = Math.max(HOUR, b - a), mid = (a + b) / 2;
  state.zoom = [Math.round(mid - span / 2), Math.round(Math.min(Date.now(), mid + span / 2))];
  commit();
}

function zoomOut() {
  if (!state.zoom) return;
  state.zoom = null;
  commit();
}

function commit({ push = true } = {}) {
  if (push) writeUrl();
  syncControls();
  loadRange();
}

// ---------- controls ----------

function buildControls() {
  for (const [key, label] of PERIODS) $("periods").append(button(label, () => setPeriod(key), { "data-period": key }));
  for (const k of ["pm25", "pm10"]) {
    const b = button("", () => {
      const other = k === "pm25" ? "pm10" : "pm25";
      if (state.show[k] && !state.show[other]) return;       // never zero pollutants
      state.show[k] = !state.show[k];
      writeUrl();
      syncControls();
      renderChart();
    }, { "data-key": k });
    b.innerHTML = `<span class="swatch" style="background:var(--${k})"></span>${NAME[k]}`;
    $("pollutants").append(b);
  }
  for (const [key, label] of DETAIL) {
    $("detail").append(button(label, () => { state.detail = key; writeUrl(); syncControls(); loadRange(); }, { "data-detail": key }));
  }
  syncControls();
}

function syncControls() {
  for (const b of $("periods").children) b.setAttribute("aria-pressed", String(!state.zoom && b.dataset.period === state.period));
  for (const b of $("pollutants").children) b.setAttribute("aria-pressed", String(state.show[b.dataset.key]));
  for (const b of $("detail").children) b.setAttribute("aria-pressed", String(b.dataset.detail === state.detail));
  $("zoom-out").hidden = !state.zoom;
}

function wire() {
  $("zoom-out").addEventListener("click", zoomOut);
  $("theme-btn").addEventListener("click", async () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("aq.theme", next); } catch (_) { /* private window */ }
    try { await api.setTheme(next); } catch (_) { /* browser-only visitor: the local choice is enough */ }
  });
  window.addEventListener("popstate", () => { readUrl(); commit({ push: false }); });
}

// ---------- render: now ----------

function renderNow() {
  const d = state.latest, rings = $("rings"), meta = $("now-meta");
  if (!state.meta) return;
  if (!d) {
    rings.replaceChildren(el("p", { class: "empty", text: "No readings yet." }));
    meta.replaceChildren();
    $("live-dot").dataset.level = "";
    $("updated").textContent = "";
    return;
  }
  const t = state.meta.thresholds;
  rings.replaceChildren(ring(196, d.pm25, t.pm25, "PM2.5", { quiet: d.stale }), ring(128, d.pm10, t.pm10, "PM10", { quiet: d.stale }));
  meta.replaceChildren(d.stale
    ? el("span", { class: "pill quiet", text: `Sensor quiet · last reading ${ago(d.age_seconds)}` })
    : el("span", { class: "pill", html: `<span class="swatch" style="background:${LEVEL[d.level].colour}"></span>Air is ${d.level_label.toLowerCase()}` }));
  $("live-dot").dataset.level = d.stale ? "" : d.level;
  $("updated").textContent = d.stale ? "" : `Updated ${ago(d.age_seconds)}`;
}

// ---------- render: the period ----------

function render() {
  if (!state.view) return;
  renderTitle();
  renderChart();
  renderStats();
  renderEpisodes();
}

function renderTitle() {
  const c = state.clock, [a, b] = state.current;
  let title;
  if (!state.zoom) title = (PERIODS.find(([k]) => k === state.period) || [])[2] || OTHER_PRESETS[state.period];
  else if (c.sameDay(a, b - 1)) title = `${c.day(a)} · ${c.hhmm(a)}–${c.hhmm(b)}`;
  else title = `${c.day(a)} ${c.hhmm(a)} → ${c.day(b)} ${c.hhmm(b)}`;
  $("chart-title").textContent = title;
}

function renderChart() {
  const host = $("chart"), s = state.view.series;
  stopChart?.();
  host.classList.toggle("single", keys().length === 1);
  stopChart = sized(host, (w, h, node) => mainChart(w, h, node, {
    series: s, keys: keys(), limits: state.meta.thresholds, clock: state.clock, shade: true,
    onZoom: zoomTo, onReset: zoomOut,
  }));
  const name = s.bucket.name;
  $("bucket-caption").textContent = name === "raw"
    ? `Each point is a single reading. Drag across the chart to zoom in.`
    : `Each point is ${BUCKET_TEXT[name] || `a ${s.bucket.seconds}-second average`}; the faint band is its lowest to highest reading.`;
}

function renderStats() {
  const s = state.view.summary, box = $("stats");
  if (s.empty) return box.replaceChildren(el("div", { class: "card empty", text: "No readings in this period." }));
  const every = state.meta.reading_interval_seconds * 1000;
  const above = (s.levels.warn + s.levels.err) * every;
  const tile = (value, label) => el("div", { class: "card stat" }, [el("b", { text: value }), el("span", { text: label })]);
  box.replaceChildren(
    tile(f1(s.pm25.avg), "average PM2.5"),
    tile(f1(s.pm25.max), "peak PM2.5"),
    tile(above ? brief(above) : "none", "above a limit"),
  );
}

function renderEpisodes() {
  const { episodes, count } = state.view.episodes, c = state.clock, box = $("episodes");
  $("eps-count").textContent = count ? `${count} ${count === 1 ? "time" : "times"}` : "";
  if (!count) return box.replaceChildren(el("p", { class: "empty", text: "Nothing went above a limit in this period." }));
  const list = el("ul", { class: "episodes" });
  for (const ep of episodes.slice(0, 4)) {
    const a = Date.parse(ep.from), b = Date.parse(ep.to);
    const when = c.sameDay(a, b - 1) ? `${c.day(a)} · ${c.hhmm(a)}–${c.hhmm(b)}` : `${c.day(a)} ${c.hhmm(a)} → ${c.day(b)} ${c.hhmm(b)}`;
    const peak = ep.pollutant === "pm25" ? ep.pm25_peak : ep.pm10_peak;
    list.append(el("li", {}, [
      el("i", { style: `background:${LEVEL[ep.level].colour}` }),
      el("span", {}, [el("b", { text: when }), `${ep.level_label} for ${duration(b - a)}, ${NAME[ep.pollutant]} peaked at ${f1(peak)} µg/m³`]),
    ]));
  }
  if (count > 4) list.append(el("li", { class: "more", text: `+ ${count - 4} earlier in this period` }));
  box.replaceChildren(list);
}

// ---------- theme ----------

function preferredTheme() {
  try { const stored = localStorage.getItem("aq.theme"); if (stored) return stored; } catch (_) { /* unavailable */ }
  if (telegram && telegram.colorScheme) return telegram.colorScheme;
  return (state.meta && state.meta.theme) || "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = $("theme-btn"), dark = theme === "dark";
  btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  btn.innerHTML = dark
    ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
    : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  // Inside Telegram, paint the Mini App's own header and bottom to match the page.
  if (telegram && telegram.isVersionAtLeast && telegram.isVersionAtLeast("6.1")) {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    try { telegram.setHeaderColor(bg); telegram.setBackgroundColor(bg); } catch (_) { /* older client */ }
  }
}

// ---------- url ----------

function readUrl() {
  const q = new URLSearchParams(location.search);
  const from = Date.parse(q.get("from") || ""), to = Date.parse(q.get("to") || "");
  const preset = q.get("range");
  state.period = preset && (PERIODS.some(([k]) => k === preset) || preset in OTHER_PRESETS) ? preset : "24h";
  state.zoom = !Number.isNaN(from) && !Number.isNaN(to) && to > from ? [from, to] : null;
  state.detail = DETAIL.some(([k]) => k === q.get("detail")) ? q.get("detail") : "auto";
  const show = q.get("show");
  state.show = { pm25: show !== "pm10", pm10: show !== "pm25" };
}

function writeUrl() {
  const q = new URLSearchParams(location.search);
  for (const k of ["range", "from", "to", "bucket", "detail", "show", "shade"]) q.delete(k);
  q.set("range", state.period);
  if (state.zoom) { q.set("from", new Date(state.zoom[0]).toISOString()); q.set("to", new Date(state.zoom[1]).toISOString()); }
  if (state.detail !== "auto") q.set("detail", state.detail);
  if (keys().length === 1) q.set("show", keys()[0]);
  const next = `${location.pathname}?${q}`;
  if (next !== `${location.pathname}${location.search}`) history.pushState(null, "", next);
}

// ---------- helpers ----------

/* A duration that fits a stat tile on a phone: "45 min", "3.5 h", "37 h", "4 days". */
function brief(ms) {
  const h = ms / HOUR;
  if (h < 1) return `${Math.round(ms / MINUTE)} min`;
  if (h < 10) return `${Math.round(h * 2) / 2} h`;
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} days`;
}

const keys = () => ["pm25", "pm10"].filter((k) => state.show[k]);

function el(tag, props = {}, kids = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k === "html") e.innerHTML = v;
    else if (k === "style") e.style.cssText = v;
    else e.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) if (c != null) e.append(c);
  return e;
}

function button(label, onClick, attrs = {}) {
  const b = el("button", { type: "button", text: label, ...attrs });
  b.addEventListener("click", onClick);
  return b;
}

function busy(on) {
  for (const sel of [".main", ".stats", ".episodes-card"]) document.querySelector(sel).setAttribute("aria-busy", String(on));
}

function fail(err) {
  const message = err instanceof ApiError && err.status === 401
    ? "This dashboard opens from inside Telegram. Use the bot's menu button."
    : `Could not load: ${err.message}`;
  $("chart-title").textContent = "";
  $("bucket-caption").replaceChildren(el("span", { class: "error", text: message }));
}

boot();
