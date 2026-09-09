from copy import deepcopy

import models
from agent import SupervisorPlanner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from worker import _apply_task_status, _ensure_subtask, requeue_failed_task


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
