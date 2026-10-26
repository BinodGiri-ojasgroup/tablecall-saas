/* Super admin: restaurants, demo requests, plans, platform administrators and landing page content. */
import { $, $$, api, confirmDialog, dateTime, fail, html, icon, modal, passwordModal, put, raw, randomPassword, setBusy, toast } from "/static/common.js";
import { renderShell } from "/static/shell.js";

const TABS = [["overview", "Overview"], ["restaurants", "Restaurants"], ["leads", "Demo requests"], ["plans", "Plans"], ["admins", "Administrators"], ["site", "Website"], ["account", "Account"]];
const P = {};
const slugify = (s) => s.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
const limitText = (n) => (n ? n : "unlimited");

export async function start(root, me, hooks) {
  P.ac?.abort();
  Object.assign(P, { root, me, hooks, ac: new AbortController(), tab: "overview", prefill: null, plans: [] });
  const s = await api("/api/platform/overview");
  draw(s.new_leads);
  bind();
  P.timer = setInterval(refreshStats, 15000);
  return { stop() { P.ac.abort(); clearInterval(P.timer); } };
}

function draw(leadCount = 0) {
  renderShell(P.root, { name: "TableCall control center", email: P.me.email, roleLabel: "Platform admin", active: P.tab,
    tabs: TABS.map(([id, label]) => (id === "leads" ? [id, label, leadCount] : [id, label])) });
  document.title = "Control center";
  renderTab();
}

async function renderTab() {
  const box = $("#tab-content");
  const view = { overview: tabOverview, restaurants: tabRestaurants, leads: tabLeads, plans: tabPlans, admins: tabAdmins, site: tabSite, account: tabAccount }[P.tab];
  try { await view(box); } catch (e) { if (e.status !== 401) put(box, html`<div class="notice error" role="alert">${e.message}</div>`); }
}

async function refreshStats() {
  if (document.hidden) return;
  try {
    const s = await api("/api/platform/overview");
    $$("[data-stat]").forEach((el) => { const v = String(s[el.dataset.stat]); if (el.textContent !== v) el.textContent = v; });
    const b = $('[data-badge="leads"]'); if (b) { b.textContent = s.new_leads; b.hidden = s.new_leads === 0; }
  } catch { /* offline; next tick */ }
}

// ───────────────────────────────────────────────────────────── overview
async function tabOverview(box) {
  const [s, log] = await Promise.all([api("/api/platform/overview"), api("/api/platform/audit?limit=15")]);
  const stat = (key, label) => html`<div class="card stat"><b data-stat="${key}">${s[key]}</b><span>${label}</span></div>`;
  put(box, html`<div class="page-head"><h1>Overview</h1><p class="small">Numbers update by themselves every few seconds.</p></div>
    <div class="stats">${stat("restaurants", "Restaurants")}${stat("active_restaurants", "Active")}${stat("tables", "Tables")}${stat("pending_requests", "Requests waiting now")}
      ${stat("requests_today", "Requests, last 24 h")}${stat("requests", "Requests, all time")}${stat("users", "Accounts")}${stat("new_leads", "New demo requests")}</div>
    <div class="card"><h2>Recent platform activity</h2><div style="margin-top:8px">${log.length ? log.map((a) => html`<div class="logline"><div><b>${a.action.replace(/^(platform|admin)\./, "").replace(/_/g, " ")}</b>
      <div class="small">${a.actor}${a.detail ? " · " + a.detail : ""}</div></div><span class="small">${dateTime(a.created_at)}</span></div>`) : html`<div class="empty"><strong>Nothing yet</strong></div>`}</div></div>`);
}

