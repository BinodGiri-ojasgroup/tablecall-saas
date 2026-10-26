import { html, icon, initial, put, raw, $ } from "/static/common.js";

/** Page chrome shared by the restaurant dashboard and the super admin. */
export function renderShell(root, { name, logoUrl, email, roleLabel, tabs, active, showLive }) {
  put(root, html`<header class="top"><div class="top-in">
      <a class="brand" href="/app">${logoUrl ? html`<img class="brand-mark" src="${logoUrl}" alt="">` : html`<span class="brand-mark">${initial(name)}</span>`}<span>${name}</span></a>
      <div class="top-right">${showLive ? html`<span class="live" id="live" role="status"><i></i><span>Connecting…</span></span>` : ""}
        <span class="who">${email} · ${roleLabel}</span>
        <button class="btn ghost sm" data-action="logout" aria-label="Sign out">${icon("logout")}<span>Sign out</span></button></div></div></header>
    <nav class="tabs" aria-label="Sections"><div class="tabs-in">${tabs.map(([id, label, badge]) => html`<button class="tab ${active === id ? "active" : ""}" data-tab="${id}" ${active === id ? raw('aria-current="page"') : ""}>${label}${badge !== undefined ? html`<span class="badge" data-badge="${id}" ${badge ? "" : raw("hidden")}>${badge}</span>` : ""}</button>`)}</div></nav>
    <main class="page" id="tab-content"><div class="loading">Loading…</div></main>`);
  return $("#tab-content");
}

export function setLiveState(ok) {
  const el = $("#live");
  if (!el) return;
  el.classList.toggle("on", ok);
  $("span", el).textContent = ok ? "Live" : "Reconnecting…";
}
