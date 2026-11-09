// Tiny single-page app, no framework or build step.
// SECURITY: every piece of data from the API (URLs, referrers...) is user-controlled,
// so we only ever put it in the page via textContent / setAttribute, never innerHTML.
"use strict";

const $app = document.getElementById("app");
const $nav = document.getElementById("nav");

// ---------- token storage (wrapped: storage can be blocked) ----------
const store = {
  get() { try { return localStorage.getItem("token"); } catch { return null; } },
  set(t) { try { localStorage.setItem("token", t); } catch {} },
  clear() { try { localStorage.removeItem("token"); } catch {} },
};

// ---------- helpers ----------
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k.nodeType ? k : document.createTextNode(String(k)));
  return el;
}
const svgNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(svgNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  for (const k of kids) if (k) el.append(k);
  return el;
}
const fmt = (n) => Number(n).toLocaleString();
const fmtDate = (iso) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const t = store.get();
  if (t) headers["Authorization"] = "Bearer " + t;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && t) { store.clear(); renderNav(); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorText(res, data));
  return data;
}
function errorText(res, data) {
  if (res.status === 429) return "Slow down - too many requests. Try again in " + (res.headers.get("Retry-After") || "a few") + "s.";
  if (res.status === 503) return "The service is temporarily unavailable. Please retry in a moment.";
  const d = data.detail;
  if (Array.isArray(d)) return d.map((e) => (e.msg || "").replace(/^Value error, /, "")).join("; ");
  return d || "Something went wrong (" + res.status + ")";
}
async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); } catch { return; }
  const old = btn.textContent; btn.textContent = "Copied!";
  setTimeout(() => (btn.textContent = old), 1200);
}
function logout() { store.clear(); location.hash = "#/"; renderNav(); route(); }

// ---------- nav ----------
function renderNav() {
  const here = location.hash || "#/";
  const link = (href, text) => h("a", { href, class: here === href ? "active" : "" }, text);
  $nav.replaceChildren(...(store.get()
    ? [link("#/", "Shorten"), link("#/dashboard", "My links"), h("button", { onclick: logout }, "Log out")]
    : [link("#/", "Shorten"), link("#/login", "Log in"), link("#/register", "Sign up")]));
}

// ---------- pages ----------
function homePage() {
  const msg = h("p", { class: "msg" });
  const result = h("div");
  const url = h("input", { id: "url", type: "url", placeholder: "https://example.com/a-very-long-link", required: true, autofocus: true });
  const alias = h("input", { id: "alias", placeholder: "my-custom-name", maxlength: 30 });
  const exp = h("input", { id: "exp", type: "datetime-local" });
  const btn = h("button", { class: "primary", type: "submit" }, "Shorten");

  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault();
      btn.disabled = true; msg.className = "msg"; msg.textContent = ""; result.replaceChildren();
      const body = { url: url.value.trim() };
      if (alias.value.trim()) body.alias = alias.value.trim();
      if (exp.value) body.expires_at = new Date(exp.value).toISOString();
      try {
        const link = await api("/api/links", { method: "POST", body });
        const copyBtn = h("button", { onclick: (ev) => copy(link.short_url, ev.target) }, "Copy");
        result.replaceChildren(h("div", { class: "card result" },
          h("a", { class: "short", href: link.short_url, target: "_blank", rel: "noopener" }, link.short_url), copyBtn));
        if (!store.get()) msg.textContent = "Tip: sign up to track clicks on your links.", msg.className = "msg";
      } catch (err) { msg.textContent = err.message; msg.className = "msg err"; }
      btn.disabled = false;
    },
  },
    h("div", { class: "row" }, h("div", { class: "grow" }, h("label", { for: "url" }, "Long URL"), url), h("div", {}, h("label", { }, " "), btn)),
    h("details", {}, h("summary", {}, "Options"),
      h("div", { class: "row" },
        h("div", { class: "grow" }, h("label", { for: "alias" }, "Custom alias (3-30 chars)"), alias),
        h("div", { class: "grow" }, h("label", { for: "exp" }, "Expires at"), exp))),
    msg);

  return h("section", {}, h("h1", {}, "Short links, real stats."),
    h("p", { class: "lead" }, "Paste a long URL and get a short one that redirects instantly. Create an account to see how many people clicked."),
    h("div", { class: "card" }, form), result);
}

