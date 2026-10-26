import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TEST_DB = ROOT / "test-suite.db"
TEST_DB.unlink(missing_ok=True)
os.environ.update({
    "APP_ENV": "development", "DATABASE_URL": f"sqlite:///{TEST_DB}",
    "SECRET_KEY": "test-only-secret-key-with-more-than-32-characters",
    "BOOTSTRAP_ADMIN_EMAIL": "admin@example.com", "BOOTSTRAP_ADMIN_PASSWORD": "test-superadmin-password",
    "APP_BASE_URL": "http://127.0.0.1:8000", "TRUSTED_PROXY_COUNT": "0",
})

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = ("admin@example.com", "test-superadmin-password")
PASSWORD = "a-good-test-password"


def login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


@pytest.fixture(scope="session", autouse=True)
def _lifespan():
    with TestClient(app):
        yield
    TEST_DB.unlink(missing_ok=True)


@pytest.fixture()
def admin():
    c = TestClient(app)
    assert login(c, *ADMIN).status_code == 200
    return c


@pytest.fixture()
def make_tenant(admin):
    """Creates a restaurant and returns (slug, signed-in owner client)."""
    counter = {"n": 0}

    def _make(slug=None):
        counter["n"] += 1
        slug = slug or f"cafe-{os.urandom(3).hex()}"
        email = f"owner-{slug}@example.com"
        r = admin.post("/api/platform/restaurants", json={
            "name": slug.title(), "slug": slug, "owner_email": email, "owner_password": PASSWORD, "plan": "trial"})
        assert r.status_code == 201, r.text
        owner = TestClient(app)
        assert login(owner, email, PASSWORD).status_code == 200
        return slug, owner
    return _make


@pytest.fixture()
def guest():
    return TestClient(app)


def open_table_with_pin(owner, code="T01"):
    table = owner.post("/api/restaurant/tables", json={"code": code}).json()
    assert owner.post(f"/api/restaurant/tables/{table['id']}/session/open").status_code == 200
    pin = owner.post(f"/api/restaurant/tables/{table['id']}/pin/rotate").json()["pin"]
    return table["id"], pin
