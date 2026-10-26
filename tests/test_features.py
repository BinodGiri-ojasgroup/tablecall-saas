"""v2 features: PIN policy, Wi-Fi, uploads, plans and limits, leads, super admin, live broker."""
import asyncio
from datetime import timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from app.db import DiningTable, SessionLocal, utcnow
from app.live import Broker
from app.main import app
from conftest import PASSWORD, login, open_table_with_pin


def png_bytes(size=(1600, 1200), color=(200, 40, 40)):
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def upload(owner, kind="item", data=None):
    return owner.post(f"/api/restaurant/media?kind={kind}", content=data if data is not None else png_bytes(),
                      headers={"Content-Type": "application/octet-stream"})


def make_staff(owner, email="floor-v2@example.com"):
    owner.post("/api/restaurant/staff", json={"email": email, "password": PASSWORD})
    staff = TestClient(app)
    assert login(staff, email, PASSWORD).status_code == 200
    return staff


# ------------------------------------------------------------------ PIN policy
def test_pin_valid_until_session_ends_when_not_time_based(make_tenant, guest):
    slug, owner = make_tenant()
    assert owner.patch("/api/restaurant/settings", json={"pin_mode": "session"}).status_code == 200
    table_id, pin = open_table_with_pin(owner, "T01")
    # Re-issue to read the response shape.
    d = owner.post(f"/api/restaurant/tables/{table_id}/pin/rotate").json()
    assert d["mode"] == "session" and d["expires_at"] is None and d["valid_minutes"] is None
    # Even long after the old 15 minute window, the PIN keeps working until the table closes.
    with SessionLocal() as db:
        assert db.get(DiningTable, table_id).pin_expires_at is None
    assert guest.post(f"/api/public/{slug}/T01/requests", json={"pin": d["pin"], "request_type": "call_waiter"}).status_code == 201
    owner.post(f"/api/restaurant/tables/{table_id}/session/close")
    assert guest.post(f"/api/public/{slug}/T01/requests", json={"pin": d["pin"], "request_type": "need_water"}).status_code == 403


def test_custom_pin_minutes_and_expiry(make_tenant, guest):
    slug, owner = make_tenant()
    r = owner.patch("/api/restaurant/settings", json={"pin_mode": "timed", "pin_ttl_minutes": 90})
    assert r.json()["pin_mode"] == "timed" and r.json()["pin_ttl_minutes"] == 90
    table_id, _ = open_table_with_pin(owner, "T01")
    d = owner.post(f"/api/restaurant/tables/{table_id}/pin/rotate").json()
    assert d["valid_minutes"] == 90
    with SessionLocal() as db:
        expiry = db.get(DiningTable, table_id).pin_expires_at
        assert expiry is not None
        t = db.get(DiningTable, table_id)
        t.pin_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert guest.post(f"/api/public/{slug}/T01/requests", json={"pin": d["pin"], "request_type": "call_waiter"}).status_code == 403
    assert owner.patch("/api/restaurant/settings", json={"pin_ttl_minutes": 0}).status_code == 422


# ------------------------------------------------------------------ settings, Wi-Fi, branding
def test_wifi_is_owner_managed_and_only_shown_to_seated_guests(make_tenant, guest):
    slug, owner = make_tenant()
    staff = make_staff(owner)
    assert staff.patch("/api/restaurant/settings", json={"wifi_ssid": "x"}).status_code == 403
    r = owner.patch("/api/restaurant/settings", json={"wifi_ssid": "Cafe Guest", "wifi_password": "momo-2026"})
    assert r.status_code == 200 and r.json()["wifi_password"] == "momo-2026"
    table = owner.post("/api/restaurant/tables", json={"code": "W1"}).json()
    closed = guest.get(f"/api/public/{slug}/W1/menu").json()
    assert closed["table"]["wifi"] is None  # nothing leaks to someone who only has the link
    owner.post(f"/api/restaurant/tables/{table['id']}/session/open")
    opened = guest.get(f"/api/public/{slug}/W1/menu").json()
    assert opened["table"]["wifi"] == {"ssid": "Cafe Guest", "password": "momo-2026"}
    assert staff.get("/api/restaurant").json()["wifi_ssid"] == "Cafe Guest"
    qr = owner.get("/api/restaurant/wifi-qr.png")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/png"
    owner.patch("/api/restaurant/settings", json={"wifi_ssid": "", "wifi_password": ""})
    assert guest.get(f"/api/public/{slug}/W1/menu").json()["table"]["wifi"] is None
    assert owner.get("/api/restaurant/wifi-qr.png").status_code == 404