// ───────────────────────────────────────────────────────────── restaurants
async function tabRestaurants(box) {
  const [rows, plans] = await Promise.all([api("/api/platform/restaurants"), api("/api/platform/plans")]);
  P.plans = plans;
  const pf = P.prefill || {};
  P.prefill = null;
  put(box, html`<div class="card"><h2>New restaurant</h2><p class="sub">Creates the restaurant and its owner account. Send the owner their sign-in details securely.</p>
    <form data-form="create-restaurant" class="form-grid" style="margin-top:6px">
      <div class="field"><label>Restaurant name</label><input name="name" required minlength="2" maxlength="120" value="${pf.name || ""}" placeholder="Lake View Café"></div>
      <div class="field"><label>URL name</label><input name="slug" required pattern="[a-z0-9]+(-[a-z0-9]+)*" value="${pf.slug || ""}" placeholder="lake-view-cafe" title="Lowercase letters, numbers and hyphens"></div>
      <div class="field"><label>Owner email</label><input name="owner_email" type="email" required value="${pf.email || ""}"></div>
      <div class="field"><label>Temporary password</label><div class="row" style="flex-wrap:nowrap;gap:8px"><input name="owner_password" type="text" minlength="10" maxlength="128" required autocomplete="off" value="${randomPassword()}" class="mono">
        <button type="button" class="btn secondary" data-action="gen-password" style="flex:none">New</button></div></div>
      <div class="field"><label>Plan</label><select name="plan">${plans.map((p) => html`<option value="${p.key}">${p.name}</option>`)}</select></div>
      <div class="field" style="align-self:end"><button class="btn primary block" type="submit">${icon("plus")} Create restaurant</button></div></form></div>
    <div class="card"><div class="toolbar"><div><h2>Restaurants <span class="pill">${rows.length}</span></h2></div>
      <input id="tenant-filter" class="inline-input" type="search" placeholder="Search restaurants" aria-label="Search restaurants"></div>
      <div id="tenants" style="margin-top:6px">${rows.length ? rows.map((r) => tenant(r, plans)) : html`<div class="empty"><strong>No restaurants yet</strong>Create the first one above.</div>`}</div></div>`);
}

function tenant(r, plans) {
  const cap = (used, max) => `${used}${max ? " / " + max : ""}`;
  return html`<div class="tenant" data-search="${(r.name + " " + r.slug + " " + (r.owner_email || "")).toLowerCase()}">
    <div style="min-width:0"><h3>${r.name} <span class="pill ${r.is_active ? "ok" : "bad"}">${r.is_active ? "Active" : "Suspended"}</span></h3>
      <div class="small"><span class="mono">/${r.slug}</span> · Owner: ${r.owner_email || "not set"}</div>
      <div class="small">${cap(r.table_count, r.limits.tables)} tables · ${cap(r.staff_count, r.limits.staff)} staff · ${r.request_count} requests · since ${new Date(r.created_at).toLocaleDateString()}</div></div>
    <div><label class="sr-only" for="plan-${r.id}">Plan</label><select id="plan-${r.id}" class="plan-select" data-plan-for="${r.id}">${plans.map((p) => html`<option value="${p.key}" ${p.key === r.plan ? raw("selected") : ""}>${p.name}</option>`)}</select></div>
    <div class="acts"><button class="btn secondary sm" data-action="tenant-rename" data-id="${r.id}" data-name="${r.name}">${icon("edit")} Rename</button>
      <button class="btn secondary sm" data-action="owner-reset" data-id="${r.id}" data-name="${r.name}">Reset owner password</button>
      <button class="btn ${r.is_active ? "danger" : "secondary"} sm" data-action="tenant-toggle" data-id="${r.id}" data-name="${r.name}" data-active="${r.is_active}">${r.is_active ? "Suspend" : "Reactivate"}</button>
      <button class="btn ghost sm" data-action="tenant-delete" data-id="${r.id}" data-name="${r.name}" data-slug="${r.slug}" aria-label="Delete ${r.name}">${icon("trash")}</button></div></div>`;
}

// ───────────────────────────────────────────────────────────── leads
const LEAD_STATUS = { new: "New", contacted: "Contacted", won: "Became a customer", dismissed: "Dismissed" };

async function tabLeads(box) {
  const rows = (P.leads = await api("/api/platform/leads"));
  put(box, html`<div class="card"><h2>Demo requests</h2><p class="sub">Submitted from the form on your website.</p><div style="margin-top:8px">${rows.length ? rows.map((l) => html`<div class="lead">
    <div class="row between"><div><strong>${l.name}</strong>${l.business ? " · " + l.business : ""} <span class="pill ${l.status === "new" ? "pending" : l.status === "won" ? "ok" : ""}">${LEAD_STATUS[l.status]}</span></div><span class="small">${dateTime(l.created_at)}</span></div>
    <div class="small"><a href="mailto:${l.email}">${l.email}</a>${l.phone ? " · " + l.phone : ""}</div>${l.message ? html`<p>${l.message}</p>` : ""}
    <div class="row" style="gap:8px;margin-top:8px"><button class="btn primary sm" data-action="lead-convert" data-id="${l.id}">Create restaurant</button>
      <select class="plan-select" data-lead-status="${l.id}" aria-label="Status">${Object.entries(LEAD_STATUS).map(([k, v]) => html`<option value="${k}" ${k === l.status ? raw("selected") : ""}>${v}</option>`)}</select>
      <button class="btn ghost sm" data-action="lead-delete" data-id="${l.id}" aria-label="Delete request">${icon("trash")}</button></div></div>`)
    : html`<div class="empty"><strong>No demo requests yet</strong>They will appear here when someone fills in the form on your website.</div>`}</div></div>`);
}

