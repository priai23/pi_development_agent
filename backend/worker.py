import asyncio
import json
import logging
import re
import threading
import time
import traceback
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

import models
from agent import (
    ERPImplementationAgent,
    SupervisorPlanner,
    MAX_TASK_RETRIES,
    connect_odoo,
    connect_odoo_json2,
)
from config import settings
from database import SessionLocal
from deployment import execute_deployment
from security import decrypt_secret
from source_indexer import index_addon_roots
from workspace import Workspace
from permissions import PermissionEngine, active_grant, check_permission, resource_for
from pri_erp_adapter import connect_pri_erp
from tool_registry import TOOL_REGISTRY, validate_tool_call
from specification import CHECK_GATED_TASKS, compile_specification

# ─── Watchdog tunables ─────────────────────────────────────────────────────────
SUBTASK_STALE_SECONDS = 90   # per sub-task heartbeat threshold
RUN_STALE_SECONDS = 300      # whole-run safety net (supervisor itself silent)

logger = logging.getLogger(__name__)


class ProjectBusy(RuntimeError):
    pass


def is_in_flight_budget_error(exc: BaseException) -> bool:
    """Identify provider errors caused by the account's concurrent-request cap."""
    message = str(exc).casefold()
    return "in_flight_budget_exhausted" in message or (
        ("error code: 402" in message or "code': 402" in message or 'code": 402' in message)
        and "available credits" in message
    )


def dispatch_due_schedules() -> int:
    """Turn due schedules into normal durable runs.

    This is deliberately performed by the worker, so a browser closing or an
    API process restarting cannot lose a recurring prompt. A schedule advances
    only after its due occurrence has been claimed in the same transaction as
    the run enqueue.
    """
    dispatched = 0
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        schedules = db.query(models.AgentSchedule).filter(
            models.AgentSchedule.enabled.is_(True),
            models.AgentSchedule.next_run_at <= now,
        ).order_by(models.AgentSchedule.next_run_at).with_for_update(skip_locked=True).limit(20).all()
        for schedule in schedules:
            schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)
            schedule.last_run_at = now
            schedule.last_error = None
            project = db.get(models.Project, schedule.project_id)
            instance = db.query(models.Instance).filter(
                models.Instance.project_id == schedule.project_id,
                models.Instance.is_active.is_(True),
            ).first()
            active = db.query(models.AgentRun).filter(
                models.AgentRun.project_id == schedule.project_id,
                models.AgentRun.intent == "write",
                models.AgentRun.status.in_(("queued", "running", "awaiting_question", "awaiting_approval", "cancelling")),
            ).first()
            key = db.get(models.Setting, "openrouter_api_key")
            if active:
                schedule.last_error = f"Deferred: project already has active run {active.id}"
                schedule.next_run_at = now + timedelta(seconds=min(schedule.interval_seconds, 300))
                db.commit()
                continue
            if not project or not instance or instance.status not in {"ready", "connected"}:
                schedule.last_error = "Deferred: no active staging instance"
                db.commit()
                continue
            try:
                key_configured = bool(key and decrypt_secret(key.value).strip())
            except ValueError:
                key_configured = False
            if not key_configured:
                schedule.last_error = "Deferred: no API key configured"
                db.commit()
                continue
            budget = project.monthly_budget_usd or project.organization.monthly_budget_usd
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            spent = db.query(models.AgentRun.cost_usd).filter(
                models.AgentRun.project_id == project.id,
                models.AgentRun.created_at >= month_start,
            ).all()
            if budget is None or sum(row[0] or 0 for row in spent) >= budget:
                schedule.last_error = "Deferred: monthly agent budget is not configured or has been reached"
                db.commit()
                continue
            version_info = instance.version_info or {}
            if instance.erp_type in ("pri_erp", "pi_erp"):
                version = "pri_erp"
                edition = "managed"
            else:
                raw_version = str(version_info.get("server_serie") or version_info.get("server_version") or "").strip()
                version_match = re.match(r"^(\d+\.\d+)", raw_version)
                version = version_match.group(1) if version_match else "unknown"
                edition = str(version_info.get("server_edition") or "unknown").casefold()
                if version != "19.0" or edition not in {"community", "enterprise"}:
                    schedule.last_error = "Deferred: staging Odoo 19 version and edition are not verified"
                    db.commit()
                    continue
            schedule.idempotency_key = hashlib.sha256(f"{schedule.id}:{now.isoformat()}".encode()).hexdigest()
            schedule.retry_count = 0
            is_read_only = (
                SupervisorPlanner.is_read_only_request(schedule.prompt)
                or SupervisorPlanner.is_conversational_request(schedule.prompt)
            )
            intent = "read_only" if is_read_only else "write"
            workspace = Workspace(project.workspace_slug, create=intent != "read_only")
            run = models.AgentRun(
                project_id=project.id,
                requested_by_id=schedule.requested_by_id,
                prompt=schedule.prompt,
                thread_id=f"project:{project.id}:schedule:{schedule.id}:run:{uuid4()}",
                planner_model=(db.get(models.Setting, "llm_model_name").value if db.get(models.Setting, "llm_model_name") else None),
                fallback_model=(db.get(models.Setting, "llm_fallback_model_name").value if db.get(models.Setting, "llm_fallback_model_name") else None),
                workspace_base_revision=workspace.head() if intent != "read_only" else None,
                intent=intent,
                workspace_slug=project.workspace_slug if intent != "read_only" else None,
                status="queued",
            )
            db.add(run)
            db.flush()
            snapshot = models.SourceSnapshot(
                instance_id=instance.id,
                odoo_version=version,
                odoo_edition=edition,
                fingerprint="pending",
                status="pending_index",
            )
            db.add(snapshot)
            db.flush()
            run.source_snapshot_id = snapshot.id
            schedule.last_run_id = run.id
            emit(db, run.id, "run.queued", {"support_id": run.support_id, "scheduled": True, "schedule_id": schedule.id})
            enqueue(db, "run.prepare", run.id)
            db.commit()
            dispatched += 1
    return dispatched


def required_acceptance_failures(db, run_id: str, task_id: str | None) -> list[str]:
    if not task_id:
        return ["AcceptanceCheckMissing: task id is required"]
    checks = db.query(models.AcceptanceCheck).filter(
        models.AcceptanceCheck.run_id == run_id,
        models.AcceptanceCheck.task_id == task_id,
        models.AcceptanceCheck.required == True,
    ).all()
    if task_id in CHECK_GATED_TASKS and not checks:
        return [f"AcceptanceCheckMissing: no required checks were compiled for {task_id}"]
    return [
        f"{check.kind}:{check.spec_target}"
        for check in checks
        if check.status != "passed"
    ]


