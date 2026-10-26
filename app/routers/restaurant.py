from __future__ import annotations

import re
import secrets
from datetime import timedelta
from io import BytesIO
from typing import Optional
from urllib.parse import quote

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from qrcode.constants import ERROR_CORRECT_M
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import settings
from ..db import (AuditLog, DiningTable, MenuCategory, MenuItem, Media, Restaurant, SessionLocal, TableSession, User,
                  WaiterRequest, dt_aware, get_db, iso, utcnow)
from ..live import broker, sse_response, staff_channel, table_channel
from ..media import MAX_UPLOAD_BYTES, process_image
from ..schemas import (AvailabilityIn, CategoryIn, MenuItemIn, PasswordIn, SettingsIn, StaffCreate, StaffUpdate,
                       StatusIn, TableCreate)
from ..security import (active_session_for, audit, hash_password, pin_digest, require_owner,
                        require_restaurant_user, restaurant_for_user, table_for_restaurant, user_from_request)
from ..site import enforce_limit, limits, plan_by_key, usage

router = APIRouter(prefix="/api/restaurant", tags=["restaurant"])
MAX_MEDIA_PER_RESTAURANT = 400


def table_url(slug: str, code: str) -> str:
    return f"{settings.app_base_url}/t/{slug}/{quote(code)}"


def media_url(media_id: Optional[int]) -> Optional[str]:
    return f"/media/{media_id}" if media_id else None


def pin_is_valid(table: DiningTable, session: Optional[TableSession]) -> bool:
    """A PIN is valid for exactly one table session. It may also carry an expiry (timed mode)."""
    if not (table.pin_hash and session and table.pin_session_id == session.id):
        return False
    expiry = dt_aware(table.pin_expires_at)
    return expiry is None or expiry > utcnow()


def table_state(restaurant: Restaurant, table: DiningTable, session: Optional[TableSession]) -> dict:
    """What a guest at this table is allowed to know. Wi-Fi details are only revealed while the table is open."""
    open_ = session is not None
    wifi = None
    if open_ and restaurant.wifi_ssid:
        wifi = {"ssid": restaurant.wifi_ssid, "password": restaurant.wifi_password or ""}
    return {"code": table.code, "session_active": open_, "pin_active": pin_is_valid(table, session),
            "pin_expires_at": iso(table.pin_expires_at), "wifi": wifi}


def notify_table(db: Session, restaurant: Restaurant, table: DiningTable) -> None:
    """Tell staff dashboards and the guests at this table that something about the table changed."""
    session = active_session_for(db, restaurant.id, table.id)
    broker.publish(staff_channel(restaurant.id), "tables", None)
    broker.publish(table_channel(table.id), "state", table_state(restaurant, table, session))


def request_dict(q: WaiterRequest, code: str, handled_by: Optional[str] = None) -> dict:
    return {"id": q.id, "table_code": code.split("~")[0], "request_type": q.request_type, "status": q.status,
            "created_at": iso(q.created_at), "updated_at": iso(q.updated_at), "handled_by": handled_by}


def restaurant_payload(db: Session, r: Restaurant, user: User) -> dict:
    plan = plan_by_key(db, r.plan)
    return {"id": r.id, "name": r.name, "slug": r.slug, "plan": r.plan, "plan_name": plan.name if plan else r.plan,
            "role": user.role, "email": user.email, "logo_url": media_url(r.logo_media_id),
            "brand_color": r.brand_color or "", "wifi_ssid": r.wifi_ssid or "", "wifi_password": r.wifi_password or "",
            "pin_mode": r.effective_pin_mode, "pin_ttl_minutes": r.effective_pin_minutes,
            "limits": limits(plan), "usage": usage(db, r)}


def _drop_media(db: Session, restaurant_id: int, media_id: Optional[int]) -> None:
    if media_id:
        row = db.get(Media, media_id)
        if row and row.restaurant_id == restaurant_id:
            db.delete(row)


