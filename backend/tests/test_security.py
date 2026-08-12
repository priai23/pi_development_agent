import pytest

from security import decrypt_secret, encrypt_secret, hash_password, verify_password


def test_passwords_are_argon2_hashed_and_verified():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2")
    assert verify_password(encoded, "correct horse battery staple")
    assert not verify_password(encoded, "wrong password")


def test_short_password_is_rejected():
    with pytest.raises(ValueError):
        hash_password("too-short")


def test_secret_decryption_fails_closed():
    assert decrypt_secret(encrypt_secret("private")) == "private"
    with pytest.raises(ValueError):
        decrypt_secret("plaintext")