def requeue_failed_task(db, run: models.AgentRun, task_id: str, report: dict) -> bool:
    if not settings.autonomous_repair_enabled:
        return False
    task = next((item for item in (run.task_graph or []) if item["task_id"] == task_id), None)
    if not task:
        return False
    retry_count = int(task.get("retry_count", 0))
    if retry_count >= int(task.get("max_retries", MAX_TASK_RETRIES)):
        return False
    graph = _apply_task_status(
        db,
        run,
        task_id,
        "pending",
        retry_count=retry_count + 1,
        result=report,
        context_bundle={
            "repair_required": True,
            "failed_verification": report.get("verification", ""),
            "errors": report.get("errors", ""),
        },
    )
    run.active_task_id = None
    run.subtask_heartbeat_at = None
    run.status = "queued"
    run.retryable = True
    run.finished_at = None
    _emit_task_transition(db, run, "task.retrying", task_id, {
        "task_graph": graph,
        "retry_count": retry_count + 1,
        "max_retries": task.get("max_retries", MAX_TASK_RETRIES),
        "errors": report.get("errors", ""),
    })
    enqueue(db, "run.resume", run.id)
    return True


def update_worker_lease() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as db:
        setting = db.get(models.Setting, "worker_last_seen_at")
        if not setting:
            db.add(models.Setting(key="worker_last_seen_at", value=now))
        else:
            setting.value = now
        db.commit()


def start_worker_lease_thread(shutdown_event: threading.Event | None = None) -> threading.Thread:
    def _run():
        while shutdown_event is None or not shutdown_event.is_set():
            try:
                update_worker_lease()
            except Exception:
                logger.exception("Worker lease heartbeat failed")
            if shutdown_event:
                if shutdown_event.wait(5):
                    break
            else:
                time.sleep(5)

    thread = threading.Thread(target=_run, daemon=True, name="worker_lease_heartbeat")
    thread.start()
    return thread


def emit(db, run_id: str, event_type: str, payload: dict) -> None:
    sequence = db.query(func.coalesce(func.max(models.ToolEvent.sequence), 0)).filter(
        models.ToolEvent.run_id == run_id
    ).scalar() + 1
    db.add(models.ToolEvent(run_id=run_id, sequence=sequence, event_type=event_type, payload=payload))
    db.flush()


def enqueue(db, event_type: str, aggregate_id: str, payload: dict | None = None) -> None:
    db.add(models.OutboxEvent(event_type=event_type, aggregate_id=aggregate_id, payload=payload or {}))


def _replace_task(run: models.AgentRun, task_id: str, **changes) -> list[dict]:
    """Return and persist an immutable task-graph update."""
    graph = [
        {**task, **changes} if task.get("task_id") == task_id else {**task}
        for task in (run.task_graph or [])
    ]
    run.task_graph = graph
    flag_modified(run, "task_graph")
    return graph


def _transition_emitted(db, run_id: str, event_type: str, task_id: str) -> bool:
    events = db.query(models.ToolEvent.payload).filter(
        models.ToolEvent.run_id == run_id,
        models.ToolEvent.event_type == event_type,
    ).all()
    return any((payload or {}).get("task_id") == task_id for (payload,) in events)


def _emit_task_transition(db, run: models.AgentRun, event_type: str, task_id: str, payload: dict) -> None:
    if not _transition_emitted(db, run.id, event_type, task_id):
        emit(db, run.id, event_type, {"task_id": task_id, **payload})


def _cancel_active_task(db, run: models.AgentRun) -> list[dict]:
    task_id = run.active_task_id
    graph = run.task_graph or []
    if task_id:
        child = db.query(models.AgentSubtask).filter(
            models.AgentSubtask.parent_run_id == run.id,
            models.AgentSubtask.task_id == task_id,
        ).first()
        if child:
            _set_subtask_status(child, "cancelled")
        graph = _replace_task(run, task_id, status="cancelled")
        _emit_task_transition(db, run, "task.cancelled", task_id, {"task_graph": graph})
    run.active_task_id = None
    run.subtask_heartbeat_at = None
    return graph


def _scope_tool_event(db, run: models.AgentRun, event_type: str, payload: dict) -> dict:
    if not event_type.startswith("tool.") or not run.active_task_id:
        return payload
    scoped = {**payload, "task_id": run.active_task_id}
    if event_type == "tool.started":
        prior = db.query(models.ToolEvent.payload).filter(
            models.ToolEvent.run_id == run.id,
            models.ToolEvent.event_type == "tool.started",
        ).all()
        iterations = sum((value or {}).get("task_id") == run.active_task_id for (value,) in prior)
        if iterations >= 40:
            raise RuntimeError("TaskExecutionLimitExceeded: current task exceeded 40 tool iterations")
    return scoped


def _update_subtask_heartbeat(db, run: models.AgentRun, task_id: str) -> list[dict]:
    """Bump both run-level and per-task heartbeat, and mirror into task_graph JSON."""
    now = datetime.now(timezone.utc)
    run.heartbeat_at = now
    run.subtask_heartbeat_at = now
    if run.active_task_id != task_id:
        run.active_task_id = task_id
    return _replace_task(run, task_id, heartbeat_at=now.isoformat()) if run.task_graph else []


def _apply_task_status(db, run: models.AgentRun, task_id: str, status: str, **changes) -> list[dict]:
    """Update a task node's status in the persisted task_graph JSON."""
    if not run.task_graph:
        return []
    return _replace_task(run, task_id, status=status, **changes)


def _subtask_role(task: dict) -> str:
    title = f"{task.get('task_id', '')} {task.get('title', '')}".casefold()
    if any(word in title for word in ("inspect", "discover", "schema", "database")):
        return "discovery_analyst"
    if any(word in title for word in ("model", "python", "backend")):
        return "backend_specialist"
    if any(word in title for word in ("view", "xml", "frontend")):
        return "frontend_specialist"
    if any(word in title for word in ("security", "access")):
        return "security_specialist"
    if any(word in title for word in ("test", "verify", "validate", "package")):
        return "verification_specialist"
    return "implementation_specialist"


def _ensure_subtask(db, run: models.AgentRun, task: dict) -> models.AgentSubtask:
    subtask = db.query(models.AgentSubtask).filter(
        models.AgentSubtask.parent_run_id == run.id,
        models.AgentSubtask.task_id == task["task_id"],
    ).first()
    if subtask is None:
        subtask = models.AgentSubtask(
            parent_run_id=run.id,
            project_id=run.project_id,
            task_id=task["task_id"],
            title=task.get("title", task["task_id"]),
            role=_subtask_role(task),
            thread_id=f"{run.thread_id}:subtask:{task['task_id']}",
            prompt=run.prompt,
        )
        db.add(subtask)
        db.flush()
    return subtask


