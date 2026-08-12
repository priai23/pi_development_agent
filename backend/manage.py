import argparse
import getpass

from sqlalchemy.orm import Session

import models
from database import SessionLocal
from security import hash_password


def create_admin(db: Session, email: str) -> None:
    normalized = email.strip().lower()
    if db.query(models.User).filter(models.User.email == normalized).first():
        raise SystemExit("A user with that email already exists")
    password = getpass.getpass("Password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    db.add(models.User(email=normalized, password_hash=hash_password(password), role="admin"))
    db.commit()
    print(f"Created administrator {normalized}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ERP Agent administration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin", help="Create an administrator interactively")
    create.add_argument("email")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.command == "create-admin":
            create_admin(db, args.email)


if __name__ == "__main__":
    main()