function authPage(mode) {
  const isLogin = mode === "login";
  const msg = h("p", { class: "msg err" });
  const email = h("input", { id: "email", type: "email", required: true, autocomplete: "email" });
  const pw = h("input", { id: "pw", type: "password", required: true, minlength: 8, autocomplete: isLogin ? "current-password" : "new-password" });
  const btn = h("button", { class: "primary", type: "submit" }, isLogin ? "Log in" : "Create account");
  const form = h("form", {
    onsubmit: async (e) => {
      e.preventDefault(); btn.disabled = true; msg.textContent = "";
      try {
        const r = await api("/api/auth/" + (isLogin ? "login" : "register"), { method: "POST", body: { email: email.value, password: pw.value } });
        store.set(r.access_token); location.hash = "#/dashboard";
      } catch (err) { msg.textContent = err.message; }
      btn.disabled = false;
    },
  }, h("div", {}, h("label", { for: "email" }, "Email"), email), h("div", { style: "margin-top:12px" }, h("label", { for: "pw" }, "Password" + (isLogin ? "" : " (min 8 characters)")), pw),
    h("div", { style: "margin-top:16px" }, btn), msg);
  return h("section", { style: "max-width:420px" }, h("h1", {}, isLogin ? "Welcome back" : "Create your account"), h("div", { class: "card" }, form),
    h("p", { class: "muted" }, isLogin ? "No account? " : "Already registered? ", h("a", { href: isLogin ? "#/register" : "#/login" }, isLogin ? "Sign up" : "Log in")));
}

async function dashboardPage() {
  if (!store.get()) { location.hash = "#/login"; return h("div"); }
  const box = h("div", { class: "card" }, h("p", { class: "empty" }, "Loading..."));
  api("/api/links?limit=100").then((links) => {
    if (!links.length) { box.replaceChildren(h("p", { class: "empty" }, "No links yet. ", h("a", { href: "#/" }, "Create your first one."))); return; }
    const rows = links.map((l) => {
      const tr = h("tr", {},
        h("td", {}, h("a", { href: l.short_url, target: "_blank", rel: "noopener" }, l.short_url.replace(/^https?:\/\//, "")),
          " ", h("button", { class: "small", onclick: (e) => copy(l.short_url, e.target) }, "Copy")),
        h("td", {}, h("span", { class: "trunc muted", title: l.original_url }, l.original_url)),
        h("td", { class: "num" }, fmt(l.clicks ?? 0)),
        h("td", {}, l.expires_at ? fmtDate(l.expires_at) : "Never"),
        h("td", {}, h("a", { class: "btn small", href: "#/stats/" + encodeURIComponent(l.code) }, "Stats"), " ",
          h("button", { class: "small danger", onclick: async () => {
            if (!confirm("Delete " + l.short_url + "? This can't be undone.")) return;
            try { await api("/api/links/" + encodeURIComponent(l.code), { method: "DELETE" }); tr.remove(); } catch (e) { alert(e.message); }
          } }, "Delete")));
      return tr;
    });
    box.replaceChildren(h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Short link"), h("th", {}, "Destination"), h("th", { class: "num" }, "Clicks"), h("th", {}, "Expires"), h("th", {}, ""))),
      h("tbody", {}, rows))));
  }).catch((e) => box.replaceChildren(h("p", { class: "msg err" }, e.message)));
  return h("section", {}, h("h1", {}, "My links"), box);
}

// ---------- stats page + chart ----------
async function statsPage(code) {
  if (!store.get()) { location.hash = "#/login"; return h("div"); }
  const root = h("section", {}, h("a", { class: "back", href: "#/dashboard" }, "← My links"), h("p", { class: "empty" }, "Loading..."));
  api("/api/links/" + encodeURIComponent(code) + "/stats").then((d) => {
    const tiles = h("div", { class: "tiles" },
      tile(fmt(d.total_clicks), "Total clicks"), tile(fmt(d.unique_visitors), "Unique visitors"),
      tile(fmt(d.clicks_by_day.slice(-7).reduce((a, x) => a + x.clicks, 0)), "Last 7 days"));
    root.replaceChildren(h("a", { class: "back", href: "#/dashboard" }, "← My links"),
      h("h1", {}, "/" + d.code), h("p", { class: "lead trunc", title: d.original_url, style: "max-width:100%" }, d.original_url),
      tiles, chartCard(d.clicks_by_day),
      h("div", { class: "two" }, listCard("Top referrers", d.top_referrers), listCard("Devices", d.devices)),
      h("p", { class: "muted" }, "Clicks appear after a few seconds - they are processed asynchronously by a background worker. Bots (curl, crawlers) are counted as the \"bot\" device."));
  }).catch((e) => root.replaceChildren(h("a", { class: "back", href: "#/dashboard" }, "← My links"), h("p", { class: "msg err" }, e.message)));
  return root;
}
const tile = (v, l) => h("div", { class: "tile" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l));

