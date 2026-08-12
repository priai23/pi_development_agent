import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from config import settings


_fernet = Fernet(settings.encryption_key.encode())
_passwords = PasswordHasher()


def encrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("Secret cannot be empty")
    return _fernet.encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("Encrypted secret is missing")
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted") from exc


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    return _passwords.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _passwords.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Compatibility names for callers while keeping fail-closed behavior.
encrypt_password = encrypt_secret
decrypt_password = decrypt_secret
