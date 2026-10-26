"""Super admin API: tenants, plans, leads, platform administrators and site settings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .. import pages
from ..db import (AuditLog, DiningTable, Lead, Media, MenuCategory, MenuItem, PinAttempt, Plan, Restaurant,
                  TableSession, User, WaiterRequest, get_db, iso)
from ..schemas import (AdminCreate, AdminUpdate, LeadUpdate, PasswordIn, PlanIn, RestaurantCreate, RestaurantUpdate,
                       SiteSettingsIn)
from ..security import audit, hash_password, require_platform_admin, window_start
from ..site import get_site, limits, plan_by_key, set_site

router = APIRouter(prefix="/api/platform", tags=["platform"])


def _plan_or_400(db: Session, key: str) -> Plan:
    plan = plan_by_key(db, key)
    if not plan:
        raise HTTPException(status_code=400, detail="That plan doesn't exist. Create it under Plans first.")
    return plan


def _restaurant_or_404(db: Session, restaurant_id: int) -> Restaurant:
    restaurant = db.get(Restaurant, restaurant_id)
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found.")
    return restaurant


# ------------------------------------------------------------------ tenants
@router.get("/restaurants")
def list_restaurants(db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    table_counts = dict(db.execute(select(DiningTable.restaurant_id, func.count(DiningTable.id))
                                   .where(DiningTable.is_active.is_(True)).group_by(DiningTable.restaurant_id)).all())
    request_counts = dict(db.execute(select(WaiterRequest.restaurant_id, func.count(WaiterRequest.id))
                                     .group_by(WaiterRequest.restaurant_id)).all())
    staff_counts = dict(db.execute(select(User.restaurant_id, func.count(User.id))
                                   .where(User.role == "staff", User.is_active.is_(True)).group_by(User.restaurant_id)).all())
    plans = {p.key: p for p in db.scalars(select(Plan)).all()}
    owners = {}
    for owner in db.scalars(select(User).where(User.role == "owner").order_by(User.id)).all():
        owners.setdefault(owner.restaurant_id, owner.email)
    return [{
        "id": r.id, "name": r.name, "slug": r.slug, "plan": r.plan, "is_active": r.is_active,
        "plan_name": plans[r.plan].name if r.plan in plans else r.plan, "limits": limits(plans.get(r.plan)),
        "owner_email": owners.get(r.id), "table_count": table_counts.get(r.id, 0), "staff_count": staff_counts.get(r.id, 0),
        "request_count": request_counts.get(r.id, 0), "created_at": iso(r.created_at),
    } for r in db.scalars(select(Restaurant).order_by(Restaurant.created_at.desc())).all()]


@router.post("/restaurants", status_code=201)
def create_restaurant(data: RestaurantCreate, db: Session = Depends(get_db),
                      admin: User = Depends(require_platform_admin)):
    _plan_or_400(db, data.plan)
    if db.scalar(select(Restaurant.id).where(Restaurant.slug == data.slug)):
        raise HTTPException(status_code=409, detail="Restaurant slug already exists.")
    email = data.owner_email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Owner email already exists.")
    restaurant = Restaurant(name=data.name.strip(), slug=data.slug, plan=data.plan)
    db.add(restaurant)
    db.flush()
    db.add(User(email=email, password_hash=hash_password(data.owner_password), role="owner",
                restaurant_id=restaurant.id))
    audit(db, admin, "platform.restaurant_created", f"{restaurant.slug} (owner {email})", restaurant.id)
    db.commit()
    return {"id": restaurant.id, "name": restaurant.name, "slug": restaurant.slug, "plan": restaurant.plan}


@router.patch("/restaurants/{restaurant_id}")
def update_restaurant(restaurant_id: int, data: RestaurantUpdate, db: Session = Depends(get_db),
                      admin: User = Depends(require_platform_admin)):
    restaurant = _restaurant_or_404(db, restaurant_id)
    changes = []
    if data.name is not None and data.name.strip() != restaurant.name:
        restaurant.name = data.name.strip()
        changes.append("renamed")
    if data.plan is not None:
        restaurant.plan = _plan_or_400(db, data.plan).key
        changes.append(f"plan={data.plan}")
    if data.is_active is not None:
        restaurant.is_active = data.is_active
        changes.append("activated" if data.is_active else "suspended")
    if changes:
        audit(db, admin, "platform.restaurant_updated", f"{restaurant.slug}: {', '.join(changes)}", restaurant.id)
    db.commit()
    return {"id": restaurant.id, "is_active": restaurant.is_active, "plan": restaurant.plan, "name": restaurant.name}


@router.delete("/restaurants/{restaurant_id}")
def delete_restaurant(restaurant_id: int, confirm: str = Query(""), db: Session = Depends(get_db),
                      admin: User = Depends(require_platform_admin)):
    """Permanently removes a tenant and everything it owns. The caller must echo the slug as confirmation."""
    restaurant = _restaurant_or_404(db, restaurant_id)
    if confirm != restaurant.slug:
        raise HTTPException(status_code=400, detail="Type the restaurant's URL slug to confirm deletion.")
    user_ids = select(User.id).where(User.restaurant_id == restaurant_id)
    for stmt in (
        delete(WaiterRequest).where(WaiterRequest.restaurant_id == restaurant_id),
        delete(PinAttempt).where(PinAttempt.restaurant_id == restaurant_id),
        delete(TableSession).where(TableSession.restaurant_id == restaurant_id),
        delete(AuditLog).where(or_(AuditLog.restaurant_id == restaurant_id, AuditLog.actor_user_id.in_(user_ids))),
        delete(DiningTable).where(DiningTable.restaurant_id == restaurant_id),
        delete(MenuItem).where(MenuItem.restaurant_id == restaurant_id),
        delete(MenuCategory).where(MenuCategory.restaurant_id == restaurant_id),
        delete(Media).where(Media.restaurant_id == restaurant_id),
        delete(User).where(User.restaurant_id == restaurant_id),
        delete(Restaurant).where(Restaurant.id == restaurant_id),
    ):
        db.execute(stmt)
    audit(db, admin, "platform.restaurant_deleted", restaurant.slug, None)
    db.commit()
    return {"ok": True}


@router.post("/restaurants/{restaurant_id}/owner-password")
def reset_owner_password(restaurant_id: int, data: PasswordIn, db: Session = Depends(get_db),
                         admin: User = Depends(require_platform_admin)):
    _restaurant_or_404(db, restaurant_id)
    owner = db.scalar(select(User).where(User.restaurant_id == restaurant_id, User.role == "owner")
                      .order_by(User.id).limit(1))
    if not owner:
        raise HTTPException(status_code=404, detail="Owner account not found.")
    owner.password_hash = hash_password(data.password)
    audit(db, admin, "platform.owner_password_reset", owner.email, restaurant_id)
    db.commit()
    return {"ok": True, "message": "Owner password reset. Share the new password securely."}


# ------------------------------------------------------------------ overview + activity
@router.get("/overview")
def platform_overview(db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    def count(column, *where):
        return db.scalar(select(func.count(column)).where(*where)) or 0
    return {
        "restaurants": count(Restaurant.id),
        "active_restaurants": count(Restaurant.id, Restaurant.is_active.is_(True)),
        "tables": count(DiningTable.id, DiningTable.is_active.is_(True)),
        "requests": count(WaiterRequest.id),
        "requests_today": count(WaiterRequest.id, WaiterRequest.created_at >= window_start(24 * 60)),
        "pending_requests": count(WaiterRequest.id, WaiterRequest.status == "pending"),
        "users": count(User.id),
        "new_leads": count(Lead.id, Lead.status == "new"),
    }


@router.get("/audit")
def platform_audit(limit: int = Query(60, ge=1, le=200), db: Session = Depends(get_db),
                   _: User = Depends(require_platform_admin)):
    names = {r.id: r.name for r in db.scalars(select(Restaurant)).all()}
    rows = db.scalars(select(AuditLog).where(AuditLog.action.like("platform.%") | AuditLog.action.like("admin.%"))
                      .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)).all()
    return [{"id": a.id, "actor": a.actor_email, "action": a.action, "detail": a.detail,
             "restaurant": names.get(a.restaurant_id), "created_at": iso(a.created_at)} for a in rows]


# ------------------------------------------------------------------ plans
def _plan_dict(p: Plan, in_use: int) -> dict:
    return {"id": p.id, "key": p.key, "name": p.name, "price_npr": p.price_npr, "max_tables": p.max_tables,
            "max_staff": p.max_staff, "max_menu_items": p.max_menu_items, "features": p.features,
            "is_public": p.is_public, "sort_order": p.sort_order, "restaurants": in_use}


@router.get("/plans")
def list_plans(db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    usage = dict(db.execute(select(Restaurant.plan, func.count(Restaurant.id)).group_by(Restaurant.plan)).all())
    return [_plan_dict(p, usage.get(p.key, 0)) for p in db.scalars(select(Plan).order_by(Plan.sort_order, Plan.id)).all()]


@router.post("/plans", status_code=201)
def create_plan(data: PlanIn, db: Session = Depends(get_db), admin: User = Depends(require_platform_admin)):
    if plan_by_key(db, data.key):
        raise HTTPException(status_code=409, detail="A plan with that key already exists.")
    plan = Plan(**data.model_dump())
    db.add(plan)
    audit(db, admin, "platform.plan_created", data.key)
    db.commit()
    pages.invalidate()
    return _plan_dict(plan, 0)


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, data: PlanIn, db: Session = Depends(get_db), admin: User = Depends(require_platform_admin)):
    plan = db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    for field, value in data.model_dump(exclude={"key"}).items():  # the key is an identifier and never changes
        setattr(plan, field, value)
    audit(db, admin, "platform.plan_updated", plan.key)
    db.commit()
    pages.invalidate()
    return _plan_dict(plan, db.scalar(select(func.count(Restaurant.id)).where(Restaurant.plan == plan.key)) or 0)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), admin: User = Depends(require_platform_admin)):
    plan = db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    if db.scalar(select(func.count(Restaurant.id)).where(Restaurant.plan == plan.key)):
        raise HTTPException(status_code=409, detail="Restaurants are still on this plan. Move them to another plan first.")
    db.delete(plan)
    audit(db, admin, "platform.plan_deleted", plan.key)
    db.commit()
    pages.invalidate()
    return {"ok": True}


# ------------------------------------------------------------------ leads
def _lead_dict(lead: Lead) -> dict:
    return {"id": lead.id, "name": lead.name, "business": lead.business, "email": lead.email, "phone": lead.phone,
            "message": lead.message, "status": lead.status, "created_at": iso(lead.created_at)}


@router.get("/leads")
def list_leads(db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    return [_lead_dict(x) for x in db.scalars(select(Lead).order_by(Lead.created_at.desc()).limit(200)).all()]


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: int, data: LeadUpdate, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Request not found.")
    lead.status = data.status
    db.commit()
    return _lead_dict(lead)


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Request not found.")
    db.delete(lead)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ platform administrators
def _admin_dict(u: User, me: User) -> dict:
    return {"id": u.id, "email": u.email, "is_active": u.is_active, "created_at": iso(u.created_at), "is_me": u.id == me.id}


@router.get("/admins")
def list_admins(db: Session = Depends(get_db), me: User = Depends(require_platform_admin)):
    return [_admin_dict(u, me) for u in db.scalars(select(User).where(User.role == "platform_admin").order_by(User.id)).all()]


@router.post("/admins", status_code=201)
def create_admin(data: AdminCreate, db: Session = Depends(get_db), me: User = Depends(require_platform_admin)):
    email = data.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="That email is already registered.")
    admin = User(email=email, password_hash=hash_password(data.password), role="platform_admin")
    db.add(admin)
    audit(db, me, "admin.created", email)
    db.commit()
    return _admin_dict(admin, me)


def _other_admin(db: Session, admin_id: int, me: User) -> User:
    target = db.scalar(select(User).where(User.id == admin_id, User.role == "platform_admin"))
    if not target:
        raise HTTPException(status_code=404, detail="Administrator not found.")
    return target


@router.patch("/admins/{admin_id}")
def update_admin(admin_id: int, data: AdminUpdate, db: Session = Depends(get_db), me: User = Depends(require_platform_admin)):
    target = _other_admin(db, admin_id, me)
    if target.id == me.id and not data.is_active:
        raise HTTPException(status_code=400, detail="You can't disable your own account.")
    target.is_active = data.is_active
    audit(db, me, "admin.activated" if data.is_active else "admin.deactivated", target.email)
    db.commit()
    return _admin_dict(target, me)


@router.post("/admins/{admin_id}/password")
def reset_admin_password(admin_id: int, data: PasswordIn, db: Session = Depends(get_db),
                         me: User = Depends(require_platform_admin)):
    target = _other_admin(db, admin_id, me)
    target.password_hash = hash_password(data.password)
    audit(db, me, "admin.password_reset", target.email)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ site settings (landing page content)
@router.get("/settings")
def read_settings(db: Session = Depends(get_db), _: User = Depends(require_platform_admin)):
    return get_site(db)


@router.put("/settings")
def write_settings(data: SiteSettingsIn, db: Session = Depends(get_db), admin: User = Depends(require_platform_admin)):
    set_site(db, {k: v.strip() for k, v in data.model_dump().items()})
    audit(db, admin, "platform.settings_updated", "site settings")
    db.commit()
    pages.invalidate()
    return get_site(db)

