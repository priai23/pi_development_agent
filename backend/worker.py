import asyncio
import json
import traceback
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

import models
from agent import ERPImplementationAgent, SupervisorPlanner, MAX_TASK_RETRIES, connect_odoo, connect_odoo_json2
from config import settings
from database import SessionLocal
from deployment import execute_deployment
from security import decrypt_secret

# ─── Watchdog tunables ─────────────────────────────────────────────────────────
SUBTASK_STALE_SECONDS = 90   # per sub-task heartbeat threshold
RUN_STALE_SECONDS = 300      # whole-run safety net (supervisor itself silent)


class ProjectBusy(RuntimeError):
    pass


def emit(db, run_id: str, event_type: str, payload: dict) -> None:
    sequence = db.query(func.coalesce(func.max(models.ToolEvent.sequence), 0)).filter(
        models.ToolEvent.run_id == run_id
    ).scalar() + 1
    db.add(models.ToolEvent(run_id=run_id, sequence=sequence, event_type=event_type, payload=payload))
    db.flush()


def enqueue(db, event_type: str, aggregate_id: str, payload: dict | None = None) -> None:
    db.add(models.OutboxEvent(event_type=event_type, aggregate_id=aggregate_id, payload=payload or {}))


def _update_subtask_heartbeat(db, run: models.AgentRun, task_id: str) -> None:
    """Bump both run-level and per-task heartbeat, and mirror into task_graph JSON."""
    now = datetime.now(timezone.utc)
    run.heartbeat_at = now
    run.subtask_heartbeat_at = now
    if run.active_task_id != task_id:
        run.active_task_id = task_id
    if run.task_graph:
        graph = run.task_graph
        for task in graph:
            if task["task_id"] == task_id:
                task["heartbeat_at"] = now.isoformat()
                break
        run.task_graph = graph


def _apply_task_status(db, run: models.AgentRun, task_id: str, status: str) -> None:
    """Update a task node's status in the persisted task_graph JSON."""
    if not run.task_graph:
        return
    graph = list(run.task_graph)
    for task in graph:
        if task["task_id"] == task_id:
            task["status"] = status
            break
    run.task_graph = graph


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
                emit(db, run.id, "task.recovering", {
                    "task_id": task_id,
                    "attempt": current_retries + 1,
                    "max_attempts": MAX_TASK_RETRIES,
                    "reason": f"Sub-task stalled (no heartbeat for {SUBTASK_STALE_SECONDS}s)",
                })
                enqueue(db, "run.resume", run.id)
            else:
                run.status = "failed"
                run.error_category = "SubTaskMaxRetriesExceeded"
                run.error_message = f"Sub-task '{task_id}' failed after {MAX_TASK_RETRIES} attempts."
                run.finished_at = now
                _apply_task_status(db, run, task_id, "failed")
                emit(db, run.id, "task.failed", {
                    "task_id": task_id,
                    "retries_exhausted": True,
                    "message": run.error_message,
                })
                emit(db, run.id, "run.failed", {
                    "message": run.error_message,
                    "retryable": False,
                    "support_id": run.support_id,
                })

        # 4. Whole-run stale fallback
        stale_runs = db.query(models.AgentRun).filter(
            models.AgentRun.status.in_(["running", "cancelling"]),
            (models.AgentRun.heartbeat_at < run_cutoff) | (models.AgentRun.heartbeat_at.is_(None)),
        ).all()
        for run in stale_runs:
            run.status = "queued"
            enqueue(db, "run.resume", run.id)

        # 5. Unclaim stale outbox events
        db.query(models.OutboxEvent).filter(
            models.OutboxEvent.completed_at.is_(None),
            models.OutboxEvent.claimed_at < stale,
        ).update({"claimed_at": None})

        db.commit()


