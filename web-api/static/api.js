/* The only place that talks to the backend.
 *
 * Every request carries whatever credential this page happens to have: the
 * signed initData when Telegram launched us, the ?token= from the link
 * otherwise. Callers never think about it. */

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
const initData = tg && tg.initData ? tg.initData : "";
const linkToken = new URLSearchParams(location.search).get("token") || "";

export const telegram = tg;

function headers() {
  const h = { "Accept": "application/json" };
  if (initData) h["X-Telegram-Init-Data"] = initData;
  if (linkToken) h["X-Access-Token"] = linkToken;
  return h;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `request failed (${status})`);
    this.status = status;
  }
}

async function request(path, params, options = {}) {
  const url = new URL(path, location.href);
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== null && v !== undefined && v !== "") url.searchParams.set(k, v);
  }
  const res = await fetch(url, {
    ...options,
    headers: { ...headers(), ...(options.headers || {}) },
  });
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail; } catch (_) { /* not JSON */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

/* A range is either a preset name, which the server resolves against its own
 * clock and timezone, or an explicit pair of instants. */
function rangeParams(range) {
  return range.preset
    ? { range: range.preset }
    : { from: new Date(range.from).toISOString(), to: new Date(range.to).toISOString() };
}

export const api = {
  meta: () => request("api/meta"),
  latest: () => request("api/latest"),
  series: (range, bucket) => request("api/series", { ...rangeParams(range), bucket }),
  summary: (range) => request("api/summary", rangeParams(range)),
  episodes: (range) => request("api/episodes", rangeParams(range)),
  setTheme: (theme) => request("api/theme", null, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theme }),
  }),
};
