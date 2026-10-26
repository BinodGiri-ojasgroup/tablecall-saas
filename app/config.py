"""Environment-driven configuration. Imported once at startup."""
from __future__ import annotations

import logging
import os
import secrets

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("tablecall")

PLACEHOLDER_SECRET = "replace-with-a-long-random-secret"


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./tablecall.db")
        self.app_env = os.getenv("APP_ENV", "development").strip().lower()
        self.is_production = self.app_env == "production"
        self.cookie_secure = _bool("COOKIE_SECURE")
        self.app_base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.bootstrap_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        self.bootstrap_password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")

        # Number of trusted reverse proxies in front of the app (0 = connect directly).
        # With 1, the client IP is read from the right-most X-Forwarded-For entry the proxy appended.
        self.trusted_proxy_count = _int("TRUSTED_PROXY_COUNT", 0)

        self.pin_ttl_minutes = _int("PIN_TTL_MINUTES", 15)
        self.pin_failure_limit = 5            # per client+table
        self.pin_table_failure_limit = 30     # per table, across all clients
        self.pin_failure_window_minutes = 10
        self.login_failure_limit = 5
        self.login_failure_window_minutes = 15
        self.session_hours = 12
        self.retention_days = _int("RETENTION_DAYS", 30)  # pruning of attempt records

        self.secret_key = self._resolve_secret()

    def _resolve_secret(self) -> str:
        secret = os.getenv("SECRET_KEY", "").strip()
        if len(secret) >= 32 and secret != PLACEHOLDER_SECRET:
            return secret
        if self.is_production or not self.database_url.startswith("sqlite"):
            raise RuntimeError("Set a stable SECRET_KEY of at least 32 characters before starting in production.")
        log.warning("Using an ephemeral development SECRET_KEY; set SECRET_KEY in .env for stable sessions.")
        return secrets.token_urlsafe(48)


settings = Settings()
