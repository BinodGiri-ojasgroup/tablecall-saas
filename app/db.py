"""Database engine, ORM models and lightweight schema upgrade helpers."""
from __future__ import annotations

from datetime import datetime, UTC
from typing import Optional
from collections.abc import Iterator

from sqlalchemy import (Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint,
                        create_engine, inspect, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .config import settings

_engine_kwargs: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def dt_aware(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; every app datetime is UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def iso(value: Optional[datetime]) -> Optional[str]:
    value = dt_aware(value)
    return value.isoformat() if value else None


class Restaurant(Base):
    __tablename__ = "restaurants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(30), default="trial")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Legacy MVP columns, kept so older databases keep loading. Unused.
    pin_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pin_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Branding, guest Wi-Fi and PIN policy (all nullable so older databases upgrade in place).
    logo_media_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brand_color: Mapped[Optional[str]] = mapped_column(String(9), nullable=True)
    wifi_ssid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    wifi_password: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pin_mode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)   # "session" | "timed"
    pin_ttl_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    @property
    def effective_pin_mode(self) -> str:
        return self.pin_mode if self.pin_mode in ("session", "timed") else "timed"

    @property
    def effective_pin_minutes(self) -> int:
        return self.pin_ttl_minutes or settings.pin_ttl_minutes


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))  # platform_admin, owner, staff
    restaurant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("restaurants.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiningTable(Base):
    __tablename__ = "dining_tables"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    pin_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    pin_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pin_session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint("restaurant_id", "code", name="uq_table_restaurant_code"),)


class TableSession(Base):
    __tablename__ = "table_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"), index=True)
    session_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class MenuCategory(Base):
    __tablename__ = "menu_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class MenuItem(Base):
    __tablename__ = "menu_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    category_id: Mapped[Optional[int]] = mapped_column(ForeignKey("menu_categories.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    price_paisa: Mapped[int] = mapped_column(Integer)  # NPR 100.00 = 10000 paisa
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    image_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class WaiterRequest(Base):
    __tablename__ = "waiter_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"), index=True)
    table_session_id: Mapped[int] = mapped_column(ForeignKey("table_sessions.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    handled_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    client_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)


class PinAttempt(Base):
    __tablename__ = "pin_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("dining_tables.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class LoginAttempt(Base):
    """Failed sign-ins, keyed by a keyed digest of (email, client IP). Cleared on success."""
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[Optional[int]] = mapped_column(ForeignKey("restaurants.id"), nullable=True, index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_email: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Media(Base):
    """Uploaded logos and dish photos, resized on upload and stored in the database (one backup covers everything)."""
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # logo | item
    content_type: Mapped[str] = mapped_column(String(40), default="image/webp")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Plan(Base):
    """Subscription plans, managed by the platform administrator. A limit of 0 means unlimited."""
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(60))
    price_npr: Mapped[int] = mapped_column(Integer, default=0)  # per month
    max_tables: Mapped[int] = mapped_column(Integer, default=0)
    max_staff: Mapped[int] = mapped_column(Integer, default=0)
    max_menu_items: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[str] = mapped_column(Text, default="")  # one feature per line, shown on the landing page
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Lead(Base):
    """Demo requests submitted from the landing page."""
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    business: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(40), default="")
    message: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)  # new|contacted|won|dismissed
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Setting(Base):
    """Platform-wide key/value settings edited from the super admin (site name, tagline, contact details)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


DEFAULT_PLANS = (
    # key, name, NPR/month, tables, staff, items, features, sort
    ("trial", "Trial", 0, 5, 2, 40, "Up to 5 tables\nQR menu with photos\nLive waiter requests", 0),
    ("starter", "Starter", 999, 10, 3, 100, "Up to 10 tables\n3 staff accounts\nYour logo and colours\nGuest Wi-Fi card", 1),
    ("standard", "Standard", 1999, 30, 10, 300, "Up to 30 tables\n10 staff accounts\nPrintable QR cards\nActivity log", 2),
    ("premium", "Premium", 3999, 0, 0, 0, "Unlimited tables\nUnlimited staff\nUnlimited menu items\nPriority support", 3),
)

DEFAULT_SETTINGS = {
    "brand_name": "TableCall",
    "tagline": "Guests scan, tap, and your waiter comes.",
    "contact_email": "",
    "contact_phone": "",
}

# Columns added after the first MVP; applied to existing databases on startup.
_COMPAT_COLUMNS = {
    "restaurants": {
        "logo_media_id": "INTEGER", "brand_color": "VARCHAR(9)", "wifi_ssid": "VARCHAR(64)",
        "wifi_password": "VARCHAR(128)", "pin_mode": "VARCHAR(10)", "pin_ttl_minutes": "INTEGER",
    },
    "menu_items": {"image_id": "INTEGER"},
    "dining_tables": {
        "pin_hash": "VARCHAR(128)",
        "pin_expires_at": "TIMESTAMP WITH TIME ZONE",
        "pin_session_id": "INTEGER",
    },
    "waiter_requests": {"client_fingerprint": "VARCHAR(64)"},
}


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        for table_name, columns in _COMPAT_COLUMNS.items():
            if table_name not in tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table_name)}
            for col_name, col_type in columns.items():
                if col_name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
    with SessionLocal() as db:
        if not db.scalar(select(Plan.id).limit(1)):
            for key, name, price, tables, staff, items, features, order in DEFAULT_PLANS:
                db.add(Plan(key=key, name=name, price_npr=price, max_tables=tables, max_staff=staff,
                            max_menu_items=items, features=features, sort_order=order))
        have = set(db.scalars(select(Setting.key)).all())
        for key, value in DEFAULT_SETTINGS.items():
            if key not in have:
                db.add(Setting(key=key, value=value))
        db.commit()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
