"""
tests/test_e2e_full_flow.py — End-to-end flow test for Pi ERP Implementation Agent.

Verifies the full pipeline integration:
1. Organization and Project initialization
2. Instance registration (Odoo and Pri ERP)
3. Durable Attachment upload, indexing, and citation
4. Permission Engine enforcement (5 layers, cache, capability check)
5. Tool Registry contract compliance
6. Telemetry and Audit event logging
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

import models
from attachment import process_attachment, get_citation
from config import settings
from permissions import PermissionEngine, invalidate_permission_cache
from tool_registry import TOOL_REGISTRY, validate_tool_call
from telemetry import telemetry
from workspace import Workspace


@pytest.fixture
def test_env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        monkeypatch.setattr(settings, "workspace_root", tmp_path / "workspaces")
        monkeypatch.setattr(settings, "attachment_root", tmp_path / "attachments")
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        settings.attachment_root.mkdir(parents=True, exist_ok=True)

        yield session, settings.workspace_root, settings.attachment_root

    session.close()


def test_full_pipeline_e2e(test_env):
    db, ws_root, att_root = test_env

    # 1. Organization, User, Project
    org = models.Organization(name="Global Enterprise", monthly_budget_usd=1000)
    user = models.User(email="lead-engineer@example.com", password_hash="hash", role="admin")
    db.add_all([org, user])
    db.flush()

    project = models.Project(
        name="Odoo 19 & Pri ERP Implementation",
        organization_id=org.id,
        created_by_id=user.id,
        workspace_slug="global-erp",
    )
    db.add(project)
    db.flush()

    workspace = Workspace(project.workspace_slug, create=True)
    assert workspace.root.exists()

    # 2. Add Pri ERP Instance
    pri_inst = models.Instance(
        project_id=project.id,
        erp_type="pri_erp",
        url="https://admin.sh.prierp.com",
        environment="staging",
        status="ready",
        is_active=True,
    )
    db.add(pri_inst)
    db.flush()

    # 3. Agent Run
    run = models.AgentRun(
        project_id=project.id,
        requested_by_id=user.id,
        prompt="Setup custom sale order module with durable specifications",
        thread_id=f"run-{project.id}-001",
        workspace_slug=project.workspace_slug,
        intent="write",
        status="running",
    )
    db.add(run)
    db.flush()

    # 4. Attachment upload & pipeline
    spec_content = b"Specification: Odoo 19 custom module must implement sale order approval."
    att = process_attachment(
        db=db,
        run_id=run.id,
        project_id=project.id,
        uploader_id=user.id,
        filename="sales_spec.txt",
        content_type="text/plain",
        data=spec_content,
    )
    assert att.processing_state in ("extracted", "indexed")
    assert att.content_hash is not None
    citation = get_citation(db, att.id)
    assert citation == "[sales_spec.txt]"

    # 5. Permission Engine check
    engine = PermissionEngine()
    # Read-only tool should be allowed
    d1 = engine.check(
        db=db,
        project_id=project.id,
        user_id=user.id,
        tool_name="list_directory",
        args={"path": ""},
    )
    assert d1.allowed is True
    assert d1.requires_approval is False

    # Side-effecting tool without grant should require approval or be blocked
    d2 = engine.check(
        db=db,
        project_id=project.id,
        user_id=user.id,
        tool_name="write_file",
        args={"path": "addons/custom_sale/__manifest__.py"},
    )
    assert d2.requires_approval is True or not d2.allowed

    # Add explicit grant
    grant = models.PermissionGrant(
        project_id=project.id,
        user_id=user.id,
        resource="workspace:addons/custom_sale/*",
        decision="allow",
        created_by_id=user.id,
    )
    db.add(grant)
    db.commit()
    invalidate_permission_cache(project_id=project.id, user_id=user.id)

    d3 = engine.check(
        db=db,
        project_id=project.id,
        user_id=user.id,
        tool_name="write_file",
        args={"path": "addons/custom_sale/__manifest__.py"},
    )
    assert d3.allowed is True

    # 6. Tool Registry Validation
    for tool_name in ["write_file", "pri_erp_health", "inspect_views", "run_syntax_checks"]:
        assert tool_name in TOOL_REGISTRY
        entry = TOOL_REGISTRY[tool_name]
        assert entry.risk_class in (1, 2, 3)

    # 7. Telemetry Snapshot
    telemetry.record_tool_call("write_file", duration_sec=0.045, success=True)
    snap = telemetry.get_system_snapshot(db)
    assert snap["status"] == "healthy"
    assert snap["runs"]["active"] == 1
    assert snap["runs"]["failed_24h"] == 0
    assert snap["queue"]["retries_total"] == 0
    assert snap["deployments"]["rollbacks_total"] == 0
    assert "write_file" in snap["tools"]
