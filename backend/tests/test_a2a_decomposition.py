"""Tests for the A2A SupervisorPlanner task decomposition logic."""
import pytest
from agent import SupervisorPlanner, MAX_TASK_RETRIES, _STANDARD_MODULE_TASK_GRAPH, _SIMPLE_TASK_GRAPH, module_label_matches


# ─── decompose() ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_decompose_module_build_returns_standard_graph():
    """Complex module prompts decompose into the 6-task standard graph."""
    prompt = "Build a custom Odoo module for manufacturing cost tracker with computed fields"
    graph = await SupervisorPlanner.decompose(prompt)
    task_ids = [t["task_id"] for t in graph]
    assert "task_01_inspect_ground" in task_ids
    assert "task_06_verify_install" in task_ids
    assert len(graph) == len(_STANDARD_MODULE_TASK_GRAPH)


@pytest.mark.asyncio
async def test_decompose_simple_prompt_returns_single_task():
    """Simple non-build prompts get a single-task graph."""
    prompt = "What version of Odoo is installed?"
    graph = await SupervisorPlanner.decompose(prompt)
    assert len(graph) == 1
    assert graph[0]["task_id"] == "task_01_execute"


@pytest.mark.asyncio
async def test_decompose_returns_deep_copy():
    """Each call returns an independent copy — mutations don't affect the template."""
    g1 = await SupervisorPlanner.decompose("build custom module tracker")
    g2 = await SupervisorPlanner.decompose("build custom module tracker")
    g1[0]["status"] = "done"
    assert g2[0]["status"] == "pending"


# ─── is_module_build() ────────────────────────────────────────────────────────

def test_is_module_build_requires_two_signals():
    """Single keyword is not enough to trigger module build decomposition."""
    assert not SupervisorPlanner.is_module_build("install something")
    assert SupervisorPlanner.is_module_build("build custom odoo module")


def test_is_module_build_case_insensitive():
    assert SupervisorPlanner.is_module_build("Build a Manufacturing Cost Tracker Module")


def test_module_lookup_matches_human_and_technical_names():
    module = {"name": "training_centre", "shortdesc": "Training Centre"}
    assert module_label_matches(module, "Training Centre")
    assert module_label_matches(module, "training_center")
    assert module_label_matches(module, "training_centre")
    assert not module_label_matches(module, "Sales")


def test_read_only_inspection_does_not_get_a_build_graph():
    prompt = "Inspect the database and find any module named Training Centre"
    assert SupervisorPlanner.is_read_only_request(prompt)
    assert not SupervisorPlanner.is_module_build(prompt)


def test_read_only_guardrail_does_not_treat_prohibited_writes_as_requested_writes():
    prompt = "Inspect the connected database and find whether a module named Training Centre is installed. Do not create or modify any files."
    assert SupervisorPlanner.is_read_only_request(prompt)
    assert not SupervisorPlanner.is_read_only_request("Create a Training Centre module. Do not modify unrelated files.")


@pytest.mark.asyncio
async def test_read_only_inspection_uses_one_read_only_task():
    graph = await SupervisorPlanner.decompose("Inspect the database and find any module named Training Centre")
    assert [task["task_id"] for task in graph] == ["inspect_request"]
    assert graph[0]["risk_class"] == 1


# ─── get_next_task() ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_next_task_returns_first_pending_with_no_deps():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    next_task = SupervisorPlanner.get_next_task(graph)
    assert next_task is not None
    assert next_task["task_id"] == "task_01_inspect_ground"
    assert next_task["depends_on"] == []


@pytest.mark.asyncio
async def test_get_next_task_respects_dependencies():
    """No task with unmet dependencies is returned as next."""
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    # Mark inspect_ground as done
    graph[0]["status"] = "done"
    next_task = SupervisorPlanner.get_next_task(graph)
    assert next_task is not None
    # task_02 or task_03 — both depend only on task_01 (now done)
    assert next_task["task_id"] in {"task_02_extend_models", "task_03_config_model"}


@pytest.mark.asyncio
async def test_get_next_task_returns_none_when_all_done():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    for t in graph:
        t["status"] = "done"
    assert SupervisorPlanner.get_next_task(graph) is None


