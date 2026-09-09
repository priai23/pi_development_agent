"""
tests/test_workspace_isolation.py — Unit tests for workspace security and isolation.

Ensures:
- Absolute paths and traversal escapes ('../') are rejected
- Workspaces cannot read or write outside their assigned slug directory
- Max file size limits are enforced
- Git operations stay contained within the workspace
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from config import settings
from workspace import Workspace


@pytest.fixture
def temp_workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_root:
        monkeypatch.setattr(settings, "workspace_root", Path(tmp_root))
        ws = Workspace("test-workspace-slug", create=True)
        yield ws


def test_path_traversal_rejected(temp_workspace):
    with pytest.raises(ValueError, match="outside the workspace"):
        temp_workspace.resolve("../outside.txt")

    with pytest.raises(ValueError, match="outside the workspace"):
        temp_workspace.resolve("../../etc/passwd")


def test_write_and_read_isolation(temp_workspace):
    temp_workspace.write_file("sub/dir/test.txt", "hello secure world")
    content = temp_workspace.read_file("sub/dir/test.txt")
    assert content == "hello secure world"
    assert (temp_workspace.root / "sub" / "dir" / "test.txt").exists()


def test_max_file_size_enforced(temp_workspace, monkeypatch):
    monkeypatch.setattr(settings, "max_workspace_file_bytes", 100)
    large_text = "x" * 500
    with pytest.raises(ValueError, match="too large"):
        temp_workspace.write_file("large.txt", large_text)


def test_git_operations_contained(temp_workspace):
    temp_workspace.write_file("module/test.py", "# initial code")
    head = temp_workspace.head()
    assert head is not None

    status = temp_workspace.status()
    assert status["clean"] is True
    assert status["branch"] in ("master", "main")

    branch_msg = temp_workspace.create_branch("feature/odoo19")
    assert "feature/odoo19" in branch_msg
    assert "feature/odoo19" in temp_workspace.branches()


def test_diff_preview(temp_workspace):
    temp_workspace.write_file("models/partner.py", "# v1")
    head = temp_workspace.head()
    # Now modify directly without committing
    target = temp_workspace.resolve("models/partner.py")
    target.write_text("# v2 modified", encoding="utf-8")

    preview = temp_workspace.diff_preview(target="HEAD")
    assert "models/partner.py" in preview["changed_files"]
    assert "v2 modified" in preview["diff"]
