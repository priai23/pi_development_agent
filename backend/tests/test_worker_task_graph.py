from copy import deepcopy

import models
from agent import SupervisorPlanner
from worker import _apply_task_status


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