def _media_or_400(db: Session, restaurant_id: int, media_id: Optional[int], kind: str) -> None:
    if media_id is not None and not db.scalar(select(Media.id).where(
            Media.id == media_id, Media.restaurant_id == restaurant_id, Media.kind == kind)):
        raise HTTPException(status_code=400, detail="That image was not found. Upload it again.")


def _qr_png(data: str, filename: str) -> StreamingResponse:
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=14, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    buf = BytesIO()
    qr.make_image(fill_color="#14213d", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png",
                             headers={"Content-Disposition": f'inline; filename="{filename}"',
                                      "Cache-Control": "private, no-cache"})


# ------------------------------------------------------------------ restaurant
@router.get("")
def get_restaurant(db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    return restaurant_payload(db, restaurant_for_user(db, user), user)


@router.patch("/settings")
def update_settings(data: SettingsIn, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    changes = data.model_dump(exclude_unset=True)
    changed: list[str] = []
    if "name" in changes and changes["name"]:
        r.name = changes["name"].strip()
        changed.append("name")
    if "brand_color" in changes:
        r.brand_color = (changes["brand_color"] or "").lower() or None
        changed.append("brand colour")
    if "wifi_ssid" in changes:
        r.wifi_ssid = (changes["wifi_ssid"] or "").strip() or None
        changed.append("Wi-Fi name")
    if "wifi_password" in changes:
        r.wifi_password = changes["wifi_password"] or None
        changed.append("Wi-Fi password")
    if "pin_mode" in changes and changes["pin_mode"]:
        r.pin_mode = changes["pin_mode"]
        changed.append(f"PIN rule={r.pin_mode}")
    if "pin_ttl_minutes" in changes and changes["pin_ttl_minutes"]:
        r.pin_ttl_minutes = changes["pin_ttl_minutes"]
        changed.append(f"PIN minutes={r.pin_ttl_minutes}")
    if changes.get("remove_logo"):
        _drop_media(db, r.id, r.logo_media_id)
        r.logo_media_id = None
        changed.append("logo removed")
    elif changes.get("logo_media_id"):
        _media_or_400(db, r.id, changes["logo_media_id"], "logo")
        if r.logo_media_id != changes["logo_media_id"]:
            _drop_media(db, r.id, r.logo_media_id)
        r.logo_media_id = changes["logo_media_id"]
        changed.append("logo")
    if changed:
        audit(db, user, "restaurant.settings_updated", ", ".join(changed))
    db.commit()
    # Open guest pages pick up new Wi-Fi details immediately.
    for t in db.scalars(select(DiningTable).where(DiningTable.restaurant_id == r.id, DiningTable.is_active.is_(True))):
        broker.publish(table_channel(t.id), "state", table_state(r, t, active_session_for(db, r.id, t.id)))
    return restaurant_payload(db, r, user)


@router.post("/media", status_code=201)
async def upload_media(request: Request, kind: str = Query(pattern="^(item|logo)$"), db: Session = Depends(get_db),
                       user: User = Depends(require_owner)):
    """Raw image bytes in the request body. Returns the stored media id and URL."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That image is too large. Use a file under 8 MB.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="That image is too large. Use a file under 8 MB.")
        chunks.append(chunk)
    if not total:
        raise HTTPException(status_code=400, detail="No image received.")

    def store() -> dict:
        r = restaurant_for_user(db, user)
        if (db.scalar(select(func.count(Media.id)).where(Media.restaurant_id == r.id)) or 0) >= MAX_MEDIA_PER_RESTAURANT:
            raise HTTPException(status_code=400, detail="Image limit reached. Delete unused menu items first.")
        row = Media(restaurant_id=r.id, kind=kind, content_type="image/webp", data=process_image(b"".join(chunks), kind))
        db.add(row)
        db.commit()
        return {"id": row.id, "url": media_url(row.id)}
    return await run_in_threadpool(store)


@router.get("/wifi-qr.png")
def wifi_qr(db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    if not r.wifi_ssid:
        raise HTTPException(status_code=404, detail="Add a Wi-Fi name in Settings first.")

    def esc(v: str) -> str:
        return re.sub(r'([\\;,:"])', r"\\\1", v)
    payload = (f"WIFI:T:WPA;S:{esc(r.wifi_ssid)};P:{esc(r.wifi_password)};;" if r.wifi_password
               else f"WIFI:T:nopass;S:{esc(r.wifi_ssid)};;")
    return _qr_png(payload, f"{r.slug}-wifi-qr.png")


@router.get("/events")
async def staff_events(request: Request):
    """Server-Sent Events: new and changed requests, table changes. The browser reconnects automatically."""
    def authenticate() -> int:
        with SessionLocal() as db:
            user = user_from_request(request, db)
            if user.role not in ("owner", "staff") or not user.restaurant_id:
                raise HTTPException(status_code=403, detail="Restaurant staff access required.")
            return restaurant_for_user(db, user).id
    restaurant_id = await run_in_threadpool(authenticate)
    return sse_response(staff_channel(restaurant_id), hello={"ok": True})


# ------------------------------------------------------------------ tables
@router.get("/tables")
def list_tables(db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    tables = db.scalars(select(DiningTable).where(DiningTable.restaurant_id == r.id, DiningTable.is_active.is_(True))
                        .order_by(DiningTable.code)).all()
    open_sessions = {s.table_id: s for s in db.scalars(select(TableSession).where(
        TableSession.restaurant_id == r.id, TableSession.ended_at.is_(None)).order_by(TableSession.started_at)).all()}
    pending = dict(db.execute(select(WaiterRequest.table_id, func.count(WaiterRequest.id)).where(
        WaiterRequest.restaurant_id == r.id, WaiterRequest.status == "pending").group_by(WaiterRequest.table_id)).all())
    return [{
        "id": t.id, "code": t.code, "session_active": t.id in open_sessions,
        "pin_active": pin_is_valid(t, open_sessions.get(t.id)), "pin_expires_at": iso(t.pin_expires_at),
        "pending_requests": pending.get(t.id, 0), "qr_url": table_url(r.slug, t.code),
    } for t in tables]


@router.post("/tables", status_code=201)
def create_table(data: TableCreate, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    code = data.code.strip().upper()
    if db.scalar(select(DiningTable.id).where(DiningTable.restaurant_id == r.id, DiningTable.code == code)):
        raise HTTPException(status_code=409, detail="A table with that code already exists.")
    enforce_limit(db, r, "tables")
    table = DiningTable(restaurant_id=r.id, code=code)
    db.add(table)
    audit(db, user, "table.created", code)
    db.commit()
    broker.publish(staff_channel(r.id), "tables", None)
    return {"id": table.id, "code": table.code, "qr_url": table_url(r.slug, table.code)}


@router.delete("/tables/{table_id}")
def delete_table(table_id: int, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    table = table_for_restaurant(db, r.id, table_id)
    session = active_session_for(db, r.id, table.id)
    if session:
        session.ended_at = utcnow()
    db.execute(update(WaiterRequest).where(WaiterRequest.table_id == table.id,
                                           WaiterRequest.status.in_(("pending", "accepted")))
               .values(status="cancelled", updated_at=utcnow()))
    audit(db, user, "table.deleted", table.code)
    # Soft delete keeps request history intact; the renamed code frees the original for reuse.
    table.is_active = False
    table.code = f"{table.code[:18]}~{table.id}"
    table.pin_hash = table.pin_expires_at = table.pin_session_id = None
    db.commit()
    broker.publish(staff_channel(r.id), "tables", None)
    broker.publish(staff_channel(r.id), "requests", None)
    broker.publish(table_channel(table.id), "state", table_state(r, table, None))
    return {"ok": True}


@router.post("/tables/{table_id}/session/open")
def open_table_session(table_id: int, db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    table = table_for_restaurant(db, r.id, table_id)
    if active_session_for(db, r.id, table.id):
        raise HTTPException(status_code=409, detail="This table already has an active session.")
    db.add(TableSession(restaurant_id=r.id, table_id=table.id, session_token=secrets.token_urlsafe(24),
                        started_by_user_id=user.id))
    audit(db, user, "table.session_opened", table.code)
    db.commit()
    notify_table(db, r, table)
    return {"ok": True, "table_id": table.id, "session_active": True}


@router.post("/tables/{table_id}/session/close")
def close_table_session(table_id: int, db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    table = table_for_restaurant(db, r.id, table_id)
    active = active_session_for(db, r.id, table.id)
    if not active:
        raise HTTPException(status_code=409, detail="This table has no active session.")
    active.ended_at = utcnow()
    table.pin_hash = table.pin_expires_at = table.pin_session_id = None
    # Nothing is left open for a table nobody is sitting at.
    db.execute(update(WaiterRequest).where(WaiterRequest.table_session_id == active.id,
                                           WaiterRequest.status.in_(("pending", "accepted")))
               .values(status="cancelled", updated_at=utcnow()))
    audit(db, user, "table.session_closed", table.code)
    db.commit()
    broker.publish(staff_channel(r.id), "requests", None)
    notify_table(db, r, table)
    return {"ok": True, "session_active": False}


@router.post("/tables/{table_id}/pin/rotate")
def rotate_table_pin(table_id: int, db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    table = table_for_restaurant(db, r.id, table_id)
    active = active_session_for(db, r.id, table.id)
    if not active:
        raise HTTPException(status_code=409, detail="Open this table's session before generating its PIN.")
    pin = f"{secrets.randbelow(1_000_000):06d}"
    table.pin_hash = pin_digest(pin)
    timed = r.effective_pin_mode == "timed"
    table.pin_expires_at = utcnow() + timedelta(minutes=r.effective_pin_minutes) if timed else None
    table.pin_session_id = active.id
    audit(db, user, "table.pin_rotated", table.code)
    db.commit()
    notify_table(db, r, table)
    return {"pin": pin, "table_code": table.code, "expires_at": iso(table.pin_expires_at), "mode": r.effective_pin_mode,
            "valid_minutes": r.effective_pin_minutes if timed else None,
            "message": "PIN created for this table only."}


@router.get("/tables/{table_id}/qr.png")
def table_qr(table_id: int, db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    table = table_for_restaurant(db, r.id, table_id)
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{r.slug}-{table.code}-qr.png")
    return _qr_png(table_url(r.slug, table.code), filename)


# ------------------------------------------------------------------ menu
def menu_item_dict(i: MenuItem) -> dict:
    return {"id": i.id, "category_id": i.category_id, "name": i.name, "description": i.description,
            "price_npr": i.price_paisa / 100, "image_url": i.image_url, "image_id": i.image_id,
            "image": media_url(i.image_id) or i.image_url, "is_available": i.is_available,
            "sort_order": i.sort_order}


def _check_category(db: Session, restaurant_id: int, category_id: Optional[int]) -> None:
    if category_id is not None and not db.scalar(select(MenuCategory.id).where(
            MenuCategory.id == category_id, MenuCategory.restaurant_id == restaurant_id)):
        raise HTTPException(status_code=400, detail="Category does not belong to this restaurant.")


def _item_or_404(db: Session, restaurant_id: int, item_id: int) -> MenuItem:
    item = db.scalar(select(MenuItem).where(MenuItem.id == item_id, MenuItem.restaurant_id == restaurant_id))
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found.")
    return item


@router.get("/menu")
def list_menu(db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    categories = db.scalars(select(MenuCategory).where(MenuCategory.restaurant_id == r.id)
                            .order_by(MenuCategory.sort_order, MenuCategory.name)).all()
    items = db.scalars(select(MenuItem).where(MenuItem.restaurant_id == r.id)
                       .order_by(MenuItem.sort_order, MenuItem.name)).all()
    return {"categories": [{"id": c.id, "name": c.name, "sort_order": c.sort_order} for c in categories],
            "items": [menu_item_dict(i) for i in items]}


@router.post("/menu/categories", status_code=201)
def create_category(data: CategoryIn, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    category = MenuCategory(restaurant_id=r.id, name=data.name.strip(), sort_order=data.sort_order)
    db.add(category)
    db.commit()
    return {"id": category.id, "name": category.name, "sort_order": category.sort_order}


@router.patch("/menu/categories/{category_id}")
def update_category(category_id: int, data: CategoryIn, db: Session = Depends(get_db),
                    user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    category = db.scalar(select(MenuCategory).where(MenuCategory.id == category_id, MenuCategory.restaurant_id == r.id))
    if not category:
        raise HTTPException(status_code=404, detail="Category not found.")
    category.name, category.sort_order = data.name.strip(), data.sort_order
    db.commit()
    return {"id": category.id, "name": category.name, "sort_order": category.sort_order}


@router.delete("/menu/categories/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    category = db.scalar(select(MenuCategory).where(MenuCategory.id == category_id, MenuCategory.restaurant_id == r.id))
    if not category:
        raise HTTPException(status_code=404, detail="Category not found.")
    db.execute(update(MenuItem).where(MenuItem.category_id == category.id).values(category_id=None))
    db.delete(category)
    db.commit()
    return {"ok": True}


@router.post("/menu/items", status_code=201)
def create_menu_item(data: MenuItemIn, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    _check_category(db, r.id, data.category_id)
    _media_or_400(db, r.id, data.image_id, "item")
    enforce_limit(db, r, "items")
    item = MenuItem(restaurant_id=r.id, category_id=data.category_id, name=data.name.strip(),
                    description=data.description.strip(), price_paisa=round(data.price_npr * 100),
                    image_url=data.image_url, image_id=data.image_id, is_available=data.is_available,
                    sort_order=data.sort_order)
    db.add(item)
    db.commit()
    return menu_item_dict(item)


@router.patch("/menu/items/{item_id}")
def update_menu_item(item_id: int, data: MenuItemIn, db: Session = Depends(get_db),
                     user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    item = _item_or_404(db, r.id, item_id)
    _check_category(db, r.id, data.category_id)
    _media_or_400(db, r.id, data.image_id, "item")
    item.name, item.description = data.name.strip(), data.description.strip()
    item.price_paisa = round(data.price_npr * 100)
    item.category_id, item.image_url = data.category_id, data.image_url
    if item.image_id != data.image_id:
        _drop_media(db, r.id, item.image_id)
        item.image_id = data.image_id
    item.is_available, item.sort_order = data.is_available, data.sort_order
    db.commit()
    return menu_item_dict(item)


@router.patch("/menu/items/{item_id}/availability")
def set_item_availability(item_id: int, data: AvailabilityIn, db: Session = Depends(get_db),
                          user: User = Depends(require_restaurant_user)):
    """Staff (not only owners) can mark an item sold out during service."""
    r = restaurant_for_user(db, user)
    item = _item_or_404(db, r.id, item_id)
    item.is_available = data.is_available
    db.commit()
    return menu_item_dict(item)


@router.delete("/menu/items/{item_id}")
def delete_menu_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    item = _item_or_404(db, r.id, item_id)
    _drop_media(db, r.id, item.image_id)
    db.delete(item)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ requests
_OPEN = ("pending", "accepted")
_TRANSITIONS = {"accepted": ("pending",), "completed": _OPEN, "cancelled": _OPEN}


@router.get("/requests")
def list_requests(status: str = Query("all", pattern="^(all|open|pending|accepted|completed|cancelled)$"),
                  limit: int = Query(100, ge=1, le=200),
                  db: Session = Depends(get_db), user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    query = select(WaiterRequest).where(WaiterRequest.restaurant_id == r.id)
    if status == "open":
        query = query.where(WaiterRequest.status.in_(_OPEN))
    elif status != "all":
        query = query.where(WaiterRequest.status == status)
    rows = db.scalars(query.order_by(WaiterRequest.created_at.desc()).limit(limit)).all()
    codes = {t.id: t.code for t in db.scalars(select(DiningTable).where(DiningTable.restaurant_id == r.id)).all()}
    handlers = {u.id: u.email for u in db.scalars(select(User).where(User.restaurant_id == r.id)).all()}
    return [request_dict(q, codes.get(q.table_id, "?"), handlers.get(q.handled_by_user_id)) for q in rows]


@router.patch("/requests/{request_id}")
def update_request(request_id: int, data: StatusIn, db: Session = Depends(get_db),
                   user: User = Depends(require_restaurant_user)):
    r = restaurant_for_user(db, user)
    q = db.scalar(select(WaiterRequest).where(WaiterRequest.id == request_id, WaiterRequest.restaurant_id == r.id))
    if not q:
        raise HTTPException(status_code=404, detail="Request not found.")
    if q.status not in _TRANSITIONS[data.status]:
        raise HTTPException(status_code=409, detail=f"A {q.status} request cannot be marked {data.status}.")
    q.status, q.updated_at, q.handled_by_user_id = data.status, utcnow(), user.id
    db.commit()
    table = db.get(DiningTable, q.table_id)
    broker.publish(staff_channel(r.id), "request", request_dict(q, table.code if table else "?", user.email))
    broker.publish(table_channel(q.table_id), "request", {"id": q.id, "request_type": q.request_type, "status": q.status})
    return {"id": q.id, "status": q.status}


# ------------------------------------------------------------------ staff (owner only)
def _staff_dict(u: User) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active, "created_at": iso(u.created_at)}


def _staff_or_404(db: Session, restaurant_id: int, user_id: int) -> User:
    staff = db.scalar(select(User).where(User.id == user_id, User.restaurant_id == restaurant_id,
                                         User.role == "staff"))
    if not staff:
        raise HTTPException(status_code=404, detail="Staff account not found.")
    return staff


@router.get("/staff")
def list_staff(db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    return [_staff_dict(u) for u in db.scalars(select(User).where(User.restaurant_id == r.id)
                                               .order_by(User.role.desc(), User.email)).all()]


@router.post("/staff", status_code=201)
def create_staff(data: StaffCreate, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    email = data.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="That email is already registered.")
    enforce_limit(db, r, "staff")
    staff = User(email=email, password_hash=hash_password(data.password), role="staff", restaurant_id=r.id)
    db.add(staff)
    audit(db, user, "staff.created", email)
    db.commit()
    return _staff_dict(staff)


@router.patch("/staff/{user_id}")
def update_staff(user_id: int, data: StaffUpdate, db: Session = Depends(get_db), user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    staff = _staff_or_404(db, r.id, user_id)
    if data.is_active and not staff.is_active:
        enforce_limit(db, r, "staff")
    staff.is_active = data.is_active
    audit(db, user, "staff.activated" if data.is_active else "staff.deactivated", staff.email)
    db.commit()
    return _staff_dict(staff)


@router.post("/staff/{user_id}/password")
def reset_staff_password(user_id: int, data: PasswordIn, db: Session = Depends(get_db),
                         user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    staff = _staff_or_404(db, r.id, user_id)
    staff.password_hash = hash_password(data.password)
    audit(db, user, "staff.password_reset", staff.email)
    db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
              user: User = Depends(require_owner)):
    r = restaurant_for_user(db, user)
    rows = db.scalars(select(AuditLog).where(AuditLog.restaurant_id == r.id)
                      .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)).all()
    return [{"id": a.id, "actor": a.actor_email, "action": a.action, "detail": a.detail,
             "created_at": iso(a.created_at)} for a in rows]

