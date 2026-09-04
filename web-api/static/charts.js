/* Drawing.
 *
 * Two pollutants, two panes - the same reason the Telegram charts stack them:
 * a shared y-axis cannot carry PM2.5 warning at 35 and PM10 at 50 honestly.
 * Threshold bands always carry a text label, so colour never has to be read
 * on its own. */

export const Y_FLOOR = 25;   // a quiet day must look quiet, as in charts.py

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function rgba(hex, alpha) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map(c => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/* uPlot formats axis labels with the Date object's *local* getters, so hand it
 * a Date whose local fields already read as the configured timezone. */
export function makeTzDate(tzName) {
  let parts;
  try {
    parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tzName, hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch (_) {
    return (ts) => new Date(ts * 1000);   // unknown zone: fall back to the browser
  }
  return (ts) => {
    const p = {};
    for (const { type, value } of parts.formatToParts(new Date(ts * 1000))) p[type] = value;
    return new Date(+p.year, +p.month - 1, +p.day, +p.hour % 24, +p.minute, +p.second);
  };
}

/* Threshold shading + its labels, painted under the series. */
function thresholdBands(warn, err) {
  return (u) => {
    const { ctx } = u;
    const left = u.bbox.left, width = u.bbox.width, top = u.bbox.top;
    const clamp = (v) => Math.max(top, Math.min(top + u.bbox.height, u.valToPos(v, "y", true)));
    const bands = [
      { from: warn, to: err, colour: css("--warn"), label: "elevated" },
      { from: err, to: u.scales.y.max, colour: css("--err"), label: "high" },
    ];
    ctx.save();
    ctx.rect(left, top, width, u.bbox.height);
    ctx.clip();
    for (const b of bands) {
      if (b.to <= b.from) continue;
      const yTop = clamp(b.to), yBottom = clamp(b.from);
      if (yBottom - yTop < 0.5) continue;
      ctx.fillStyle = rgba(b.colour, parseFloat(css("--band-alpha")) || 0.13);
      ctx.fillRect(left, yTop, width, yBottom - yTop);
      ctx.fillStyle = css("--ink-2");
      ctx.font = `${11 * devicePixelRatio}px system-ui, sans-serif`;
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText(b.label, left + 6 * devicePixelRatio, yTop + 3 * devicePixelRatio);
    }
    ctx.restore();
  };
}

/* One pollutant pane: the bucket mean as a line, the bucket min-max spread as
 * a soft band behind it, so aggregation never hides a spike it averaged away. */
export function pollutantChart(el, { title, unitColour, t, avg, min, max,
                                     warn, err, tzName, width, showSpread }) {
  if (typeof uPlot === "undefined") {
    throw new Error("chart library missing (static/vendor is filled at image build)");
  }
  const colour = css(unitColour);
  const data = [t, min, max, avg];
  const top = Math.max(Y_FLOOR, err * 1.1, ...max.filter(Number.isFinite)) * 1.05;

  const opts = {
    title,
    width,
    height: 240,
    tzDate: makeTzDate(tzName),
    cursor: { y: false, drag: { x: true, y: false, setScale: false } },
    legend: { live: true },
    scales: { x: { time: true }, y: { range: [0, top] } },
    axes: [
      { stroke: css("--muted"), grid: { stroke: css("--grid"), width: 1 },
        ticks: { stroke: css("--axis") } },
      { stroke: css("--muted"), grid: { stroke: css("--grid"), width: 1 },
        ticks: { stroke: css("--axis") }, size: 46,
        label: "µg/m³", labelSize: 26, labelFont: "12px system-ui, sans-serif" },
    ],
    series: [
      { label: "Time" },
      { label: "min", stroke: "transparent", show: showSpread, value: (u, v) => fmt(v) },
      { label: "max", stroke: "transparent", show: showSpread, value: (u, v) => fmt(v) },
      { label: title, stroke: colour, width: 2, points: { show: false },
        value: (u, v) => fmt(v) },
    ],
    bands: showSpread ? [{ series: [2, 1], fill: rgba(colour, 0.16) }] : [],
    hooks: { drawClear: [thresholdBands(warn, err)] },
  };

  el.innerHTML = "";
  return new uPlot(opts, data, el);
}

function fmt(v) {
  return v == null ? "--" : `${v.toFixed(1)} µg/m³`;
}

/* Sequential single-hue ramp, same stops as the Telegram heatmap. */
const RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
              "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];

export function rampColour(value, lo, hi) {
  if (value == null) return null;
  const span = hi - lo || 1;
  const i = Math.round(((value - lo) / span) * (RAMP.length - 1));
  return RAMP[Math.max(0, Math.min(RAMP.length - 1, i))];
}

/* Weekday x hour grid. Plain DOM: 168 divs is cheaper than a chart library,
 * and every cell keeps its number in a tooltip rather than only a colour. */
export function heatmap(el, { days, grid, unit }) {
  const values = grid.flat().filter((v) => v != null);
  const lo = values.length ? Math.min(...values) : 0;
  const hi = values.length ? Math.max(...values) : 1;

  const box = document.createElement("div");
  box.className = "heat";
  box.append(cell("", "rowlabel"));
  for (let h = 0; h < 24; h++) {
    box.append(cell(h % 3 === 0 ? String(h) : "", "collabel"));
  }
  days.forEach((day, d) => {
    box.append(cell(day, "rowlabel"));
    for (let h = 0; h < 24; h++) {
      const v = grid[d][h];
      const c = document.createElement("div");
      c.className = "cell";
      if (v == null) {
        c.dataset.empty = "1";
        c.title = `${day} ${pad(h)}:00 - no readings`;
      } else {
        c.style.background = rampColour(v, lo, hi);
        c.title = `${day} ${pad(h)}:00 - ${v.toFixed(1)} ${unit}`;
      }
      box.append(c);
    }
  });

  el.innerHTML = "";
  el.append(box);
  el.append(scaleLegend(lo, hi, unit));
}

function scaleLegend(lo, hi, unit) {
  const wrap = document.createElement("div");
  wrap.className = "scale";
  const ramp = document.createElement("div");
  ramp.className = "ramp";
  ramp.style.background = `linear-gradient(90deg, ${RAMP.join(",")})`;
  wrap.append(span(`${lo.toFixed(1)}`), ramp, span(`${hi.toFixed(1)} ${unit}`));
  return wrap;
}

function span(text) {
  const s = document.createElement("span");
  s.textContent = text;
  return s;
}

function cell(text, cls) {
  const d = document.createElement("div");
  d.className = cls;
  d.textContent = text;
  return d;
}

function pad(n) { return String(n).padStart(2, "0"); }
