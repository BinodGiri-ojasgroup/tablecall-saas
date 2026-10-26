import { $, $$, api, applyBrand, copyText, fail, html, icon, initial, LABELS, live, money, put, raw, REQUEST_ICONS, clock, toast } from "/static/common.js";

const parts = location.pathname.split("/").filter(Boolean);
const guest = { slug: decodeURIComponent(parts[1] || ""), code: decodeURIComponent(parts[2] || ""), data: null, pin: "", sent: [], liveOk: false };
const root = $("#app");
const url = (suffix) => `/api/public/${encodeURIComponent(guest.slug)}/${encodeURIComponent(guest.code)}/${suffix}`;
const store = {
  get(k) { try { return sessionStorage.getItem(`tc:${guest.slug}:${guest.code}:${k}`); } catch { return null; } },
  set(k, v) { try { v == null ? sessionStorage.removeItem(`tc:${guest.slug}:${guest.code}:${k}`) : sessionStorage.setItem(`tc:${guest.slug}:${guest.code}:${k}`, v); } catch { /* storage unavailable */ } },
};

const SERVICES = [
  ["call_waiter", "Call waiter", "Ask for service"],
  ["request_bill", "Request bill", "Ready to pay"],
  ["need_water", "Request water", "A glass, please"],
  ["assistance", "Need assistance", "Something else"],
];
const STATUS_TEXT = { pending: "Sent", accepted: "On the way", completed: "Done", cancelled: "Cancelled" };

// ───────────────────────────────────────────────────────────── service card
function pinHelp(d) {
  if (d.pin_mode === "session") return "Staff will give you this PIN. It works for your whole visit.";
  return `Staff will give you this PIN. It works for ${d.pin_ttl_minutes} minutes after they issue it.`;
}

function renderService() {
  const d = guest.data, t = d.table;
  const typed = $("#guest-pin");
  if (typed) guest.pin = typed.value;
  let notice = "";
  if (!t.session_active) notice = html`<div class="notice warn" role="status">This table isn't open yet. Staff will open it when you are seated, and the buttons will switch on by themselves.</div>`;
  else if (!t.pin_active) notice = html`<div class="notice" role="status">Ask a member of staff for your table PIN, then type it below.</div>`;
  put($("#svc"), html`<div class="svc-head"><h2>How can we help?</h2></div>${notice}
    ${t.session_active ? html`<div class="pin-wrap"><label for="guest-pin">Table PIN</label>
      <input id="guest-pin" class="pin-input" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="••••••" autocomplete="one-time-code" value="${guest.pin}" aria-describedby="pin-help">
      <div id="pin-help" class="help">${pinHelp(d)}</div></div>` : ""}
    <div class="svc-grid">${SERVICES.map(([key, label, hint]) => html`<button type="button" class="svc ${key}" data-type="${key}" ${t.session_active ? "" : raw("disabled")}>
      <span class="ico">${icon(REQUEST_ICONS[key])}</span><span>${label}<small>${hint}</small></span></button>`)}</div>
    ${guest.sent.length ? html`<ul class="sent" aria-label="Requests from this device" aria-live="polite">${guest.sent.slice(0, 3).map((s) => html`<li class="${s.status}">
      <span>${icon(s.status === "cancelled" ? "x" : "check")}</span><span>${LABELS[s.type]}</span><span>${STATUS_TEXT[s.status] || s.status} · ${clock(s.at)}</span></li>`)}</ul>` : ""}`);
}

function renderWifi() {
  const w = guest.data.table.wifi;
  put($("#wifi"), w ? html`<section class="card wifi" aria-label="Wi-Fi"><div class="wifi-ic">${icon("wifi")}</div>
    <div class="wifi-body"><strong>Free Wi-Fi</strong>
      <div class="wifi-line"><span>Network</span><b>${w.ssid}</b><button class="btn ghost sm" data-copy="${w.ssid}" aria-label="Copy network name">${icon("copy")}</button></div>
      ${w.password ? html`<div class="wifi-line"><span>Password</span><b class="mono">${w.password}</b><button class="btn ghost sm" data-copy="${w.password}" aria-label="Copy Wi-Fi password">${icon("copy")}</button></div>`
        : html`<div class="wifi-line"><span>Open network, no password</span></div>`}</div></section>` : "");
  const wrap = $("#wifi"); if (wrap) wrap.style.marginTop = w ? "16px" : "0";
}

async function sendRequest(btn) {
  const pin = ($("#guest-pin")?.value || "").trim();
  guest.pin = pin;
  if (!/^\d{6}$/.test(pin)) { $("#guest-pin")?.focus(); return toast("Enter the 6-digit PIN from staff first.", "error"); }
  const type = btn.dataset.type;
  btn.classList.add("sending");
  try {
    const r = await api(url("requests"), { method: "POST", body: { pin, request_type: type } });
    store.set("pin", pin);
    guest.sent.unshift({ id: r.id, type, at: new Date().toISOString(), status: "pending" });
    store.set("sent", JSON.stringify(guest.sent.slice(0, 5)));
    toast(r.message);
    if (navigator.vibrate) navigator.vibrate(60);
    renderService();
    const again = $(`.svc[data-type="${type}"]`);
    if (again) { again.classList.add("done"); setTimeout(() => again.classList.remove("done"), 2500); }
  } catch (e) {
    if (e.status === 403) { guest.pin = ""; store.set("pin", null); }
    fail(e);
    btn.classList.remove("sending");
  }
}

