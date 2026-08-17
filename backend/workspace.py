import difflib
import io
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from config import settings


class Workspace:
    EMPTY_TREE_REVISION = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    REVISION_RE = re.compile(r"^(?:HEAD|[0-9a-f]{7,40})$")
    def __init__(self, slug: str):
        root = settings.workspace_root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.root = (root / slug).resolve()
        if not self.root.is_relative_to(root):
            raise ValueError("Invalid workspace")
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").exists():
            self._git("init")
            self._git("config", "user.name", "Primacy ERP Agent")
            self._git("config", "user.email", "erp-agent@localhost")

    def resolve(self, relative_path: str, *, must_exist: bool = False) -> Path:
        candidate = (self.root / relative_path).resolve(strict=False)
        if not candidate.is_relative_to(self.root):
            raise ValueError("Path is outside the workspace")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(relative_path)
        return candidate

    def list_directory(self, path: str = "") -> list[str]:
        target = self.resolve(path, must_exist=True)
        if not target.is_dir():
            raise ValueError("Path is not a directory")
        return sorted(item.name for item in target.iterdir())

    def read_file(self, path: str) -> str:
        target = self.resolve(path, must_exist=True)
        if not target.is_file() or target.stat().st_size > settings.max_workspace_file_bytes:
            raise ValueError("File is invalid or too large")
        return target.read_text(encoding="utf-8")

    def write_preview(self, path: str, content: str) -> dict:
        if len(content.encode()) > settings.max_workspace_file_bytes:
            raise ValueError("File is too large")
        target = self.resolve(path)
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), fromfile=path, tofile=path))
        return {"path": path, "diff": diff[:20_000], "truncated": len(diff) > 20_000}

    def write_file(self, path: str, content: str) -> str:
        self.write_preview(path, content)
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        self.commit(f"Write {path}")
        return f"Wrote {path}"

    def _git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True,
            timeout=30, check=False,
        )
        if check and result.returncode:
            raise ValueError(result.stderr.strip() or "Git operation failed")
        return result.stdout.strip()

    def commit(self, message: str) -> str:
        self._git("add", "--all")
        if not self._git("status", "--porcelain"):
            return self._git("rev-parse", "HEAD", check=False)
        self._git("commit", "-m", message[:200])
        return self._git("rev-parse", "HEAD")

    def history(self, limit: int = 100) -> list[dict]:
        output = self._git("log", f"-{min(limit, 200)}", "--pretty=format:%H%x09%aI%x09%s", check=False)
        return [dict(zip(("hash", "created_at", "message"), line.split("\t", 2))) for line in output.splitlines() if line]

    def head(self) -> str | None:
        return self._git("rev-parse", "--verify", "HEAD", check=False) or None

    def diff_result(self, old: str, new: str = "HEAD") -> tuple[str, bool]:
        for revision in (old, new):
            if not self.REVISION_RE.fullmatch(revision):
                raise ValueError("Invalid revision")
        value = self._git("diff", "--no-ext-diff", old, new)
        return value[:100_000], len(value) > 100_000

    def diff(self, old: str, new: str = "HEAD") -> str:
        return self.diff_result(old, new)[0]

    def restore(self, revision: str) -> str:
        if not self.REVISION_RE.fullmatch(revision):
            raise ValueError("Invalid revision")
        self._git("restore", "--source", revision, "--", ".")
        return self.commit(f"Restore workspace to {revision[:12]}")

    def tree(self, path: str = "") -> list[dict]:
        target = self.resolve(path, must_exist=True)
        if target.is_file():
            return [{"name": target.name, "path": str(target.relative_to(self.root)), "type": "file", "size": target.stat().st_size}]
        return [
            {"name": item.name, "path": str(item.relative_to(self.root)), "type": "directory" if item.is_dir() else "file", "size": None if item.is_dir() else item.stat().st_size}
            for item in sorted(target.iterdir()) if item.name not in {".git", "__pycache__", ".pytest_cache"}
        ]

    def archive(self, path: str = "") -> bytes:
        target = self.resolve(path, must_exist=True)
        files = [target] if target.is_file() else list(target.rglob("*"))
        output = io.BytesIO()
        total = 0
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in files:
                relative = item.relative_to(self.root)
                if not item.is_file() or any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts):
                    continue
                total += item.stat().st_size
                if total > settings.max_workspace_file_bytes * 20:
                    raise ValueError("Workspace archive is too large")
                archive.write(item, relative)
        return output.getvalue()