function listCard(title, items) {
  const max = Math.max(1, ...items.map((i) => i.clicks));
  return h("div", { class: "card" }, h("h2", {}, title),
    items.length ? items.map((i) => h("div", { class: "hbar" },
      h("span", { class: "name", title: i.name }, i.name),
      h("span", { class: "track" }, h("span", { class: "fill", style: "display:block;width:" + (100 * i.clicks / max) + "%" })),
      h("span", { class: "n" }, fmt(i.clicks)))) : h("p", { class: "empty" }, "No data yet"));
}

function chartCard(series) {
  const card = h("div", { class: "card" });
  let showTable = false;
  const toggle = h("button", { class: "small", onclick: () => { showTable = !showTable; draw(); } }, "View as table");
  function draw() {
    toggle.textContent = showTable ? "View as chart" : "View as table";
    card.replaceChildren(h("div", { class: "chart-head" }, h("h2", { style: "margin:0" }, "Clicks per day (last " + series.length + " days)"), toggle),
      showTable ? table(series) : bars(series));
  }
  draw();
  return card;
}
function table(series) {
  return h("div", { class: "table-wrap", style: "max-height:320px;overflow:auto" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", { class: "num" }, "Clicks"))),
    h("tbody", {}, [...series].reverse().map((p) => h("tr", {}, h("td", {}, fmtDate(p.date + "T00:00:00")), h("td", { class: "num" }, fmt(p.clicks)))))));
}
function niceMax(v) {            // round the axis top up to a tidy number: 1,2,5 x 10^n
  if (v <= 4) return 4;
  const p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
function bars(series) {
  const total = series.reduce((a, x) => a + x.clicks, 0);
  if (!total) return h("p", { class: "empty" }, "No clicks yet. Share the link and check back in a few seconds.");
  const W = 720, H = 240, m = { l: 36, r: 8, t: 8, b: 24 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const max = niceMax(Math.max(...series.map((p) => p.clicks)));
  const slot = iw / series.length, bw = Math.max(2, slot - 2);       // 2px gap between bars
  const y = (v) => m.t + ih - (v / max) * ih;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Bar chart of clicks per day" });
  for (let i = 0; i <= 4; i++) {                                       // recessive grid + y labels
    const v = (max / 4) * i;
    svg.append(s("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
    const t = s("text", { class: "axis-label", x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }); t.textContent = fmt(Math.round(v)); svg.append(t);
  }
  const tip = h("div", { class: "tip" });
  const wrap = h("div", { class: "chart" }, svg, tip);
  series.forEach((p, i) => {
    const x = m.l + i * slot + (slot - bw) / 2, top = y(p.clicks), bh = m.t + ih - top, r = Math.min(4, bw / 2, bh);
    const g = s("g", { class: "slot", tabindex: "0" });
    g.append(s("rect", { x: m.l + i * slot, y: m.t, width: slot, height: ih, fill: "transparent" }));   // hit area bigger than the mark
    if (p.clicks > 0)   // 4px rounded data-end, flat on the baseline
      g.append(s("path", { class: "bar", d: `M${x},${m.t + ih} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${m.t + ih} Z` }));
    const show = () => {
      tip.replaceChildren(h("b", {}, fmt(p.clicks)), " click" + (p.clicks === 1 ? "" : "s"), h("br"), h("span", { class: "muted" }, fmtDate(p.date + "T00:00:00")));
      tip.style.display = "block";
      tip.style.left = (100 * (x + bw / 2) / W) + "%"; tip.style.top = (100 * Math.max(top, 24) / H) + "%";
    };
    g.addEventListener("mouseenter", show); g.addEventListener("focus", show);
    g.addEventListener("mouseleave", () => (tip.style.display = "none")); g.addEventListener("blur", () => (tip.style.display = "none"));
    svg.append(g);
    if (i % Math.ceil(series.length / 6) === 0 || i === series.length - 1) {     // sparse x labels
      const t = s("text", { class: "axis-label", x: x + bw / 2, y: H - 6, "text-anchor": "middle" });
      t.textContent = new Date(p.date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }); svg.append(t);
    }
  });
  return wrap;
}

// ---------- router ----------
async function route() {
  renderNav();
  const hash = location.hash || "#/";
  let page;
  if (hash === "#/login") page = authPage("login");
  else if (hash === "#/register") page = authPage("register");
  else if (hash === "#/dashboard") page = await dashboardPage();
  else if (hash.startsWith("#/stats/")) page = await statsPage(decodeURIComponent(hash.slice(8)));
  else page = homePage();
  $app.replaceChildren(page);
}
window.addEventListener("hashchange", route);
route();