@pytest.mark.asyncio
async def test_get_next_task_skips_in_progress():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    graph[0]["status"] = "in_progress"
    # Only task_01 has no deps — if it's in_progress, no task should be returned
    next_task = SupervisorPlanner.get_next_task(graph)
    assert next_task is None


# ─── is_complete() / has_failure() ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_complete_false_when_tasks_pending():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    assert not SupervisorPlanner.is_complete(graph)


@pytest.mark.asyncio
async def test_is_complete_true_when_all_done():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    for t in graph:
        t["status"] = "done"
    assert SupervisorPlanner.is_complete(graph)


@pytest.mark.asyncio
async def test_has_failure_detects_exhausted_task():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    graph[0]["status"] = "failed"
    graph[0]["retry_count"] = MAX_TASK_RETRIES
    assert SupervisorPlanner.has_failure(graph)


@pytest.mark.asyncio
async def test_has_failure_false_when_retries_remain():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    graph[0]["status"] = "failed"
    graph[0]["retry_count"] = MAX_TASK_RETRIES - 1
    assert not SupervisorPlanner.has_failure(graph)


# ─── format_progress() ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_format_progress_shows_correct_ratio():
    graph = await SupervisorPlanner.decompose("build custom odoo module")
    graph[0]["status"] = "done"
    graph[1]["status"] = "done"
    result = SupervisorPlanner.format_progress(graph)
    assert "2/" in result
    assert str(len(graph)) in result


# ─── MAX_TASK_RETRIES constant ────────────────────────────────────────────────

def test_max_task_retries_is_two():
    assert MAX_TASK_RETRIES == 2


# ─── Conversational & Antigravity Inquiries ──────────────────────────────────

def test_is_conversational_recognizes_greetings():
    assert SupervisorPlanner.is_conversational_request("hi")
    assert SupervisorPlanner.is_conversational_request("hello")
    assert SupervisorPlanner.is_conversational_request("good morning")


def test_is_conversational_recognizes_antigravity_and_architecture_questions():
    assert SupervisorPlanner.is_conversational_request("How was this agent built with antigravity docs?")
    assert SupervisorPlanner.is_conversational_request("Explain the architecture of this agent")
    assert SupervisorPlanner.is_conversational_request("What docs did we use to build this?")
    assert SupervisorPlanner.is_conversational_request("Tell me about antigravity SDK")
    assert SupervisorPlanner.is_conversational_request("How does the agent architecture work?")


def test_questions_with_module_keywords_are_not_mistaken_for_builds():
    prompt = "Explain how Odoo 19 module manifests and views work"
    assert not SupervisorPlanner.is_module_build(prompt)
    assert SupervisorPlanner.is_conversational_request(prompt)


@pytest.mark.asyncio
async def test_antigravity_inquiry_decomposes_to_conversational_graph():
    graph = await SupervisorPlanner.decompose("Can you explain how this was built using antigravity docs?")
    assert len(graph) == 1
    assert graph[0]["task_id"] == "conversational_dialogue"
    assert "Google Antigravity" in graph[0]["acceptance_criteria"]


def test_is_continuation_request():
    assert SupervisorPlanner.is_continuation_request("do it")
    assert SupervisorPlanner.is_continuation_request("proceed")
    assert SupervisorPlanner.is_continuation_request("build that")
    assert SupervisorPlanner.is_continuation_request("create that model")
    assert SupervisorPlanner.is_continuation_request("yes please")
    assert not SupervisorPlanner.is_continuation_request("Hello how are you?")


@pytest.mark.asyncio
async def test_continuation_with_module_history_decomposes_to_module_build():
    class DummyMsg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    history = [
        DummyMsg("user", "Can we build an Odoo custom module for equipment maintenance?"),
        DummyMsg("agent", "I propose building custom module equipment_maintenance with models equipment.item and maintenance.request."),
    ]
    # Standalone "do it" without context would not be a module build
    assert not SupervisorPlanner.is_module_build("do it")
    
    # With history, "do it" or "proceed" recognizes the module build context
    assert SupervisorPlanner.is_module_build("do it", conversation_history=history)
    graph = await SupervisorPlanner.decompose("do it", conversation_history=history)
    assert len(graph) == len(_STANDARD_MODULE_TASK_GRAPH)
    assert graph[0]["task_id"] == "task_01_inspect_ground"


