"""Server-rendered landing page. Plans and site text come from the database, so the super admin controls them live."""
from __future__ import annotations

import time
from html import escape
from pathlib import Path

from sqlalchemy import select

from .db import Plan, SessionLocal
from .site import get_site

TEMPLATE = Path(__file__).resolve().parent / "static" / "landing.html"
TTL_SECONDS = 20
_cache: dict = {"at": 0.0, "html": ""}


def invalidate() -> None:
    _cache["at"] = 0.0


def _price(plan: Plan) -> str:
    if plan.price_npr <= 0:
        return '<p class="price">Free<small> to try</small></p>'
    return f'<p class="price"><span>NPR</span> {plan.price_npr:,}<small> / month</small></p>'


def _plans_html(plans: list[Plan]) -> str:
    featured = len(plans) // 2 if len(plans) >= 3 else -1
    cards = []
    for i, p in enumerate(plans):
        features = "".join(f"<li>{escape(line.strip())}</li>" for line in p.features.splitlines() if line.strip())
        cards.append(
            f'<article class="plan{" featured" if i == featured else ""}"><h3>{escape(p.name)}</h3>{_price(p)}'
            f'<ul>{features}</ul><a class="btn {"primary" if i == featured else "secondary"}" href="#demo">Ask about {escape(p.name)}</a></article>')
    return "".join(cards)


def _render() -> str:
    with SessionLocal() as db:
        site = get_site(db)
        plans = list(db.scalars(select(Plan).where(Plan.is_public.is_(True)).order_by(Plan.sort_order, Plan.id)).all())
    contact = []
    if site["contact_email"]:
        e = escape(site["contact_email"])
        contact.append(f'<a href="mailto:{e}">{e}</a>')
    if site["contact_phone"]:
        contact.append(f'<a href="tel:{escape(site["contact_phone"], quote=True)}">{escape(site["contact_phone"])}</a>')
    html = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{brand}}": escape(site["brand_name"]), "{{tagline}}": escape(site["tagline"]),
        "{{plans}}": _plans_html(plans), "{{pricing_class}}": "" if plans else "hidden",
        "{{contact}}": " &nbsp;·&nbsp; ".join(contact) or "Questions? Use the form above.",
    }
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html


def landing_html() -> str:
    if not _cache["html"] or time.monotonic() - _cache["at"] > TTL_SECONDS:
        _cache.update(html=_render(), at=time.monotonic())
    return _cache["html"]
