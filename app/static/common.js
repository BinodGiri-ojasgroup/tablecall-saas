/* Shared helpers for every TableCall page. Vanilla ES modules, no build step.
 * All dynamic text goes through the `html` tag, which escapes every interpolation unless it is
 * explicitly wrapped with raw(). That keeps names, emails, Wi-Fi passwords etc. from ever becoming markup. */

// ───────────────────────────────────────────────────────────── templating
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
class Safe { constructor(s) { this.s = s; } }
export const raw = (s) => new Safe(s);
export const toStr = (v) => (v instanceof Safe ? v.s : Array.isArray(v) ? v.map(toStr).join("") : v === false || v == null ? "" : esc(v));
export const html = (strings, ...vals) => new Safe(strings.reduce((out, s, i) => out + s + (i < vals.length ? toStr(vals[i]) : ""), ""));
export const $ = (sel, scope = document) => scope.querySelector(sel);
export const $$ = (sel, scope = document) => [...scope.querySelectorAll(sel)];
export const put = (el, tpl) => { if (el) el.innerHTML = toStr(tpl); };
export function toEl(tpl) {
  const t = document.createElement("template");
  t.innerHTML = toStr(tpl).trim();
  return t.content.firstElementChild;
}

// ───────────────────────────────────────────────────────────── icons (inline SVG, so they look the same on every phone)
const PATHS = {
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
  bill: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  water: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
  hand: '<path d="M18 11V6a2 2 0 0 0-4 0v1M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.9-5.9-2.4L3.4 16a2 2 0 0 1 3.2-2.4L8 15"/>',
  wifi: '<path d="M5 12.55a11 11 0 0 1 14.08 0M1.42 9a16 16 0 0 1 21.16 0M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M21 14v.01M14 21h3M21 17v4"/>',
  printer: '<path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
  volume: '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
  mute: '<path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="m22 9-6 6M16 9l6 6"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
};
export const icon = (name, cls = "icon") => raw(`<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || ""}</svg>`);

export const LABELS = { call_waiter: "Call waiter", request_bill: "Request bill", need_water: "Request water", assistance: "Need assistance" };
export const REQUEST_ICONS = { call_waiter: "bell", request_bill: "bill", need_water: "water", assistance: "hand" };

