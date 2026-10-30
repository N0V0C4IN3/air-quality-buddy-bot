/* Every chart on the page, drawn as SVG.
 *
 * No chart library: the page has five small, specific charts, and the Mini App
 * has to load fast on a phone. Colours are CSS custom properties referenced by
 * name (style="fill:var(--pm25)"), so switching theme repaints without
 * redrawing. The amber/red hues (--warn/--err) only ever mark threshold state,
 * never a series - the same rule the bot's PNGs follow. */

import { DAY, HOUR } from "./clock.js";

const NS = "http://www.w3.org/2000/svg";
let uid = 0;
const nid = (p) => `${p}-${++uid}`;

export const NAME = { pm25: "PM2.5", pm10: "PM10" };
export const LEVEL = {
  ok: { label: "Good", colour: "var(--ok)" },
  warn: { label: "Elevated", colour: "var(--warn)" },
  err: { label: "High", colour: "var(--err)" },
};
export const f1 = (v) => (v == null ? "–" : Number(v).toFixed(1));

export function sv(tag, attrs = {}, kids = []) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) if (c) e.append(c);
  return e;
}
const lin = (d0, d1, r0, r1) => (v) => r0 + ((v - d0) / (d1 - d0 || 1)) * (r1 - r0);
const niceMax = (v) => [10, 20, 25, 40, 50, 60, 75, 80, 100, 120, 150, 200, 250, 300, 400, 500]
  .find((x) => x >= v) || Math.ceil(v / 100) * 100;
const levelOf = (v, limits) => (v >= limits.err ? "err" : v >= limits.warn ? "warn" : "ok");
const text = (x, y, t, style, anchor) =>
  sv("text", { x, y, "text-anchor": anchor, style, text: String(t) });

function root(w, h, label) {
  return sv("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": label });
}
function glow(svg, sd = 3) {
  const id = nid("glow");
  svg.append(sv("defs", {}, sv("filter", { id, x: "-20%", y: "-50%", width: "140%", height: "200%" }, [
    sv("feGaussianBlur", { stdDeviation: sd, result: "b" }),
    sv("feMerge", {}, [sv("feMergeNode", { in: "b" }), sv("feMergeNode", { in: "SourceGraphic" })]),
  ])));
  return `url(#${id})`;
}
function fade(svg, colour, top = 0.45, bottom = 0) {
  const id = nid("fade");
  svg.append(sv("defs", {}, sv("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, [
    sv("stop", { offset: "0", style: `stop-color:${colour};stop-opacity:${top}` }),
    sv("stop", { offset: "1", style: `stop-color:${colour};stop-opacity:${bottom}` }),
  ])));
  return `url(#${id})`;
}

/* A smooth line that never overshoots its data (monotone cubic): a spike to 48
 * is drawn as 48, not as 52 with a dip below zero beside it. */
