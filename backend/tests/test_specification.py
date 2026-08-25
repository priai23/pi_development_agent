"""Tests for the specification compiler (backend/specification.py).

Tests compile_specification and get_specification_summary against synthetic
and indexed fixture symbols.
"""
from __future__ import annotations

import pytest
from database import SessionLocal
import models
from pathlib import Path
from source_indexer import index_addon
from specification import (
    CHECK_GATED_TASKS,
    TASK_VALIDATE_AND_VERIFY,
    VALID_CHECK_KINDS,
    compile_specification,
    get_specification_summary,
)
from worker import required_acceptance_failures

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "test_addon"


from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_valid_check_kinds():
    expected_kinds = {
        "source_reuse",
        "module_install",
        "module_upgrade",
        "artifact_digest",
        "model_field",
        "xml_id",
        "view_load",
        "acl",
        "record_rule",
        "python_test",
        "business_scenario",
    }
    assert VALID_CHECK_KINDS == expected_kinds


def test_compile_specification_basic(db_session):
    prompt = "Create a leave request management module with employee_id, date_from, date_to, and state fields."
    run_id = f"test-run-{pytest.importorskip('uuid').uuid4().hex[:8]}"

    # Insert a minimal user and project to satisfy foreign keys if needed
    user = db_session.query(models.User).first()
    if not user:
        user = models.User(email="test@example.com", password_hash="hash", role="admin")
        db_session.add(user)
        db_session.flush()

    project = db_session.query(models.Project).first()
    if not project:
        org = db_session.query(models.Organization).first()
        if not org:
            org = models.Organization(name="Test Org")
            db_session.add(org)
            db_session.flush()
        project = models.Project(organization_id=org.id, name="Test Project", workspace_slug="test_slug", created_by_id=user.id)
        db_session.add(project)
        db_session.flush()

    run = models.AgentRun(
        id=run_id,
        project_id=project.id,
        requested_by_id=user.id,
        prompt=prompt,
        thread_id=f"thread:{run_id}",
    )
    db_session.add(run)
    db_session.flush()

    res = compile_specification(
        run_id=run.id,
        project_id=project.id,
        snapshot_id=None,
        prompt=prompt,
        module_name="primacy_leave_request",
        is_upgrade=False,
        snapshot_symbols=[],
        db=db_session,
    )
    db_session.commit()

    assert res.run_id == run_id
    assert len(res.requirements) >= 2
    assert len(res.check_ids) >= 2
    assert res.digest is not None

    summary = get_specification_summary(run_id, db_session)
    assert summary is not None
    assert summary["run_id"] == run_id
    assert len(summary["acceptance_checks"]) == len(res.check_ids)
    for c in summary["acceptance_checks"]:
        assert c["kind"] in VALID_CHECK_KINDS
        assert c["status"] == "pending"
        assert not c["task_id"].startswith("run:")

    task_ids = {c["task_id"] for c in summary["acceptance_checks"]}
    assert TASK_VALIDATE_AND_VERIFY in task_ids
    assert task_ids <= CHECK_GATED_TASKS


def test_acceptance_gate_fails_closed_and_requires_passed_checks(db_session):
    assert required_acceptance_failures(db_session, "missing-run", TASK_VALIDATE_AND_VERIFY) == [
        "AcceptanceCheckMissing: no required checks were compiled for validate_and_verify"
    ]

    check = models.AcceptanceCheck(
        run_id="run-1",
        task_id=TASK_VALIDATE_AND_VERIFY,
        kind="module_install",
        spec_target={"module": "sample"},
        required=True,
        status="pending",
    )
    db_session.add(check)
    db_session.flush()
    assert required_acceptance_failures(db_session, "run-1", TASK_VALIDATE_AND_VERIFY)

    check.status = "passed"
    db_session.flush()
    assert required_acceptance_failures(db_session, "run-1", TASK_VALIDATE_AND_VERIFY) == []


def test_compile_specification_with_indexed_symbols(db_session):
    addon_dir = FIXTURE_ROOT / "primacy_test_fixture"
    snapshot_id = f"snap-{pytest.importorskip('uuid').uuid4().hex[:8]}"
    symbols = index_addon(addon_dir, snapshot_id)

    prompt = "Add leave approval workflow and extra date fields to primacy.leave.request model."
    run_id = f"test-run-{pytest.importorskip('uuid').uuid4().hex[:8]}"

    user = db_session.query(models.User).first()
    if not user:
        user = models.User(email="test@example.com", password_hash="hash", role="admin")
        db_session.add(user)
        db_session.flush()

    project = db_session.query(models.Project).first()
    if not project:
        org = models.Organization(name="Test Org")
        db_session.add(org)
        db_session.flush()
        project = models.Project(organization_id=org.id, name="Test Project", workspace_slug="test_slug", created_by_id=user.id)
        db_session.add(project)
        db_session.flush()

    run = models.AgentRun(
        id=run_id,
        project_id=project.id,
        requested_by_id=user.id,
        prompt=prompt,
        thread_id=f"thread:{run_id}",
    )
    db_session.add(run)
    
    # Ensure an instance exists
    instance = db_session.query(models.Instance).filter(models.Instance.project_id == project.id).first()
    if not instance:
        instance = models.Instance(
            project_id=project.id,
            url="http://localhost:8069",
            erp_type="odoo",
        )
        db_session.add(instance)
        db_session.flush()

    snapshot = models.SourceSnapshot(
        id=snapshot_id,
        instance_id=instance.id,
        odoo_version="19.0",
        odoo_edition="community",
        fingerprint="dummy_fp",
        status="indexed",
    )
    db_session.add(snapshot)
    db_session.flush()

    # Convert symbol dataclasses to dummy records with attrs
    class SymProxy:
        def __init__(self, s):
            self.kind = s.kind
            self.name = s.name
            self.model = s.model
            self.payload = s.payload

    proxies = [SymProxy(s) for s in symbols]

    res = compile_specification(
        run_id=run.id,
        project_id=project.id,
        snapshot_id=snapshot_id,
        prompt=prompt,
        module_name="primacy_leave_request",
        is_upgrade=True,
        snapshot_symbols=proxies,
        db=db_session,
    )
    db_session.commit()

    assert any("Upgrade module" in req.title for req in res.requirements)
    summary = get_specification_summary(run_id, db_session)
    assert summary is not None
    kinds = {c["kind"] for c in summary["acceptance_checks"]}
    assert "module_upgrade" in kinds
    assert "business_scenario" in kinds