// ───────────────────────────────────────────────────────────── plans
async function tabPlans(box) {
  const plans = (P.plans = await api("/api/platform/plans"));
  put(box, html`<div class="card"><div class="toolbar"><div><h2>Plans</h2><p class="sub">Limits are enforced for each restaurant. Public plans appear in the pricing section of your website.</p></div>
    <button class="btn primary" data-action="plan-add">${icon("plus")} New plan</button></div><div style="margin-top:8px">${plans.map((p) => html`<div class="urow">
    <div class="row-main"><strong>${p.name}</strong> <span class="mono small">${p.key}</span> ${p.is_public ? "" : html`<span class="pill">Hidden</span>`}
      <div class="small">${p.price_npr ? "NPR " + p.price_npr.toLocaleString() + " / month" : "Free"} · ${limitText(p.max_tables)} tables · ${limitText(p.max_staff)} staff · ${limitText(p.max_menu_items)} menu items · ${p.restaurants} restaurant${p.restaurants === 1 ? "" : "s"}</div></div>
    <div class="row" style="gap:8px"><button class="btn secondary sm" data-action="plan-edit" data-id="${p.id}">${icon("edit")} Edit</button>
      <button class="btn ghost sm" data-action="plan-delete" data-id="${p.id}" data-name="${p.name}" aria-label="Delete ${p.name}">${icon("trash")}</button></div></div>`)}</div></div>`);
}

function planForm(p) {
  const x = p || { key: "", name: "", price_npr: 0, max_tables: 0, max_staff: 0, max_menu_items: 0, features: "", is_public: true, sort_order: 0 };
  return html`<div class="form-grid"><div class="field"><label>Name</label><input name="name" required maxlength="60" value="${x.name}"></div>
    <div class="field"><label>Key</label><input name="key" required pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="30" value="${x.key}" ${p ? raw("readonly") : ""} title="Lowercase letters, numbers and hyphens"></div>
    <div class="field"><label>Price (NPR per month)</label><input name="price_npr" type="number" min="0" value="${x.price_npr}"></div>
    <div class="field"><label>Position</label><input name="sort_order" type="number" value="${x.sort_order}"></div>
    <div class="field"><label>Tables</label><input name="max_tables" type="number" min="0" value="${x.max_tables}"></div>
    <div class="field"><label>Staff accounts</label><input name="max_staff" type="number" min="0" value="${x.max_staff}"></div>
    <div class="field"><label>Menu items</label><input name="max_menu_items" type="number" min="0" value="${x.max_menu_items}"></div>
    <div class="field"><label>Show on website</label><select name="is_public"><option value="true" ${x.is_public ? raw("selected") : ""}>Yes</option><option value="false" ${x.is_public ? "" : raw("selected")}>No</option></select></div></div>
    <div class="help">0 means unlimited.</div><label>Features (one per line, shown on the website)</label><textarea name="features" rows="4" maxlength="800">${x.features}</textarea>`;
}
const planPayload = (f) => ({ key: f.key, name: f.name, price_npr: Number(f.price_npr || 0), max_tables: Number(f.max_tables || 0), max_staff: Number(f.max_staff || 0),
  max_menu_items: Number(f.max_menu_items || 0), features: f.features || "", is_public: f.is_public === "true", sort_order: Number(f.sort_order || 0) });

// ───────────────────────────────────────────────────────────── administrators, site, account
async function tabAdmins(box) {
  const rows = await api("/api/platform/admins");
  put(box, html`<div class="card"><h2>Add an administrator</h2><p class="sub">Administrators can manage every restaurant, plan and setting.</p>
    <form data-form="admin" class="form-grid" style="margin-top:6px"><div class="field"><label>Email</label><input name="email" type="email" required autocomplete="off"></div>
    <div class="field"><label>Temporary password (10+ characters)</label><input name="password" type="text" minlength="10" maxlength="128" required value="${randomPassword()}" class="mono" autocomplete="off"></div>
    <div class="field" style="align-self:end"><button class="btn primary block" type="submit">${icon("plus")} Add administrator</button></div></form></div>
    <div class="card"><h2>Administrators</h2>${rows.map((a) => html`<div class="urow"><div class="row-main"><strong>${a.email}</strong>${a.is_me ? html` <span class="pill">You</span>` : ""}<div class="small">Added ${new Date(a.created_at).toLocaleDateString()}</div></div>
      <div class="row" style="gap:8px"><span class="pill ${a.is_active ? "ok" : "bad"}">${a.is_active ? "Active" : "Disabled"}</span>
      ${a.is_me ? "" : html`<button class="btn secondary sm" data-action="admin-reset" data-id="${a.id}" data-name="${a.email}">Reset password</button>
        <button class="btn ${a.is_active ? "danger" : "secondary"} sm" data-action="admin-toggle" data-id="${a.id}" data-active="${!a.is_active}" data-name="${a.email}">${a.is_active ? "Disable" : "Enable"}</button>`}</div></div>`)}</div>`);
}