export function monotone(pts) {
  const n = pts.length;
  if (!n) return "";
  if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
  const x = pts.map((p) => p[0]), y = pts.map((p) => p[1]), m = [], t = [];
  for (let i = 0; i < n - 1; i++) m.push((y[i + 1] - y[i]) / ((x[i + 1] - x[i]) || 1));
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${x[0].toFixed(1)},${y[0].toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const dx = (x[i + 1] - x[i]) / 3;
    d += `C${(x[i] + dx).toFixed(1)},${(y[i] + t[i] * dx).toFixed(1)} ` +
         `${(x[i + 1] - dx).toFixed(1)},${(y[i + 1] - t[i + 1] * dx).toFixed(1)} ` +
         `${x[i + 1].toFixed(1)},${y[i + 1].toFixed(1)}`;
  }
  return d;
}
const arc = (cx, cy, r, a0, a1) => {
  const p = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const [x0, y0] = p(a0), [x1, y1] = p(a1);
  return `M${x0.toFixed(2)},${y0.toFixed(2)}A${r},${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
};

/* Draw into `host` now and again whenever its width changes. Returns a
 * disconnect function so a re-render does not leave observers behind. */
export function sized(host, draw) {
  let last = 0;
  const ro = new ResizeObserver(([e]) => {
    const w = Math.round(e.contentRect.width);
    if (w === last || w < 20) return;
    last = w;
    host.replaceChildren();
    draw(w, host.clientHeight || 200, host);
  });
  ro.observe(host);
  return () => ro.disconnect();
}

// ---------- the "now" rings ----------

/* The reading against its own limits: the arc runs from zero to that
 * pollutant's high limit, the warn limit is where the track turns amber. */
export function ring(size, value, limits, name, { quiet = false } = {}) {
  const lv = quiet ? null : levelOf(value, limits);
  const svg = root(size, size, `${name} now ${f1(value)} µg/m³, ${quiet ? "sensor quiet" : LEVEL[lv].label}`);
  const g = glow(svg, 4);
  const c = size / 2, r = size / 2 - 12, a0 = Math.PI * 0.75, sweep = Math.PI * 1.5;
  const w = size > 160 ? 12 : 9;
  const ang = (v) => a0 + (Math.min(Math.max(v, 0), limits.err) / limits.err) * sweep;
  const colour = quiet ? "var(--ink2)" : LEVEL[lv].colour;
  svg.append(sv("path", { d: arc(c, c, r, a0, ang(limits.warn)), style: `fill:none;stroke:var(--ok);stroke-opacity:.18;stroke-width:${w};stroke-linecap:round` }));
  svg.append(sv("path", { d: arc(c, c, r, ang(limits.warn), a0 + sweep), style: `fill:none;stroke:var(--warn);stroke-opacity:.2;stroke-width:${w}` }));
  svg.append(sv("path", { d: arc(c, c, r, a0, Math.max(ang(value), a0 + 0.05)), style: `fill:none;stroke:${colour};stroke-width:${w};stroke-linecap:round`, filter: quiet ? null : g }));
  svg.append(sv("circle", { cx: c + r * Math.cos(ang(value)), cy: c + r * Math.sin(ang(value)), r: w / 2 + 2, style: `fill:var(--bg);stroke:${colour};stroke-width:3` }));
  for (const v of size > 160 ? [limits.warn, limits.err] : [limits.warn]) {
    const a = ang(v), ro = r - w / 2 - 9;
    svg.append(text(c + ro * Math.cos(a), c + ro * Math.sin(a) + 4, v, "font-size:10px;fill:var(--ink2)", "middle"));
  }
  svg.append(text(c, c - size * 0.02, f1(value), `font-size:${size * 0.21}px;font-weight:700;fill:var(--ink)`, "middle"));
  svg.append(text(c, c + size * 0.12, `${name} · µg/m³`, `font-size:${Math.max(10, size * 0.065)}px;fill:var(--ink2)`, "middle"));
  svg.append(sv("circle", { cx: c - size * 0.1, cy: c + size * 0.24, r: 3.5, style: `fill:${colour}` }));
  svg.append(text(c - size * 0.06, c + size * 0.24 + 4, quiet ? "Quiet" : LEVEL[lv].label, `font-size:${Math.max(10, size * 0.062)}px;font-weight:600;fill:var(--ink)`));
  return svg;
}

// ---------- the main chart ----------

/* One pane per pollutant, stacked on a shared time axis: a single y-axis
 * cannot carry two different warn limits honestly.
 *
 * series: the /api/series response.  keys: which pollutants to draw.
 * Hover reads values; dragging across the plot calls onZoom(fromMs, toMs). */
export function mainChart(w, h, host, { series, keys, limits, clock, shade, onZoom, onReset }) {
  const a = Date.parse(series.range.from), b = Date.parse(series.range.to);
  const size = series.bucket.seconds * 1000, raw = series.bucket.name === "raw";
  const n = series.t.length;
  const mid = (i) => Math.min(b, Math.max(a, series.t[i] * 1000 + (raw ? 0 : size / 2)));
  const svg = root(w, h, `${keys.map((k) => NAME[k]).join(" and ")} from ${clock.day(a)} ${clock.hhmm(a)} to ${clock.day(b)} ${clock.hhmm(b)}`);
  const g = glow(svg, 3);
  const L = 32, R = 10, top = 8, bottom = h - 22, gap = 22;
  const ph = (bottom - top - gap * (keys.length - 1)) / keys.length;
  const x = lin(a, b, L, w - R), tAt = (px) => a + ((px - L) / (w - L - R)) * (b - a);
  const ys = {};

  keys.forEach((k, i) => {
    const y0 = top + i * (ph + gap), lim = limits[k];
    const peak = n ? Math.max(...series[k].max) : 0;
    const dom = niceMax(Math.max(peak * 1.1, lim.warn * 1.2));
    const y = lin(0, dom, y0 + ph, y0);
    ys[k] = y;
    if (shade) {
      for (let j = 0; j < n; j++) {
        const hi = series[k].max[j];
        if (hi < lim.warn) continue;
        const s = series.t[j] * 1000, e = Math.min(b, s + size);
        svg.append(sv("rect", { x: x(Math.max(a, s)), y: y0, width: Math.max(2, x(e) - x(Math.max(a, s))), height: ph,
          style: `fill:var(--${hi >= lim.err ? "err" : "warn"});fill-opacity:.16` }));
      }
    }
    svg.append(sv("line", { x1: L, x2: w - R, y1: y(lim.warn), y2: y(lim.warn), style: "stroke:var(--warn);stroke-dasharray:4 4;stroke-width:1.2" }));
    svg.append(text(w - R, y(lim.warn) - 5, `warn ${lim.warn}`, "font-size:10px;fill:var(--ink2)", "end"));
    if (lim.err <= dom) {
      svg.append(sv("line", { x1: L, x2: w - R, y1: y(lim.err), y2: y(lim.err), style: "stroke:var(--err);stroke-dasharray:4 4;stroke-width:1.2" }));
      svg.append(text(w - R, y(lim.err) - 5, `high ${lim.err}`, "font-size:10px;fill:var(--ink2)", "end"));
    }
    for (const v of [0, dom]) svg.append(text(L - 6, y(v) + 4, v, "font-size:10px;fill:var(--ink2)", "end"));
    svg.append(sv("line", { x1: L, x2: w - R, y1: y0 + ph, y2: y0 + ph, style: "stroke:var(--line)" }));
    svg.append(text(L + 4, y0 + 12, NAME[k], "font-size:11px;font-weight:600;fill:var(--ink)"));
    if (!n) return;
    if (!raw) {
      const up = series[k].max.map((v, j) => [x(mid(j)), y(v)]);
      const lo = series[k].min.map((v, j) => [x(mid(j)), y(v)]).reverse();
      svg.append(sv("path", { d: `${monotone(up)}L${lo.map((p) => p.join(",")).join("L")}Z`, style: `fill:var(--${k});fill-opacity:.13` }));
    }
    const line = monotone(series[k].avg.map((v, j) => [x(mid(j)), y(v)]));
    svg.append(sv("path", { d: `${line}L${x(mid(n - 1))},${y0 + ph}L${x(mid(0))},${y0 + ph}Z`, fill: fade(svg, `var(--${k})`) }));
    svg.append(sv("path", { d: line, style: `fill:none;stroke:var(--${k});stroke-width:2`, filter: g }));
  });

  for (const tk of clock.ticks(a, b, w - L - R)) svg.append(text(x(tk.t), h - 4, tk.label, "font-size:11px;fill:var(--ink2)", "middle"));
  if (!n) {
    svg.append(text(w / 2, h / 2, "No readings in this range", "font-size:13px;fill:var(--ink2)", "middle"));
    host.append(svg);
    return;
  }

  // hover + drag-to-zoom
  const cursor = sv("g", { style: "pointer-events:none;opacity:0" });
  const vline = sv("line", { y1: top, y2: bottom, class: "cursor" });
  const dots = keys.map((k) => sv("circle", { r: 4, style: `fill:var(--${k});stroke:var(--bg);stroke-width:2` }));
  const sel = sv("rect", { y: top, height: bottom - top, class: "selection", style: "display:none" });
  cursor.append(vline, ...dots);
  svg.append(sel, cursor);
  const tip = document.createElement("div");
  tip.className = "tip";
  const hit = sv("rect", { x: L, y: top, width: w - L - R, height: bottom - top, fill: "transparent", class: "hit" });
  svg.append(hit);

  const nearest = (px) => {
    const t = tAt(px);
    let lo = 0, hi = n - 1;
    while (lo < hi) { const m = (lo + hi) >> 1; if (mid(m) < t) lo = m + 1; else hi = m; }
    return lo > 0 && Math.abs(mid(lo - 1) - t) < Math.abs(mid(lo) - t) ? lo - 1 : lo;
  };
  const pxOf = (e) => Math.min(Math.max(e.clientX - svg.getBoundingClientRect().left, L), w - R);
  const place = (left, y) => { tip.style.left = `${Math.min(Math.max(left, 0), w - tip.offsetWidth)}px`; tip.style.top = `${y}px`; };
  let down = null;
  hit.addEventListener("pointerdown", (e) => { down = pxOf(e); hit.setPointerCapture(e.pointerId); });
  hit.addEventListener("pointermove", (e) => {
    const px = pxOf(e);
    if (down != null && Math.abs(px - down) > 6) {
      const l = Math.min(px, down), r = Math.max(px, down);
      cursor.style.opacity = 0;
      sel.style.display = "";
      sel.setAttribute("x", l); sel.setAttribute("width", r - l);
      tip.textContent = `Zoom to ${clock.hhmm(tAt(l))}–${clock.hhmm(tAt(r))}`;
      tip.style.opacity = 1;
      place(l, top + 30);
      return;
    }
    const i = nearest(px), cx = x(mid(i)), s = series.t[i] * 1000;
    vline.setAttribute("x1", cx); vline.setAttribute("x2", cx);
    keys.forEach((k, j) => { dots[j].setAttribute("cx", cx); dots[j].setAttribute("cy", ys[k](series[k].avg[i])); });
    cursor.style.opacity = 1;
    const when = raw ? `${clock.day(s)} ${clock.hhmm(s)}` : `${clock.day(s)} ${clock.hhmm(s)}–${clock.hhmm(Math.min(b, s + size))}`;
    tip.innerHTML = when + keys.map((k) =>
      `<br><span class="swatch" style="background:var(--${k})"></span>${NAME[k]} <b>${f1(series[k].avg[i])}</b>` +
      (raw ? "" : ` <span class="range">(${f1(series[k].min[i])}–${f1(series[k].max[i])})</span>`)).join("");
    tip.style.opacity = 1;
    place(cx - tip.offsetWidth / 2, top - 6);
  });
  hit.addEventListener("pointerup", (e) => {
    const px = pxOf(e), start = down;
    down = null;
    sel.style.display = "none";
    if (start != null && Math.abs(px - start) > 6) onZoom(tAt(Math.min(px, start)), tAt(Math.max(px, start)));
  });
  hit.addEventListener("pointerleave", () => { if (down == null) { cursor.style.opacity = 0; tip.style.opacity = 0; } });
  hit.addEventListener("dblclick", onReset);
  host.append(svg, tip);
}

// ---------- the 90-day timeline ----------

/* An overview of every day kept, with a window marking the range on screen.
 * Tap a day to centre the range there; drag the window to move it, or its
 * edges to resize it. Nothing is fetched until the drag ends. */
export function timeline(w, h, host, { days, start, end, range, clock, onPreview, onCommit, onJump, minSpan }) {
  const svg = root(w, h, "Timeline of the days kept. Tap a day to move the range there, or drag the highlighted window.");
  svg.setAttribute("tabindex", "0");
  const top = 12, bottom = h - 16;
  const x = lin(start, end, 0, w), tOf = (px) => start + (px / w) * (end - start);
  const max = Math.max(1, ...days.map((d) => d.pm25_max));
  const pts = days.map((d) => [x(Date.parse(d.start) + DAY / 2), bottom - (d.pm25_max / max) * (bottom - top - 4)]);
  if (pts.length) {
    svg.append(sv("path", { d: `${monotone(pts)}L${pts.at(-1)[0]},${bottom}L${pts[0][0]},${bottom}Z`, fill: fade(svg, "var(--pm25)", 0.5, 0.05) }));
    svg.append(sv("path", { d: monotone(pts), style: "fill:none;stroke:var(--pm25);stroke-width:1.2;stroke-opacity:.8" }));
  }
  for (const d of days) {
    if (d.level === "ok") continue;
    svg.append(sv("circle", { cx: x(Date.parse(d.start) + DAY / 2), cy: 5, r: 2.6, style: `fill:var(--${d.level})` },
      sv("title", { text: `${clock.day(Date.parse(d.start))}: ${LEVEL[d.level].label.toLowerCase()} at its worst` })));
  }
  let month = null;
  for (const d of days) {
    const t = Date.parse(d.start), m = clock.month(t);
    if (m !== month && clock.parts(t).day <= 7) { month = m; svg.append(text(x(t), h - 2, m, "font-size:10px;fill:var(--ink2)")); }
  }

  const minW = 14;
  const shadeOut = sv("path", { class: "outside" });
  const win = sv("rect", { y: top - 4, height: bottom - top + 4, rx: 6, class: "window", "data-part": "win" });
  const hl = sv("rect", { y: top + 6, height: bottom - top - 16, width: 6, rx: 3, class: "handle", "data-part": "l" });
  const hr = sv("rect", { y: top + 6, height: bottom - top - 16, width: 6, rx: 3, class: "handle", "data-part": "r" });
  svg.append(shadeOut, win, hl, hr);
  let view = range.slice();
  const place = ([a, b]) => {
    let xa = x(a), xb = x(b);
    if (xb - xa < minW) { const m = (xa + xb) / 2; xa = m - minW / 2; xb = m + minW / 2; }
    win.setAttribute("x", xa); win.setAttribute("width", xb - xa);
    hl.setAttribute("x", xa - 3); hr.setAttribute("x", xb - 3);
    shadeOut.setAttribute("d", `M0,${top - 4}H${xa}V${bottom}H0Z M${xb},${top - 4}H${w}V${bottom}H${xb}Z`);
  };
  place(view);

  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    const px = e.clientX - svg.getBoundingClientRect().left, part = e.target.dataset && e.target.dataset.part;
    if (!part) { onJump(tOf(px)); return; }
    drag = { part, px, a: range[0], b: range[1] };
    svg.setPointerCapture(e.pointerId);
    win.classList.add("dragging");
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dt = ((e.clientX - svg.getBoundingClientRect().left - drag.px) / w) * (end - start);
    let { a, b } = drag;
    if (drag.part === "win") { const span = b - a; a = Math.min(Math.max(start, a + dt), end - span); b = a + span; }
    if (drag.part === "l") a = Math.min(Math.max(start, a + dt), b - minSpan);
    if (drag.part === "r") b = Math.max(Math.min(end, b + dt), a + minSpan);
    view = [a, b];
    place(view);
    onPreview(view);
  });
  const finish = () => {
    if (!drag) return;
    drag = null;
    win.classList.remove("dragging");
    onCommit(view[0], view[1]);
  };
  svg.addEventListener("pointerup", finish);
  svg.addEventListener("pointercancel", finish);
  host.append(svg);
}

// ---------- hour of day ----------

export function hourBars(w, h, host, { values, key }) {
  const svg = root(w, h, `Average ${NAME[key]} by hour of day`);
  const fill = fade(svg, `var(--${key})`, 0.95, 0.35);
  const known = values.filter((v) => v != null);
  const max = Math.max(...known), peak = values.indexOf(max);
  const bw = (w - 8) / 24, y = lin(0, max * 1.15, h - 20, 14);
  values.forEach((v, i) => {
    if (i % 6 === 0) svg.append(text(4 + i * bw + bw / 2, h - 4, String(i).padStart(2, "0"), "font-size:10px;fill:var(--ink2)", "middle"));
    if (v == null) return;
    svg.append(sv("rect", { x: 4 + i * bw + bw * 0.18, y: y(v), width: bw * 0.64, height: h - 20 - y(v), rx: Math.min(4, bw * 0.3), fill, style: `opacity:${i === peak ? 1 : 0.6}` },
      sv("title", { text: `${String(i).padStart(2, "0")}:00 · ${f1(v)} µg/m³` })));
  });
  svg.append(text(4 + peak * bw + bw / 2, y(max) - 4, f1(max), "font-size:10px;font-weight:600;fill:var(--ink)", "middle"));
  host.append(svg);
  return peak;
}

// ---------- weekday by hour ----------

export function weekMatrix(w, h, host, { grid, days, key }) {
  const svg = root(w, h, `Average ${NAME[key]} by weekday and hour`);
  const known = grid.flat().filter((v) => v != null), max = Math.max(...known);
  const L = 34, top = 16, cw = (w - L - 4) / 24, rh = (h - top - 4) / 7, rmax = Math.min(cw, rh) / 2 - 1;
  days.forEach((label, r) => {
    svg.append(text(0, top + r * rh + rh / 2 + 4, label, "font-size:10px;fill:var(--ink2)"));
    grid[r].forEach((v, c) => {
      const cx = L + c * cw + cw / 2, cy = top + r * rh + rh / 2;
      if (v == null) { svg.append(sv("circle", { cx, cy, r: 1.5, style: "fill:var(--line)" })); return; }
      svg.append(sv("circle", { cx, cy, r: Math.max(1.5, Math.sqrt(v / max) * rmax), style: `fill:var(--${key});fill-opacity:${0.25 + (0.75 * v) / max}` },
        sv("title", { text: `${label} ${String(c).padStart(2, "0")}:00 · ${f1(v)} µg/m³` })));
    });
  });
  for (const hh of [0, 6, 12, 18]) svg.append(text(L + hh * cw + cw / 2, 10, String(hh).padStart(2, "0"), "font-size:10px;fill:var(--ink2)", "middle"));
  host.append(svg);
}

export { HOUR };
