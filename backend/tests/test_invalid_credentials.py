"""Authentication failures: every way a caller can present bad credentials must be refused,
and refused identically, so the response never helps an attacker learn which part was wrong."""

import os
from datetime import UTC, datetime, timedelta

import jwt
import pytest

EMAIL = "creds-victim@example.com"
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="module")
def registered(client) -> None:
    response = client.post("/api/users/createUser", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _login(client, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _token_claims(user_id: int = 1, **overrides) -> dict:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": "test-jti",
        "token_type": "access",
        "mfa": False,
    }
    claims.update(overrides)
    return claims


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- login ----------------------------------------------------------------------------------


def test_wrong_password_is_rejected(client, registered) -> None:
    response = _login(client, EMAIL, "wrong-password")
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert "access_token" not in response.text


def test_unknown_email_gets_the_same_response_as_a_wrong_password(client, registered) -> None:
    """If these differed, an attacker could enumerate which emails have accounts."""
    wrong_password = _login(client, EMAIL, "wrong-password")
    unknown_email = _login(client, "nobody-here@example.com", "wrong-password")
    assert unknown_email.status_code == wrong_password.status_code == 401
    assert unknown_email.json() == wrong_password.json()


def test_empty_password_is_rejected(client, registered) -> None:
    response = _login(client, EMAIL, "")
    assert response.status_code == 401


def test_password_is_case_sensitive(client, registered) -> None:
    assert _login(client, EMAIL, PASSWORD.upper()).status_code == 401


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": EMAIL},
        {"password": PASSWORD},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": EMAIL, "password": None},
    ],
)
def test_malformed_login_bodies_are_422(client, registered, payload) -> None:
    assert client.post("/api/auth/login", json=payload).status_code == 422


def test_sql_injection_in_login_fields_does_not_authenticate(client, registered) -> None:
    for email in ("' OR '1'='1", f"{EMAIL}' --"):
        assert _login(client, email, "x").status_code in (401, 422)
    assert _login(client, EMAIL, "' OR '1'='1").status_code == 401


# ---- tokens ---------------------------------------------------------------------------------


def test_missing_token_is_401(client, registered) -> None:
    response = client.get("/api/users/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_garbage_token_is_401(client, registered) -> None:
    assert client.get("/api/users/me", headers=_bearer("not.a.jwt")).status_code == 401
    assert client.get("/api/users/me", headers=_bearer("x")).status_code == 401


def test_wrong_auth_scheme_is_401(client, registered) -> None:
    token = _login(client, EMAIL, PASSWORD).json()["access_token"]
    response = client.get("/api/users/me", headers={"Authorization": f"Basic {token}"})
    assert response.status_code == 401


def test_tampered_signature_is_401(client, registered) -> None:
    token = _login(client, EMAIL, PASSWORD).json()["access_token"]
    head, payload, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert client.get("/api/users/me", headers=_bearer(f"{head}.{payload}.{flipped}")).status_code == 401


def test_token_signed_with_a_different_secret_is_401(client, registered) -> None:
    forged = jwt.encode(_token_claims(), "a-completely-different-secret-0123456789", algorithm="HS256")
    assert client.get("/api/users/me", headers=_bearer(forged)).status_code == 401


def test_expired_token_is_401(client, registered) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    expired = jwt.encode(_token_claims(exp=past), os.environ["JWT_SECRET"], algorithm="HS256")
    assert client.get("/api/users/me", headers=_bearer(expired)).status_code == 401


def test_a_refresh_type_token_cannot_be_used_as_an_access_token(client, registered) -> None:
    wrong_type = jwt.encode(_token_claims(token_type="refresh"), os.environ["JWT_SECRET"], algorithm="HS256")
    assert client.get("/api/users/me", headers=_bearer(wrong_type)).status_code == 401


def test_alg_none_token_is_401(client, registered) -> None:
    """Classic forgery: an unsigned token whose header claims no algorithm is needed."""
    unsigned = jwt.encode(_token_claims(), key=None, algorithm="none")
    assert client.get("/api/users/me", headers=_bearer(unsigned)).status_code == 401


def test_token_for_a_user_that_does_not_exist_is_401(client, registered) -> None:
    ghost = jwt.encode(_token_claims(user_id=987654321), os.environ["JWT_SECRET"], algorithm="HS256")
    assert client.get("/api/users/me", headers=_bearer(ghost)).status_code == 401


def test_token_missing_required_claims_is_401(client, registered) -> None:
    now = datetime.now(UTC)
    no_jti = jwt.encode(
        {"sub": "1", "iat": now, "exp": now + timedelta(minutes=5), "token_type": "access"},
        os.environ["JWT_SECRET"],
        algorithm="HS256",
    )
    assert client.get("/api/users/me", headers=_bearer(no_jti)).status_code == 401


def test_all_token_failures_return_one_identical_body(client, registered) -> None:
    """The 401 must not reveal whether the signature, expiry, or type was the problem."""
    secret = os.environ["JWT_SECRET"]
    attempts = [
        _bearer("garbage"),
        _bearer(jwt.encode(_token_claims(exp=datetime.now(UTC) - timedelta(minutes=1)), secret, algorithm="HS256")),
        _bearer(jwt.encode(_token_claims(token_type="refresh"), secret, algorithm="HS256")),
        _bearer(jwt.encode(_token_claims(), "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")),
        {},
    ]
    bodies = {client.get("/api/users/me", headers=headers).text for headers in attempts}
    assert len(bodies) == 1, f"failure responses leak distinguishing detail: {bodies}"


# ---- authorisation and credential changes ---------------------------------------------------


def test_normal_user_cannot_reach_admin_routes(client, new_user) -> None:
    _, headers = new_user("plain")
    assert client.get("/api/users", headers=headers).status_code == 403
    assert (
        client.post("/api/assets", headers=headers, json={"symbol": "NOPE", "company_name": "x"}).status_code == 403
    )


def test_cannot_register_the_same_email_twice(client, registered) -> None:
    response = client.post("/api/users/createUser", json={"email": EMAIL, "password": "another-password-1"})
    assert response.status_code == 400
    # and the original password must still be the one that works
    assert _login(client, EMAIL, PASSWORD).status_code == 200
    assert _login(client, EMAIL, "another-password-1").status_code == 401


def test_password_change_requires_the_correct_current_password(client, new_user) -> None:
    email, headers = new_user("pwchange")
    wrong = client.patch(
        "/api/users/me", headers=headers, json={"new_password": "brand-new-pass-1", "current_password": "nope"}
    )
    assert wrong.status_code == 403
    missing = client.patch("/api/users/me", headers=headers, json={"new_password": "brand-new-pass-1"})
    assert missing.status_code == 422
    # the old password is untouched
    assert _login(client, email, "fixture-password-123").status_code == 200


def test_old_password_stops_working_after_a_change(client, new_user) -> None:
    email, headers = new_user("pwrotate")
    changed = client.patch(
        "/api/users/me",
        headers=headers,
        json={"new_password": "rotated-password-9", "current_password": "fixture-password-123"},
    )
    assert changed.status_code == 200, changed.text
    assert _login(client, email, "fixture-password-123").status_code == 401
    assert _login(client, email, "rotated-password-9").status_code == 200