// ───────────────────────────────────────────────────────────── menu
function dish(i) {
  return html`<article class="dish" data-name="${(i.name + " " + (i.description || "")).toLowerCase()}"><div class="dish-body"><h3>${i.name}</h3>${i.description ? html`<p>${i.description}</p>` : ""}
    <div class="price">${money(i.price_npr)}</div></div>${i.image ? html`<img src="${i.image}" alt="" width="92" height="92" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : ""}</article>`;
}

function sections() {
  const d = guest.data;
  return [...d.categories.map((c) => ({ id: "cat-" + c.id, name: c.name, items: d.items.filter((i) => i.category_id === c.id) })),
    { id: "cat-other", name: d.categories.length ? "More" : "Menu", items: d.items.filter((i) => !i.category_id) }].filter((s) => s.items.length);
}

function renderMenu() {
  const list = sections();
  put($("#menu"), html`<div class="row between"><h2>Menu</h2><span class="small">${guest.data.items.length} ${guest.data.items.length === 1 ? "item" : "items"}</span></div>
    ${guest.data.items.length > 8 ? html`<div class="search"><span aria-hidden="true">${icon("search")}</span><input type="search" id="menu-search" placeholder="Search the menu" aria-label="Search the menu" autocomplete="off"></div>` : ""}
    ${list.length > 1 ? html`<nav class="chips" aria-label="Menu sections">${list.map((s, n) => html`<a href="#${s.id}" class="${n === 0 ? "active" : ""}">${s.name}</a>`)}</nav>` : ""}
    <div id="menu-body">${list.length ? list.map((s) => html`<section data-sec><h3 class="cat" id="${s.id}">${s.name}</h3><div class="dishes">${s.items.map(dish)}</div></section>`)
      : html`<div class="empty menu-empty"><strong>The menu is being prepared</strong>Please ask a member of staff.</div>`}</div>
    <div id="no-match" class="empty" hidden><strong>No dishes match your search</strong>Try a different word.</div>`);
  watchSections();
}

function watchSections() {
  const links = $$(".chips a");
  if (!links.length || !("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id));
  }, { rootMargin: "-90px 0px -70% 0px" });
  $$(".cat").forEach((h) => io.observe(h));
}

function filterMenu(q) {
  q = q.trim().toLowerCase();
  let any = false;
  $$("[data-sec]").forEach((sec) => {
    let shown = 0;
    $$(".dish", sec).forEach((d) => { const hit = !q || d.dataset.name.includes(q); d.hidden = !hit; if (hit) shown++; });
    sec.hidden = shown === 0;
    any ||= shown > 0;
  });
  $("#no-match").hidden = any || !q;
}

// ───────────────────────────────────────────────────────────── live state
function setLive(ok) {
  guest.liveOk = ok;
  const el = $("#live-dot"); if (el) { el.classList.toggle("on", ok); $("span", el).textContent = ok ? "Live" : "Reconnecting…"; }
}
function onState(t) {
  if (!t) return;
  const before = JSON.stringify(guest.data.table);
  guest.data.table = t;
  if (JSON.stringify(t) !== before) { renderService(); renderWifi(); }
}
function onRequest(r) {
  if (!r) return;
  const mine = guest.sent.find((s) => s.id === r.id);
  if (mine && mine.status !== r.status) {
    mine.status = r.status;
    store.set("sent", JSON.stringify(guest.sent.slice(0, 5)));
    renderService();
    if (r.status === "accepted") toast("Your waiter is on the way.");
  }
}
async function fallbackPoll() {
  if (guest.liveOk || document.hidden) return;
  try { onState(await api(url("status"))); } catch { /* try again next time */ }
}

// ───────────────────────────────────────────────────────────── start
async function start() {
  try { guest.data = await api(url("menu")); }
  catch (e) {
    put(root, html`<main class="g-main" style="margin-top:12vh"><div class="card"><h2>This menu isn't available</h2><p class="sub" style="margin-top:8px">${e.message}</p></div></main>`);
    return;
  }
  const d = guest.data, r = d.restaurant;
  applyBrand(r.brand_color);
  document.title = `${r.name} · Table ${d.table.code}`;
  guest.pin = store.get("pin") || "";
  try { guest.sent = JSON.parse(store.get("sent") || "[]"); } catch { guest.sent = []; }
  put(root, html`<header class="g-hero"><div class="g-hero-in"><div class="g-logo">${r.logo_url ? html`<img src="${r.logo_url}" alt="">` : initial(r.name)}</div>
      <div><div class="g-name">${r.name}</div><span class="g-table">Table ${d.table.code}</span></div></div></header>
    <main class="g-main"><section class="card" id="svc" aria-label="Request service"></section><div id="wifi"></div>
      <section class="g-menu" id="menu"></section>
      <footer class="g-foot"><span class="g-live" id="live-dot"><i></i><span>Connecting…</span></span><div style="margin-top:6px">Powered by <a href="/">TableCall</a></div></footer></main>`);
  renderService(); renderWifi(); renderMenu();

  root.addEventListener("click", (e) => {
    const svc = e.target.closest(".svc"); if (svc && !svc.disabled) return sendRequest(svc);
    const cp = e.target.closest("[data-copy]");
    if (cp) copyText(cp.dataset.copy).then((ok) => toast(ok ? "Copied." : "Couldn't copy. Select the text instead.", ok ? "success" : "error"));
    const chip = e.target.closest(".chips a");
    if (chip) { e.preventDefault(); $(chip.getAttribute("href"))?.scrollIntoView({ behavior: "smooth", block: "start" }); }
  });
  root.addEventListener("input", (e) => {
    if (e.target.id === "guest-pin") e.target.value = e.target.value.replace(/\D/g, "").slice(0, 6);
    if (e.target.id === "menu-search") filterMenu(e.target.value);
  });
  live(url("events"), { hello: onState, state: onState, request: onRequest }, setLive);
  setInterval(fallbackPoll, 20000);
}
start();