def _set_subtask_status(subtask: models.AgentSubtask, status: str, *, result: dict | None = None, error: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    subtask.status = status
    subtask.heartbeat_at = now
    if status == "running":
        subtask.started_at = subtask.started_at or now
    if result is not None:
        subtask.result = result
    if error is not None:
        subtask.error_message = error
    if status in {"succeeded", "failed", "cancelled"}:
        subtask.finished_at = now


# ─── Watchdog / recovery ───────────────────────────────────────────────────────

def recover_stale_work() -> None:
    """Per-task heartbeat watchdog.

    1. Expire pending actions past TTL.
    2. Re-release stuck claimed/executing pending actions.
    3. Sub-task stale: if subtask_heartbeat_at older than SUBTASK_STALE_SECONDS
       and retries remain → re-queue with task.recovering event.
       If retries exhausted → fail the run.
    4. Run-level fallback: whole-run heartbeat older than RUN_STALE_SECONDS.
    5. Unclaim stale outbox events.
    """
    now = datetime.now(timezone.utc)
    stale = now - timedelta(minutes=5)
    subtask_cutoff = now - timedelta(seconds=SUBTASK_STALE_SECONDS)
    run_cutoff = now - timedelta(seconds=RUN_STALE_SECONDS)

    with SessionLocal() as db:
        expired_questions = db.query(models.AgentQuestion).filter(
            models.AgentQuestion.status == "pending",
            models.AgentQuestion.expires_at <= now,
        ).all()
        for question in expired_questions:
            question.status = "expired"
            run = db.get(models.AgentRun, question.run_id)
            if run and run.status == "awaiting_question":
                run.status = "expired"
                run.finished_at = now
                emit(db, run.id, "run.expired", {"question_id": question.id})

        # 1. Expire pending actions
        expired = db.query(models.PendingAction).filter(
            models.PendingAction.status == "pending",
            models.PendingAction.expires_at <= now,
        ).all()
        for action in expired:
            action.status = "expired"
            if action.run_id:
                run = db.get(models.AgentRun, action.run_id)
                if run:
                    run.status = "expired"
                    run.finished_at = now
                    emit(db, run.id, "run.expired", {"action_id": action.id})
                    enqueue(db, "action.resume", run.id, {"action_id": action.id, "decision": "reject"})

        # 2. Re-release stuck claimed/executing pending actions
        stale_pending = db.query(models.PendingAction).filter(
            ((models.PendingAction.status == "claimed") & (models.PendingAction.claimed_at < stale)) |
            ((models.PendingAction.status == "executing") & (models.PendingAction.execution_started_at < stale))
        ).all()
        for action in stale_pending:
            action.status = "approved"
            action.claimed_at = None
            if action.run_id:
                enqueue(db, "action.resume", action.run_id, {"action_id": action.id, "decision": "approve"})

        # 3. Per-task sub-task stale watchdog
        stale_task_runs = db.query(models.AgentRun).filter(
            models.AgentRun.status.in_(["running", "cancelling"]),
            models.AgentRun.active_task_id.isnot(None),
            models.AgentRun.task_graph.isnot(None),
            (models.AgentRun.subtask_heartbeat_at < subtask_cutoff) |
            (models.AgentRun.subtask_heartbeat_at.is_(None)),
            (models.AgentRun.heartbeat_at > run_cutoff) |
            (models.AgentRun.heartbeat_at.is_(None)),
        ).all()

        for run in stale_task_runs:
            task_id = run.active_task_id
            retries = (run.task_retries or {}).copy()
            current_retries = retries.get(task_id, 0)

            if current_retries < MAX_TASK_RETRIES:
                retries[task_id] = current_retries + 1
                run.task_retries = retries
                _apply_task_status(db, run, task_id, "pending")
                run.active_task_id = None
                run.subtask_heartbeat_at = None
                run.status = "queued"
                child = db.query(models.AgentSubtask).filter(
                    models.AgentSubtask.parent_run_id == run.id,
                    models.AgentSubtask.task_id == task_id,
                ).first()
                if child:
                    child.retry_count = current_retries + 1
                    _set_subtask_status(child, "queued")
                emit(db, run.id, "task.recovering", {
                    "task_id": task_id,
                    "attempt": current_retries + 1,
                    "max_attempts": MAX_TASK_RETRIES,
                    "reason": f"Sub-task stalled (no heartbeat for {SUBTASK_STALE_SECONDS}s)",
                })
                emit(db, run.id, "run.queued", {
                    "task_id": task_id,
                    "support_id": run.support_id,
                })
                enqueue(db, "run.resume", run.id)
            else:
                run.status = "failed"
                run.error_category = "SubTaskMaxRetriesExceeded"
                run.error_message = f"Sub-task '{task_id}' failed after {MAX_TASK_RETRIES} attempts."
                run.finished_at = now
                _apply_task_status(db, run, task_id, "failed")
                child = db.query(models.AgentSubtask).filter(
                    models.AgentSubtask.parent_run_id == run.id,
                    models.AgentSubtask.task_id == task_id,
                ).first()
                if child:
                    _set_subtask_status(child, "failed", error=run.error_message)
                emit(db, run.id, "task.failed", {
                    "task_id": task_id,
                    "retries_exhausted": True,
                    "message": run.error_message,
                })
                emit(db, run.id, "run.failed", {
                    "category": "SubTaskMaxRetriesExceeded",
                    "message": run.error_message,
                    "retryable": False,
                    "support_id": run.support_id,
                    "task_id": task_id,
                    "tool": None,
                })

        # 4. Whole-run stale fallback
        stale_runs = db.query(models.AgentRun).filter(
            models.AgentRun.status.in_(["running", "cancelling"]),
            (models.AgentRun.heartbeat_at < run_cutoff) | (models.AgentRun.heartbeat_at.is_(None)),
        ).all()
        for run in stale_runs:
            run.status = "queued"
            emit(db, run.id, "run.queued", {"support_id": run.support_id})
            enqueue(db, "run.resume", run.id)

        # 5. Unclaim stale outbox events
        db.query(models.OutboxEvent).filter(
            models.OutboxEvent.completed_at.is_(None),
            models.OutboxEvent.claimed_at < stale,
        ).update({"claimed_at": None})

        db.commit()


def claim_outbox():
    with SessionLocal() as db:
        # Find active aggregate_ids (runs that have an event currently claimed)
        active_aggregates = select(models.OutboxEvent.aggregate_id).where(
            models.OutboxEvent.completed_at.is_(None),
            models.OutboxEvent.claimed_at.is_not(None)
        )
        
        event = db.execute(
            select(models.OutboxEvent)
            .where(
                models.OutboxEvent.completed_at.is_(None),
                models.OutboxEvent.claimed_at.is_(None),
                models.OutboxEvent.available_at <= datetime.now(timezone.utc),
                models.OutboxEvent.aggregate_id.not_in(active_aggregates)
            )
            .order_by(models.OutboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()
        if not event:
            return None
        event.claimed_at = datetime.now(timezone.utc)
        event.attempts += 1
        result = (event.id, event.event_type, event.aggregate_id, event.payload)
        db.commit()
        return result


async def build_agent(db, run: models.AgentRun, checkpointer):
    project = db.get(models.Project, run.project_id)
    instance = db.query(models.Instance).filter(
        models.Instance.project_id == project.id, models.Instance.is_active.is_(True)
    ).first()
    if not instance:
        raise ValueError("No active ERP instance is connected")
    if instance.environment != "staging":
        raise ValueError("Agent implementation runs require a staging instance")
    if instance.erp_type in ("pri_erp", "pi_erp"):
        if instance.api_key_encrypted:
            client = await connect_pri_erp(
                url=instance.url,
                api_key=decrypt_secret(instance.api_key_encrypted),
            )
        else:
            client = await connect_pri_erp(
                url=instance.url,
                username=instance.username or "",
                password=decrypt_secret(instance.password_encrypted or ""),
            )
    else:
        client = await (
            connect_odoo_json2(instance.url, instance.db_name or "", decrypt_secret(instance.api_key_encrypted or ""))
            if instance.auth_method == "json2"
            else connect_odoo(instance.url, instance.db_name or "", instance.username or "", decrypt_secret(instance.password_encrypted or ""))
        )
    model = db.get(models.Setting, "llm_model_name")
    key = db.get(models.Setting, "openrouter_api_key")
    timeout = db.get(models.Setting, "llm_timeout_seconds")
    max_tokens = db.get(models.Setting, "llm_max_output_tokens")
    fallback_model = run.fallback_model or (db.get(models.Setting, "llm_fallback_model_name").value if db.get(models.Setting, "llm_fallback_model_name") else "anthropic/claude-3.5-sonnet")
    auto_writes_setting = db.get(models.Setting, "autonomous_workspace_writes")
    auto_writes = bool(auto_writes_setting and auto_writes_setting.value and auto_writes_setting.value.lower() in ("true", "1", "yes"))
    return ERPImplementationAgent(
        client,
        run.workspace_slug or project.workspace_slug,
        checkpointer,
        run.planner_model or (model.value if model else "gpt-4o-mini"),
        decrypt_secret(key.value) if key else None,
        "https://openrouter.ai/api/v1" if key else None,
        int(timeout.value) if timeout else 120,
        int(max_tokens.value) if max_tokens else 16000,
        project.id,
        run.requested_by_id,
        instance.id,
        fallback_model,
        autonomous_workspace_writes=auto_writes,
        read_only=run.intent == "read_only",
    )


def process_run_prepare(run_id: str) -> None:
    """Execute idempotent run preparation: index, spec, task graph, then enqueue run.start."""
    with SessionLocal() as db:
        run = db.get(models.AgentRun, run_id)
        if not run or run.status not in ("queued", "running"):
            return
        project = db.get(models.Project, run.project_id)
        snapshot = db.get(models.SourceSnapshot, run.source_snapshot_id) if run.source_snapshot_id else None

        read_only = run.intent == "read_only"

        # 1. Index snapshot. Database-only inspection must not create a workspace.
        if snapshot and snapshot.status == "pending_index":
            try:
                snapshot.status = "indexing"
                db.commit()
                symbol_count = 0
                if not read_only:
                    ws = Workspace(run.workspace_slug or project.workspace_slug)
                    symbol_count = index_addon_roots([ws.root], snapshot.id, db)
                snapshot.status = "indexed"
                snapshot.symbol_count = symbol_count
                snapshot.indexed_at = datetime.now(timezone.utc)
                db.commit()
            except Exception as exc:
                snapshot.status = "failed"
                snapshot.index_error = str(exc)[:2000]
                run.status = "failed"
                run.error_message = f"Indexing failed: {exc}"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                return

        # 2. Resolve install vs upgrade
        is_upgrade = False
        if snapshot:
            mod_exists = db.query(models.SourceSymbol).filter(
                models.SourceSymbol.snapshot_id == snapshot.id,
                models.SourceSymbol.kind == "module",
                models.SourceSymbol.name == run.module_name
            ).first()
            if mod_exists:
                is_upgrade = True

        # 3. Compile implementation specifications only for implementation requests.
        if not read_only and not run.specification_id:
            symbols = db.query(models.SourceSymbol).filter(models.SourceSymbol.snapshot_id == snapshot.id).all() if snapshot else []
            try:
                spec = compile_specification(
                    run_id=run.id,
                    project_id=project.id,
                    snapshot_id=snapshot.id if snapshot else None,
                    prompt=run.prompt,
                    module_name=run.module_name or "unknown",
                    is_upgrade=is_upgrade,
                    snapshot_symbols=symbols,
                    db=db
                )
                run.specification_id = spec.specification_id
                db.commit()
            except Exception as exc:
                run.status = "failed"
                run.error_message = f"Specification compilation failed: {exc}"
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                return

        # 4 & 5. Build task graph
        if not run.task_graph and not read_only:
            spec_row = db.get(models.RunSpecification, run.specification_id)
            if spec_row and spec_row.requirements:
                graph = SupervisorPlanner.build_from_specification(spec_row.requirements)
                run.task_graph = graph
                run.task_retries = {}
                db.commit()

        # 6. enqueue run.start
        enqueue(db, "run.start", run.id)
        db.commit()


async def _initialise_task_graph(db, run: models.AgentRun) -> list[dict]:
    """Decompose prompt into task graph and persist — requirement-driven if spec exists."""
    if run.intent == "read_only":
        graph = await SupervisorPlanner.decompose(run.prompt)
        run.task_graph = graph
        run.task_retries = {}
        db.flush()
        return graph

    if run.task_graph:
        return run.task_graph

    spec = db.query(models.RunSpecification).filter(models.RunSpecification.run_id == run.id).first()
    if spec and spec.requirements:
        graph = SupervisorPlanner.build_from_specification(spec.requirements)
        run.task_graph = graph
        run.task_retries = {}
        db.flush()
        return graph

    llm = None
    if run.planner_model:
        from langchain_openai import ChatOpenAI
        key = db.get(models.Setting, "openrouter_api_key")
        max_tokens = db.get(models.Setting, "llm_max_output_tokens")
        llm = ChatOpenAI(
            model=run.planner_model,
            api_key=decrypt_secret(key.value) if key else None,
            base_url="https://openrouter.ai/api/v1" if key else None,
            timeout=120,
            max_retries=2,
            max_tokens=int(max_tokens.value) if max_tokens else 16000,
        )

    recent_interactions = db.query(models.Interaction).filter(
        models.Interaction.project_id == run.project_id
    ).order_by(models.Interaction.created_at.desc()).limit(10).all()[::-1]

    graph = await SupervisorPlanner.decompose(run.prompt, llm, conversation_history=recent_interactions)
    run.task_graph = graph
    run.task_retries = {}
    db.flush()
    return graph


async def process_run(
    run_id: str,
    checkpointer,
    *,
    action_id: str | None = None,
    decision: str | None = None,
    cancel_after: bool = False,
    is_resume: bool = False,
    auto_approve_task: bool = False,
):
    with SessionLocal() as db:
        run = db.get(models.AgentRun, run_id)
        if not run or run.status in {"succeeded", "failed", "cancelled"}:
            return
        subtask_thread_id = run.thread_id
        subtask_id = None
        if run.status == "cancelling":
            graph = _cancel_active_task(db, run)
            run.status = "cancelled"
            run.finished_at = datetime.now(timezone.utc)
            emit(db, run.id, "run.cancelled", {"task_graph": graph})
            db.commit()
            return
        active = db.query(models.AgentRun.id).filter(
            models.AgentRun.project_id == run.project_id,
            models.AgentRun.id != run.id,
            models.AgentRun.intent == "write",
            models.AgentRun.status.in_(["running", "awaiting_question", "awaiting_approval", "cancelling"]),
        ).first()
        if active:
            raise ProjectBusy("Another project run is active")
        try:
            if run.intent == "write":
                db.execute(text("SELECT pg_advisory_xact_lock(:project_id)"), {"project_id": run.project_id})
            run.status = "running"
            run.started_at = run.started_at or datetime.now(timezone.utc)
            run.heartbeat_at = datetime.now(timezone.utc)
            run.attempt += 1

            # A2A: initialise task graph on fresh starts
            if not action_id and not is_resume:
                task_graph = await _initialise_task_graph(db, run)
                first_task = SupervisorPlanner.get_next_task(task_graph)
                if first_task:
                    run.active_task_id = first_task["task_id"]
                    run.subtask_heartbeat_at = datetime.now(timezone.utc)
                    task_graph = _apply_task_status(db, run, first_task["task_id"], "in_progress")
                    emit(db, run.id, "supervisor.plan", {
                        "task_graph": task_graph,
                        "active_task_id": first_task["task_id"],
                    })
                    _emit_task_transition(db, run, "task.started", first_task["task_id"], {
                        "title": first_task["title"],
                        "task_graph": task_graph,
                        "progress": SupervisorPlanner.format_progress(task_graph),
                    })
            elif is_resume and run.task_graph:
                task_graph = run.task_graph
                next_task = next((task for task in task_graph if task["status"] == "in_progress"), None)
                next_task = next_task or SupervisorPlanner.get_next_task(task_graph)
                if next_task:
                    run.active_task_id = next_task["task_id"]
                    run.subtask_heartbeat_at = datetime.now(timezone.utc)
                    task_graph = _apply_task_status(db, run, next_task["task_id"], "in_progress")
                    _emit_task_transition(db, run, "task.started", next_task["task_id"], {
                        "title": next_task["title"],
                        "task_graph": task_graph,
                        "progress": SupervisorPlanner.format_progress(task_graph),
                    })

            active_task = next(
                (task for task in (run.task_graph or []) if task.get("task_id") == run.active_task_id),
                None,
            )
            if active_task:
                child = _ensure_subtask(db, run, active_task)
                subtask_id = child.id
                subtask_thread_id = child.thread_id
                _set_subtask_status(child, "running")

            emit(db, run.id, "run.started", {"attempt": run.attempt})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise RuntimeError("Another project run is active")

        agent = await build_agent(db, run, checkpointer)
        if auto_approve_task and not agent.read_only:
            agent.safe_tools = set(agent.safe_tools) | {"write_file", "patch_file", "create_directory"}
        interactions = db.query(models.Interaction).filter(
            models.Interaction.project_id == run.project_id
        ).order_by(models.Interaction.created_at.desc()).limit(20).all()[::-1]

        response = ""
        task_report = None
        question_payload = None
        task_prompt = run.prompt
        if run.active_task_id and run.task_graph:
            active_task = next((task for task in run.task_graph if task["task_id"] == run.active_task_id), None)
            if active_task:
                dependency_context = []
                for dependency_id in active_task.get("depends_on", []):
                    dependency = next((task for task in run.task_graph if task["task_id"] == dependency_id), None)
                    if dependency and dependency.get("context_bundle"):
                        dependency_context.append(f"{dependency_id}: {json.dumps(dependency['context_bundle'])}")
                request_label = "Original inspection request" if agent.read_only else "Original implementation request"
                read_only_suffix = "\nDo not modify the workspace or ERP; return findings only." if agent.read_only else ""
                context_prefix = ""
                if SupervisorPlanner.is_continuation_request(run.prompt) and interactions:
                    context_lines = [f"{m.role.upper()}: {m.content[:300]}" for m in interactions[-4:] if getattr(m, "content", "").strip()]
                    if context_lines:
                        context_prefix = f"Conversation context:\n" + "\n".join(context_lines) + "\n\n"
                task_prompt = (
                    f"{request_label}:\n{context_prefix}{run.prompt}\n\n"
                    f"Execute only supervisor task {active_task['task_id']}: {active_task['title']}.\n"
                    f"Acceptance criteria: {active_task.get('acceptance_criteria', 'Complete and verify this task without doing later tasks.')}\n"
                    f"Dependencies: {', '.join(active_task.get('depends_on', [])) or 'none'}\n"
                    f"Handoff context: {'; '.join(dependency_context) or 'none'}\n"
                    "Verify the result, then call complete_task exactly once."
                    f"{read_only_suffix}"
                )

        if is_resume:
            stream = agent.stream(task_prompt, subtask_thread_id)
        elif action_id:
            action = db.get(models.PendingAction, action_id)
            if not action or action.run_id != run.id:
                raise ValueError("Approval no longer matches this run")
            if decision != "approve":
                task_id = run.active_task_id
                cancelled = cancel_after
                rejection_message = (
                    "ActionRejected: the requested action was rejected by the approver."
                    if action.tool_name != "install_module_dependency"
                    else "DependencyRejected: the required Odoo module installation was rejected."
                )
                report = {
                    "task_id": task_id,
                    "outcome": "CANCELLED" if cancelled else "FAILED",
                    "done": [],
                    "verification": "Rejected action was not executed.",
                    "errors": rejection_message,
                }
                emit(db, run.id, "task.report", report)
                if task_id:
                    task_status = "cancelled" if cancelled else "failed"
                    graph = _apply_task_status(db, run, task_id, task_status, result=report)
                    _emit_task_transition(db, run, f"task.{task_status}", task_id, {"task_graph": graph, "error_category": "ActionRejected"})
                if subtask_id:
                    child = db.get(models.AgentSubtask, subtask_id)
                    if child:
                        _set_subtask_status(child, "cancelled" if cancelled else "failed", result=report, error=report["errors"])
                run.active_task_id = None
                run.status = "cancelled" if cancelled else "failed"
                run.error_category = None if cancelled else ("DependencyRejected" if action.tool_name == "install_module_dependency" else "ActionRejected")
                run.error_message = report["errors"]
                run.finished_at = datetime.now(timezone.utc)
                action.status = "rejected"
                emit(db, run.id, "final_report", {"scope": "run", **report})
                emit(db, run.id, "run.cancelled" if cancelled else "run.failed", {
                    "category": "ActionRejected" if not cancelled else "CancellationRequested",
                    "message": run.error_message,
                    "retryable": False,
                    "support_id": run.support_id,
                    "task_id": task_id,
                    "tool": action.tool_name,
                })
                db.commit()
                return
            if decision == "approve":
                action.status = "claimed"
                action.claimed_at = datetime.now(timezone.utc)
                db.commit()
                action.status = "executing"
                action.execution_started_at = datetime.now(timezone.utc)
                db.commit()
            if subtask_id:
                child = db.get(models.AgentSubtask, subtask_id)
                if child:
                    _set_subtask_status(child, "running")
            stream = agent.stream(None, subtask_thread_id, reject=decision != "approve")
        else:
            db.add(models.Interaction(project_id=run.project_id, role="user", content=run.prompt))
            db.commit()
            # Inject context bundle from completed dependencies if running a sub-task
            if run.active_task_id and run.task_graph:
                active_task = next((t for t in run.task_graph if t["task_id"] == run.active_task_id), None)
                if active_task:
                    handoffs = []
                    for dep_id in active_task.get("depends_on", []):
                        dep_task = next((t for t in run.task_graph if t["task_id"] == dep_id), None)
                        if dep_task and dep_task.get("context_bundle", {}).get("handoff_summary"):
                            handoffs.append(f"From {dep_id}:\n{dep_task['context_bundle']['handoff_summary']}")
                    
                    if handoffs:
                        combined_handoff = "\n\n".join(handoffs)
                        interactions.append(models.Interaction(
                            project_id=run.project_id, 
                            role="system", 
                            content=f"[A2A Context from previous tasks]\n{combined_handoff}"
                        ))
                        
            stream = agent.stream(task_prompt, subtask_thread_id, interactions)

        async for chunk in stream:
            response += chunk
            with SessionLocal() as event_db:
                current = event_db.get(models.AgentRun, run.id)
                if current.status == "cancelling":
                    graph = _cancel_active_task(event_db, current)
                    current.status = "cancelled"
                    current.finished_at = datetime.now(timezone.utc)
                    emit(event_db, run.id, "run.cancelled", {"task_graph": graph})
                    event_db.commit()
                    return

                now = datetime.now(timezone.utc)
                current.heartbeat_at = now
                if current.active_task_id:
                    current.subtask_heartbeat_at = now
                if subtask_id:
                    child = event_db.get(models.AgentSubtask, subtask_id)
                    if child and child.status == "running":
                        child.heartbeat_at = now

                for event_type, payload in agent.drain_activity():
                    payload = _scope_tool_event(event_db, current, event_type, payload)
                    if event_type == "task.report":
                        if task_report is not None:
                            raise RuntimeError("DuplicateTaskCompletion: current task reported completion more than once")
                        task_report = {**payload, "task_id": current.active_task_id}
                        payload = task_report
                    emit(event_db, run.id, event_type, payload)
                    if event_type == "question":
                        question_payload = payload
                    elif event_type in ("thinking", "plan.updated") and current.active_task_id:
                        _update_subtask_heartbeat(event_db, current, current.active_task_id)

                emit(event_db, run.id, "message.delta", {"text": chunk})
                event_db.commit()

        with SessionLocal() as finish_db:
            current = finish_db.get(models.AgentRun, run.id)
            current_child = finish_db.get(models.AgentSubtask, subtask_id) if subtask_id else None
            for event_type, payload in agent.drain_activity():
                payload = _scope_tool_event(finish_db, current, event_type, payload)
                if event_type == "task.report":
                    if task_report is not None:
                        raise RuntimeError("DuplicateTaskCompletion: current task reported completion more than once")
                    task_report = {**payload, "task_id": current.active_task_id}
                    payload = task_report
                emit(finish_db, run.id, event_type, payload)
                if event_type == "question":
                    question_payload = payload

            attempt_usage = dict(agent.usage)
            current.input_tokens = (current.input_tokens or 0) + attempt_usage["input_tokens"]
            current.output_tokens = (current.output_tokens or 0) + attempt_usage["output_tokens"]
            current.cost_usd = (current.cost_usd or 0) + attempt_usage["cost_usd"]
            cumulative_usage = {
                "input_tokens": current.input_tokens,
                "output_tokens": current.output_tokens,
                "cost_usd": current.cost_usd,
            }
            emit(finish_db, run.id, "usage", {"attempt": attempt_usage, "cumulative": cumulative_usage})
            if response.strip():
                finish_db.add(models.Interaction(project_id=run.project_id, role="agent", content=response))

            if cancel_after:
                graph = _cancel_active_task(finish_db, current)
                current.status = "cancelled"
                current.finished_at = datetime.now(timezone.utc)
                emit(finish_db, run.id, "run.cancelled", {"task_graph": graph})
                finish_db.commit()
                return

            if question_payload:
                question = models.AgentQuestion(
                    run_id=run.id,
                    project_id=run.project_id,
                    requested_by_id=run.requested_by_id,
                    question=str(question_payload.get("question", "")),
                    options=list(question_payload.get("options") or []),
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.action_expiry_minutes),
                )
                finish_db.add(question)
                finish_db.flush()
                current.status = "awaiting_question"
                if current_child:
                    _set_subtask_status(current_child, "awaiting_question")
                emit(finish_db, run.id, "question.required", {
                    "question_id": question.id,
                    "question": question.question,
                    "options": question.options,
                    "expires_at": question.expires_at.isoformat(),
                })
                finish_db.commit()
                return

            call = await agent.pending_call(subtask_thread_id)
            if call:
                if subtask_id:
                    child = finish_db.get(models.AgentSubtask, subtask_id)
                    if child:
                        _set_subtask_status(child, "awaiting_approval")
                preview = agent.preview(call)
                reg_entry = TOOL_REGISTRY.get(call["name"])
                risk_class = reg_entry.risk_class if reg_entry else agent.RISK_CLASSES.get(call["name"], 2)
                action = models.PendingAction(
                    project_id=run.project_id,
                    run_id=run.id,
                    requested_by_id=run.requested_by_id,
                    thread_id=subtask_thread_id,
                    tool_call_id=call["id"],
                    tool_name=call["name"],
                    arguments=call["args"],
                    preview=preview,
                    risk_class=risk_class,
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.action_expiry_minutes),
                    idempotency_key=f"{run.id}:{call['id']}",
                )
                finish_db.add(action)
                finish_db.flush()
                
                active_inst = finish_db.query(models.Instance).filter(
                    models.Instance.project_id == run.project_id,
                    models.Instance.is_active.is_(True),
                ).first()
                decision = PermissionEngine().check(
                    db=finish_db,
                    project_id=run.project_id,
                    user_id=run.requested_by_id,
                    tool_name=call["name"],
                    args=call.get("args") or {},
                    erp_capabilities=getattr(active_inst, "capabilities", None),
                )
                if decision.allowed and not decision.requires_approval:
                    action.status = "executing"
                    action.execution_started_at = datetime.now(timezone.utc)
                    outbox = models.OutboxEvent(
                        aggregate_id=run.id,
                        event_type="action.resume",
                        payload={"action_id": action.id, "decision": "approve"}
                    )
                    finish_db.add(outbox)
                elif not decision.allowed:
                    action.status = "rejected"
                    current.status = "queued"
                    emit(finish_db, run.id, "permission.denied", {
                        "action_id": action.id, "tool": action.tool_name,
                        "resource": resource_for(call["name"], call.get("args") or {}),
                        "reason": decision.reason,
                    })
                    finish_db.add(models.OutboxEvent(
                        aggregate_id=run.id, event_type="action.resume",
                        payload={"action_id": action.id, "decision": "reject"},
                    ))
                else:
                    current.status = "awaiting_approval"
                    emit(finish_db, run.id, "approval.required", {
                        "action_id": action.id, "tool": action.tool_name,
                        "risk_class": action.risk_class, "preview": preview,
                        "arguments": action.arguments,
                        "expires_at": action.expires_at.isoformat(),
                    })
            else:
                if not task_report:
                    if current_child:
                        _set_subtask_status(
                            current_child,
                            "failed",
                            error="Agent finished without calling complete_task.",
                        )
                    current.status = "failed"
                    current.error_category = "MissingTaskReport"
                    current.error_message = "Agent finished without calling complete_task."
                    current.finished_at = datetime.now(timezone.utc)
                    emit(finish_db, run.id, "run.failed", {
                        "category": "MissingTaskReport",
                        "message": current.error_message,
                        "retryable": True,
                        "support_id": current.support_id,
                        "task_id": current.active_task_id,
                        "tool": None,
                    })
                else:
                    task_id = current.active_task_id or task_report.get("task_id")
                    outcome = str(task_report.get("outcome", "FAILED")).upper()
                    task_succeeded = outcome == "SUCCESS"

                    # Backend acceptance check gate: verify if any required acceptance check failed or is pending
                    acceptance_failures = required_acceptance_failures(finish_db, current.id, task_id)
                    if acceptance_failures:
                        task_succeeded = False
                        task_report["errors"] = (
                            f"AcceptanceCheckFailed: {len(acceptance_failures)} required checks missing, pending, or failed: "
                            f"{', '.join(acceptance_failures)}"
                        )
                    if current_child and task_succeeded:
                        _set_subtask_status(current_child, "succeeded", result=task_report)

                    handoff = {
                        "handoff_summary": "\n".join(task_report.get("done") or []),
                        "verification": task_report.get("verification", ""),
                        "errors": task_report.get("errors", ""),
                    }
                    graph = _apply_task_status(
                        finish_db,
                        current,
                        task_id,
                        "done" if task_succeeded else "failed",
                        result=task_report,
                        context_bundle=handoff,
                    )
                    current.active_task_id = None
                    current.subtask_heartbeat_at = None

                    if task_succeeded:
                        _emit_task_transition(finish_db, current, "task.completed", task_id, {
                            "task_graph": graph,
                            "progress": SupervisorPlanner.format_progress(graph),
                        })
                        next_task = SupervisorPlanner.get_next_task(graph)
                        if next_task:
                            current.active_task_id = next_task["task_id"]
                            current.status = "queued"
                            emit(finish_db, run.id, "run.queued", {
                                "task_id": next_task["task_id"],
                                "support_id": run.support_id,
                            })
                            enqueue(finish_db, "run.resume", run.id)
                        elif SupervisorPlanner.is_complete(graph):
                            current.status = "succeeded"
                            current.finished_at = datetime.now(timezone.utc)
                            emit(finish_db, run.id, "supervisor.complete", {
                                "task_graph": graph,
                                "progress": SupervisorPlanner.format_progress(graph),
                            })
                            # Gather files created/modified in the workspace
                            project = finish_db.get(models.Project, run.project_id)
                            changed_files = []
                            diff_warning = None
                            if project and not agent.read_only:
                                try:
                                    ws = Workspace(run.workspace_slug or project.workspace_slug)
                                    base_rev = run.workspace_base_revision or Workspace.EMPTY_TREE_REVISION
                                    diff_text = ws.diff(base_rev, "HEAD")
                                    diff_files = [line[6:].strip() for line in diff_text.splitlines() if line.startswith("+++ b/")]
                                    changed_files = sorted(set(f for f in diff_files if f and not f.startswith(".git")))
                                except Exception as exc:
                                    diff_warning = f"Workspace diff collection encountered an issue: {str(exc)}"

                            done_tasks = [task.get("title", task["task_id"]) for task in graph]

                            # Synthesize task verifications
                            task_verifications = []
                            for task in graph:
                                res = task.get("result") or {}
                                v = res.get("verification")
                                title = task.get("title", task.get("task_id", "Task"))
                                if v:
                                    task_verifications.append(f"{title}: {v}")

                            if task_verifications:
                                verification_text = "\n".join(task_verifications)
                            elif changed_files:
                                verification_text = f"Verified {len(changed_files)} changed workspace file(s)."
                            else:
                                verification_text = "Supervisor tasks completed and verified."

                            if diff_warning:
                                verification_text += f"\n({diff_warning})"

                            run_report = {
                                "scope": "run",
                                "outcome": "PARTIAL" if diff_warning else "SUCCESS",
                                "done": done_tasks,
                                "verification": verification_text,
                                "errors": diff_warning or "",
                                "files": changed_files,
                            }
                            emit(finish_db, run.id, "final_report", run_report)
                            emit(finish_db, run.id, "run.completed", {"status": "succeeded"})

                            # Emit markdown summary of what was built to the chat
                            summary_lines = [
                                f"### {'⚠️ Implementation Complete with Warnings' if diff_warning else '🎉 Implementation Complete'}\n",
                                "**Tasks Completed:**",
                                *(f"- ✅ {t}" for t in done_tasks),
                            ]
                            if changed_files:
                                summary_lines.append("\n**Files Built & Created in Workspace:**")
                                for f in changed_files:
                                    summary_lines.append(f"- `{f}`")
                                summary_lines.append("\nAll module files, schemas, and views are saved and ready in the IDE workspace.")
                            else:
                                summary_lines.append("\nInspection and task verification completed.")
                            if diff_warning:
                                summary_lines.append(f"\n> ⚠️ *Note:* {diff_warning}")
                            summary_message = "\n".join(summary_lines)

                            finish_db.add(models.Interaction(project_id=run.project_id, role="agent", content=summary_message))
                            emit(finish_db, run.id, "message.delta", {"text": summary_message})
                        else:
                            current.status = "failed"
                            current.error_category = "TaskDependencyDeadlock"
                            current.error_message = "No dependency-satisfied supervisor task is available."
                            current.finished_at = datetime.now(timezone.utc)
                            emit(finish_db, run.id, "run.failed", {
                                "category": "TaskDependencyDeadlock",
                                "message": current.error_message,
                                "retryable": False,
                                "support_id": current.support_id,
                                "task_id": None,
                                "tool": None,
                            })
                    else:
                        error_text = str(task_report.get("errors") or "Task reported failure")
                        error_category = "DependencyInstallFailed" if "DependencyInstallFailed" in error_text else "TaskFailed"
                        if not requeue_failed_task(finish_db, current, task_id, task_report):
                            if current_child:
                                _set_subtask_status(current_child, "failed", result=task_report, error=error_text)
                            _emit_task_transition(finish_db, current, "task.failed", task_id, {
                                "task_graph": graph, "error_category": error_category,
                            })
                            current.status = "failed"
                            current.error_category = error_category
                            current.error_message = error_text[:500]
                            current.finished_at = datetime.now(timezone.utc)
                            emit(finish_db, run.id, "final_report", {"scope": "run", **task_report})
                            emit(finish_db, run.id, "run.failed", {
                                "category": error_category,
                                "message": current.error_message,
                                "retryable": False,
                                "support_id": current.support_id,
                                "task_id": task_id,
                                "tool": None,
                            })
                        elif current_child:
                            current_child.retry_count += 1
                            _set_subtask_status(current_child, "queued")

                if action_id:
                    action_rec = finish_db.get(models.PendingAction, action_id)
                    if action_rec and action_rec.status != "expired":
                        action_rec.status = "succeeded" if decision == "approve" else "rejected"

            finish_db.commit()


async def handle_event(event, checkpointer):
    event_id, event_type, run_id, payload = event
    try:
        if event_type == "run.start":
            await process_run(run_id, checkpointer)
        elif event_type == "run.resume":
            await process_run(run_id, checkpointer, is_resume=True)
        elif event_type == "action.resume":
            await process_run(
                run_id,
                checkpointer,
                action_id=payload["action_id"],
                decision=payload["decision"],
                cancel_after=payload.get("cancel_after", False),
                auto_approve_task=payload.get("auto_approve_task", False),
            )
        elif event_type == "deployment.start":
            await asyncio.to_thread(execute_deployment, run_id)
        elif event_type == "run.prepare":
            await asyncio.to_thread(process_run_prepare, run_id)
        with SessionLocal() as db:
            stored = db.get(models.OutboxEvent, event_id)
            stored.completed_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            if isinstance(exc, ProjectBusy):
                stored = db.get(models.OutboxEvent, event_id)
                stored.claimed_at = None
                stored.available_at = datetime.now(timezone.utc) + timedelta(seconds=2)
                stored.last_error = None
                db.commit()
                return
            run = db.get(models.AgentRun, run_id)
            if run:
                budget_exhausted = is_in_flight_budget_error(exc)
                error_category = (
                    "AgentBudgetExceeded"
                    if budget_exhausted
                    else ("TaskExecutionLimitExceeded" if str(exc).startswith("TaskExecutionLimitExceeded") else type(exc).__name__)
                )
                failed_task_id = run.active_task_id
                if failed_task_id:
                    child = db.query(models.AgentSubtask).filter(
                        models.AgentSubtask.parent_run_id == run.id,
                        models.AgentSubtask.task_id == failed_task_id,
                    ).first()
                    if child:
                        _set_subtask_status(child, "failed", error=str(exc))
                    graph = _apply_task_status(db, run, failed_task_id, "failed")
                    _emit_task_transition(db, run, "task.failed", failed_task_id, {
                        "task_graph": graph,
                        "error_category": error_category,
                    })
                    run.active_task_id = None
                run.status = "failed"
                run.error_category = error_category
                run.error_message = (
                    "The AI provider rejected this request because the in-flight request budget is exhausted. "
                    "Wait for active requests to settle or increase the provider credit limit."
                    if budget_exhausted else str(exc)[:500]
                )
                run.error_detail = traceback.format_exc()[-20_000:]
                run.retryable = not budget_exhausted and isinstance(exc, (TimeoutError, ConnectionError, RuntimeError))
                run.finished_at = datetime.now(timezone.utc)
                emit(db, run.id, "run.failed", {
                    "category": error_category,
                    "message": run.error_message,
                    "retryable": run.retryable,
                    "support_id": run.support_id,
                    "task_id": failed_task_id,
                    "tool": None,
                })
            stored = db.get(models.OutboxEvent, event_id)
            stored.last_error = str(exc)[:2000]
            stored.completed_at = datetime.now(timezone.utc)
            db.commit()


async def worker_loop(worker_id: int, checkpointer):
    while True:
        event = await asyncio.to_thread(claim_outbox)
        if event:
            await handle_event(event, checkpointer)
        else:
            await asyncio.sleep(1)


async def serve():
    shutdown_event = threading.Event()
    start_worker_lease_thread(shutdown_event)
    
    async def recovery_loop():
        while not shutdown_event.is_set():
            try:
                await asyncio.to_thread(dispatch_due_schedules)
            except Exception:
                logger.exception("Schedule dispatch failed")
            await asyncio.to_thread(recover_stale_work)
            await asyncio.sleep(10)
            
    recovery_task = asyncio.create_task(recovery_loop())
    
    try:
        async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_url) as checkpointer:
            await checkpointer.setup()
            workers = [asyncio.create_task(worker_loop(i, checkpointer)) for i in range(settings.worker_concurrency)]
            await asyncio.gather(*workers)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        shutdown_event.set()
        recovery_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
