/* Restaurant dashboard (owners and staff). Requests and tables update in place from a live event stream;
 * nothing here polls on a timer except a slow safety net, and no update re-renders the whole screen. */
import { $, $$, ago, api, clock, confirmDialog, copyText, dateTime, fail, html, icon, LABELS, live, modal, money, passwordModal,
  put, raw, reconcile, REQUEST_ICONS, setBusy, toast, toStr, uploadImage } from "/static/common.js";
import { renderShell, setLiveState } from "/static/shell.js";

const OPEN = ["pending", "accepted"];
const STATUS_NAME = { pending: "Waiting", accepted: "On the way", completed: "Done", cancelled: "Cancelled" };
const TABS = [["requests", "Requests"], ["tables", "Tables"], ["menu", "Menu"], ["settings", "Settings", true], ["team", "Team", true], ["activity", "Activity", true], ["account", "Account"]];
const PRESET_COLORS = ["#2f3bbd", "#0e7c66", "#b4232a", "#c2570c", "#7a3db8", "#14213d"];
const S = {};

const pref = (k, d = "") => { try { return localStorage.getItem("tc_" + k) ?? d; } catch { return d; } };
const setPref = (k, v) => { try { localStorage.setItem("tc_" + k, v); } catch { /* private mode */ } };
const owner = () => S.me.role === "owner";
const pendingCount = () => [...S.reqs.values()].filter((q) => q.status === "pending").length;

// ───────────────────────────────────────────────────────────── lifecycle
export async function start(root, me, hooks) {
  S.ac?.abort();
  Object.assign(S, {
    root, me, hooks, ac: new AbortController(), reqs: new Map(), seen: new Set(), seenReady: false, pins: {}, tables: [], menu: null,
    liveOk: false, filter: "open", sound: pref("sound") === "1", notify: pref("notify") === "1" && "Notification" in window && Notification.permission === "granted",
    audio: null, timers: [], stream: null,
  });
  S.r = await api("/api/restaurant");
  S.tab = TABS.some(([id, , o]) => id === pref("tab") && (!o || owner())) ? pref("tab") : "requests";
  await loadRequests();
  draw();
  bind();
  S.stream = live("/api/restaurant/events", { hello: onHello, request: onRequest, requests: () => loadRequests({ alert: true }), tables: onTablesChanged, resync: onHello }, (ok) => { S.liveOk = ok; setLiveState(ok); });
  S.timers.push(setInterval(refreshAgo, 20000), setInterval(safetyNet, 15000));
  return { stop };
}

function stop() {
  S.ac?.abort();
  S.stream?.close();
  S.timers.forEach(clearInterval);
  document.title = "Sign in";
}

function draw() {
  const r = S.r;
  const tabs = TABS.filter(([, , o]) => !o || owner()).map(([id, label]) => (id === "requests" ? [id, label, pendingCount()] : [id, label]));
  S.box = renderShell(S.root, { name: r.name, logoUrl: r.logo_url, email: S.me.email, roleLabel: S.me.role, tabs, active: S.tab, showLive: true });
  setLiveState(S.liveOk);
  updateBadge();
  renderTab();
}

async function renderTab() {
  const box = $("#tab-content");
  if (!box) return;
  S.box = box;
  const view = { requests: tabRequests, tables: tabTables, menu: tabMenu, settings: tabSettings, team: tabTeam, activity: tabActivity, account: tabAccount }[S.tab];
  try { await view(box); }
  catch (e) { if (e.status !== 401) put(box, html`<div class="notice error" role="alert">${e.message}</div>`); }
}

// ───────────────────────────────────────────────────────────── live data
async function loadRequests({ alert = false } = {}) {
  const rows = await api("/api/restaurant/requests?status=all&limit=200");
  const fresh = rows.filter((q) => q.status === "pending" && !S.seen.has(q.id));
  S.reqs = new Map(rows.map((q) => [q.id, q]));
  rows.forEach((q) => S.seen.add(q.id));
  if (alert && S.seenReady && fresh.length) announce(fresh);
  S.seenReady = true;
  afterRequests();
}

function onRequest(q) {
  if (!q) return;
  const isNew = q.status === "pending" && !S.seen.has(q.id);
  S.seen.add(q.id);
  S.reqs.set(q.id, q);
  if (isNew) announce([q]);
  afterRequests();
  if (S.tab === "tables") scheduleTables();
}

function afterRequests() {
  updateBadge();
  if (S.tab === "requests") paintRequests();
}

function onHello() { loadRequests({ alert: true }).catch(() => {}); if (S.tab === "tables") scheduleTables(); }
function onTablesChanged() { if (S.tab === "tables") scheduleTables(); }
function scheduleTables() { clearTimeout(S.tablesTimer); S.tablesTimer = setTimeout(async () => { try { await loadTables(); paintTables(); } catch { /* next event will retry */ } }, 250); }
function safetyNet() { if (!document.hidden && (!S.liveOk || (S.beat = (S.beat || 0) + 1) % 4 === 0)) loadRequests({ alert: true }).catch(() => {}); }
function refreshAgo() { $$("[data-ago]").forEach((el) => { el.textContent = ago(el.dataset.ago); }); }

