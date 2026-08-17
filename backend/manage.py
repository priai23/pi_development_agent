import argparse
import asyncio
import getpass
import os
import signal
import subprocess
import sys

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


def run_worker() -> None:
    from worker import serve
    asyncio.run(serve())


def run_supervisor(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Supervised single-command entry point running API + worker."""
    env = os.environ.copy()
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", host, "--port", str(port)],
        env=env,
    )
    worker_proc = subprocess.Popen(
        [sys.executable, "worker.py"],
        env=env,
    )

    def _shutdown(signum, frame):
        print(f"\nReceived signal {signum}, shutting down supervised processes...")
        api_proc.terminate()
        worker_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()
        try:
            worker_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker_proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            api_code = api_proc.poll()
            worker_code = worker_proc.poll()
            if api_code is not None:
                print(f"API process exited with code {api_code}; stopping worker...")
                worker_proc.terminate()
                break
            if worker_code is not None:
                print(f"Worker process exited with code {worker_code}; restarting worker in 3s...")
                worker_proc = subprocess.Popen([sys.executable, "worker.py"], env=env)
            asyncio.run(asyncio.sleep(1))
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="ERP Agent administration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-admin", help="Create an administrator interactively")
    create.add_argument("email")

    subparsers.add_parser("worker", help="Run the background worker process")

    serve_parser = subparsers.add_parser("serve", help="Run supervised API + worker processes")
    serve_parser.add_argument("--host", default="0.0.0.0", help="API host (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8000, help="API port (default: 8000)")

    args = parser.parse_args()

    if args.command == "create-admin":
        with SessionLocal() as db:
            create_admin(db, args.email)
    elif args.command == "worker":
        run_worker()
    elif args.command == "serve":
        run_supervisor(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
