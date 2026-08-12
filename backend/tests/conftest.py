import os
from pathlib import Path

from cryptography.fernet import Fernet


os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("WORKSPACE_ROOT", str(Path(__file__).parent / ".workspaces"))
