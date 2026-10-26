"""Platform-wide settings and plan helpers shared by routers and the landing page."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import DEFAULT_SETTINGS, DiningTable, MenuItem, Plan, Restaurant, Setting, User

LIMIT_LABELS = {"tables": "tables", "staff": "staff accounts", "items": "menu items"}


def get_site(db: Session) -> dict[str, str]:
    values = dict(DEFAULT_SETTINGS)
    values.update({s.key: s.value for s in db.scalars(select(Setting)).all() if s.key in DEFAULT_SETTINGS})
    return values


def set_site(db: Session, updates: dict[str, str]) -> None:
    for key, value in updates.items():
        if key not in DEFAULT_SETTINGS:
            continue
        row = db.get(Setting, key)
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))


def plan_by_key(db: Session, key: str) -> Plan | None:
    return db.scalar(select(Plan).where(Plan.key == key))


def usage(db: Session, restaurant: Restaurant) -> dict[str, int]:
    return {
        "tables": db.scalar(select(func.count(DiningTable.id)).where(
            DiningTable.restaurant_id == restaurant.id, DiningTable.is_active.is_(True))) or 0,
        "staff": db.scalar(select(func.count(User.id)).where(
            User.restaurant_id == restaurant.id, User.role == "staff", User.is_active.is_(True))) or 0,
        "items": db.scalar(select(func.count(MenuItem.id)).where(MenuItem.restaurant_id == restaurant.id)) or 0,
    }


def limits(plan: Plan | None) -> dict[str, int]:
    """0 means unlimited. A restaurant whose plan row was removed is treated as unlimited."""
    if not plan:
        return {"tables": 0, "staff": 0, "items": 0}
    return {"tables": plan.max_tables, "staff": plan.max_staff, "items": plan.max_menu_items}


def enforce_limit(db: Session, restaurant: Restaurant, resource: str) -> None:
    cap = limits(plan_by_key(db, restaurant.plan))[resource]
    if cap and usage(db, restaurant)[resource] >= cap:
        raise HTTPException(status_code=402, detail=f"Your plan allows up to {cap} {LIMIT_LABELS[resource]}. "
                                                    "Ask the platform administrator to upgrade your plan.")
