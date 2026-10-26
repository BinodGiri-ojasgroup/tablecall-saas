"""Unauthenticated guest endpoints. Everything here is rate-limited and tenant-scoped by slug + table code."""
from __future__ import annotations

import hmac
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import (DiningTable, Lead, Media, MenuCategory, MenuItem, PinAttempt, Restaurant, SessionLocal,
                  WaiterRequest, get_db, utcnow)
from ..live import broker, sse_response, staff_channel, table_channel
from ..schemas import LeadIn, RequestIn
from ..security import active_session_for, client_fingerprint, pin_digest, window_start
from .restaurant import media_url, menu_item_dict, pin_is_valid, request_dict, table_state

router = APIRouter(prefix="/api/public", tags=["public"])
MAX_GUEST_STREAMS_PER_TABLE = 25


def _restaurant(db: Session, slug: str) -> Restaurant:
    restaurant = db.scalar(select(Restaurant).where(Restaurant.slug == slug, Restaurant.is_active.is_(True)))
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found or inactive.")
    return restaurant


def _table(db: Session, restaurant: Restaurant, code: str) -> DiningTable:
    table = db.scalar(select(DiningTable).where(
        DiningTable.restaurant_id == restaurant.id, DiningTable.code == code.strip().upper(),
        DiningTable.is_active.is_(True)))
    if not table:
        raise HTTPException(status_code=404, detail="Table not found.")
    return table


def _state(db: Session, restaurant: Restaurant, table: DiningTable) -> dict:
    return table_state(restaurant, table, active_session_for(db, restaurant.id, table.id))


@router.get("/{slug}/{table_code}/status")
def public_status(slug: str, table_code: str, db: Session = Depends(get_db)):
    """Cheap state check, used as a fallback when the live stream is unavailable."""
    restaurant = _restaurant(db, slug)
    return _state(db, restaurant, _table(db, restaurant, table_code))


@router.get("/{slug}/{table_code}/events")
async def public_events(slug: str, table_code: str):
    """Live stream for the guest page: table opened/closed, PIN issued, Wi-Fi changed, request progress."""
    def lookup() -> tuple[int, dict]:
        with SessionLocal() as db:
            restaurant = _restaurant(db, slug)
            table = _table(db, restaurant, table_code)
            return table.id, _state(db, restaurant, table)
    table_id, state = await run_in_threadpool(lookup)
    return sse_response(table_channel(table_id), hello=state, limit=MAX_GUEST_STREAMS_PER_TABLE)


@router.get("/{slug}/{table_code}/menu")
def public_menu(slug: str, table_code: str, db: Session = Depends(get_db)):
    restaurant = _restaurant(db, slug)
    table = _table(db, restaurant, table_code)
    categories = db.scalars(select(MenuCategory).where(MenuCategory.restaurant_id == restaurant.id)
                            .order_by(MenuCategory.sort_order, MenuCategory.name)).all()
    items = db.scalars(select(MenuItem).where(MenuItem.restaurant_id == restaurant.id, MenuItem.is_available.is_(True))
                       .order_by(MenuItem.sort_order, MenuItem.name)).all()
    return {"restaurant": {"name": restaurant.name, "slug": restaurant.slug,
                           "logo_url": media_url(restaurant.logo_media_id), "brand_color": restaurant.brand_color or ""},
            "table": _state(db, restaurant, table),
            "pin_mode": restaurant.effective_pin_mode, "pin_ttl_minutes": restaurant.effective_pin_minutes,
            "categories": [{"id": c.id, "name": c.name} for c in categories],
            "items": [menu_item_dict(i) for i in items]}