async function tabSite(box) {
  const s = await api("/api/platform/settings");
  put(box, html`<div class="card"><div class="toolbar"><div><h2>Website</h2><p class="sub">Text shown on your public landing page. Changes appear within seconds.</p></div>
    <a class="btn secondary sm" href="/" target="_blank" rel="noopener">${icon("link")} View website</a></div>
    <form data-form="site"><label>Product name</label><input name="brand_name" required maxlength="40" value="${s.brand_name}">
    <label>Headline</label><input name="tagline" maxlength="140" value="${s.tagline}"><div class="help">The large line at the top of the page.</div>
    <div class="form-grid"><div class="field"><label>Contact email</label><input name="contact_email" type="email" maxlength="120" value="${s.contact_email}"></div>
    <div class="field"><label>Contact phone</label><input name="contact_phone" maxlength="40" value="${s.contact_phone}"></div></div>
    <div class="form-actions"><button class="btn primary" type="submit">Save website</button></div></form></div>`);
}

function tabAccount(box) {
  put(box, html`<div class="set-grid"><div class="card"><h2>Account</h2><div class="kv"><span>Signed in as</span><strong>${P.me.email}</strong></div>
    <div class="form-actions"><button class="btn secondary" data-action="logout">${icon("logout")} Sign out</button></div></div>
    <div class="card"><h2>Change password</h2><form data-form="password"><label>Current password</label><input name="current_password" type="password" required autocomplete="current-password">
    <label>New password (10+ characters)</label><input name="new_password" type="password" minlength="10" maxlength="128" required autocomplete="new-password">
    <div class="form-actions"><button class="btn primary" type="submit">Update password</button></div></form></div></div>`);
}

// ───────────────────────────────────────────────────────────── events
const reload = async (message) => { await renderTab(); if (message) toast(message); };
const goto = (tab) => { P.tab = tab; $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab)); return renderTab(); };

const actions = {
  logout: () => P.hooks.signOut(),
  "gen-password"() { $("[name=owner_password]").value = randomPassword(); },
  async "tenant-toggle"(el) {
    const active = el.dataset.active !== "true";
    if (!active && !(await confirmDialog("Suspend this restaurant?", `${el.dataset.name} and its guests lose access immediately.`, "Suspend", true))) return;
    await api(`/api/platform/restaurants/${el.dataset.id}`, { method: "PATCH", body: { is_active: active } });
    await reload(active ? "Restaurant reactivated." : "Restaurant suspended.");
  },
  async "tenant-rename"(el) {
    const f = await modal({ title: "Rename restaurant", body: html`<label>Name</label><input name="name" required minlength="2" maxlength="120" value="${el.dataset.name}">` });
    if (!f) return;
    await api(`/api/platform/restaurants/${el.dataset.id}`, { method: "PATCH", body: { name: f.name } });
    await reload("Restaurant renamed.");
  },
  async "owner-reset"(el) {
    const f = await passwordModal("Reset owner password", `Set a new password for the owner of ${el.dataset.name}.`);
    if (!f) return;
    await api(`/api/platform/restaurants/${el.dataset.id}/owner-password`, { method: "POST", body: { password: f.password } });
    toast("Owner password reset.");
  },
  async "tenant-delete"(el) {
    const f = await modal({ title: `Delete ${el.dataset.name}?`, confirmText: "Delete forever", danger: true,
      body: html`<p class="sub">This permanently deletes the restaurant, its menu, photos, tables, requests and staff accounts. It can't be undone.</p>
        <label>Type <b class="mono">${el.dataset.slug}</b> to confirm</label><input name="confirm" required autocomplete="off">` });
    if (!f) return;
    await api(`/api/platform/restaurants/${el.dataset.id}?confirm=${encodeURIComponent(f.confirm)}`, { method: "DELETE" });
    await reload("Restaurant deleted.");
  },
  async "lead-convert"(el) {
    const l = P.leads.find((x) => x.id === Number(el.dataset.id));
    P.prefill = { name: l.business || l.name, slug: slugify(l.business || l.name), email: l.email };
    await api(`/api/platform/leads/${l.id}`, { method: "PATCH", body: { status: "contacted" } });
    await goto("restaurants");
  },
  async "lead-delete"(el) {
    if (!(await confirmDialog("Delete this request?", "It will be removed from the list.", "Delete", true))) return;
    await api(`/api/platform/leads/${el.dataset.id}`, { method: "DELETE" });
    await reload("Request deleted.");
  },
  async "plan-add"() {
    const f = await modal({ title: "New plan", confirmText: "Create plan", body: planForm(null) });
    if (!f) return;
    await api("/api/platform/plans", { method: "POST", body: planPayload(f) });
    await reload("Plan created.");
  },
  async "plan-edit"(el) {
    const p = P.plans.find((x) => x.id === Number(el.dataset.id));
    const f = await modal({ title: `Edit ${p.name}`, body: planForm(p) });
    if (!f) return;
    await api(`/api/platform/plans/${p.id}`, { method: "PATCH", body: planPayload({ ...f, key: p.key }) });
    await reload("Plan saved.");
  },
  async "plan-delete"(el) {
    if (!(await confirmDialog("Delete this plan?", `“${el.dataset.name}” will be removed. Plans still in use can't be deleted.`, "Delete plan", true))) return;
    await api(`/api/platform/plans/${el.dataset.id}`, { method: "DELETE" });
    await reload("Plan deleted.");
  },
  async "admin-toggle"(el) {
    const active = el.dataset.active === "true";
    if (!active && !(await confirmDialog("Disable this administrator?", `${el.dataset.name} will be signed out and unable to sign in.`, "Disable", true))) return;
    await api(`/api/platform/admins/${el.dataset.id}`, { method: "PATCH", body: { is_active: active } });
    await reload(active ? "Administrator enabled." : "Administrator disabled.");
  },
  async "admin-reset"(el) {
    const f = await passwordModal("Reset administrator password", `Set a new password for ${el.dataset.name}.`);
    if (!f) return;
    await api(`/api/platform/admins/${el.dataset.id}/password`, { method: "POST", body: { password: f.password } });
    toast("Password reset.");
  },
};

