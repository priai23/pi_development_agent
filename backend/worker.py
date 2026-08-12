import asyncio
import json
import traceback
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

import models
from agent import ERPImplementationAgent, connect_odoo, connect_odoo_json2
from config import settings
from database import SessionLocal
from deployment import execute_deployment
from security import decrypt_secret


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


def recover_stale_work() -> None:
    now = datetime.now(timezone.utc)
    stale = now - timedelta(minutes=5)
    with SessionLocal() as db:
        expired = db.query(models.PendingAction).filter(
            models.PendingAction.status == "pending", models.PendingAction.expires_at <= now
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
        stale_pending = db.query(models.PendingAction).filter(
            ((models.PendingAction.status == "claimed") & (models.PendingAction.claimed_at < stale)) |
            ((models.PendingAction.status == "executing") & (models.PendingAction.execution_started_at < stale))
        ).all()
        for action in stale_pending:
            action.status = "approved"
            action.claimed_at = None
            if action.run_id:
                enqueue(db, "action.resume", action.run_id, {"action_id": action.id, "decision": "approve"})
        stale_runs = db.query(models.AgentRun).filter(
            models.AgentRun.status.in_(["running", "cancelling"]),
            models.AgentRun.heartbeat_at < stale,
        ).all()
        for run in stale_runs:
            run.status = "queued"
            enqueue(db, "run.resume", run.id)
        db.query(models.OutboxEvent).filter(
            models.OutboxEvent.completed_at.is_(None), models.OutboxEvent.claimed_at < stale
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


async def process_run(run_id: str, checkpointer, *, action_id: str | None = None, decision: str | None = None, cancel_after: bool = False, is_resume: bool = False):
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
                current.heartbeat_at = datetime.now(timezone.utc)
                for event_type, payload in agent.drain_activity():
                    emit(event_db, run.id, event_type, payload)
                emit(event_db, run.id, "message.delta", {"text": chunk})
                event_db.commit()

        with SessionLocal() as finish_db:
            current = finish_db.get(models.AgentRun, run.id)
            for event_type, payload in agent.drain_activity():
                emit(finish_db, run.id, event_type, payload)
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
                current.status = "awaiting_approval"
                emit(finish_db, run.id, "approval.required", {
                    "action_id": action.id, "tool": action.tool_name,
                    "risk_class": action.risk_class, "preview": preview,
                })
            else:
                current.status = "cancelled" if cancel_after else "succeeded"
                current.finished_at = datetime.now(timezone.utc)
                if action_id:
                    action = finish_db.get(models.PendingAction, action_id)
                    if action.status != "expired":
                        action.status = "succeeded" if decision == "approve" else "rejected"
                emit(finish_db, run.id, "run.cancelled" if cancel_after else "run.completed", {"status": current.status})
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
