import asyncio
import socket
import ssl
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import schemas
import main
from config import settings


@pytest.fixture
def sync_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def setup_data(sync_db):
    org = models.Organization(id=1, name="Test Org", monthly_budget_usd=1000.0)
    sync_db.add(org)
    user = models.User(id=1, email="test@example.com", password_hash="dummy", role="admin")
    sync_db.add(user)
    membership = models.OrganizationMembership(organization_id=org.id, user_id=user.id, role="admin", is_approver=True)
    sync_db.add(membership)
    project = models.Project(id=1, name="Test Project", organization_id=org.id, created_by_id=user.id, workspace_slug="test_project_slug")
    sync_db.add(project)
    sync_db.commit()
    return user, project


def test_action_approval_transitions_run_status_to_queued(sync_db, setup_data):
    user, project = setup_data
    run = models.AgentRun(
        id="run_test_123",
        project_id=project.id,
        requested_by_id=user.id,
        prompt="Build test module",
        thread_id="thread_test_123",
        status="awaiting_approval",
        support_id="SUPP12345",
    )
    sync_db.add(run)
    sync_db.commit()

    action = models.PendingAction(
        id="act_test_123",
        project_id=project.id,
        run_id=run.id,
        thread_id=run.thread_id,
        tool_name="write_file",
        tool_call_id="call_123",
        risk_class="B",
        arguments={"path": "test.py", "content": "print('hello')"},
        preview={"summary": "write test.py"},
        status="pending",
        requested_by_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    sync_db.add(action)
    sync_db.commit()

    payload = schemas.ActionDecision(decision="approve", auto_approve_task=False)
    updated_run = main.decide_run_action(action.id, payload, user=user, db=sync_db)

    assert updated_run.status == "queued"
    sync_db.refresh(action)
    assert action.status == "approved"

    # Verify outbox event enqueued
    outbox = sync_db.query(models.OutboxEvent).filter(
        models.OutboxEvent.aggregate_id == run.id,
        models.OutboxEvent.event_type == "action.resume",
    ).first()
    assert outbox is not None
    assert outbox.payload["decision"] == "approve"


def test_action_rejection_transitions_run_status_to_queued(sync_db, setup_data):
    user, project = setup_data
    run = models.AgentRun(
        id="run_test_reject",
        project_id=project.id,
        requested_by_id=user.id,
        prompt="Build test module",
        thread_id="thread_test_reject",
        status="awaiting_approval",
        support_id="SUPP12345",
    )
    sync_db.add(run)
    sync_db.commit()

    action = models.PendingAction(
        id="act_test_reject",
        project_id=project.id,
        run_id=run.id,
        thread_id=run.thread_id,
        tool_name="write_file",
        tool_call_id="call_rej",
        risk_class="B",
        arguments={"path": "test.py", "content": "print('bad')"},
        preview={"summary": "write test.py"},
        status="pending",
        requested_by_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    sync_db.add(action)
    sync_db.commit()

    payload = schemas.ActionDecision(decision="reject", auto_approve_task=False)
    updated_run = main.decide_run_action(action.id, payload, user=user, db=sync_db)

    assert updated_run.status == "queued"
    sync_db.refresh(action)
    assert action.status == "rejected"


def test_schedule_crud_is_project_scoped(sync_db, setup_data):
    user, project = setup_data
    schedule = main.create_schedule(
        project.id,
        schemas.ScheduleCreate(prompt="Inspect installed modules", interval_seconds=3600),
        user=user,
        db=sync_db,
    )
    assert schedule.project_id == project.id
    assert schedule.enabled is True
    assert schedule.last_run_id is None

    updated = main.update_schedule(
        schedule.id,
        schemas.ScheduleUpdate(enabled=False),
        user=user,
        db=sync_db,
    )
    assert updated.enabled is False
    main.delete_schedule(schedule.id, user=user, db=sync_db)
    assert sync_db.get(models.AgentSchedule, schedule.id) is None


@pytest.mark.asyncio
async def test_answer_run_question_transitions_run_status_to_queued(sync_db, setup_data):
    user, project = setup_data
    run = models.AgentRun(
        id="run_test_question",
        project_id=project.id,
        requested_by_id=user.id,
        prompt="Build test module",
        thread_id="thread_test_q",
        status="awaiting_question",
        support_id="SUPP12345",
    )
    sync_db.add(run)
    sync_db.commit()

    question = models.AgentQuestion(
        id="q_test_123",
        run_id=run.id,
        project_id=project.id,
        requested_by_id=user.id,
        question="Which currency should be default?",
        options=["USD", "EUR"],
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    sync_db.add(question)
    sync_db.commit()

    mock_agent = MagicMock()
    mock_agent.answer_question = AsyncMock()

    mock_request = MagicMock(spec=Request)

    with patch("main.project_agent", AsyncMock(return_value=mock_agent)):
        payload = schemas.QuestionAnswer(answer="USD")
        updated_run = await main.answer_run_question(run.id, payload, request=mock_request, user=user, db=sync_db)

        assert updated_run.status == "queued"
        sync_db.refresh(question)
        assert question.status == "answered"
        assert question.answer == "USD"

        # Verify outbox event enqueued
        outbox = sync_db.query(models.OutboxEvent).filter(
            models.OutboxEvent.aggregate_id == run.id,
            models.OutboxEvent.event_type == "run.resume",
        ).first()
        assert outbox is not None


@pytest.mark.asyncio
async def test_detect_instance_subdomain_heuristic_for_odoo_cloud(sync_db, setup_data):
    user, project = setup_data
    payload = schemas.DetectRequest(url="https://mycompany.odoo.com", erp_type="odoo")

    with patch("xmlrpc.client.ServerProxy") as mock_proxy, patch("httpx.AsyncClient") as mock_http:
        proxy_instance = MagicMock()
        mock_proxy.return_value = proxy_instance
        proxy_instance.list.side_effect = Exception("AccessDenied")
        proxy_instance.version.return_value = {"server_version": "19.0"}

        mock_client = AsyncMock()
        mock_http.return_value.__aenter__.return_value = mock_client
        mock_res = MagicMock()
        mock_res.status_code = 403
        mock_client.post.return_value = mock_res

        result = await main.detect_instance(payload, _=user, db=sync_db)

        assert result["status"] == "manual_required"
        assert result["suggested_db"] == "mycompany"
        assert "mycompany" in result["databases"]
        assert "19.0" in result["server_version"]


@pytest.mark.asyncio
async def test_detect_instance_success_with_db_list(sync_db, setup_data):
    user, project = setup_data
    payload = schemas.DetectRequest(url="http://localhost:8069", erp_type="odoo")

    with patch("xmlrpc.client.ServerProxy") as mock_proxy, patch("httpx.AsyncClient") as mock_http:
        proxy_instance = MagicMock()
        mock_proxy.return_value = proxy_instance
        proxy_instance.list.return_value = ["demo_db"]
        proxy_instance.version.return_value = {"server_version": "19.0"}

        mock_client = AsyncMock()
        mock_http.return_value.__aenter__.return_value = mock_client
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"result": ["demo_db"]}
        mock_client.post.return_value = mock_res

        result = await main.detect_instance(payload, _=user, db=sync_db)

        assert result["status"] == "success"
        assert result["databases"] == ["demo_db"]
        assert result["suggested_db"] == "demo_db"


@pytest.mark.asyncio
async def test_detect_instance_dns_and_tls_diagnostics(sync_db, setup_data):
    user, project = setup_data
    payload = schemas.DetectRequest(url="https://nonexistent-odoo-subdomain.odoo.com", erp_type="odoo")

    with patch("xmlrpc.client.ServerProxy") as mock_proxy, patch("httpx.AsyncClient") as mock_http:
        proxy_instance = MagicMock()
        mock_proxy.return_value = proxy_instance
        proxy_instance.list.side_effect = socket.gaierror("Name or service not known")

        mock_client = AsyncMock()
        mock_http.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = socket.gaierror("Name or service not known")

        result = await main.detect_instance(payload, _=user, db=sync_db)

        assert result["status"] == "dns_error"
        assert "could not be resolved" in result["message"]
