/* Time, as the sensor's timezone tells it.
 *
 * Every timestamp on the page is shown in the configured timezone (meta.timezone),
 * not the browser's: someone opening the Mini App while travelling still sees
 * the sensor's 08:00 peak at 08:00. Date's own getters only know the browser's
 * zone, so everything here goes through Intl. */

const pad = (n) => String(n).padStart(2, "0");

export const MINUTE = 60e3;
export const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;

export function makeClock(timeZone) {
  const partsFmt = new Intl.DateTimeFormat("en-GB", {
    timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", weekday: "short",
  });
  const dayFmt = new Intl.DateTimeFormat("en-GB", { timeZone, weekday: "short", day: "numeric", month: "short" });
  const shortFmt = new Intl.DateTimeFormat("en-GB", { timeZone, day: "numeric", month: "short" });
  const monthFmt = new Intl.DateTimeFormat("en-GB", { timeZone, month: "short" });

  function parts(ms) {
    const o = {};
    for (const p of partsFmt.formatToParts(ms)) o[p.type] = p.value;
    return { year: +o.year, month: +o.month, day: +o.day, hour: +o.hour % 24, minute: +o.minute, weekday: o.weekday };
  }

  const clock = {
    parts,
    hhmm: (ms) => { const p = parts(ms); return `${pad(p.hour)}:${pad(p.minute)}`; },
    day: (ms) => dayFmt.format(ms),          // "Sat 31 Oct"
    short: (ms) => shortFmt.format(ms),      // "31 Oct"
    month: (ms) => monthFmt.format(ms),      // "Oct"
    weekday: (ms) => parts(ms).weekday,      // "Sat"
    isMidnight: (ms) => { const p = parts(ms); return p.hour === 0 && p.minute === 0; },
    /* <input type="datetime-local"> holds a naive wall time; these read and
     * write it as the sensor's wall time rather than the browser's. */
    toInput(ms) {
      const p = parts(ms);
      return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
    },
    fromInput(value) {
      const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
      if (!m) return NaN;
      const wall = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
      const offsetAt = (ms) => { const p = parts(ms); return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute) - Math.floor(ms / MINUTE) * MINUTE; };
      let ms = wall - offsetAt(wall);
      ms = wall - offsetAt(ms);            // second pass settles a DST edge
      return ms;
    },
    sameDay(a, b) {
      const p = parts(a), q = parts(b);
      return p.year === q.year && p.month === q.month && p.day === q.day;
    },

    /* Axis ticks on whole local hours or days, roughly one per `gap` pixels.
     * Walks the hours of the range rather than doing offset arithmetic, so a
     * DST change mid-range still lands every tick on a real local boundary. */
    ticks(a, b, px, gap = 70) {
      const want = Math.max(2, Math.floor(px / gap));
      const steps = [1, 3, 6, 12, 24, 48, 168, 336, 720];
      const stepH = steps.find((s) => (b - a) / (s * HOUR) <= want) || 720;
      const out = [];
      let midnights = 0;
      for (let t = Math.ceil(a / HOUR) * HOUR; t <= b; t += HOUR) {
        const p = parts(t);
        if (p.minute !== 0) continue;               // half-hour zones: no ticks rather than wrong ones
        if (stepH < 24) {
          if (p.hour % stepH === 0) out.push({ t, label: p.hour === 0 ? p.weekday : `${pad(p.hour)}:00` });
        } else if (p.hour === 0) {
          if (midnights++ % (stepH / 24) === 0) out.push({ t, label: clock.short(t) });
        }
      }
      return out;
    },
  };
  return clock;
}

export function duration(ms) {
  const m = Math.round(ms / MINUTE);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  if (h < 48) return r ? `${h} h ${r} min` : `${h} h`;
  return `${Math.round(h / 24)} days`;
}

export function ago(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}