def claim_outbox():
    with SessionLocal() as db:
        event = db.execute(
            select(models.OutboxEvent)
            .where(
                models.OutboxEvent.completed_at.is_(None),
                models.OutboxEvent.claimed_at.is_(None),
                models.OutboxEvent.available_at <= datetime.now(timezone.utc),
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
        raise ValueError("No active Odoo instance is connected")
    if instance.environment != "staging":
        raise ValueError("Agent implementation runs require a staging instance")
    client = await (
        connect_odoo_json2(instance.url, instance.db_name or "", decrypt_secret(instance.api_key_encrypted or ""))
        if instance.auth_method == "json2"
        else connect_odoo(instance.url, instance.db_name or "", instance.username or "", decrypt_secret(instance.password_encrypted or ""))
    )
    model = db.get(models.Setting, "llm_model_name")
    key = db.get(models.Setting, "openrouter_api_key")
    timeout = db.get(models.Setting, "llm_timeout_seconds")
    max_tokens = db.get(models.Setting, "llm_max_output_tokens")
    return ERPImplementationAgent(
        client,
        project.workspace_slug,
        checkpointer,
        model.value if model else "gpt-4o-mini",
        decrypt_secret(key.value) if key else None,
        "https://openrouter.ai/api/v1" if key else None,
        int(timeout.value) if timeout else 120,
        int(max_tokens.value) if max_tokens else 16000,
        project.id,
        run.requested_by_id,
        instance.id,
    )


async def _initialise_task_graph(db, run: models.AgentRun) -> list[dict]:
    """Decompose prompt into task graph and persist — idempotent."""
    if run.task_graph:
        return run.task_graph

    llm = None
    if run.planner_model:
        from langchain_openai import ChatOpenAI
        key = db.get(models.Setting, "openrouter_api_key")
        llm = ChatOpenAI(
            model=run.planner_model,
            api_key=decrypt_secret(key.value) if key else None,
            base_url="https://openrouter.ai/api/v1" if key else None,
            timeout=120,
            max_retries=2,
        )

    graph = await SupervisorPlanner.decompose(run.prompt, llm)
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
):
    with SessionLocal() as db:
        run = db.get(models.AgentRun, run_id)
        if not run or run.status in {"succeeded", "failed", "cancelled"}:
            return
        if run.status == "cancelling":
            run.status = "cancelled"
            run.finished_at = datetime.now(timezone.utc)
            emit(db, run.id, "run.cancelled", {})
            db.commit()
            return
        active = db.query(models.AgentRun.id).filter(
            models.AgentRun.project_id == run.project_id,
            models.AgentRun.id != run.id,
            models.AgentRun.status.in_(["running", "awaiting_approval", "cancelling"]),
        ).first()
        if active:
            raise ProjectBusy("Another project run is active")
        try:
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
                    _apply_task_status(db, run, first_task["task_id"], "in_progress")
                    emit(db, run.id, "supervisor.plan", {
                        "task_graph": task_graph,
                        "active_task_id": first_task["task_id"],
                    })
            elif is_resume and run.task_graph:
                task_graph = run.task_graph
                next_task = SupervisorPlanner.get_next_task(task_graph)
                if next_task:
                    run.active_task_id = next_task["task_id"]
                    run.subtask_heartbeat_at = datetime.now(timezone.utc)
                    _apply_task_status(db, run, next_task["task_id"], "in_progress")
                    emit(db, run.id, "task.started", {
                        "task_id": next_task["task_id"],
                        "title": next_task["title"],
                        "progress": SupervisorPlanner.format_progress(task_graph),
                    })

            emit(db, run.id, "run.started", {"attempt": run.attempt})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise RuntimeError("Another project run is active")

        agent = await build_agent(db, run, checkpointer)
        interactions = db.query(models.Interaction).filter(
            models.Interaction.project_id == run.project_id
        ).order_by(models.Interaction.created_at.desc()).limit(20).all()[::-1]

        response = ""
        _final_report_seen = False

        if is_resume:
            stream = agent.stream(None, run.thread_id)
        elif action_id:
            action = db.get(models.PendingAction, action_id)
            if not action or action.run_id != run.id:
                raise ValueError("Approval no longer matches this run")
            if decision == "approve":
                action.status = "claimed"
                action.claimed_at = datetime.now(timezone.utc)
                db.commit()
                action.status = "executing"
                action.execution_started_at = datetime.now(timezone.utc)
                db.commit()
            stream = agent.stream(None, run.thread_id, reject=decision != "approve")
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
                        
            stream = agent.stream(run.prompt, run.thread_id, interactions)

        async for chunk in stream:
            response += chunk
            with SessionLocal() as event_db:
                current = event_db.get(models.AgentRun, run.id)
                if current.status == "cancelling":
                    current.status = "cancelled"
                    current.finished_at = datetime.now(timezone.utc)
                    emit(event_db, run.id, "run.cancelled", {})
                    event_db.commit()
                    return

                now = datetime.now(timezone.utc)
                current.heartbeat_at = now
                if current.active_task_id:
                    current.subtask_heartbeat_at = now

                for event_type, payload in agent.drain_activity():
                    emit(event_db, run.id, event_type, payload)
                    if event_type == "final_report":
                        _final_report_seen = True
                    elif event_type in ("thinking", "plan.updated") and current.active_task_id:
                        _update_subtask_heartbeat(event_db, current, current.active_task_id)

                emit(event_db, run.id, "message.delta", {"text": chunk})
                event_db.commit()

        with SessionLocal() as finish_db:
            current = finish_db.get(models.AgentRun, run.id)
            for event_type, payload in agent.drain_activity():
                emit(finish_db, run.id, event_type, payload)
                if event_type == "final_report":
                    _final_report_seen = True

            current.input_tokens = agent.usage["input_tokens"]
            current.output_tokens = agent.usage["output_tokens"]
            current.cost_usd = agent.usage["cost_usd"]
            emit(finish_db, run.id, "usage", agent.usage)
            if response.strip():
                finish_db.add(models.Interaction(project_id=run.project_id, role="agent", content=response))

            call = await agent.pending_call(run.thread_id)
            if call:
                preview = agent.preview(call)
                action = models.PendingAction(
                    project_id=run.project_id,
                    run_id=run.id,
                    requested_by_id=run.requested_by_id,
                    thread_id=run.thread_id,
                    tool_call_id=call["id"],
                    tool_name=call["name"],
                    arguments=call["args"],
                    preview=preview,
                    risk_class=agent.RISK_CLASSES[call["name"]],
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.action_expiry_minutes),
                    idempotency_key=f"{run.id}:{call['id']}",
                )
                finish_db.add(action)
                finish_db.flush()
                
                is_safe = call["name"] in agent.SAFE_TOOLS
                if is_safe:
                    action.status = "executing"
                    action.execution_started_at = datetime.now(timezone.utc)
                    outbox = models.OutboxEvent(
                        aggregate_id=run.id,
                        event_type="action.resume",
                        payload={"action_id": action.id, "decision": "approve"}
                    )
                    finish_db.add(outbox)
                else:
                    current.status = "awaiting_approval"
                    emit(finish_db, run.id, "approval.required", {
                        "action_id": action.id, "tool": action.tool_name,
                        "risk_class": action.risk_class, "preview": preview,
                    })
            else:
                # Gate "succeeded" strictly on emit_final_report
                if _final_report_seen:
                    current.status = "cancelled" if cancel_after else "succeeded"
                else:
                    current.status = "failed"
                    current.error_category = "MissingFinalReport"
                    current.error_message = "Agent finished without calling emit_final_report."
                    emit(finish_db, run.id, "run.failed", {
                        "message": current.error_message,
                        "retryable": True,
                        "support_id": current.support_id,
                    })

                current.finished_at = datetime.now(timezone.utc)
                if current.status in ["succeeded", "failed", "cancelled"] and current.task_graph:
                    graph = current.task_graph
                    
                    # Extract context for the finished task if succeeded
                    messages = []
                    context = {}
                    if current.status == "succeeded":
                        if checkpointer:
                            state = checkpointer.get({"configurable": {"thread_id": run.thread_id}})
                            if state and hasattr(state, "values") and "messages" in state.values:
                                messages = state.values["messages"]

                        if messages and hasattr(agent, "llm"):
                            context = await SupervisorPlanner.extract_context(current.active_task_id or "", messages, agent.llm)
                    
                    for task in graph:
                        if task["status"] == "in_progress":
                            task["status"] = "done" if current.status == "succeeded" else "failed"
                            if current.status == "succeeded":
                                task["context_bundle"] = context
                            
                    current.task_graph = graph
                    current.active_task_id = None
                    emit(finish_db, run.id, "supervisor.complete", {
                        "task_graph": current.task_graph,
                        "progress": SupervisorPlanner.format_progress(current.task_graph),
                    })

                if action_id:
                    action_rec = finish_db.get(models.PendingAction, action_id)
                    if action_rec and action_rec.status != "expired":
                        action_rec.status = "succeeded" if decision == "approve" else "rejected"

                emit(finish_db, run.id,
                     "run.cancelled" if cancel_after else "run.completed",
                     {"status": current.status})

            finish_db.commit()