function updateBadge() {
  const n = pendingCount();
  const b = $('[data-badge="requests"]');
  if (b) { b.textContent = n; b.hidden = n === 0; }
  document.title = `${n > 0 ? `(${n}) ` : ""}Dashboard · ${S.r.name}`;
}

function announce(rows) {
  playAlert();
  if (document.hidden && S.notify) {
    try { const q = rows[0]; new Notification(`Table ${q.table_code} · ${LABELS[q.request_type]}`, { body: rows.length > 1 ? `and ${rows.length - 1} more` : "A guest needs you.", tag: "tablecall" }); } catch { /* unsupported */ }
  }
  if (S.tab !== "requests") toast(`New request: Table ${rows[0].table_code} · ${LABELS[rows[0].request_type]}${rows.length > 1 ? ` (+${rows.length - 1})` : ""}`);
}

function playAlert() {
  if (navigator.vibrate) navigator.vibrate([120, 60, 120]);
  if (!S.sound || !S.audio) return;
  const now = S.audio.currentTime;
  [660, 880].forEach((freq, i) => {
    const osc = S.audio.createOscillator(), gain = S.audio.createGain(), t = now + i * 0.22;
    osc.type = "sine"; osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t); gain.gain.exponentialRampToValueAtTime(0.28, t + 0.02); gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    osc.connect(gain); gain.connect(S.audio.destination); osc.start(t); osc.stop(t + 0.2);
  });
}
async function ensureAudio() {
  S.audio = S.audio || new (window.AudioContext || window.webkitAudioContext)();
  await S.audio.resume();
}

// ───────────────────────────────────────────────────────────── requests tab
const matches = (q) => S.filter === "all" || (S.filter === "open" ? OPEN.includes(q.status) : q.status === S.filter);
const counts = () => {
  const all = [...S.reqs.values()];
  return { open: all.filter((q) => OPEN.includes(q.status)).length, completed: all.filter((q) => q.status === "completed").length, cancelled: all.filter((q) => q.status === "cancelled").length, all: all.length };
};
const EMPTY_TEXT = { open: ["All caught up", "New guest requests will appear here the moment they are sent."], completed: ["Nothing completed yet", "Finished requests will be listed here."], cancelled: ["Nothing cancelled", "Cancelled requests will be listed here."], all: ["No requests yet", "Once guests start tapping, their requests will show up here."] };

function requestRow(q) {
  const open = OPEN.includes(q.status);
  return html`<div class="req ${q.status}"><div class="req-ic">${icon(REQUEST_ICONS[q.request_type] || "bell", "icon")}</div>
    <div class="req-main"><b>Table ${q.table_code}</b><span class="what">${LABELS[q.request_type] || q.request_type}</span>
      <div class="small"><span data-ago="${q.created_at}">${ago(q.created_at)}</span>${q.handled_by ? " · " + q.handled_by : ""}</div></div>
    <div class="req-actions"><span class="pill ${q.status}">${STATUS_NAME[q.status]}</span>
      ${q.status === "pending" ? html`<button class="btn secondary sm" data-action="request" data-id="${q.id}" data-status="accepted">Accept</button>` : ""}
      ${open ? html`<button class="btn primary sm" data-action="request" data-id="${q.id}" data-status="completed">${icon("check")} Done</button>
        <button class="btn ghost sm" data-action="request" data-id="${q.id}" data-status="cancelled" aria-label="Cancel request">${icon("x")}</button>` : ""}</div></div>`;
}

function paintRequests() {
  const list = $("#request-list");
  if (!list) return;
  const rank = { pending: 0, accepted: 1 };
  const rows = [...S.reqs.values()].filter(matches)
    .sort((a, b) => (S.filter === "open" ? rank[a.status] - rank[b.status] : 0) || (a.created_at < b.created_at ? 1 : -1));
  reconcile(list, rows, { key: (q) => q.id, sig: (q) => q.status + "|" + (q.handled_by || ""), render: requestRow });
  const [title, text] = EMPTY_TEXT[S.filter];
  const empty = $("#req-empty");
  empty.hidden = rows.length > 0;
  empty.innerHTML = toStr(html`<strong>${title}</strong>${text}`);
  const c = counts();
  $$("[data-count]").forEach((el) => { el.textContent = c[el.dataset.count]; });
}

function tabRequests(box) {
  const chip = (id, label) => html`<button class="chip ${S.filter === id ? "active" : ""}" data-action="filter" data-id="${id}">${label} <b data-count="${id}">0</b></button>`;
  put(box, html`<div class="card"><div class="toolbar"><div><h2>Waiter requests</h2><p class="small">Updates appear instantly. No need to refresh.</p></div>
    <div class="row" style="gap:8px"><button class="btn sm ${S.sound ? "primary" : "secondary"}" data-action="sound">${icon(S.sound ? "volume" : "mute")} ${S.sound ? "Sound on" : "Sound off"}</button>
    ${"Notification" in window && Notification.permission !== "denied" ? html`<button class="btn sm ${S.notify ? "primary" : "secondary"}" data-action="notify" title="Pop-up alerts when this tab is in the background">${icon("bell")} ${S.notify ? "Pop-ups on" : "Pop-ups off"}</button>` : ""}</div></div>
    <div class="chips">${chip("open", "Open")}${chip("completed", "Done")}${chip("cancelled", "Cancelled")}${chip("all", "All")}</div>
    <div id="request-list"></div><div id="req-empty" class="empty" hidden></div></div>`);
  paintRequests();
  $$("#request-list .enter").forEach((n) => n.classList.remove("enter")); // no entrance animation for the first paint
}

