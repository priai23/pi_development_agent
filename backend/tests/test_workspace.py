from pathlib import Path
from uuid import uuid4

import pytest

from workspace import Workspace


def test_workspace_blocks_traversal_and_prefix_collision():
    workspace = Workspace(f"project-safe-{uuid4().hex}")
    with pytest.raises(ValueError):
        workspace.resolve("../project-safe-evil/secret.txt")
    with pytest.raises(ValueError):
        workspace.resolve("../../secret.txt")


def test_workspace_blocks_symlink_escape(tmp_path: Path):
    workspace = Workspace(f"project-link-{uuid4().hex}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        workspace.resolve("link/secret.txt")


def test_workspace_writes_atomically_and_limits_reads():
    workspace = Workspace(f"project-write-{uuid4().hex}")
    preview = workspace.write_preview("models/example.py", "value = 1\n")
    assert preview["path"] == "models/example.py"
    workspace.write_file("models/example.py", "value = 1\n")
    assert workspace.read_file("models/example.py") == "value = 1\n"
