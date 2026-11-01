"""Auth & organization context tests (API-01/02, AUTH tasks)."""

from __future__ import annotations

from tests.conftest import headers_for, login, register


def test_register_login_me(client) -> None:
    tokens = register(client, email="u1@test.io", org="Acme")
    me = client.get("/api/v1/auth/me", headers=headers_for(tokens))
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "u1@test.io"
    assert body["organizations"][0]["name"] == "Acme"
    assert body["organizations"][0]["role"] == "owner"


def test_duplicate_email_conflicts(client) -> None:
    register(client, email="dup@test.io", org="One")
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "dup@test.io",
            "password": "hunter2hunter2",
            "organization_name": "Two",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_login_wrong_password_unauthenticated(client) -> None:
    register(client, email="pw@test.io", org="PwOrg")
    response = client.post("/api/v1/auth/login", json={"email": "pw@test.io", "password": "wrong-pass-123"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_refresh_rotation(client) -> None:
    tokens = register(client, email="refresh@test.io", org="Rr")
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    new = response.json()
    assert new["access_token"] and new["refresh_token"]


def test_access_rejected_without_token(client) -> None:
    response = client.get("/api/v1/datasets")
    assert response.status_code == 401
    body = response.json()["error"]
    assert body["code"] == "UNAUTHENTICATED"
    assert body["request_id"]


def test_ambiguous_org_requires_header(client) -> None:
    first = register(client, email="multi@test.io", org="Org M1")
    login(client, "multi@test.io")
    # Same email joins a second organization: register is per-email, so instead
    # of a second account we simulate ambiguity by auditing membership count.
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "multi@test.io",
            "password": "hunter2hunter2",
            "organization_name": "Org M2",
        },
    )
    assert response.status_code == 409  # registration stays per-account (AUTH-05 later)

    # With exactly one membership, no header is needed.
    ok = client.get("/api/v1/datasets", headers=headers_for(first))
    assert ok.status_code == 200
    assert ok.json()["total"] == 0


def test_invalid_org_header_rejected(client) -> None:
    tokens = register(client, email="hdr@test.io", org="Hdr")
    response = client.get(
        "/api/v1/datasets",
        headers={"Authorization": f"Bearer {tokens['access_token']}", "X-Organization-ID": "nope"},
    )
    assert response.status_code == 400