// ───────────────────────────────────────────────────────────── tables tab
async function loadTables() { S.tables = await api("/api/restaurant/tables"); }

function pinVisible(t) {
  const p = S.pins[t.id];
  return p && t.pin_active && (!p.expires || new Date(p.expires) > new Date()) ? p : null;
}

function tableRow(t) {
  const p = pinVisible(t);
  const status = !t.session_active ? "Closed" : t.pin_active ? (t.pin_expires_at ? `PIN valid until ${clock(t.pin_expires_at)}` : "PIN valid until the table closes") : "Open · no PIN yet";
  return html`<div class="trow ${t.session_active ? "open" : ""}"><div class="tmain"><div class="tcode">${t.code}</div>
    <div style="min-width:0"><strong>Table ${t.code}</strong> ${t.pending_requests ? html`<span class="badge">${t.pending_requests} waiting</span>` : ""}
      <div class="small">${t.session_active ? "Open" : "Closed"}${t.session_active ? " · " : ""}${t.session_active ? status.replace(/^Open · /, "") : ""}</div>
      ${p ? html`<div class="pin-card"><span class="digits">${p.pin}</span><button class="btn secondary sm" data-action="copy" data-value="${p.pin}">${icon("copy")} Copy</button>
        <span class="small">Tell the guests this PIN. ${t.pin_expires_at ? "It stops working at " + clock(t.pin_expires_at) + "." : "It works until you close the table or make a new PIN."}</span></div>` : ""}</div></div>
    <div class="tctl"><div class="row">${t.session_active ? html`<button class="btn secondary sm" data-action="pin" data-id="${t.id}">${t.pin_active ? "New PIN" : "Get PIN"}</button>
        <button class="btn danger sm" data-action="close-table" data-id="${t.id}">Close table</button>`
        : html`<button class="btn primary sm" data-action="open-table" data-id="${t.id}">Open table</button>`}</div>
      <div class="row"><a class="btn ghost sm" href="/api/restaurant/tables/${t.id}/qr.png" target="_blank" rel="noopener">${icon("qr")} QR</a>
        <button class="btn ghost sm" data-action="copy" data-value="${t.qr_url}">${icon("link")} Link</button>
        ${owner() ? html`<button class="btn ghost sm" data-action="delete-table" data-id="${t.id}" data-name="${t.code}" aria-label="Delete table ${t.code}">${icon("trash")}</button>` : ""}</div></div></div>`;
}

function paintTables() {
  const list = $("#table-list");
  if (!list) return;
  reconcile(list, S.tables, { key: (t) => t.id, render: tableRow,
    sig: (t) => [t.code, t.session_active, t.pin_active, t.pin_expires_at, t.pending_requests, pinVisible(t)?.pin || "", owner()].join("|") });
  $("#table-empty").hidden = S.tables.length > 0;
  $$("#table-list .enter").forEach((n) => setTimeout(() => n.classList.remove("enter"), 600));
}

async function tabTables(box) {
  await loadTables();
  put(box, html`${owner() ? html`<div class="card"><h2>Add a table</h2><p class="sub">Each table gets its own QR code and its own PIN.</p>
      <form data-form="table" class="row" style="margin-top:12px"><input name="code" placeholder="Table name, e.g. T01" required maxlength="20" class="grow" aria-label="Table name">
      <button class="btn primary" type="submit">${icon("plus")} Add table</button></form>
      ${usageLine("tables", "tables")}</div>` : ""}
    <div class="card"><div class="toolbar"><div><h2>Tables</h2><p class="small">Open a table when guests sit down and give them its PIN. Closing the table switches the PIN off and clears its open requests.</p></div>
      <button class="btn secondary sm" data-action="print-cards">${icon("printer")} Print QR cards</button></div>
      <div id="table-list" style="margin-top:6px"></div>
      <div id="table-empty" class="empty" hidden><strong>No tables yet</strong>${owner() ? "Add your first table above." : "Ask the owner to add tables."}</div></div>`);
  paintTables();
  $$("#table-list .enter").forEach((n) => n.classList.remove("enter"));
}

function usageLine(key, noun) {
  const cap = S.r.limits[key], used = S.r.usage[key === "items" ? "items" : key];
  if (!cap) return "";
  return html`<div class="help" style="margin-top:12px">${used} of ${cap} ${noun} used on the ${S.r.plan_name} plan.</div>`;
}

