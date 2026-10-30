/* The page.
 *
 * One filter drives every panel except "Now": a time range (a preset the
 * server resolves, or an explicit pair of instants), a level of detail, which
 * pollutants to show, and whether to shade above-limit periods. All of it
 * lives in the URL, so a view can be linked and survives a refresh.
 *
 * Presets are resolved by the server against its clock and the sensor's
 * timezone; the page then works from the instants the server answered with
 * (state.current), so stepping back from "Today" lands on real local days. */

import { api, ApiError, telegram } from "./api.js";
import { DAY, HOUR, MINUTE, ago, duration, makeClock } from "./clock.js";
import { LEVEL, NAME, f1, hourBars, mainChart, ring, sized, timeline, weekMatrix } from "./charts.js";

const PRESETS = [
  ["24h", "24 hours", DAY], ["today", "Today", null], ["yesterday", "Yesterday", DAY],
  ["7d", "7 days", 7 * DAY], ["30d", "30 days", 30 * DAY], ["90d", "90 days", 90 * DAY],
];
// The server knows two more; a link carrying one still works.
const SERVER_PRESETS = { "1h": HOUR, "12h": 12 * HOUR };
const DETAIL = [["smooth", "Smooth"], ["auto", "Auto"], ["detailed", "Detailed"]];
const BUCKETS = [["raw", null], ["5m", 300], ["15m", 900], ["1h", 3600], ["6h", 21600], ["1d", 86400]];
const BUCKET_TEXT = {
  "5m": "a 5-minute average", "15m": "a 15-minute average", "1h": "an hourly average",
  "6h": "a 6-hour average", "1d": "a daily average",
};
const DEFAULTS = { range: { preset: "24h" }, detail: "auto", show: { pm25: true, pm10: true }, shade: true };

const state = {
  ...structuredClone(DEFAULTS),
  meta: null,
  clock: null,
  latest: null,
  days: null,
  view: null,          // {series, summary, patterns, episodes} for the current range
  current: null,       // [fromMs, toMs] the server resolved the range to
};
const $ = (id) => document.getElementById(id);
const stop = {};       // disconnectors for the charts that redraw on resize
let seq = 0, ticks = 0;

// ---------- boot ----------

async function boot() {
  if (telegram) { telegram.ready(); telegram.expand(); }
  readUrl();
  buildControls();
  wire();
  $("range-label").textContent = "Loading…";

  try {
    state.meta = await api.meta();
  } catch (err) {
    return fail(err);
  }
  state.clock = makeClock(state.meta.timezone);
  applyTheme(preferredTheme());
  $("foot").textContent = `Times in ${state.meta.timezone}. A reading every ${state.meta.reading_interval_seconds} s, kept for ${state.meta.retention_days} days.`;

  await Promise.all([loadLive(), loadDays(), loadRange()]);
  // The dashboard is only ever as fresh as the reader; polling faster than it
  // writes just burns the Pi.
  setInterval(tick, Math.max(30, state.meta.reading_interval_seconds) * 1000);
}

function tick() {
  loadLive();
  if (++ticks % 10 === 0) loadDays();
  // A preset moves with the clock; an explicit range is a fixed piece of the past.
  if (state.range.preset && state.range.preset !== "yesterday") loadRange({ quiet: true });
}

// ---------- data ----------

async function loadLive() {
  try {
    state.latest = await api.latest();
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) return;
    state.latest = null;
  }
  renderNow();
}

async function loadDays() {
  try {
    state.days = await api.days();
    renderTimeline();
  } catch (_) { /* the timeline is a convenience; the rest still works */ }
}

