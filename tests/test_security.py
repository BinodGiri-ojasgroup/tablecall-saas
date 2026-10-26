"""Authentication, authorisation, tenant isolation and abuse protection."""
from fastapi.testclient import TestClient

from app.main import app
from conftest import PASSWORD, login, open_table_with_pin


def test_login_is_throttled_per_account_and_client():
    c = TestClient(app)
    for _ in range(5):
        assert login(c, "nobody@example.com", "wrong-password-1").status_code == 401
    assert login(c, "nobody@example.com", "wrong-password-1").status_code == 429


def test_tenants_cannot_see_each_others_data(make_tenant):
    _, a = make_tenant()
    _, b = make_tenant()
    a_table = a.post("/api/restaurant/tables", json={"code": "T01"}).json()
    item = a.post("/api/restaurant/menu/items", json={"name": "Tea", "price_npr": 50}).json()
    assert b.get("/api/restaurant/tables").json() == []
    assert b.post(f"/api/restaurant/tables/{a_table['id']}/session/open").status_code == 404
    assert b.patch(f"/api/restaurant/menu/items/{item['id']}", json={"name": "x", "price_npr": 1}).status_code == 404
    assert b.delete(f"/api/restaurant/menu/items/{item['id']}").status_code == 404


def test_role_boundaries(make_tenant, admin):
    _, owner = make_tenant()
    assert owner.get("/api/platform/overview").status_code == 403
    assert admin.get("/api/restaurant/tables").status_code == 403
    assert TestClient(app).get("/api/restaurant").status_code == 401

    staff = owner.post("/api/restaurant/staff", json={"email": "waiter@example.com", "password": PASSWORD})
    assert staff.status_code == 201
    waiter = TestClient(app)
    assert login(waiter, "waiter@example.com", PASSWORD).status_code == 200
    # Staff can work the floor but cannot configure the restaurant.
    assert waiter.post("/api/restaurant/tables", json={"code": "Z"}).status_code == 403
    assert waiter.get("/api/restaurant/staff").status_code == 403
    assert waiter.get("/api/restaurant/audit").status_code == 403
    table_id, _ = open_table_with_pin(owner, "T01")
    assert waiter.post(f"/api/restaurant/tables/{table_id}/pin/rotate").status_code == 200
    # Deactivated staff are locked out immediately.
    assert owner.patch(f"/api/restaurant/staff/{staff.json()['id']}", json={"is_active": False}).status_code == 200
    assert waiter.get("/api/restaurant").status_code == 401


def test_staff_can_toggle_availability_only(make_tenant):
    _, owner = make_tenant()
    item = owner.post("/api/restaurant/menu/items", json={"name": "Momo", "price_npr": 220}).json()
    owner.post("/api/restaurant/staff", json={"email": "floor@example.com", "password": PASSWORD})
    waiter = TestClient(app)
    login(waiter, "floor@example.com", PASSWORD)
    r = waiter.patch(f"/api/restaurant/menu/items/{item['id']}/availability", json={"is_available": False})
    assert r.status_code == 200 and r.json()["is_available"] is False
    assert waiter.delete(f"/api/restaurant/menu/items/{item['id']}").status_code == 403


def test_password_change_invalidates_other_sessions(make_tenant):
    slug, owner = make_tenant()
    email = f"owner-{slug}@example.com"
    other_device = TestClient(app)
    login(other_device, email, PASSWORD)
    bad = owner.post("/api/auth/password", json={"current_password": "wrong-password", "new_password": "brand-new-password"})
    assert bad.status_code == 400
    ok = owner.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "brand-new-password"})
    assert ok.status_code == 200
    assert owner.get("/api/auth/me").status_code == 200          # this device stays signed in
    assert other_device.get("/api/auth/me").status_code == 401   # others are signed out


def test_suspended_tenant_loses_access(make_tenant, admin, guest):
    slug, owner = make_tenant()
    rid = next(r["id"] for r in admin.get("/api/platform/restaurants").json() if r["slug"] == slug)
    assert admin.patch(f"/api/platform/restaurants/{rid}", json={"is_active": False}).status_code == 200
    assert owner.get("/api/restaurant").status_code == 403
    assert guest.get(f"/api/public/{slug}/T01/menu").status_code == 404
    admin.patch(f"/api/platform/restaurants/{rid}", json={"is_active": True, "plan": "premium"})
    assert owner.get("/api/restaurant").json()["plan"] == "premium"


def test_cross_origin_writes_are_blocked(make_tenant):
    _, owner = make_tenant()
    evil = owner.post("/api/restaurant/tables", json={"code": "EVIL"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    same = owner.post("/api/restaurant/tables", json={"code": "GOOD"}, headers={"Origin": "http://127.0.0.1:8000"})
    assert same.status_code == 201


def test_input_validation(make_tenant):
    _, owner = make_tenant()
    assert owner.post("/api/restaurant/menu/items", json={"name": "x", "price_npr": 5, "image_url": "javascript:alert(1)"}).status_code == 422
    assert owner.post("/api/restaurant/menu/items", json={"name": "x", "price_npr": -5}).status_code == 422
    assert owner.post("/api/restaurant/tables", json={"code": "../etc"}).status_code == 422


def test_audit_log_records_sensitive_actions(make_tenant):
    _, owner = make_tenant()
    open_table_with_pin(owner, "T01")
    actions = {a["action"] for a in owner.get("/api/restaurant/audit").json()}
    assert {"table.created", "table.session_opened", "table.pin_rotated"} <= actions


def test_proxy_headers_ignored_unless_trusted(make_tenant, guest, monkeypatch):
    """Spoofed X-Forwarded-For must not let an attacker dodge the per-client lockout."""
    slug, owner = make_tenant()
    _, pin = open_table_with_pin(owner)
    for i in range(5):
        guest.post(f"/api/public/{slug}/T01/requests", json={"pin": "111111", "request_type": "need_water"},
                   headers={"X-Forwarded-For": f"10.0.0.{i}"})
    r = guest.post(f"/api/public/{slug}/T01/requests", json={"pin": pin, "request_type": "need_water"},
                   headers={"X-Forwarded-For": "10.9.9.9"})
    assert r.status_code == 429


def test_health_and_pages(guest):
    assert guest.get("/health").json()["ok"] is True
    assert guest.get("/ready").json()["ok"] is True
    page = guest.get("/t/any/T01")
    assert page.status_code == 200 and "Content-Security-Policy" in page.headers
    assert guest.get("/api/does-not-exist").status_code == 404


def test_client_ip_honours_only_trusted_proxy_hops(monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    from app.security import client_ip

    def fake(xff, host="203.0.113.5"):
        return SimpleNamespace(client=SimpleNamespace(host=host), headers={"x-forwarded-for": xff} if xff else {})

    monkeypatch.setattr(settings, "trusted_proxy_count", 0)
    assert client_ip(fake("1.1.1.1")) == "203.0.113.5"             # header ignored: nobody is trusted
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    assert client_ip(fake("6.6.6.6, 9.9.9.9")) == "9.9.9.9"        # spoofed left entry is ignored
    assert client_ip(fake("")) == "203.0.113.5"                    # no header: fall back to socket peer
