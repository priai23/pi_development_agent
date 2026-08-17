import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
import models
import worker


@pytest.fixture
def test_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_worker_status_missing_lease(test_db):
    status, last_seen, age, queue_depth = main.get_worker_status(test_db)
    assert status == "offline"
    assert last_seen is None
    assert age is None
    assert queue_depth == 0


def test_worker_status_fresh_lease(test_db):
    now = datetime.now(timezone.utc)
    test_db.add(models.Setting(key="worker_last_seen_at", value=now.isoformat()))
    test_db.commit()

    status, last_seen, age, queue_depth = main.get_worker_status(test_db)
    assert status == "healthy"
    assert last_seen == now.isoformat()
    assert age is not None and age <= 2.0
    assert queue_depth == 0


def test_worker_status_stale_lease(test_db):
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=20)
    test_db.add(models.Setting(key="worker_last_seen_at", value=stale_time.isoformat()))
    test_db.commit()

    status, last_seen, age, queue_depth = main.get_worker_status(test_db)
    assert status == "offline"
    assert age is not None and age >= 19.0


def test_health_endpoint(test_db):
    now = datetime.now(timezone.utc)
    test_db.add(models.Setting(key="worker_last_seen_at", value=now.isoformat()))
    test_db.add(models.OutboxEvent(event_type="test", aggregate_id="agg-1", payload={}))
    test_db.commit()

    main.app.dependency_overrides[main.get_db] = lambda: test_db
    try:
        client = TestClient(main.app, base_url="http://localhost:8001")
        res = client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["api"] == "healthy"
        assert data["database"] == "healthy"
        assert data["worker"] == "healthy"
        assert data["queue_depth"] == 1
        assert data["worker_last_seen_at"] == now.isoformat()
    finally:
        main.app.dependency_overrides.pop(main.get_db, None)


def test_retry_run_preserves_models_and_revision(test_db):
    user = models.User(email="admin@example.com", password_hash="hash", role="admin")
    org = models.Organization(name="Org")
    test_db.add_all([user, org])
    test_db.flush()
    test_db.add(models.OrganizationMembership(user_id=user.id, organization_id=org.id, is_approver=True))
    project = models.Project(name="Proj", organization_id=org.id, created_by_id=user.id, workspace_slug="proj")
    test_db.add(project)
    test_db.flush()

    interrupted_run = models.AgentRun(
        project_id=project.id,
        requested_by_id=user.id,
        status="interrupted",
        prompt="Build Odoo feature",
        thread_id="thread-old",
        planner_model="gpt-4o",
        fallback_model="claude-3-5",
        workspace_base_revision="git-rev-123",
        retryable=True,
    )
    test_db.add(interrupted_run)
    test_db.commit()

    retried = main.retry_run(interrupted_run.id, user, test_db)
    assert retried.status == "queued"
    assert retried.retry_of_id == interrupted_run.id
    assert retried.prompt == "Build Odoo feature"
    assert retried.planner_model == "gpt-4o"
    assert retried.fallback_model == "claude-3-5"
    assert retried.workspace_base_revision == "git-rev-123"

    # Check run.queued event was emitted
    event = test_db.query(models.ToolEvent).filter(
        models.ToolEvent.run_id == retried.id,
        models.ToolEvent.event_type == "run.queued",
    ).first()
    assert event is not None
    assert event.payload["support_id"] == retried.support_id


@pytest.mark.asyncio
async def test_worker_availability_monitor_interrupts_stale_runs(test_db):
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=40)
    test_db.add(models.Setting(key="worker_last_seen_at", value=stale_time.isoformat()))

    user = models.User(email="dev@example.com", password_hash="hash", role="admin")
    org = models.Organization(name="Org2")
    test_db.add_all([user, org])
    test_db.flush()
    project = models.Project(name="Proj2", organization_id=org.id, created_by_id=user.id, workspace_slug="proj2")
    test_db.add(project)
    test_db.flush()

    stale_run = models.AgentRun(
        project_id=project.id,
        requested_by_id=user.id,
        status="queued",
        prompt="Do work",
        thread_id="t1",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=35),
    )
    test_db.add(stale_run)
    test_db.commit()

    with patch("main.SessionLocal", return_value=test_db):
        cutoff_30s = datetime.now(timezone.utc) - timedelta(seconds=30)
        stale_queued = test_db.query(models.AgentRun).filter(
            models.AgentRun.status == "queued",
            models.AgentRun.created_at < cutoff_30s,
        ).all()
        for r in stale_queued:
            r.status = "interrupted"
            r.error_category = "WorkerUnavailable"
            r.error_message = "Background worker process is unavailable. Check worker process health or retry."
            r.retryable = True
            r.finished_at = datetime.now(timezone.utc)
            worker.emit(test_db, r.id, "run.interrupted", {
                "category": "WorkerUnavailable",
                "message": r.error_message,
                "retryable": True,
                "support_id": r.support_id,
                "task_id": None,
                "tool": None,
            })
        test_db.commit()

    test_db.refresh(stale_run)
    assert stale_run.status == "interrupted"
    assert stale_run.error_category == "WorkerUnavailable"
    assert stale_run.retryable is True

    interrupted_event = test_db.query(models.ToolEvent).filter(
        models.ToolEvent.run_id == stale_run.id,
        models.ToolEvent.event_type == "run.interrupted",
    ).first()
    assert interrupted_event is not None
    assert interrupted_event.payload["category"] == "WorkerUnavailable"
    assert interrupted_event.payload["retryable"] is True