def test_brand_color_validation_and_rename(make_tenant, guest):
    slug, owner = make_tenant()
    assert owner.patch("/api/restaurant/settings", json={"brand_color": "red"}).status_code == 422
    r = owner.patch("/api/restaurant/settings", json={"brand_color": "#C0392B", "name": "Blue Door Cafe"})
    assert r.json()["brand_color"] == "#c0392b" and r.json()["name"] == "Blue Door Cafe"
    owner.post("/api/restaurant/tables", json={"code": "B1"})
    pub = guest.get(f"/api/public/{slug}/B1/menu").json()["restaurant"]
    assert pub["name"] == "Blue Door Cafe" and pub["brand_color"] == "#c0392b"


# ------------------------------------------------------------------ uploads
def test_logo_and_menu_photo_upload(make_tenant, guest):
    slug, owner = make_tenant()
    staff = make_staff(owner)
    assert upload(staff).status_code == 403

    logo = upload(owner, "logo")
    assert logo.status_code == 201
    r = owner.patch("/api/restaurant/settings", json={"logo_media_id": logo.json()["id"]})
    assert r.json()["logo_url"] == logo.json()["url"]
    owner.post("/api/restaurant/tables", json={"code": "L1"})
    assert guest.get(f"/api/public/{slug}/L1/menu").json()["restaurant"]["logo_url"] == logo.json()["url"]

    photo = upload(owner, "item")
    assert photo.status_code == 201
    item = owner.post("/api/restaurant/menu/items", json={"name": "Momo", "price_npr": 220, "image_id": photo.json()["id"]})
    assert item.status_code == 201 and item.json()["image"] == photo.json()["url"]
    served = guest.get(photo.json()["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
    assert "immutable" in served.headers["cache-control"]
    img = Image.open(BytesIO(served.content))
    assert max(img.size) <= 900                                    # resized from 1600x1200

    # A logo cannot be attached to a menu item, and tenants cannot use each other's images.
    assert owner.post("/api/restaurant/menu/items", json={"name": "x", "price_npr": 1, "image_id": logo.json()["id"]}).status_code == 400
    _, other = make_tenant()
    assert other.post("/api/restaurant/menu/items", json={"name": "x", "price_npr": 1, "image_id": photo.json()["id"]}).status_code == 400

    # Replacing or deleting removes the old file; removing the logo clears it.
    owner.delete(f"/api/restaurant/menu/items/{item.json()['id']}")
    assert guest.get(photo.json()["url"]).status_code == 404
    owner.patch("/api/restaurant/settings", json={"remove_logo": True})
    assert guest.get(logo.json()["url"]).status_code == 404


def test_upload_rejects_non_images_and_huge_files(make_tenant):
    _, owner = make_tenant()
    assert upload(owner, data=b"<svg onload=alert(1)></svg>").status_code == 400
    assert upload(owner, data=b"").status_code == 400
    assert upload(owner, data=b"x" * (8 * 1024 * 1024 + 10)).status_code == 413
    assert owner.post("/api/restaurant/media?kind=banner", content=png_bytes()).status_code == 422


# ------------------------------------------------------------------ plans and limits
def test_plan_limits_are_enforced(make_tenant, admin):
    slug, owner = make_tenant()
    plan = admin.post("/api/platform/plans", json={"key": "tiny", "name": "Tiny", "price_npr": 1, "max_tables": 1,
                                                    "max_staff": 1, "max_menu_items": 1}).json()
    rid = next(r["id"] for r in admin.get("/api/platform/restaurants").json() if r["slug"] == slug)
    assert admin.patch(f"/api/platform/restaurants/{rid}", json={"plan": "tiny"}).status_code == 200
    assert owner.post("/api/restaurant/tables", json={"code": "A"}).status_code == 201
    over = owner.post("/api/restaurant/tables", json={"code": "B"})
    assert over.status_code == 402 and "1 tables" in over.json()["detail"]
    assert owner.post("/api/restaurant/menu/items", json={"name": "a", "price_npr": 1}).status_code == 201
    assert owner.post("/api/restaurant/menu/items", json={"name": "b", "price_npr": 1}).status_code == 402
    assert owner.post("/api/restaurant/staff", json={"email": "s1@example.com", "password": PASSWORD}).status_code == 201
    assert owner.post("/api/restaurant/staff", json={"email": "s2@example.com", "password": PASSWORD}).status_code == 402
    info = owner.get("/api/restaurant").json()
    assert info["limits"]["tables"] == 1 and info["usage"]["tables"] == 1 and info["plan_name"] == "Tiny"
    # Upgrading lifts the limit immediately; unlimited is 0.
    admin.patch(f"/api/platform/plans/{plan['id']}", json={"key": "tiny", "name": "Tiny", "max_tables": 0})
    assert owner.post("/api/restaurant/tables", json={"code": "B"}).status_code == 201


def test_plan_management(admin):
    p = admin.post("/api/platform/plans", json={"key": "gold-x", "name": "Gold", "features": "One\nTwo"})
    assert p.status_code == 201
    assert admin.post("/api/platform/plans", json={"key": "gold-x", "name": "Dup"}).status_code == 409
    assert admin.post("/api/platform/restaurants", json={
        "name": "Nope", "slug": "nope-plan", "owner_email": "np@example.com", "owner_password": PASSWORD,
        "plan": "does-not-exist"}).status_code == 400
    assert admin.delete(f"/api/platform/plans/{p.json()['id']}").status_code == 200


def test_cannot_delete_plan_in_use(admin, make_tenant):
    slug, _ = make_tenant()
    trial = next(p for p in admin.get("/api/platform/plans").json() if p["key"] == "trial")
    assert trial["restaurants"] >= 1
    assert admin.delete(f"/api/platform/plans/{trial['id']}").status_code == 409


# ------------------------------------------------------------------ landing page + leads
def test_landing_page_is_dynamic(admin, guest):
    admin.put("/api/platform/settings", json={"brand_name": "Dine<script>", "tagline": "Serve faster", "contact_email": "hi@example.com"})
    html = guest.get("/").text
    assert "Dine&lt;script&gt;" in html and "<script>alert" not in html and "Dine<script>" not in html
    assert "Serve faster" in html and "hi@example.com" in html
    assert "Starter" in html and "NPR" in html                      # pricing comes from the plans table
    admin.put("/api/platform/settings", json={"brand_name": "TableCall", "tagline": "Guests scan, tap, and your waiter comes."})
    assert guest.get("/app").status_code == 200 and guest.get("/login").status_code == 200


def test_demo_request_flow(admin, guest):
    body = {"name": "Sita", "business": "Lake View Cafe", "email": "sita@example.com", "phone": "9800000000", "message": "Hi"}
    assert guest.post("/api/public/leads", json=body).status_code == 201
    assert guest.post("/api/public/leads", json={**body, "name": "Bot", "website": "http://spam"}).status_code == 201  # silently dropped
    leads = admin.get("/api/platform/leads").json()
    assert [x["name"] for x in leads].count("Sita") >= 1 and "Bot" not in [x["name"] for x in leads]
    lead = next(x for x in leads if x["name"] == "Sita")
    assert admin.patch(f"/api/platform/leads/{lead['id']}", json={"status": "contacted"}).json()["status"] == "contacted"
    assert admin.patch(f"/api/platform/leads/{lead['id']}", json={"status": "bogus"}).status_code == 422
    assert admin.get("/api/platform/overview").json()["new_leads"] >= 0
    assert guest.get("/api/platform/leads").status_code == 401
    assert admin.delete(f"/api/platform/leads/{lead['id']}").status_code == 200


def test_demo_requests_are_rate_limited(guest):
    body = {"name": "Spammer", "email": "s@example.com"}
    codes = [guest.post("/api/public/leads", json=body).status_code for _ in range(7)]
    assert codes[:5] == [201] * 5 and codes[5] == 429


# ------------------------------------------------------------------ platform administrators
def test_super_admin_manages_other_admins(admin):
    created = admin.post("/api/platform/admins", json={"email": "second-admin@example.com", "password": PASSWORD})
    assert created.status_code == 201
    second = TestClient(app)
    assert login(second, "second-admin@example.com", PASSWORD).status_code == 200
    assert second.get("/api/platform/overview").status_code == 200
    me = next(a for a in admin.get("/api/platform/admins").json() if a["is_me"])
    assert admin.patch(f"/api/platform/admins/{me['id']}", json={"is_active": False}).status_code == 400
    assert admin.patch(f"/api/platform/admins/{created.json()['id']}", json={"is_active": False}).status_code == 200
    assert second.get("/api/platform/overview").status_code == 401
    assert admin.post(f"/api/platform/admins/{created.json()['id']}/password", json={"password": "another-password"}).status_code == 200


def test_delete_tenant_removes_everything(admin, make_tenant, guest):
    slug, owner = make_tenant()
    tid, pin = open_table_with_pin(owner, "T01")
    guest.post(f"/api/public/{slug}/T01/requests", json={"pin": pin, "request_type": "call_waiter"})
    photo = upload(owner)
    owner.post("/api/restaurant/menu/items", json={"name": "Tea", "price_npr": 10, "image_id": photo.json()["id"]})
    rid = next(r["id"] for r in admin.get("/api/platform/restaurants").json() if r["slug"] == slug)
    assert admin.delete(f"/api/platform/restaurants/{rid}?confirm=wrong").status_code == 400
    assert admin.delete(f"/api/platform/restaurants/{rid}?confirm={slug}").status_code == 200
    assert owner.get("/api/restaurant").status_code == 401
    assert guest.get(f"/api/public/{slug}/T01/menu").status_code == 404
    assert guest.get(photo.json()["url"]).status_code == 404
    assert all(r["slug"] != slug for r in admin.get("/api/platform/restaurants").json())


# ------------------------------------------------------------------ live updates
def test_event_streams_require_the_right_access(make_tenant, guest):
    assert guest.get("/api/restaurant/events").status_code == 401
    assert guest.get("/api/public/nope/T1/events").status_code == 404


def test_broker_fans_out_and_recovers_from_slow_clients():
    async def scenario():
        b = Broker()
        b.bind(asyncio.get_running_loop())
        q1, q2 = b.subscribe("r:1"), b.subscribe("r:1")
        assert b.subscribe("r:2", limit=0) is None                       # connection cap
        b.publish("r:1", "request", {"id": 1})
        b.publish("r:2", "request", {"id": 2})                           # nobody listening: dropped
        await asyncio.sleep(0)
        assert await q1.get() == ("request", {"id": 1}) and await q2.get() == ("request", {"id": 1})
        for i in range(150):                                             # overflow -> backlog replaced by a resync hint
            b._deliver("r:1", ("request", {"id": i}))
        assert q1.qsize() < 100 and ("resync", None) in [q1.get_nowait() for _ in range(q1.qsize())]
        b.unsubscribe("r:1", q1)
        b.unsubscribe("r:1", q2)
        assert "r:1" not in b.channels
    asyncio.run(scenario())
