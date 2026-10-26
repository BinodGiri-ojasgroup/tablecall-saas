"""TableCall application entrypoint: wiring, middleware, startup tasks and page routes."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, delete, select, text
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware

from . import pages
from .config import settings
from .db import LoginAttempt, Media, MenuItem, PinAttempt, Restaurant, SessionLocal, User, engine, get_db, init_db
from .live import broker
from .routers import auth, platform, public, restaurant
from .security import hash_password, window_start

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tablecall")

STATIC_DIR = Path(__file__).resolve().parent / "static"
APP_FILE = STATIC_DIR / "app.html"
GUEST_FILE = STATIC_DIR / "guest.html"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSP = ("default-src 'self'; img-src 'self' https: http: data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def bootstrap_admin() -> None:
    email, password = settings.bootstrap_email, settings.bootstrap_password
    if not email or not password:
        return
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            return
        if settings.is_production and (email == "admin@example.com" or password.startswith("change-this")):
            raise RuntimeError("Set a real BOOTSTRAP_ADMIN_EMAIL and unique BOOTSTRAP_ADMIN_PASSWORD "
                               "before production startup.")
        if len(password) < 10:
            log.warning("BOOTSTRAP_ADMIN_PASSWORD must be at least 10 characters; platform admin not created.")
            return
        db.add(User(email=email, password_hash=hash_password(password), role="platform_admin"))
        db.commit()
        log.info("Created platform administrator %s", email)


def prune_attempts() -> None:
    cutoff = window_start(settings.retention_days * 24 * 60)
    with SessionLocal() as db:
        db.execute(delete(PinAttempt).where(PinAttempt.created_at < cutoff))
        db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
        # Uploaded but never saved images (e.g. the owner closed the form) are removed after a day.
        used_items = select(MenuItem.image_id).where(MenuItem.image_id.is_not(None))
        used_logos = select(Restaurant.logo_media_id).where(Restaurant.logo_media_id.is_not(None))
        db.execute(delete(Media).where(and_(Media.created_at < window_start(24 * 60),
                                            Media.id.not_in(used_items), Media.id.not_in(used_logos))))
        db.commit()


async def _prune_forever() -> None:
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            await asyncio.to_thread(prune_attempts)
        except Exception:  # never let housekeeping kill the app
            log.exception("Pruning attempt records failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    broker.bind(asyncio.get_running_loop())
    bootstrap_admin()
    prune_attempts()
    task = asyncio.create_task(_prune_forever())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="TableCall", version="2.0.0", lifespan=lifespan,
              description="Multi-tenant QR menu and table-specific waiter calling platform")


class SecurityMiddleware:
    """Pure ASGI middleware (safe for streaming responses): CSRF origin check and security headers."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        path, method = scope["path"], scope["method"]
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        # CSRF defence in depth on top of SameSite=Lax: reject cross-origin state-changing API calls.
        if method in UNSAFE_METHODS and path.startswith("/api/"):
            origin = headers.get("origin")
            if origin and origin != "null":
                allowed = {urlsplit(settings.app_base_url).netloc, headers.get("host", "")}
                if urlsplit(origin).netloc not in allowed:
                    response = JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
                    return await response(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("X-Frame-Options", "DENY")
                h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                h.setdefault("Content-Security-Policy", CSP)
                if path.startswith("/api/"):
                    h.setdefault("Cache-Control", "no-store")
                elif path.startswith("/static/"):
                    h.setdefault("Cache-Control", "no-cache")  # revalidate with the ETag; cheap 304s
                if settings.cookie_secure:
                    h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.inner(scope, receive, send_with_headers)


class Compress:
    """gzip for pages, scripts and JSON, but never for the live event streams."""

    def __init__(self, inner):
        self.plain, self.gzip = inner, GZipMiddleware(inner, minimum_size=700)

    async def __call__(self, scope, receive, send):
        target = self.plain if scope["type"] != "http" or scope["path"].endswith("/events") else self.gzip
        await target(scope, receive, send)


app.include_router(auth.router)
app.include_router(platform.router)
app.include_router(restaurant.router)
app.include_router(public.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True, "service": "tablecall"}


@app.get("/ready", include_in_schema=False)
def ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness check failed")
        return JSONResponse({"ok": False}, status_code=503)
    return {"ok": True}


def _page(path: Path) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def landing():
    return HTMLResponse(pages.landing_html(), headers={"Cache-Control": "no-cache"})


@app.get("/app", include_in_schema=False)
@app.get("/login", include_in_schema=False)
def dashboard_page():
    return _page(APP_FILE)


@app.get("/t/{slug}/{table_code}", include_in_schema=False)
def customer_page(slug: str, table_code: str):
    # The page is generic; the API validates restaurant and table on every call.
    return _page(GUEST_FILE)


@app.get("/media/{media_id}", include_in_schema=False)
def media(media_id: int, db: Session = Depends(get_db)):
    return public.serve_media(media_id, db)


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse("User-agent: *\nAllow: /$\nDisallow: /\n")


# Outermost wrappers: compression, then security headers on every response.
app.add_middleware(SecurityMiddleware)
app.add_middleware(Compress)
