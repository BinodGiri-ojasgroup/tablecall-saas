"""Core customer/staff workflow."""
from conftest import open_table_with_pin


def test_pin_lifecycle_and_table_isolation(make_tenant, guest):
    slug, owner = make_tenant()
    t1, pin1 = open_table_with_pin(owner, "T01")
    t2, pin2 = open_table_with_pin(owner, "T02")

    menu = guest.get(f"/api/public/{slug}/T01/menu")
    assert menu.status_code == 200 and menu.json()["table"]["pin_active"] is True
    # Table codes are case-insensitive for guests.
    assert guest.get(f"/api/public/{slug}/t01/menu").status_code == 200

    ok = guest.post(f"/api/public/{slug}/T01/requests", json={"pin": pin1, "request_type": "call_waiter"})
    assert ok.status_code == 201, ok.text
    assert owner.get("/api/restaurant/requests").json()[0]["status"] == "pending"

    # Table one's PIN must never authorise table two.
    assert guest.post(f"/api/public/{slug}/T02/requests", json={"pin": pin1, "request_type": "call_waiter"}).status_code == 403
    assert guest.post(f"/api/public/{slug}/T02/requests", json={"pin": pin2, "request_type": "call_waiter"}).status_code == 201

    # Closing the session invalidates the PIN and cancels what was still open.
    assert owner.post(f"/api/restaurant/tables/{t1}/session/close").status_code == 200
    assert guest.post(f"/api/public/{slug}/T01/requests", json={"pin": pin1, "request_type": "request_bill"}).status_code == 403
    statuses = {r["table_code"]: r["status"] for r in owner.get("/api/restaurant/requests").json()}
    assert statuses["T01"] == "cancelled"


def test_pin_attempts_are_throttled(make_tenant, guest):
    slug, owner = make_tenant()
    _, pin = open_table_with_pin(owner)
    for _ in range(5):
        assert guest.post(f"/api/public/{slug}/T01/requests", json={"pin": "000000", "request_type": "need_water"}).status_code == 403
    locked = guest.post(f"/api/public/{slug}/T01/requests", json={"pin": pin, "request_type": "need_water"})
    assert locked.status_code == 429


def test_duplicate_request_cooldown(make_tenant, guest):
    slug, owner = make_tenant()
    _, pin = open_table_with_pin(owner)
    body = {"pin": pin, "request_type": "call_waiter"}
    assert guest.post(f"/api/public/{slug}/T01/requests", json=body).status_code == 201
    assert guest.post(f"/api/public/{slug}/T01/requests", json=body).status_code == 429


def test_request_state_machine(make_tenant, guest):
    slug, owner = make_tenant()
    _, pin = open_table_with_pin(owner)
    guest.post(f"/api/public/{slug}/T01/requests", json={"pin": pin, "request_type": "call_waiter"})
    rid = owner.get("/api/restaurant/requests").json()[0]["id"]
    def patch(status):
        return owner.patch(f"/api/restaurant/requests/{rid}", json={"status": status})

    assert patch("accepted").status_code == 200
    assert patch("accepted").status_code == 409
    assert patch("completed").status_code == 200
    assert patch("cancelled").status_code == 409  # closed requests stay closed
    assert owner.get("/api/restaurant/requests?status=open").json() == []


def test_status_endpoint_reflects_session(make_tenant, guest):
    slug, owner = make_tenant()
    table = owner.post("/api/restaurant/tables", json={"code": "A1"}).json()
    assert guest.get(f"/api/public/{slug}/A1/status").json()["session_active"] is False
    owner.post(f"/api/restaurant/tables/{table['id']}/session/open")
    assert guest.get(f"/api/public/{slug}/A1/status").json()["session_active"] is True


def test_delete_table_frees_code_and_keeps_history(make_tenant, guest):
    slug, owner = make_tenant()
    tid, pin = open_table_with_pin(owner, "X9")
    guest.post(f"/api/public/{slug}/X9/requests", json={"pin": pin, "request_type": "call_waiter"})
    assert owner.delete(f"/api/restaurant/tables/{tid}").status_code == 200
    assert guest.get(f"/api/public/{slug}/X9/menu").status_code == 404
    assert owner.post("/api/restaurant/tables", json={"code": "X9"}).status_code == 201
    assert owner.get("/api/restaurant/requests").json()[0]["table_code"] == "X9"