async function loadRange({ quiet = false } = {}) {
  const mine = ++seq;
  if (!quiet) busy(true);
  try {
    const r = state.range;
    const [series, summary, patterns, episodes] = await Promise.all([
      api.series(r, chooseBucket()), api.summary(r), api.patterns(r), api.episodes(r),
    ]);
    if (mine !== seq) return;                 // a newer filter won the race
    state.view = { series, summary, patterns, episodes };
    state.current = [Date.parse(series.range.from), Date.parse(series.range.to)];
    renderRange();
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
  const span = approxSpan() / 1000;
  const width = $("chart").clientWidth || 340;
  const target = Math.max(80, Math.min(400, width / 2.5));
  let i = ladder.findIndex(([, s]) => span / s <= target);
  if (i < 0) i = ladder.length - 1;
  if (state.detail === "smooth") i = Math.min(ladder.length - 1, i + 1);
  if (state.detail === "detailed") i = Math.max(0, i - 1);
  return ladder[i][0];
}

function approxSpan() {
  const r = state.range;
  if (!r.preset) return r.to - r.from;
  if (r.preset === "today") { const p = state.clock.parts(Date.now()); return Math.max(HOUR, p.hour * HOUR + p.minute * MINUTE); }
  const known = PRESETS.find(([k]) => k === r.preset);
  return known ? known[2] : SERVER_PRESETS[r.preset] || DAY;
}

// ---------- changing the filter ----------

function firstDay() {
  return state.days ? Date.parse(state.days.from) : Date.now() - state.meta.retention_days * DAY;
}

function setRange(a, b) {
  const now = Date.now(), first = firstDay();
  const span = Math.max(HOUR, b - a);
  if (b > now) { b = now; a = b - span; }
  if (a < first) { a = first; b = Math.min(now, a + span); }
  state.range = { from: Math.round(a), to: Math.round(b) };
  commit();
}

function setPreset(preset) {
  state.range = { preset };
  commit();
}

function step(dir) {
  if (!state.current) return;
  const [a, b] = state.current, span = b - a, p = state.range.preset;
  if (p === "today" && dir < 0) return setPreset("yesterday");
  if (p === "yesterday" && dir > 0) return setPreset("today");
  setRange(a + dir * span, b + dir * span);
}

function reset() {
  Object.assign(state, structuredClone(DEFAULTS));
  syncControls();
  commit();
}

function commit({ push = true } = {}) {
  if (push) writeUrl();
  syncControls();
  if (!state.range.preset) renderRangeLabel([state.range.from, state.range.to]);
  loadRange();
}

// ---------- controls ----------

function buildControls() {
  const presets = $("presets");
  for (const [key, label] of PRESETS) {
    presets.append(button(label, () => setPreset(key), { "data-preset": key }));
  }
  const customBtn = button("Custom…", toggleCustom, { class: "chip", "aria-expanded": "false", "aria-controls": "custom", id: "custom-btn" });
  presets.append(customBtn);

  for (const k of ["pm25", "pm10"]) {
    const b = button("", () => {
      const other = k === "pm25" ? "pm10" : "pm25";
      if (state.show[k] && !state.show[other]) return;       // never zero pollutants
      state.show[k] = !state.show[k];
      writeUrl();
      syncControls();
      renderRange();
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
  for (const b of $("presets").querySelectorAll("[data-preset]")) b.setAttribute("aria-pressed", String(b.dataset.preset === state.range.preset));
  for (const b of $("pollutants").children) b.setAttribute("aria-pressed", String(state.show[b.dataset.key]));
  for (const b of $("detail").children) b.setAttribute("aria-pressed", String(b.dataset.detail === state.detail));
  $("shade").checked = state.shade;
}

function toggleCustom() {
  const form = $("custom"), open = form.hidden;
  form.hidden = !open;
  $("custom-btn").setAttribute("aria-expanded", String(open));
  if (open && state.current && state.clock) {
    $("from").value = state.clock.toInput(state.current[0]);
    $("to").value = state.clock.toInput(state.current[1]);
    $("from").focus();
  }
}

function wire() {
  $("prev").addEventListener("click", () => step(-1));
  $("next").addEventListener("click", () => step(1));
  $("shade").addEventListener("change", (e) => { state.shade = e.target.checked; writeUrl(); renderChart(); renderSummaryLine(); });
  $("custom").addEventListener("submit", (e) => {
    e.preventDefault();
    const a = state.clock.fromInput($("from").value), b = state.clock.fromInput($("to").value);
    if (Number.isNaN(a) || Number.isNaN(b)) return void ($("custom-err").textContent = "Fill in both dates.");
    if (a >= b) return void ($("custom-err").textContent = "“From” has to be before “To”.");
    $("custom-err").textContent = "";
    toggleCustom();
    setRange(a, b);
  });
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

// ---------- render: the range ----------

function renderRange() {
  if (!state.view) return;
  renderRangeLabel(state.current);
  renderSummaryLine();
  renderChart();
  renderEpisodes();
  renderLevels();
  renderStats();
  renderHours();
  renderWeek();
  renderTimeline();
  const now = Date.now();
  $("next").disabled = state.current[1] >= now - 2 * MINUTE;
  $("prev").disabled = state.current[0] <= firstDay() + MINUTE;
}

function renderRangeLabel([a, b]) {
  const c = state.clock, tail = b >= Date.now() - 5 * MINUTE;
  const endsMidnight = c.isMidnight(b), lastDay = endsMidnight ? b - 1 : b;
  let main, sub;
  if (c.sameDay(a, lastDay)) {
    main = c.day(a);
    sub = `${c.hhmm(a)} → ${endsMidnight ? "24:00" : c.hhmm(b)}${tail ? " (now)" : ""} · ${duration(b - a)}`;
  } else {
    main = `${c.short(a)} → ${tail ? "now" : c.short(lastDay)}`;
    sub = `${c.weekday(a)} ${c.hhmm(a)} to ${tail ? "now" : `${c.weekday(b)} ${c.hhmm(b)}`} · ${duration(b - a)}`;
  }
  $("range-label").innerHTML = "";
  $("range-label").append(main, el("small", { text: sub }));
}

function renderSummaryLine() {
  const line = $("summary-line"), s = state.view.series;
  const isDefault = JSON.stringify({ range: state.range, detail: state.detail, show: state.show, shade: state.shade }) === JSON.stringify(DEFAULTS);
  line.replaceChildren(
    el("span", { html: `Showing <b>${keys().map((k) => NAME[k]).join(" + ")}</b> · <b>${duration(state.current[1] - state.current[0])}</b> · <b>${s.count.toLocaleString("en-GB")}</b> readings` }),
  );
  if (!isDefault) line.append(button("Reset", reset, { class: "link" }));
}

function renderChart() {
  const host = $("chart"), s = state.view.series;
  stop.chart?.();
  host.classList.toggle("single", keys().length === 1);
  stop.chart = sized(host, (w, h, node) => mainChart(w, h, node, {
    series: s, keys: keys(), limits: state.meta.thresholds, clock: state.clock, shade: state.shade,
    onZoom: setRange, onReset: reset,
  }));
  const name = s.bucket.name;
  $("bucket-caption").textContent = name === "raw"
    ? `Each point is a single reading (every ${state.meta.reading_interval_seconds / 60} min).`
    : `Each point is ${BUCKET_TEXT[name] || `a ${s.bucket.seconds}-second average`}; the faint band is its lowest to highest reading.`;
}

function renderEpisodes() {
  const { episodes, count } = state.view.episodes, c = state.clock, box = $("episodes");
  if (!count) return box.replaceChildren(el("p", { class: "empty", text: "Nothing went above a limit in this range." }));
  const list = el("ul", { class: "episodes" });
  for (const ep of episodes.slice(0, 4)) {
    const a = Date.parse(ep.from), b = Date.parse(ep.to);
    const when = c.sameDay(a, b - 1) ? `${c.day(a)} · ${c.hhmm(a)}–${c.hhmm(b)}` : `${c.day(a)} ${c.hhmm(a)} → ${c.day(b)} ${c.hhmm(b)}`;
    const peak = ep.pollutant === "pm25" ? ep.pm25_peak : ep.pm10_peak;
    const b2 = button("", () => setRange(a - 2 * HOUR, b + 2 * HOUR), { "aria-label": `Zoom to ${when}` });
    b2.append(
      el("span", { class: "swatch", style: `background:${LEVEL[ep.level].colour};margin:0` }),
      el("span", {}, [el("div", { class: "when", text: when }), el("div", { class: "what", text: `${ep.level_label}, ${NAME[ep.pollutant]} peaked at ${f1(peak)} µg/m³` })]),
      el("span", { class: "go", text: "Zoom" }),
    );
    list.append(el("li", {}, [b2]));
  }
  if (count > 4) list.append(el("li", { class: "more", text: `+ ${count - 4} earlier in this range` }));
  box.replaceChildren(list);
}

function renderLevels() {
  const s = state.view.summary, box = $("levels");
  if (s.empty) return box.replaceChildren(el("p", { class: "empty", text: "No readings in this range." }));
  const every = state.meta.reading_interval_seconds * 1000;
  const parts = ["ok", "warn", "err"];
  box.replaceChildren(
    el("div", { class: "segbar", role: "img", "aria-label": parts.map((k) => `${LEVEL[k].label} ${s.level_share[k]}%`).join(", ") },
      parts.filter((k) => s.levels[k]).map((k) => el("div", { style: `flex:${s.levels[k]};background:${LEVEL[k].colour}` }))),
    el("div", { class: "seglegend" }, parts.map((k) => el("div", {}, [
      el("span", { html: `<span class="swatch" style="background:${LEVEL[k].colour}"></span>${LEVEL[k].label}` }),
      el("span", { text: `${Math.round(s.level_share[k])}% · ${duration(s.levels[k] * every)}` }),
    ]))),
  );
}

function renderStats() {
  const s = state.view.summary, box = $("stats");
  if (s.empty) return box.replaceChildren(el("p", { class: "empty", text: "No readings in this range." }));
  const table = el("table", {}, [
    el("thead", {}, el("tr", {}, ["", "Avg", "Min", "Max"].map((t) => el("th", { text: t })))),
    el("tbody", {}, keys().map((k) => el("tr", {}, [
      el("td", { html: `<span class="swatch" style="background:var(--${k})"></span>${NAME[k]}` }),
      ...["avg", "min", "max"].map((f) => el("td", { text: f1(s[k][f]) })),
    ]))),
  ]);
  box.replaceChildren(table);
  if (s.worst_hour && s.best_hour) {
    box.append(el("div", { class: "hourpair" }, [
      el("div", {}, ["Worst hour", el("b", { text: s.worst_hour.local }), `${f1(s.worst_hour.pm25)} µg/m³ PM2.5`]),
      el("div", {}, ["Cleanest hour", el("b", { text: s.best_hour.local }), `${f1(s.best_hour.pm25)} µg/m³ PM2.5`]),
    ]));
  }
}

function renderHours() {
  const k = primary(), values = state.view.patterns[k].by_hour, host = $("hours");
  $("hours-name").textContent = `Hour of day · ${NAME[k]}`;
  stop.hours?.();
  if (!values.some((v) => v != null)) {
    $("hours-note").textContent = "";
    return host.replaceChildren(el("p", { class: "empty", text: "No readings in this range." }));
  }
  stop.hours = sized(host, (w, h, node) => {
    const peak = hourBars(w, h, node, { values, key: k });
    $("hours-note").textContent = `worst around ${String(peak).padStart(2, "0")}:00`;
  });
}

function renderWeek() {
  const k = primary(), p = state.view.patterns, box = $("week");
  $("week-name").textContent = `Weekday by hour · ${NAME[k]}`;
  stop.week?.();
  const span = state.current[1] - state.current[0];
  if (span < 7 * DAY - HOUR) {
    return box.replaceChildren(el("p", { class: "empty" }, [
      `Weekly patterns need at least 7 days; this range is ${duration(span)}.`,
      button("Show the last 30 days", () => setPreset("30d"), { class: "chip" }),
    ]));
  }
  const host = el("div", { class: "chart", style: "height:180px" });
  box.replaceChildren(host);
  stop.week = sized(host, (w, h, node) => weekMatrix(w, h, node, { grid: p[k].grid, days: p.days, key: k }));
}

function renderTimeline() {
  if (!state.days || !state.clock) return;
  const range = state.current || (state.range.preset ? null : [state.range.from, state.range.to]);
  if (!range) return;
  stop.timeline?.();
  stop.timeline = sized($("timeline"), (w, h, node) => timeline(w, h, node, {
    days: state.days.days, start: Date.parse(state.days.from), end: Date.now(), range,
    clock: state.clock, minSpan: HOUR,
    onPreview: renderRangeLabel,
    onCommit: setRange,
    onJump: (t) => { const span = range[1] - range[0]; setRange(t - span / 2, t + span / 2); },
  }));
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
  // Inside Telegram, paint the Mini App's own header to match the page.
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
  if (!Number.isNaN(from) && !Number.isNaN(to) && to > from) state.range = { from, to };
  else if (preset && (PRESETS.some(([k]) => k === preset) || preset in SERVER_PRESETS)) state.range = { preset };
  else state.range = { ...DEFAULTS.range };
  state.detail = DETAIL.some(([k]) => k === q.get("detail")) ? q.get("detail") : "auto";
  const show = q.get("show");
  state.show = { pm25: show !== "pm10", pm10: show !== "pm25" };
  state.shade = q.get("shade") !== "0";
}

function writeUrl() {
  const q = new URLSearchParams(location.search);
  for (const k of ["range", "from", "to", "bucket", "detail", "show", "shade"]) q.delete(k);
  if (state.range.preset) q.set("range", state.range.preset);
  else { q.set("from", new Date(state.range.from).toISOString()); q.set("to", new Date(state.range.to).toISOString()); }
  if (state.detail !== "auto") q.set("detail", state.detail);
  if (keys().length === 1) q.set("show", keys()[0]);
  if (!state.shade) q.set("shade", "0");
  const next = `${location.pathname}?${q}`;
  if (next !== `${location.pathname}${location.search}`) history.pushState(null, "", next);
}

// ---------- helpers ----------

const keys = () => ["pm25", "pm10"].filter((k) => state.show[k]);
const primary = () => (state.show.pm25 ? "pm25" : "pm10");

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
  for (const sel of [".main", ".episodes-card", ".levels", ".stats", ".hours", ".week"]) {
    document.querySelector(sel).setAttribute("aria-busy", String(on));
  }
}

function fail(err) {
  const message = err instanceof ApiError && err.status === 401
    ? "This dashboard opens from inside Telegram. Use the bot's menu button."
    : `Could not load: ${err.message}`;
  $("summary-line").replaceChildren(el("span", { class: "error", text: message }));
  if (!state.current) $("range-label").textContent = "";
}

boot();