async function printCards() {
  await loadTables();
  if (!S.tables.length) throw new Error("Add a table first.");
  const sheet = document.createElement("div");
  sheet.id = "print-sheet";
  sheet.innerHTML = toStr(html`${S.tables.map((t) => html`<div class="qr-card">${S.r.logo_url ? html`<img class="l" src="${S.r.logo_url}" alt="">` : ""}<h2>${S.r.name}</h2>
    <div class="t">Table ${t.code}</div><img class="q" src="/api/restaurant/tables/${t.id}/qr.png" alt="QR code for table ${t.code}"><p>Scan to see the menu and call your waiter</p></div>`)}`);
  document.body.appendChild(sheet);
  await Promise.all([...sheet.querySelectorAll("img")].map((i) => i.decode().catch(() => {})));
  const done = () => { document.body.classList.remove("printing"); sheet.remove(); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  document.body.classList.add("printing");
  window.print();
  setTimeout(done, 1500);
}

// ───────────────────────────────────────────────────────────── menu tab
function itemForm(item, categories) {
  const i = item || {};
  return html`<div class="uploader" data-uploader><div class="preview" data-preview>${i.image ? html`<img src="${i.image}" alt="">` : icon("image")}</div>
      <div><label class="btn secondary sm" style="margin:0;cursor:pointer"><input type="file" accept="image/*" hidden data-file>${icon("image")} ${i.image ? "Change photo" : "Add photo"}</label>
        <button type="button" class="btn ghost sm" data-remove-photo ${i.image ? "" : raw("hidden")}>Remove</button>
        <div class="help">A photo from your phone works. It is resized automatically.</div></div></div>
    <input type="hidden" name="image_id" value="${i.image_id || ""}"><input type="hidden" name="image_url" value="${i.image_url || ""}">
    <div class="form-grid"><div class="field"><label>Name</label><input name="name" required maxlength="120" value="${i.name || ""}"></div>
      <div class="field"><label>Price (NPR)</label><input name="price_npr" type="number" inputmode="decimal" min="0" max="1000000" step="0.01" required value="${i.price_npr ?? ""}"></div>
      <div class="field"><label>Category</label><select name="category_id"><option value="">No category</option>${categories.map((c) => html`<option value="${c.id}" ${c.id === i.category_id ? raw("selected") : ""}>${c.name}</option>`)}</select></div>
      <div class="field"><label>Availability</label><select name="is_available"><option value="true" ${i.is_available !== false ? raw("selected") : ""}>Available</option><option value="false" ${i.is_available === false ? raw("selected") : ""}>Sold out</option></select></div></div>
    <label>Description (optional)</label><textarea name="description" rows="2" maxlength="2000">${i.description || ""}</textarea>
    <label>Position in the menu</label><input name="sort_order" type="number" value="${i.sort_order ?? 0}" aria-describedby="sort-help"><div id="sort-help" class="help">Lower numbers appear first.</div>`;
}

function wireUploader(form, kind = "item") {
  const file = $("[data-file]", form), preview = $("[data-preview]", form), remove = $("[data-remove-photo]", form), box = $("[data-uploader]", form);
  file.addEventListener("change", async () => {
    if (!file.files[0]) return;
    form.dataset.locked = "1"; box.classList.add("busy");
    try {
      const m = await uploadImage(file.files[0], kind);
      form.elements.image_id.value = m.id; form.elements.image_url.value = "";
      preview.innerHTML = `<img src="${m.url}" alt="">`; remove.hidden = false;
    } catch (e) { fail(e); }
    finally { delete form.dataset.locked; box.classList.remove("busy"); file.value = ""; }
  });
  remove.addEventListener("click", () => { form.elements.image_id.value = ""; form.elements.image_url.value = ""; preview.innerHTML = toStr(icon("image")); remove.hidden = true; });
}

const itemPayload = (f) => ({
  name: f.name, description: f.description || "", price_npr: Number(f.price_npr), sort_order: Number(f.sort_order || 0),
  category_id: f.category_id ? Number(f.category_id) : null, image_id: f.image_id ? Number(f.image_id) : null,
  image_url: (f.image_url || "").trim() || null, is_available: f.is_available !== "false",
});

async function tabMenu(box) {
  const data = (S.menu = await api("/api/restaurant/menu"));
  const isOwner = owner();
  const groups = [...data.categories.map((c) => ({ c, items: data.items.filter((i) => i.category_id === c.id) })), { c: null, items: data.items.filter((i) => !i.category_id) }].filter((g) => g.c || g.items.length);
  const itemRow = (i) => html`<div class="irow ${i.is_available ? "" : "off"}" data-name="${(i.name + " " + i.description).toLowerCase()}">
    ${i.image ? html`<img class="thumb" src="${i.image}" alt="" loading="lazy" referrerpolicy="no-referrer">` : html`<span class="thumb">${icon("image")}</span>`}
    <div class="row-main"><strong>${i.name}</strong><div class="small">${i.description || "No description"}</div><b>${money(i.price_npr)}</b></div>
    <div class="row" style="gap:8px"><button class="switch ${i.is_available ? "on" : ""}" role="switch" aria-checked="${i.is_available}" data-action="availability" data-id="${i.id}" data-value="${!i.is_available}"><i></i>${i.is_available ? "Available" : "Sold out"}</button>
      ${isOwner ? html`<button class="btn ghost sm" data-action="item-edit" data-id="${i.id}" aria-label="Edit ${i.name}">${icon("edit")}</button><button class="btn ghost sm" data-action="item-delete" data-id="${i.id}" data-name="${i.name}" aria-label="Delete ${i.name}">${icon("trash")}</button>` : ""}</div></div>`;
  put(box, html`${isOwner ? html`<div class="card"><div class="toolbar"><div><h2>Categories</h2><p class="small">Group dishes, for example Coffee, Breakfast or Momo.</p></div>
      <form data-form="category" class="row" style="gap:8px"><input name="name" placeholder="New category" required maxlength="80" aria-label="Category name" style="width:190px"><button class="btn secondary" type="submit">${icon("plus")} Add</button></form></div>
      ${data.categories.length ? html`<div class="cat-chips">${data.categories.map((c) => html`<span class="cat-chip">${c.name}<button data-action="category-edit" data-id="${c.id}" aria-label="Rename ${c.name}">${icon("edit")}</button><button data-action="category-delete" data-id="${c.id}" data-name="${c.name}" aria-label="Delete ${c.name}">${icon("x")}</button></span>`)}</div>` : ""}</div>` : ""}
    <div class="card"><div class="toolbar"><div><h2>Menu items <span class="pill">${data.items.length}</span></h2>${isOwner ? usageLine("items", "items") : ""}</div>
      <div class="row" style="gap:8px"><input type="search" id="admin-search" placeholder="Search items" aria-label="Search items" style="width:180px">${isOwner ? html`<button class="btn primary" data-action="item-add">${icon("plus")} Add item</button>` : ""}</div></div>
      <div id="items">${groups.length ? groups.map((g) => html`<div data-group><div class="group-title">${g.c ? g.c.name : "No category"}</div>${g.items.map(itemRow)}</div>`)
        : html`<div class="empty"><strong>No menu items yet</strong>${isOwner ? "Choose Add item to create your first dish." : "Ask the owner to add dishes."}</div>`}</div></div>`);
}

// ───────────────────────────────────────────────────────────── settings tab (owner)
function meter(label, used, cap) {
  const pct = cap ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  return html`<div style="margin-top:12px"><div class="row between"><span>${label}</span><span class="small">${used} of ${cap || "unlimited"}</span></div>${cap ? html`<div class="meter ${used >= cap ? "full" : ""}"><i style="width:${pct}%"></i></div>` : ""}</div>`;
}

function tabSettings(box) {
  const r = S.r, color = r.brand_color || "#2f3bbd";
  put(box, html`<div class="set-grid"><div>
    <div class="card"><h2>Branding</h2><p class="sub">Guests see your logo, name and colour on the menu page.</p>
      <div class="uploader" style="margin-top:14px"><div class="preview logo">${r.logo_url ? html`<img src="${r.logo_url}" alt="Your logo">` : icon("image")}</div>
        <div><label class="btn secondary sm" style="margin:0;cursor:pointer"><input type="file" accept="image/*" hidden data-logo-file>${icon("image")} ${r.logo_url ? "Replace logo" : "Upload logo"}</label>
          ${r.logo_url ? html`<button class="btn ghost sm" data-action="logo-remove">Remove</button>` : ""}
          <div class="help">A square or wide PNG works best.</div></div></div>
      <form data-form="brand"><label for="s-name">Restaurant name</label><input id="s-name" name="name" required minlength="2" maxlength="120" value="${r.name}">
        <label for="s-color">Brand colour</label><div class="swatches"><input id="s-color" type="color" name="brand_color" value="${color}" aria-label="Brand colour">
          ${PRESET_COLORS.map((c) => html`<button type="button" class="swatch" style="background:${c}" data-action="swatch" data-value="${c}" aria-label="Use ${c}"></button>`)}</div>
        <label style="display:flex;gap:8px;align-items:center;font-weight:500;margin-top:12px"><input type="checkbox" name="default_color" ${r.brand_color ? "" : raw("checked")}> Use the default colour</label>
        <div class="form-actions"><button class="btn primary" type="submit">Save branding</button></div></form></div>
    <div class="card"><h2>Guest Wi-Fi</h2><p class="sub">Shown to guests with a copy button, but only while their table is open.</p>
      <form data-form="wifi"><label for="w-ssid">Network name</label><input id="w-ssid" name="wifi_ssid" maxlength="32" value="${r.wifi_ssid}" placeholder="e.g. LakeView-Guest" autocomplete="off">
        <label for="w-pass">Password</label><input id="w-pass" name="wifi_password" maxlength="63" value="${r.wifi_password}" placeholder="Leave empty for an open network" autocomplete="off" autocapitalize="off" spellcheck="false">
        <div class="help">Passwords are visible to guests, so use your guest network, not your staff one.</div>
        <div class="form-actions"><button class="btn primary" type="submit">Save Wi-Fi</button>
          ${r.wifi_ssid ? html`<a class="btn secondary" href="/api/restaurant/wifi-qr.png" target="_blank" rel="noopener">${icon("qr")} Wi-Fi QR code</a><button type="button" class="btn ghost" data-action="wifi-clear">Hide from guests</button>` : ""}</div></form></div>
  </div><div>
    <div class="card"><h2>Table PIN</h2><p class="sub">Choose how long a PIN works. It always stops when the table is closed.</p>
      <form data-form="pin"><label class="radio-card"><input type="radio" name="pin_mode" value="session" ${r.pin_mode === "session" ? raw("checked") : ""}><div><b>Until the table closes</b><span>Not time-based. The PIN works for the whole visit, until you close the table or make a new PIN.</span></div></label>
        <label class="radio-card"><input type="radio" name="pin_mode" value="timed" ${r.pin_mode === "timed" ? raw("checked") : ""}><div><b>For a set time</b><span>The PIN expires after the number of minutes you choose.</span></div></label>
        <div class="minutes"><input type="number" name="pin_ttl_minutes" min="1" max="10080" value="${r.pin_ttl_minutes}" aria-label="Minutes" ${r.pin_mode === "timed" ? "" : raw("disabled")}><span class="small">minutes</span>
          ${[15, 30, 60, 120, 240].map((m) => html`<button type="button" class="chip" data-action="minutes" data-value="${m}">${m < 60 ? m + " min" : m / 60 + " h"}</button>`)}</div>
        <div class="help" style="margin-top:12px">Applies to PINs you make from now on. PINs already in use keep their current rule.</div>
        <div class="form-actions"><button class="btn primary" type="submit">Save PIN rule</button></div></form></div>
    <div class="card"><h2>Your plan</h2><div class="kv" style="border:0;padding-bottom:0"><span>Plan</span><strong>${r.plan_name}</strong></div>
      ${meter("Tables", r.usage.tables, r.limits.tables)}${meter("Staff accounts", r.usage.staff, r.limits.staff)}${meter("Menu items", r.usage.items, r.limits.items)}
      <p class="help" style="margin-top:14px">Need more? Ask your platform administrator to change your plan.</p></div>
  </div></div>`);
}

async function saveSettings(patch, message = "Saved.") {
  S.r = await api("/api/restaurant/settings", { method: "PATCH", body: patch });
  toast(message);
  draw();
}

// ───────────────────────────────────────────────────────────── team, activity, account
async function tabTeam(box) {
  const users = await api("/api/restaurant/staff");
  put(box, html`<div class="card"><h2>Add a staff member</h2><p class="sub">Staff can handle requests, open and close tables, make PINs and mark dishes sold out. They can't change the menu, tables, settings or team.</p>
    <form data-form="staff" class="form-grid" style="margin-top:6px"><div class="field"><label>Email</label><input name="email" type="email" required autocomplete="off"></div>
    <div class="field"><label>Temporary password (10+ characters)</label><input name="password" type="password" minlength="10" maxlength="128" required autocomplete="new-password"></div>
    <div class="field" style="align-self:end"><button class="btn primary block" type="submit">${icon("plus")} Add staff</button></div></form>${usageLine("staff", "staff accounts")}</div>
    <div class="card"><h2>Team</h2>${users.map((u) => html`<div class="urow"><div class="row-main"><strong>${u.email}</strong><div class="small">${u.role === "owner" ? "Owner" : "Staff"} · added ${new Date(u.created_at).toLocaleDateString()}</div></div>
      <div class="row" style="gap:8px"><span class="pill ${u.is_active ? "ok" : "bad"}">${u.is_active ? "Active" : "Disabled"}</span>
      ${u.role === "staff" ? html`<button class="btn secondary sm" data-action="staff-reset" data-id="${u.id}" data-name="${u.email}">Reset password</button>
        <button class="btn ${u.is_active ? "danger" : "secondary"} sm" data-action="staff-toggle" data-id="${u.id}" data-active="${!u.is_active}" data-name="${u.email}">${u.is_active ? "Disable" : "Enable"}</button>` : ""}</div></div>`)}</div>`);
}

const ACTIONS = { "table.created": "Added a table", "table.deleted": "Deleted a table", "table.session_opened": "Opened a table", "table.session_closed": "Closed a table", "table.pin_rotated": "Made a new PIN",
  "staff.created": "Added a staff member", "staff.activated": "Enabled a staff account", "staff.deactivated": "Disabled a staff account", "staff.password_reset": "Reset a staff password",
  "auth.login": "Signed in", "auth.password_changed": "Changed their password", "restaurant.settings_updated": "Updated settings" };

async function tabActivity(box) {
  const rows = await api("/api/restaurant/audit?limit=100");
  put(box, html`<div class="card"><h2>Activity</h2><p class="sub">Who did what in your restaurant.</p><div style="margin-top:8px">${rows.length ? rows.map((a) => html`<div class="logline"><div><b>${ACTIONS[a.action] || a.action}</b><div class="small">${a.actor || "system"}${a.detail ? " · " + a.detail : ""}</div></div><span class="small">${dateTime(a.created_at)}</span></div>`)
    : html`<div class="empty"><strong>No activity yet</strong></div>`}</div></div>`);
}

function tabAccount(box) {
  const r = S.r;
  put(box, html`<div class="set-grid"><div class="card"><h2>Account</h2><div class="kv"><span>Restaurant</span><strong>${r.name}</strong></div>
      <div class="kv"><span>Signed in as</span><strong>${S.me.email}</strong></div><div class="kv"><span>Role</span><strong>${S.me.role === "owner" ? "Owner" : "Staff"}</strong></div>
      <div class="kv"><span>Plan</span><strong>${r.plan_name}</strong></div><div class="kv"><span>Guest link</span><strong class="mono">/t/${r.slug}/…</strong></div>
      <div class="form-actions"><button class="btn secondary" data-action="logout">${icon("logout")} Sign out</button></div></div>
    <div class="card"><h2>Change password</h2><form data-form="password"><label>Current password</label><input name="current_password" type="password" required autocomplete="current-password">
      <label>New password (10+ characters)</label><input name="new_password" type="password" minlength="10" maxlength="128" required autocomplete="new-password">
      <div class="form-actions"><button class="btn primary" type="submit">Update password</button></div></form><p class="help" style="margin-top:12px">This signs you out on other devices.</p></div></div>`);
}

// ───────────────────────────────────────────────────────────── events
const reload = async (message) => { await renderTab(); if (message) toast(message); };

const actions = {
  logout: () => S.hooks.signOut(),
  async sound() { S.sound = !S.sound; setPref("sound", S.sound ? "1" : "0"); if (S.sound) { try { await ensureAudio(); playAlert(); } catch { S.sound = false; throw new Error("Sound isn't available in this browser."); } } await renderTab(); },
  async notify() {
    if (S.notify) S.notify = false;
    else {
      const perm = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
      if (perm !== "granted") throw new Error("Pop-up alerts are blocked. Allow notifications for this site in your browser settings.");
      S.notify = true;
    }
    setPref("notify", S.notify ? "1" : "0");
    await renderTab();
  },
  filter(el) { S.filter = el.dataset.id; $$(".chip[data-action=filter]").forEach((c) => c.classList.toggle("active", c === el)); paintRequests(); },
  async request(el) {
    const q = S.reqs.get(Number(el.dataset.id));
    await api(`/api/restaurant/requests/${el.dataset.id}`, { method: "PATCH", body: { status: el.dataset.status } });
    if (q) { q.status = el.dataset.status; q.handled_by = S.me.email; S.reqs.set(q.id, q); afterRequests(); }
  },
  async "open-table"(el) {
    await api(`/api/restaurant/tables/${el.dataset.id}/session/open`, { method: "POST" });
    const d = await api(`/api/restaurant/tables/${el.dataset.id}/pin/rotate`, { method: "POST" });
    S.pins[el.dataset.id] = { pin: d.pin, expires: d.expires_at };
    await loadTables(); paintTables();
  },
  async pin(el) {
    const d = await api(`/api/restaurant/tables/${el.dataset.id}/pin/rotate`, { method: "POST" });
    S.pins[el.dataset.id] = { pin: d.pin, expires: d.expires_at };
    await loadTables(); paintTables();
  },
  async "close-table"(el) {
    if (!(await confirmDialog("Close this table?", "Its PIN stops working immediately and any open requests for the table are cancelled.", "Close table", true))) return;
    await api(`/api/restaurant/tables/${el.dataset.id}/session/close`, { method: "POST" });
    delete S.pins[el.dataset.id];
    await loadTables(); paintTables(); await loadRequests();
  },
  async "delete-table"(el) {
    if (!(await confirmDialog(`Delete table ${el.dataset.name}?`, "Its QR code stops working and open requests are cancelled. Request history is kept.", "Delete table", true))) return;
    await api(`/api/restaurant/tables/${el.dataset.id}`, { method: "DELETE" });
    await reload("Table deleted.");
  },
  "print-cards": () => printCards(),
  async copy(el) { toast((await copyText(el.dataset.value)) ? "Copied." : "Couldn't copy. Select the text instead."); },
  async availability(el) {
    await api(`/api/restaurant/menu/items/${el.dataset.id}/availability`, { method: "PATCH", body: { is_available: el.dataset.value === "true" } });
    await reload();
  },
  async "item-add"() {
    const f = await modal({ title: "Add menu item", confirmText: "Add item", body: itemForm(null, S.menu.categories), onOpen: wireUploader });
    if (!f) return;
    await api("/api/restaurant/menu/items", { method: "POST", body: itemPayload(f) });
    await reload("Item added.");
  },
  async "item-edit"(el) {
    const item = S.menu.items.find((i) => i.id === Number(el.dataset.id));
    const f = await modal({ title: "Edit menu item", confirmText: "Save changes", body: itemForm(item, S.menu.categories), onOpen: wireUploader });
    if (!f) return;
    await api(`/api/restaurant/menu/items/${item.id}`, { method: "PATCH", body: itemPayload(f) });
    await reload("Item saved.");
  },
  async "item-delete"(el) {
    if (!(await confirmDialog("Delete this item?", `“${el.dataset.name}” will be removed from the menu. This can't be undone.`, "Delete item", true))) return;
    await api(`/api/restaurant/menu/items/${el.dataset.id}`, { method: "DELETE" });
    await reload("Item deleted.");
  },
  async "category-edit"(el) {
    const c = S.menu.categories.find((x) => x.id === Number(el.dataset.id));
    const f = await modal({ title: "Edit category", body: html`<label>Name</label><input name="name" required maxlength="80" value="${c.name}"><label>Position</label><input name="sort_order" type="number" value="${c.sort_order}">` });
    if (!f) return;
    await api(`/api/restaurant/menu/categories/${c.id}`, { method: "PATCH", body: { name: f.name, sort_order: Number(f.sort_order || 0) } });
    await reload("Category saved.");
  },
  async "category-delete"(el) {
    if (!(await confirmDialog("Delete this category?", `Items in “${el.dataset.name}” are kept and moved to “No category”.`, "Delete category", true))) return;
    await api(`/api/restaurant/menu/categories/${el.dataset.id}`, { method: "DELETE" });
    await reload("Category deleted.");
  },
  swatch(el) { const c = $("[name=brand_color]"); c.value = el.dataset.value; $("[name=default_color]").checked = false; },
  minutes(el) { const i = $("[name=pin_ttl_minutes]"); i.value = el.dataset.value; $("[name=pin_mode][value=timed]").checked = true; i.disabled = false; },
  async "logo-remove"() { await saveSettings({ remove_logo: true }, "Logo removed."); },
  async "wifi-clear"() { await saveSettings({ wifi_ssid: "", wifi_password: "" }, "Wi-Fi hidden from guests."); },
  async "staff-toggle"(el) {
    const active = el.dataset.active === "true";
    if (!active && !(await confirmDialog("Disable this account?", `${el.dataset.name} will be signed out and unable to sign in.`, "Disable", true))) return;
    await api(`/api/restaurant/staff/${el.dataset.id}`, { method: "PATCH", body: { is_active: active } });
    await reload(active ? "Account enabled." : "Account disabled.");
  },
  async "staff-reset"(el) {
    const f = await passwordModal("Reset staff password", `Set a new password for ${el.dataset.name}.`);
    if (!f) return;
    await api(`/api/restaurant/staff/${el.dataset.id}/password`, { method: "POST", body: { password: f.password } });
    toast("Password reset.");
  },
};

const forms = {
  async table(f) { await api("/api/restaurant/tables", { method: "POST", body: { code: f.code } }); await reload("Table added."); },
  async category(f) { await api("/api/restaurant/menu/categories", { method: "POST", body: { name: f.name, sort_order: 0 } }); await reload("Category added."); },
  async staff(f) { await api("/api/restaurant/staff", { method: "POST", body: f }); await reload("Staff account created. Share the password securely."); },
  async brand(f) { await saveSettings({ name: f.name, brand_color: f.default_color ? "" : f.brand_color }, "Branding saved."); },
  async wifi(f) { await saveSettings({ wifi_ssid: f.wifi_ssid, wifi_password: f.wifi_password }, "Wi-Fi saved."); },
  async pin(f) {
    const patch = { pin_mode: f.pin_mode };
    if (f.pin_mode === "timed") patch.pin_ttl_minutes = Number(f.pin_ttl_minutes);
    await saveSettings(patch, "PIN rule saved.");
  },
  async password(f, form) { const r = await api("/api/auth/password", { method: "POST", body: f }); form.reset(); toast(r.message); },
};

function bind() {
  const o = { signal: S.ac.signal };
  document.addEventListener("click", async (e) => {
    const tab = e.target.closest("[data-tab]");
    if (tab) {
      S.tab = tab.dataset.tab; setPref("tab", S.tab);
      $$(".tab").forEach((t) => { t.classList.toggle("active", t === tab); t.toggleAttribute("aria-current", t === tab); });
      return renderTab();
    }
    const el = e.target.closest("[data-action]");
    if (!el || el.disabled) return;
    const fn = actions[el.dataset.action];
    if (!fn) return;
    if (el.dataset.action !== "filter") setBusy(el, true);
    try { await fn(el); } catch (err) { fail(err); } finally { if (el.isConnected) setBusy(el, false); }
  }, o);
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    e.preventDefault();
    const submit = $("button[type=submit]", form);
    setBusy(submit, true);
    try { await forms[form.dataset.form](Object.fromEntries(new FormData(form)), form); } catch (err) { fail(err); } finally { if (submit?.isConnected) setBusy(submit, false); }
  }, o);
  document.addEventListener("change", async (e) => {
    if (e.target.matches("[name=pin_mode]")) { $("[name=pin_ttl_minutes]").disabled = e.target.value !== "timed"; return; }
    if (e.target.matches("[name=brand_color]")) { $("[name=default_color]").checked = false; return; }
    if (e.target.matches("[data-logo-file]") && e.target.files[0]) {
      try { const m = await uploadImage(e.target.files[0], "logo"); await saveSettings({ logo_media_id: m.id }, "Logo saved."); } catch (err) { fail(err); }
    }
  }, o);
  document.addEventListener("input", (e) => {
    if (e.target.id !== "admin-search") return;
    const q = e.target.value.trim().toLowerCase();
    $$("#items .irow").forEach((row) => { row.hidden = Boolean(q) && !row.dataset.name.includes(q); });
    $$("#items [data-group]").forEach((g) => { g.hidden = !$$(".irow", g).some((r) => !r.hidden); });
  }, o);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadRequests({ alert: true }).catch(() => {}); }, o);
  // Browsers only allow sound after a tap, so a remembered "sound on" is re-armed on the first tap of each visit.
  if (S.sound) document.addEventListener("pointerdown", () => ensureAudio().catch(() => {}), { once: true, ...o });
}
