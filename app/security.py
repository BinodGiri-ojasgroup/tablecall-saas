"""Authentication, password hashing, request fingerprinting and audit helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import AuditLog, DiningTable, Restaurant, TableSession, User, get_db, utcnow

SESSION_COOKIE = "tablecall_session"
PBKDF2_ROUNDS = 310_000
MIN_PASSWORD_LENGTH = 10
serializer = URLSafeTimedSerializer(settings.secret_key, salt="tablecall-auth-v1")


# ---------------------------------------------------------------- passwords
def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 128:
        raise HTTPException(status_code=400, detail="Password must be at most 128 characters.")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt64, digest64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt64.encode())
        expected = base64.urlsafe_b64decode(digest64.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# Used to keep login timing similar whether or not the email exists.
_DUMMY_HASH = hash_password("timing-equalisation-password")


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def _keyed(value: str) -> str:
    return hmac.new(settings.secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def pin_digest(pin: str) -> str:
    return _keyed("pin:" + pin)


def _pw_version(user: User) -> str:
    """Changes whenever the password changes, which invalidates older session cookies."""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- sessions
def issue_token(user: User) -> str:
    return serializer.dumps({"uid": user.id, "pv": _pw_version(user)})


def user_from_request(request: Request, db: Session) -> User:
    """Resolve the signed-in user from the session cookie. Plain function so streaming endpoints can use a short-lived session."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        raise HTTPException(status_code=401, detail="Please sign in.")
    try:
        payload = serializer.loads(cookie, max_age=settings.session_hours * 3600)
        user_id = int(payload["uid"])
    except (BadSignature, SignatureExpired, ValueError, KeyError, TypeError):
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.") from None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Account is inactive.")
    if payload.get("pv") != _pw_version(user):
        raise HTTPException(status_code=401, detail="Your password changed. Please sign in again.")
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    return user_from_request(request, db)


def require_platform_admin(user: User = Depends(current_user)) -> User:
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="Platform administrator access required.")
    return user


def require_restaurant_user(user: User = Depends(current_user)) -> User:
    if user.role not in ("owner", "staff") or not user.restaurant_id:
        raise HTTPException(status_code=403, detail="Restaurant staff access required.")
    return user


def require_owner(user: User = Depends(current_user)) -> User:
    if user.role != "owner" or not user.restaurant_id:
        raise HTTPException(status_code=403, detail="Restaurant owner access required.")
    return user


# ---------------------------------------------------------------- tenant helpers
def restaurant_for_user(db: Session, user: User) -> Restaurant:
    restaurant = db.get(Restaurant, user.restaurant_id)
    if not restaurant or not restaurant.is_active:
        raise HTTPException(status_code=403, detail="Restaurant account is inactive.")
    return restaurant


def table_for_restaurant(db: Session, restaurant_id: int, table_id: int) -> DiningTable:
    table = db.scalar(select(DiningTable).where(
        DiningTable.id == table_id, DiningTable.restaurant_id == restaurant_id, DiningTable.is_active.is_(True)))
    if not table:
        raise HTTPException(status_code=404, detail="Table not found.")
    return table


def active_session_for(db: Session, restaurant_id: int, table_id: int) -> Optional[TableSession]:
    return db.scalar(select(TableSession).where(
        TableSession.restaurant_id == restaurant_id, TableSession.table_id == table_id,
        TableSession.ended_at.is_(None)).order_by(TableSession.started_at.desc()).limit(1))


# ---------------------------------------------------------------- client identity
def client_ip(request: Request) -> str:
    """Client IP, honouring X-Forwarded-For only for the configured number of trusted proxies."""
    direct = request.client.host if request.client else "unknown"
    hops = settings.trusted_proxy_count
    if hops > 0:
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(forwarded) >= hops:
            return forwarded[-hops]
    return direct


def client_fingerprint(request: Request, scope: str) -> str:
    """Keyed digest of client network + scope so raw IP addresses are never stored."""
    return _keyed(f"{client_ip(request)}:{scope}")


def key_for_login(request: Request, email: str) -> str:
    return _keyed(f"login:{email.lower()}:{client_ip(request)}")


# ---------------------------------------------------------------- audit
def audit(db: Session, user: Optional[User], action: str, detail: str = "", restaurant_id: Optional[int] = None) -> None:
    """Adds an audit row to the current transaction; the caller commits."""
    db.add(AuditLog(
        restaurant_id=restaurant_id if restaurant_id is not None else (user.restaurant_id if user else None),
        actor_user_id=user.id if user else None, actor_email=user.email if user else "",
        action=action, detail=detail[:500], created_at=utcnow()))


def window_start(minutes: int):
    return utcnow() - timedelta(minutes=minutes)
