/* Entry point for /app: sign-in, then the restaurant dashboard or the super admin depending on the account. */
import { $, api, fail, html, put, setBusy, setUnauthorizedHandler } from "/static/common.js";

let current = null; // the module currently drawn: { stop() }

async function boot() {
  try {
    const me = await api("/api/auth/me");
    await enter(me);
  } catch { renderLogin(); }
}

async function enter(me) {
  if (current) current.stop();
  const root = $("#app");
  if (me.role === "platform_admin") {
    const mod = await import("/static/platform.js");
    current = await mod.start(root, me, { signOut });
  } else if (me.role === "owner" || me.role === "staff") {
    const mod = await import("/static/staff.js");
    current = await mod.start(root, me, { signOut });
  } else renderLogin();
}

async function signOut() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* already signed out */ }
  if (current) current.stop();
  current = null;
  renderLogin();
}

function renderLogin(message) {
  document.title = "Sign in";
  put($("#app"), html`<div class="login"><div class="login-box">
    <div class="login-brand"><img src="/static/favicon.svg" width="40" height="40" alt=""><span>TableCall</span></div>
    <div class="card"><h1>Sign in</h1><p class="sub" style="margin-top:4px">For restaurant staff and platform administrators.</p>
      ${message ? html`<div class="notice warn" style="margin-top:14px" role="alert">${message}</div>` : ""}
      <form id="login-form"><label for="email">Email</label>
        <input id="email" name="email" type="email" autocomplete="username" required autofocus>
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <div class="form-actions"><button class="btn primary block" type="submit">Sign in</button></div></form></div>
    <p class="login-foot small"><a href="/">← Back to the website</a></p></div></div>`);
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", e.target);
    setBusy(btn, true);
    try { await enter(await api("/api/auth/login", { method: "POST", body: Object.fromEntries(new FormData(e.target)) })); }
    catch (err) { fail(err); setBusy(btn, false); }
  });
}

setUnauthorizedHandler(() => {
  if (!current) return;
  current.stop(); current = null;
  renderLogin("Your session ended. Please sign in again.");
});

boot();
