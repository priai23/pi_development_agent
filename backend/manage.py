import argparse
import asyncio
import getpass
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from sqlalchemy.orm import Session

import models
from database import SessionLocal
from security import hash_password


def create_admin(db: Session, email: str) -> None:
    normalized = email.strip().lower()
    if db.query(models.User).filter(models.User.email == normalized).first():
        raise SystemExit("A user with that email already exists")
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    db.add(models.User(email=normalized, password_hash=hash_password(password), role="admin"))
    db.commit()
    print(f"Created administrator {normalized}")


def run_worker() -> None:
    from worker import serve
    asyncio.run(serve())


def _terminate_proc(proc: subprocess.Popen | None, timeout: float = 5.0) -> None:
    """Gracefully terminate a child process, escalating to kill if it hangs."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass
    except Exception:
        pass


def _runtime_python(backend_dir: str) -> str:
    """Use the project environment when manage.py was invoked by system Python."""
    project_python = Path(backend_dir) / ".venv" / "bin" / "python"
    if project_python.is_file() and Path(sys.executable).resolve() != project_python.resolve():
        return str(project_python)
    return sys.executable


def run_supervisor(host: str | None = None, port: int = 8001, reload: bool = False, loop_delay: float = 0.5) -> None:
    """Run API and worker; bind to loopback unless API_BIND_HOST is explicit."""
    host = host or os.environ.get("API_BIND_HOST", "127.0.0.1")
    backend_dir = str(Path(__file__).resolve().parent)
    runtime_python = _runtime_python(backend_dir)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    api_cmd = [runtime_python, "-m", "uvicorn", "main:app", "--host", host, "--port", str(port)]
    if reload:
        api_cmd.extend([
            "--reload",
            "--reload-exclude", "*.pyc",
            "--reload-exclude", "*__pycache__*",
            "--reload-exclude", "*.pytest_cache*",
            "--reload-exclude", "*tests*",
            "--reload-exclude", "*workspaces*",
        ])

    worker_cmd = [runtime_python, "worker.py"]

    api_proc: subprocess.Popen | None = None
    worker_proc: subprocess.Popen | None = None
    shutting_down = False

    def _shutdown(signum, _frame):
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print(f"\nReceived signal {signum}, shutting down supervised API and worker processes...")
        _terminate_proc(api_proc, timeout=5.0)
        _terminate_proc(worker_proc, timeout=5.0)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"Starting supervised stack (API on http://{host}:{port}, Worker in background, cwd={backend_dir})...")
    api_proc = subprocess.Popen(api_cmd, cwd=backend_dir, env=env)
    worker_proc = subprocess.Popen(worker_cmd, cwd=backend_dir, env=env)

    try:
        while not shutting_down:
            api_code = api_proc.poll()
            if api_code is not None:
                print(f"API process exited with code {api_code}; stopping background worker...")
                _terminate_proc(worker_proc, timeout=5.0)
                sys.exit(api_code)

            worker_code = worker_proc.poll()
            if worker_code is not None and not shutting_down:
                print(f"Worker process exited with code {worker_code}; restarting worker in 3s...")
                time.sleep(3.0)
                if shutting_down:
                    break
                if api_proc.poll() is not None:
                    print("API died during worker restart delay; aborting...")
                    sys.exit(api_proc.poll() or 1)
                worker_proc = subprocess.Popen(worker_cmd, cwd=backend_dir, env=env)

            time.sleep(loop_delay)
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)


def main() -> None:
    parser = argparse.ArgumentParser(description="ERP Agent administration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-admin", help="Create an administrator interactively")
    create.add_argument("email")

    subparsers.add_parser("worker", help="Run the background worker process")

    serve_parser = subparsers.add_parser("serve", help="Run supervised API + worker processes")
    serve_parser.add_argument("--host", default=None, help="API host (default: API_BIND_HOST or 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8001, help="API port (default: 8001)")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development (Uvicorn only)")

    args = parser.parse_args()

    if args.command == "create-admin":
        with SessionLocal() as db:
            create_admin(db, args.email)
    elif args.command == "worker":
        run_worker()
    elif args.command == "serve":
        run_supervisor(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
