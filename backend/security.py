from cryptography.fernet import Fernet
from config import settings

if not settings.encryption_key:
    raise ValueError("ENCRYPTION_KEY is not set in the environment.")

fernet = Fernet(settings.encryption_key.encode())

def encrypt_password(password: str) -> str:
    """Encrypts a plaintext password."""
    if not password:
        return password
    return fernet.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password: str) -> str:
    """Decrypts an encrypted password."""
    if not encrypted_password:
        return encrypted_password
    try:
        return fernet.decrypt(encrypted_password.encode()).decode()
    except Exception:
        # If decryption fails (e.g. it was an old plaintext password in DB)
        # return it as is for backward compatibility or raise error.
        # For security, we should ideally fail or handle it, but for smooth transition:
        return encrypted_password