const forms = {
  async "create-restaurant"(f) { await api("/api/platform/restaurants", { method: "POST", body: f }); await reload(`Restaurant created. Owner password: ${f.owner_password}`); },
  async admin(f) { await api("/api/platform/admins", { method: "POST", body: f }); await reload("Administrator added. Share the password securely."); },
  async site(f) { await api("/api/platform/settings", { method: "PUT", body: f }); toast("Website saved."); },
  async password(f, form) { const r = await api("/api/auth/password", { method: "POST", body: f }); form.reset(); toast(r.message); },
};

function bind() {
  const o = { signal: P.ac.signal };
  document.addEventListener("click", async (e) => {
    const tab = e.target.closest("[data-tab]");
    if (tab) return goto(tab.dataset.tab);
    const el = e.target.closest("[data-action]");
    if (!el || el.disabled || !actions[el.dataset.action]) return;
    setBusy(el, true);
    try { await actions[el.dataset.action](el); } catch (err) { fail(err); } finally { if (el.isConnected) setBusy(el, false); }
  }, o);
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    e.preventDefault();
    const btn = $("button[type=submit]", form);
    setBusy(btn, true);
    try { await forms[form.dataset.form](Object.fromEntries(new FormData(form)), form); } catch (err) { fail(err); } finally { if (btn?.isConnected) setBusy(btn, false); }
  }, o);
  document.addEventListener("change", async (e) => {
    const plan = e.target.closest("[data-plan-for]");
    if (plan) { try { await api(`/api/platform/restaurants/${plan.dataset.planFor}`, { method: "PATCH", body: { plan: plan.value } }); toast("Plan updated."); } catch (err) { fail(err); await renderTab(); } return; }
    const st = e.target.closest("[data-lead-status]");
    if (st) { try { await api(`/api/platform/leads/${st.dataset.leadStatus}`, { method: "PATCH", body: { status: st.value } }); toast("Status updated."); refreshStats(); } catch (err) { fail(err); } }
  }, o);
  document.addEventListener("input", (e) => {
    if (e.target.id === "tenant-filter") {
      const q = e.target.value.trim().toLowerCase();
      $$("#tenants .tenant").forEach((r) => { r.hidden = Boolean(q) && !r.dataset.search.includes(q); });
    }
    if (e.target.name === "name" && e.target.closest("[data-form=create-restaurant]")) {
      const slug = $("[name=slug]", e.target.form);
      if (!slug.dataset.touched) slug.value = slugify(e.target.value);
    }
    if (e.target.name === "slug") e.target.dataset.touched = "1";
  }, o);
}
