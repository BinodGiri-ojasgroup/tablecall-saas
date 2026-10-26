# TableCall

Multi-restaurant SaaS for **QR digital menus** and **table-specific waiter calls**. Guests scan a table's QR code, enter that table's PIN, and tap *Call waiter*, *Request bill*, *Request water* or *Need assistance*. Staff see requests appear live, with an optional sound alert. Platform admins manage all restaurants from a control center.

The menu is display-only: there is no online ordering or payment.

## What's in v2

**Public landing page** at `/` (server-rendered, no build step, one small script for the demo form). Its headline, contact details and **pricing cards come from the database**, so the super admin edits them live. Visitors can send a demo request, which lands in the super admin's *Demo requests* inbox. Sign-in moved to `/app`.

**Guests** (`/t/<restaurant>/<table>`) — phone-first page with your logo and brand colour; raised, solid service buttons (Call waiter, Request bill, Request water, Need assistance) that press down when tapped; the restaurant's **Wi-Fi name and password with copy buttons** (shown only while their table is open); menu with photos, search and section chips; live table state and **live request progress** ("On the way"). The page loads as ES modules and shares no admin code.

**Restaurant dashboard** (`/app`) — requests arrive **live over Server-Sent Events** and are patched into the list in place (no polling re-render, so scroll position and open dialogs survive), with sound, vibration, optional browser pop-ups and a count in the tab title; tables (open, per-table PIN, QR, **printable QR cards**); menu with **photo upload** (resized in the browser and again on the server), categories, sold-out switch; **Settings** (owners): logo, brand colour, restaurant name, guest Wi-Fi (plus a Wi-Fi QR image), **PIN rule**, plan usage; team, activity log, account.

**PIN rule (owner setting)** — choose *Until the table closes* (not time-based) or *For a set time* with any number of minutes (1 to 10,080). A PIN is always tied to one open table session and stops when that session closes. `PIN_TTL_MINUTES` is now only the default.

**Super admin** — live-updating overview; create, rename, suspend, **delete** restaurants; **plans managed in the UI** (price, table/staff/menu-item limits that are really enforced, features shown on the website); **more platform administrators** (add, disable, reset); **demo-request inbox** with a one-click "Create restaurant"; **website text editor**; platform activity.

## Security model

- PBKDF2-SHA256 password hashes (310k rounds); signed HTTP-only `SameSite=Lax` session cookies; changing or resetting a password **invalidates all other sessions**.
- Login throttling (5 failures / 15 min per account + client); constant-time handling of unknown emails.
- PINs stored only as keyed HMAC digests and shown once. PIN guessing is limited per client+table (5 / 10 min) **and** per table across all clients (30 / 10 min). Guest requests have duplicate, per-device and per-table rate limits.
- All restaurant APIs are tenant-scoped; role checks (platform admin / owner / staff) on every route; cross-origin writes rejected; strict CSP, `X-Frame-Options: DENY`, HSTS when `COOKIE_SECURE=true`; all UI text is HTML-escaped by construction.
- Audit log of sign-ins, password changes/resets, tenant changes, staff changes, table and session actions, PIN rotation.
- A PIN plus QR proves a guest has the table's code, not physical presence; PINs can be shared by guests.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY in .env
```

Set `BOOTSTRAP_ADMIN_EMAIL` and a unique `BOOTSTRAP_ADMIN_PASSWORD` (10+ characters), set `APP_ENV=development` for local use, then:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open <http://127.0.0.1:8000> for the landing page and <http://127.0.0.1:8000/app> to sign in (API docs at `/docs`). The first superadmin is created only if that email doesn't exist; changing the bootstrap variables later does not reset an existing password.

**Test on a phone (same Wi-Fi):** set `APP_BASE_URL=http://YOUR_LAN_IP:8000`, restart, create a table → open session → generate PIN → scan its QR. Don't use `127.0.0.1` in QR codes.

**Tests:** `pip install -r requirements-dev.txt && pytest -q` (34 tests: workflow, PIN rules, uploads, Wi-Fi, plans and limits, leads, admins, tenant isolation, roles, throttling, CSRF, sessions, validation, live broker).

## Deploy with Docker Compose

```bash
cp .env.example .env     # then edit it
```

Set strong values for `SECRET_KEY`, `BOOTSTRAP_ADMIN_PASSWORD` and a URL-safe `POSTGRES_PASSWORD`; set `APP_BASE_URL` to your public `https://` address.

**With automatic HTTPS (recommended):** point your domain's DNS at the server, open ports 80/443, and in `.env` set `DOMAIN=tablecall.example.com`, `APP_BASE_URL=https://tablecall.example.com`, `COOKIE_SECURE=true`, `TRUSTED_PROXY_COUNT=1`. Then:

```bash
docker compose --profile https up --build -d
```

**Behind your own proxy / managed platform:** keep `TRUSTED_PROXY_COUNT` equal to the number of proxies in front of the app (so each guest is rate-limited individually instead of all guests sharing the proxy's address), set `COOKIE_SECURE=true`, and run `docker compose up --build -d`. The app port is bound to `127.0.0.1` by default; set `BIND_ADDRESS=0.0.0.0` only for LAN testing.

Health endpoints: `/health` (liveness), `/ready` (checks the database).

```bash
docker compose logs -f app       # logs
docker compose ps                # status
docker compose down              # stop (keeps data; never use -v unless you mean to delete the database)
```

### Backups

`scripts/backup.sh` writes a compressed `pg_dump` to `backups/` and keeps the 14 newest. Schedule it with cron, copy dumps off the server, and rehearse a restore:

```bash
gunzip -c backups/tablecall-YYYYMMDD-HHMMSS.sql.gz | docker compose exec -T db psql -U tablecall tablecall
```

## Project layout

```
app/
  main.py          app wiring, pure-ASGI security middleware, compression, page routes
  config.py        environment settings
  db.py            engine, models, default plans/settings, startup schema upgrades
  security.py      passwords, sessions, role dependencies, client identity, audit
  schemas.py       request validation
  live.py          in-process publish/subscribe + Server-Sent Events helper
  media.py         image validation and resizing (Pillow)
  site.py          plan limits and site settings helpers
  pages.py         server-rendered landing page (cached for 20 s, cleared on edits)
  routers/         auth · platform (super admin) · restaurant · public
  static/          landing.html/css/js · guest.html/css/js · app.html/css + app.js, staff.js,
                   platform.js, shell.js · base.css and common.js shared by all
tests/             pytest suite      deploy/Caddyfile   scripts/backup.sh
```

## Before taking paying customers

This is a solid, tested foundation, but these remain your responsibility:

- **Migrations:** schema upgrades currently add known missing columns at startup. Adopt Alembic before your next schema change, and take a backup first.
- **Billing:** plan limits (tables, staff, menu items) are enforced, but there is no payment collection. Add invoicing or a payment provider if you charge for tiers. The seeded plan prices are placeholders; edit them under *Plans*.
- **Email flows:** no email verification or self-service password reset (owners/admins reset passwords; share them securely).
- **Scale:** live updates use an in-process event broker, and rate-limit state lives in the database. That suits a single app process (the provided Dockerfile runs one). To run several workers or instances, swap `app/live.py` for Redis pub/sub and move rate limits to Redis. Dashboards also resync every minute as a safety net.
- **Images and Wi-Fi:** photos are stored in the database (backed up with it, but it grows). Wi-Fi passwords are stored as plain text because guests must see them; advise using a guest network.
- **Operations:** TLS, monitoring/alerting, log retention, off-site backups and restore drills, and a privacy policy covering staff accounts.
