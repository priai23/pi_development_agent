import pytest

from security import decrypt_secret, encrypt_secret, hash_password, verify_password


def test_passwords_are_argon2_hashed_and_verified():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2")
    assert verify_password(encoded, "correct horse battery staple")
    assert not verify_password(encoded, "wrong password")


def test_empty_password_is_rejected():
    with pytest.raises(ValueError):
        hash_password("")


def test_secret_decryption_fails_closed():
    assert decrypt_secret(encrypt_secret("private")) == "private"
    with pytest.raises(ValueError):
        decrypt_secret("plaintext")
import pytest

from config import Settings
import main
from fastapi.testclient import TestClient


def test_production_mode_requires_secure_transport():
    with pytest.raises(ValueError, match="SECURE_COOKIES"):
        Settings(
            encryption_key="test-key",
            production_mode=True,
            frontend_origin="https://app.example",
            public_base_url="https://api.example",
            erp_allowed_hosts="erp.example",
        )


def test_production_mode_rejects_wildcard_erp_hosts():
    with pytest.raises(ValueError, match="ERP_ALLOWED_HOSTS"):
        Settings(
            encryption_key="test-key",
            production_mode=True,
            secure_cookies=True,
            frontend_origin="https://app.example",
            public_base_url="https://api.example",
            erp_allowed_hosts="*",
        )


def test_security_headers_are_present():
    response = TestClient(main.app, base_url="http://localhost:8001").get("/auth/setup-status")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_login_rate_limit_returns_retry_after(monkeypatch):
    main._login_attempts.clear()
    client = TestClient(main.app, base_url="http://localhost:8001")
    for _ in range(main._LOGIN_MAX_ATTEMPTS):
        client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    response = client.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    assert response.status_code == 429
    assert response.headers["Retry-After"]
    main._login_attempts.clear()
