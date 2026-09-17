from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import models
from agent import SupervisorPlanner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import worker
import agent
from worker import _apply_task_status, _ensure_subtask, is_in_flight_budget_error, requeue_failed_task


def test_nested_task_updates_are_immutable_and_drive_dependency_selection():
    graph = [
        {"task_id": "one", "title": "One", "depends_on": [], "status": "in_progress", "context_bundle": {}, "result": None},
        {"task_id": "two", "title": "Two", "depends_on": ["one"], "status": "pending", "context_bundle": {}, "result": None},
    ]
    original = deepcopy(graph)
    run = models.AgentRun(project_id=1, requested_by_id=1, prompt="x", thread_id="thread", task_graph=graph)

    updated = _apply_task_status(
        None,
        run,
        "one",
        "done",
        result={"outcome": "SUCCESS"},
        context_bundle={"handoff_summary": "ready"},
    )

    assert graph == original
    assert updated[0]["status"] == "done"
    assert run.task_graph == updated
    assert SupervisorPlanner.get_next_task(updated)["task_id"] == "two"


def test_in_flight_budget_errors_are_detected_without_matching_other_402s():
    assert is_in_flight_budget_error(Exception("Error code: 402 - in_flight_budget_exhausted")) is True
    assert is_in_flight_budget_error(Exception("code': 402 available credits")) is True
    assert is_in_flight_budget_error(Exception("payment required for another reason")) is False


def test_agent_bounds_provider_stream_stalls(monkeypatch):
    calls = []

    class FakeChat:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def with_fallbacks(self, fallbacks):
            return self

        def bind_tools(self, tools):
            return self

    monkeypatch.setattr(agent, "ChatOpenAI", FakeChat)
    monkeypatch.setattr(agent, "Workspace", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr(agent, "create_react_agent", lambda *args, **kwargs: MagicMock())
    agent.ERPImplementationAgent(
        client=MagicMock(),
        workspace_slug="stream-stall-test",
        checkpointer=None,
        llm_model="primary",
        fallback_model="fallback",
        read_only=True,
    )

    assert len(calls) == 2
    assert all(call["stream_chunk_timeout"] == 30 for call in calls)


def test_requirement_graph_generates_tests_before_validation():
    graph = SupervisorPlanner.build_from_specification([{"title": "Build module"}])
    by_id = {task["task_id"]: task for task in graph}

    assert "generate_tests" in by_id
    assert by_id["validate_and_verify"]["depends_on"] == ["generate_tests"]


def test_failed_task_is_requeued_with_repair_context(monkeypatch):
    graph = [{
        "task_id": "validate_and_verify",
        "title": "Validate",
        "depends_on": [],
        "status": "failed",
        "retry_count": 0,
        "max_retries": 2,
        "context_bundle": {},
        "result": None,
    }]
    run = models.AgentRun(
        id="run-repair",
        project_id=1,
        requested_by_id=1,
        prompt="build module",
        thread_id="thread",
        task_graph=graph,
        active_task_id="validate_and_verify",
    )
    queued = []
    monkeypatch.setattr("worker.enqueue", lambda db, event, aggregate, payload=None: queued.append((event, aggregate)))
    monkeypatch.setattr("worker._emit_task_transition", lambda *args, **kwargs: None)

    assert requeue_failed_task(None, run, "validate_and_verify", {"errors": "ViewValidationError"}) is True
    task = run.task_graph[0]
    assert task["status"] == "pending"
    assert task["retry_count"] == 1
    assert task["context_bundle"]["repair_required"] is True
    assert queued == [("run.resume", "run-repair")]


def test_each_supervisor_task_gets_one_durable_child_thread():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        run = models.AgentRun(
            id="parent-run",
            project_id=1,
            requested_by_id=1,
            prompt="inspect database",
            thread_id="parent-thread",
        )
        db.add(run)
        db.commit()
        task = {"task_id": "inspect_schema", "title": "Inspect Odoo schema"}

        first = _ensure_subtask(db, run, task)
        db.commit()
        second = _ensure_subtask(db, run, task)

        assert first.id == second.id
        assert first.thread_id == "parent-thread:subtask:inspect_schema"
        assert first.role == "discovery_analyst"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_rejected_action_terminates_without_resuming_agent(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        run = models.AgentRun(
            id="run-rejected-action",
            project_id=1,
            requested_by_id=1,
            prompt="build a module",
            thread_id="thread-rejected-action",
            intent="read",
            status="queued",
        )
        action = models.PendingAction(
            id="action-rejected-action",
            project_id=1,
            run_id=run.id,
            requested_by_id=1,
            thread_id=run.thread_id,
            tool_name="write_file",
            tool_call_id="call-rejected-action",
            risk_class="2",
            arguments={"path": "module.py", "content": "# denied"},
            preview={"summary": "write module.py"},
            status="rejected",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        db.add_all([run, action]); db.commit()

        agent = MagicMock(read_only=True)
        agent.stream = MagicMock(side_effect=AssertionError("rejected actions must not resume the agent"))
        agent.usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

        @contextmanager
        def session_context():
            yield db

        monkeypatch.setattr(worker, "SessionLocal", session_context)
        monkeypatch.setattr(worker, "build_agent", AsyncMock(return_value=agent))

        await worker.process_run(
            run.id,
            checkpointer=None,
            action_id=action.id,
            decision="reject",
        )

        db.refresh(run); db.refresh(action)
        assert run.status == "failed"
        assert run.error_category == "ActionRejected"
        assert action.status == "rejected"
        agent.stream.assert_not_called()
    finally:
        db.close()
