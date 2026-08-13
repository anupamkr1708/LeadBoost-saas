"""
Regression tests for POST /api/v2/refresh.

Found during the frontend integration audit: the frontend
(src/lib/api-client.ts) implements a standard silent-refresh-on-401 flow --
on any 401 it POSTs the stored refresh_token to /api/v2/refresh and retries
the original request with the new access token, only logging the user out
if that call itself fails.

That call could never succeed. /refresh called verify_token() on the
refresh token, but verify_token() (core/infrastructure/auth/security.py --
also used by get_current_user() to gate every protected endpoint)
unconditionally requires token_type == "access". Every refresh token is
minted by create_refresh_token() with {"type": "refresh"}, so the type
check always failed, was caught by /refresh's own broad `except Exception`,
and came back as 401 "Invalid refresh token" -- for every user, every
time. In practice this meant every session was hard-capped at the
30-minute access-token lifetime: the frontend's own refresh attempt would
itself 401, and the interceptor would redirect to /login, regardless of
how fresh the user's refresh token was.

No prior test exercised /refresh at all -- this file is net-new coverage,
not a change to an existing suite.
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Refresh"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    return body["access_token"], body["refresh_token"]


def test_refresh_token_issues_a_new_working_access_token(client):
    """The exact flow the frontend relies on: POST the refresh_token from
    /login to /refresh and get back a new, usable access token."""
    access_token, refresh_token = _register_and_login(client, "refresh_flow@example.com")
    assert refresh_token

    r = client.post("/api/v2/refresh", params={"refresh_token": refresh_token})
    assert r.status_code == 200, r.text
    new_access_token = r.json()["access_token"]
    assert new_access_token

    # The new access token must actually work against a protected route.
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {new_access_token}"})
    assert me.status_code == 200, me.text


def test_refresh_rejects_an_access_token_used_as_a_refresh_token(client):
    """The other half of the fix: /refresh must still reject an access
    token presented in place of a refresh token (someone forwarding the
    wrong token, or a client bug) -- verify_refresh_token() must not
    become the mirror-image bug of accepting anything."""
    access_token, _refresh_token = _register_and_login(client, "refresh_flow_wrong_type@example.com")

    r = client.post("/api/v2/refresh", params={"refresh_token": access_token})
    assert r.status_code == 401


def test_refresh_rejects_garbage_token(client):
    r = client.post("/api/v2/refresh", params={"refresh_token": "not.a.real.jwt"})
    assert r.status_code == 401