async def handle_event(event, checkpointer):
    event_id, event_type, run_id, payload = event
    try:
        if event_type == "run.start":
            await process_run(run_id, checkpointer)
        elif event_type == "run.resume":
            await process_run(run_id, checkpointer, is_resume=True)
        elif event_type == "action.resume":
            await process_run(run_id, checkpointer, action_id=payload["action_id"], decision=payload["decision"], cancel_after=payload.get("cancel_after", False))
        elif event_type == "deployment.start":
            await asyncio.to_thread(execute_deployment, run_id)
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
                run.status = "failed"
                run.error_category = type(exc).__name__
                run.error_message = str(exc)[:500]
                run.error_detail = traceback.format_exc()[-20_000:]
                run.retryable = isinstance(exc, (TimeoutError, ConnectionError, RuntimeError))
                run.finished_at = datetime.now(timezone.utc)
                emit(db, run.id, "run.failed", {
                    "message": run.error_message, "retryable": run.retryable, "support_id": run.support_id,
                })
            stored = db.get(models.OutboxEvent, event_id)
            stored.last_error = str(exc)[:2000]
            stored.completed_at = datetime.now(timezone.utc)
            db.commit()


async def serve():
    recover_stale_work()
    async with AsyncPostgresSaver.from_conn_string(settings.checkpoint_url) as checkpointer:
        await checkpointer.setup()
        while True:
            event = claim_outbox()
            if event:
                await handle_event(event, checkpointer)
            else:
                await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(serve())