// ───────────────────────────────────────────────────────────── formatting
export const money = (n) => "NPR " + Number(n).toLocaleString("en-NP", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
export const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
export const dateTime = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
export function ago(iso) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h} h ${m % 60} min ago` : dateTime(iso);
}

// ───────────────────────────────────────────────────────────── network
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

export async function api(url, { method = "GET", body, raw: rawBody, headers = {} } = {}) {
  const init = { method, credentials: "same-origin", headers: { ...headers } };
  if (rawBody !== undefined) init.body = rawBody;
  else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, init); }
  catch { throw Object.assign(new Error("You appear to be offline. Check your connection and try again."), { status: 0 }); }
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => (d.msg || "").replace(/^Value error, /, "")).join(" ");
    const err = new Error(detail || `Something went wrong (${res.status}).`);
    err.status = res.status;
    if (res.status === 401 && !url.startsWith("/api/auth/")) onUnauthorized();
    throw err;
  }
  return data;
}

/** Subscribe to a Server-Sent Events stream. Reconnects on its own; `onState(true|false)` reports connectivity. */
export function live(url, handlers, onState) {
  let es, closed = false, attempt = 0, timer;
  const parse = (e) => { try { return JSON.parse(e.data); } catch { return null; } };
  const open = () => {
    es = new EventSource(url);
    for (const [name, fn] of Object.entries(handlers)) es.addEventListener(name, (e) => fn(parse(e)));
    es.addEventListener("hello", () => { attempt = 0; if (onState) onState(true); });
    es.onerror = () => {
      if (onState) onState(false);
      if (es.readyState === EventSource.CLOSED && !closed) { es.close(); timer = setTimeout(open, Math.min(30000, 2000 * 2 ** attempt++)); }
    };
  };
  open();
  return { close() { closed = true; clearTimeout(timer); if (es) es.close(); } };
}

// ───────────────────────────────────────────────────────────── UI primitives
let toastTimer;
export function toast(text, type = "success") {
  let el = $("#toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.setAttribute("role", "status"); el.setAttribute("aria-live", "polite"); document.body.appendChild(el); }
  el.className = `${type} show`;
  el.textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), type === "error" ? 6000 : 3200);
}
export const fail = (e) => toast(e.message || String(e), "error");

export function setBusy(btn, busy) {
  if (!btn) return;
  btn.disabled = busy;
  btn.classList.toggle("busy", busy);
}

/** Modal form. Resolves with the form values on confirm, or null on cancel. `onOpen(form)` can wire extra behaviour. */
export function modal({ title, body, confirmText = "Save", danger = false, onOpen }) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = toStr(html`<form class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <h2 id="modal-title">${title}</h2><div class="modal-body">${body}</div>
      <div class="modal-actions"><button type="button" class="btn secondary" data-cancel>Cancel</button>
        <button type="submit" class="btn ${danger ? "danger-solid" : "primary"}">${confirmText}</button></div></form>`);
    const form = $("form", overlay);
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    const close = (value) => { document.removeEventListener("keydown", onKey); overlay.remove(); if (previous && previous.focus) previous.focus(); resolve(value); };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(null); });
    form.addEventListener("click", (e) => { if (e.target.closest("[data-cancel]")) close(null); });
    form.addEventListener("submit", (e) => { e.preventDefault(); if (form.dataset.locked) return; close(Object.fromEntries(new FormData(form))); });
    document.body.appendChild(overlay);
    if (onOpen) onOpen(form);
    (($("input:not([type=hidden]):not([type=file]), select, textarea", form)) || $("button[type=submit]", form)).focus();
  });
}
export const confirmDialog = (title, message, confirmText = "Confirm", danger = false) =>
  modal({ title, body: html`<p class="sub">${message}</p>`, confirmText, danger });

export const passwordModal = (title, who) => modal({
  title, confirmText: "Set password",
  body: html`<p class="sub">${who} Share the new password through a secure channel.</p>
    <label for="np">New password (10+ characters)</label>
    <input id="np" name="password" type="password" minlength="10" maxlength="128" required autocomplete="new-password">`,
});

export function randomPassword(len = 14) {
  const chars = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(len));
  return [...bytes].map((b) => chars[b % chars.length]).join("");
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch {
    // Plain-http pages (LAN testing) have no clipboard API; fall back to a temporary textarea.
    const ta = Object.assign(document.createElement("textarea"), { value: text });
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand && document.execCommand("copy");
    ta.remove();
    return Boolean(ok);
  }
}

// ───────────────────────────────────────────────────────────── keyed list updates (no full re-render)
/** Make `container`'s children match `items`: only new or changed rows are touched, scroll and focus stay put. */
export function reconcile(container, items, { key, sig, render }) {
  const existing = new Map();
  for (const child of [...container.children]) existing.set(child.dataset.key, child);
  let cursor = container.firstElementChild;
  for (const item of items) {
    const k = String(key(item)), s = sig(item);
    let node = existing.get(k);
    existing.delete(k);
    if (!node || node.dataset.sig !== s) {
      const fresh = toEl(render(item));
      fresh.dataset.key = k; fresh.dataset.sig = s;
      if (!node) fresh.classList.add("enter");
      if (node) { node.replaceWith(fresh); if (cursor === node) cursor = fresh; }
      node = fresh;
    }
    if (node === cursor) cursor = cursor.nextElementSibling;
    else container.insertBefore(node, cursor);
  }
  for (const stale of existing.values()) stale.remove();
}

// ───────────────────────────────────────────────────────────── branding + images
export function readableOn(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  const L = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return 1.05 / (L + 0.05) >= (L + 0.05) / 0.05 ? "#ffffff" : "#141a33";
}
export function applyBrand(hex) {
  const root = document.documentElement.style;
  if (/^#[0-9a-f]{6}$/i.test(hex || "")) { root.setProperty("--brand", hex); root.setProperty("--brand-ink", readableOn(hex)); }
  else { root.removeProperty("--brand"); root.removeProperty("--brand-ink"); }
}
export const initial = (name) => (String(name || "?").trim()[0] || "?").toUpperCase();

/** Shrink a photo in the browser before upload so phone pictures don't burn mobile data. */
async function shrink(file, max, type) {
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale); canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((res) => canvas.toBlob(res, type, 0.86));
    return blob || file;
  } catch { return file; }
}
export async function uploadImage(file, kind) {
  if (!file) throw new Error("Choose an image first.");
  const blob = await shrink(file, kind === "logo" ? 600 : 1200, kind === "logo" ? "image/png" : "image/jpeg");
  return api(`/api/restaurant/media?kind=${kind}`, { method: "POST", raw: blob, headers: { "Content-Type": blob.type || "application/octet-stream" } });
}
