from fastapi.testclient import TestClient

from backend.auth import create_session, hash_password, read_session, verify_password
from backend.config import Settings
from backend.main import app, settings as app_settings


def test_scrypt_hash_never_contains_plain_password():
    encoded = hash_password("a-strong-password")
    assert "a-strong-password" not in encoded
    assert verify_password("a-strong-password", encoded)
    assert not verify_password("wrong", encoded)


def test_initial_shared_login_is_admin():
    settings = Settings(_env_file=None)
    assert settings.app_auth_username == "admin"
    assert verify_password("admin", settings.app_auth_password_hash)
    assert not verify_password("smerchan", settings.app_auth_password_hash)


def test_signed_session_rejects_tampering():
    settings = Settings(app_auth_username="user", app_session_secret="secret", app_session_hours=1)
    token = create_session("user", settings)
    assert read_session(token, settings) == "user"
    assert read_session(token + "changed", settings) is None


def test_login_cookie_session_and_logout(monkeypatch):
    monkeypatch.setattr(app_settings, "app_auth_username", "smerchan")
    monkeypatch.setattr(app_settings, "app_auth_password_hash", hash_password("unit-test-password"))
    monkeypatch.setattr(app_settings, "app_session_secret", "test-session-secret")
    monkeypatch.setattr(app_settings, "app_cookie_secure", True)
    client = TestClient(app, base_url="https://testserver")

    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/auth/login", json={"username": "smerchan", "password": "wrong"}).status_code == 401

    login = client.post("/api/auth/login", json={"username": "smerchan", "password": "unit-test-password"})
    assert login.status_code == 200
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert client.get("/api/auth/session").json()["username"] == "Local: smerchan"

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/session").status_code == 401


def test_openapi_is_protected():
    client = TestClient(app, base_url="https://testserver")
    assert client.get("/openapi.json").status_code == 401


def test_cloudera_remote_user_has_priority_over_local_login():
    client = TestClient(app, base_url="https://testserver", headers={"REMOTE-USER": "cml-admin"})
    session = client.get("/api/auth/session")
    assert session.status_code == 200
    assert session.json() == {
        "authenticated": True,
        "username": "Cloudera: cml-admin",
        "source": "cloudera",
    }
    assert client.get("/api/config").status_code == 200