@router.post("/{slug}/{table_code}/requests", status_code=201)
def create_public_request(slug: str, table_code: str, data: RequestIn, request: Request,
                          db: Session = Depends(get_db)):
    restaurant = _restaurant(db, slug)
    table = _table(db, restaurant, table_code)
    now = utcnow()
    session = active_session_for(db, restaurant.id, table.id)
    if not session:
        raise HTTPException(status_code=403, detail="This table is not open yet. Please ask a member of staff.")

    fingerprint = client_fingerprint(request, f"table:{table.id}")
    since = window_start(settings.pin_failure_window_minutes)
    failed = select(func.count(PinAttempt.id)).where(
        PinAttempt.restaurant_id == restaurant.id, PinAttempt.table_id == table.id,
        PinAttempt.succeeded.is_(False), PinAttempt.created_at >= since)
    if (db.scalar(failed.where(PinAttempt.fingerprint == fingerprint)) or 0) >= settings.pin_failure_limit:
        raise HTTPException(status_code=429, detail="Too many incorrect PIN attempts from this device/network. "
                                                    "Try again in 10 minutes.")
    if (db.scalar(failed) or 0) >= settings.pin_table_failure_limit:
        raise HTTPException(status_code=429, detail="This table is temporarily locked after repeated incorrect PINs. "
                                                    "Please ask staff for help.")

    if not (pin_is_valid(table, session) and hmac.compare_digest(pin_digest(data.pin), table.pin_hash)):
        db.add(PinAttempt(restaurant_id=restaurant.id, table_id=table.id, fingerprint=fingerprint,
                          succeeded=False, created_at=now))
        db.commit()
        raise HTTPException(status_code=403, detail="That PIN isn't valid for this table right now. "
                                                    "Ask staff for the current PIN.")

    recent = select(func.count(WaiterRequest.id)).where(
        WaiterRequest.restaurant_id == restaurant.id, WaiterRequest.table_session_id == session.id,
        WaiterRequest.created_at >= now - timedelta(minutes=2))
    if (db.scalar(recent.where(WaiterRequest.client_fingerprint == fingerprint)) or 0) >= 4:
        raise HTTPException(status_code=429, detail="Too many requests from this device. Please wait a little.")
    if (db.scalar(recent) or 0) >= 12:
        raise HTTPException(status_code=429, detail="This table has sent several requests recently. "
                                                    "Please ask staff directly if urgent.")
    duplicate = db.scalar(select(WaiterRequest.id).where(
        WaiterRequest.restaurant_id == restaurant.id, WaiterRequest.table_session_id == session.id,
        WaiterRequest.request_type == data.request_type,
        WaiterRequest.status.in_(("pending", "accepted")),
        WaiterRequest.created_at >= now - timedelta(seconds=45)).limit(1))
    if duplicate:
        raise HTTPException(status_code=429, detail="A similar request was sent recently. Please wait a moment.")

    q = WaiterRequest(restaurant_id=restaurant.id, table_id=table.id, table_session_id=session.id,
                      request_type=data.request_type, status="pending", created_at=now, updated_at=now,
                      client_fingerprint=fingerprint)
    db.add(q)
    db.commit()
    broker.publish(staff_channel(restaurant.id), "request", request_dict(q, table.code))
    return {"id": q.id, "status": q.status, "message": "Request sent! Your waiter has been notified."}


@router.post("/leads", status_code=201)
def create_lead(data: LeadIn, request: Request, db: Session = Depends(get_db)):
    """Demo request from the landing page. Bots that fill the hidden field get a silent success."""
    if data.website:
        return {"ok": True}
    fingerprint = client_fingerprint(request, "lead")
    recent = db.scalar(select(func.count(Lead.id)).where(
        Lead.fingerprint == fingerprint, Lead.created_at >= window_start(60))) or 0
    if recent >= 5:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
    db.add(Lead(name=data.name.strip(), business=data.business.strip(), email=data.email.lower(),
                phone=data.phone.strip(), message=data.message.strip(), fingerprint=fingerprint, created_at=utcnow()))
    db.commit()
    return {"ok": True}


def serve_media(media_id: int, db: Session) -> Response:
    row = db.get(Media, media_id)
    if not row:
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(row.data, media_type=row.content_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})
