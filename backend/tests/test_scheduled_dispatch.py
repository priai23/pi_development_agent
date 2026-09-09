from datetime import datetime, timedelta, timezone

import models
import worker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def db_setup():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def seed(Session, *, instance=True, active=False):
    db = Session()
    org = models.Organization(id=1, name="Org", monthly_budget_usd=100)
    user = models.User(id=1, email="u@example.com", password_hash="x", role="admin")
    project = models.Project(id=1, name="P", organization_id=1, created_by_id=1, workspace_slug="p")
    db.add_all([org, user, project])
    db.add(models.OrganizationMembership(organization_id=1, user_id=1, role="admin", is_approver=True))
    db.add(models.Setting(key="openrouter_api_key", value="configured", encrypted=True))
    if instance:
        db.add(models.Instance(project_id=1, erp_type="odoo", url="http://erp", environment="staging", status="ready", is_active=True, version_info={"server_serie": "19.0", "server_edition": "community"}))
    db.add(models.AgentSchedule(project_id=1, requested_by_id=1, prompt="list installed modules", interval_seconds=3600, next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    if active:
        db.add(models.AgentRun(project_id=1, requested_by_id=1, prompt="busy", thread_id="busy", intent="write", status="running"))
    db.commit(); db.close()


def run_dispatch(monkeypatch, Session):
    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(worker, "decrypt_secret", lambda value: "configured")
    monkeypatch.setattr(worker, "enqueue", lambda *args: None)
    return worker.dispatch_due_schedules()


def test_due_schedule_dispatches_run(monkeypatch):
    Session = db_setup(); seed(Session)
    assert run_dispatch(monkeypatch, Session) == 1
    schedule = Session().query(models.AgentSchedule).one()
    assert schedule.last_run_id is not None


def test_active_run_defers_schedule(monkeypatch):
    Session = db_setup(); seed(Session, active=True)
    assert run_dispatch(monkeypatch, Session) == 0
    schedule = Session().query(models.AgentSchedule).one()
    assert schedule.last_error.startswith("Deferred: project already has active run")


def test_failed_prerequisite_is_actionable(monkeypatch):
    Session = db_setup(); seed(Session, instance=False)
    assert run_dispatch(monkeypatch, Session) == 0
    schedule = Session().query(models.AgentSchedule).one()
    assert schedule.last_error == "Deferred: no active staging instance"


def test_due_schedule_stamps_idempotency_key(monkeypatch):
    Session = db_setup(); seed(Session)
    assert run_dispatch(monkeypatch, Session) == 1
    schedule = Session().query(models.AgentSchedule).one()
    assert schedule.idempotency_key is not None
    assert len(schedule.idempotency_key) == 64  # sha256


def test_pri_erp_instance_schedule_dispatches(monkeypatch):
    Session = db_setup()
    db = Session()
    org = models.Organization(id=1, name="Org", monthly_budget_usd=100)
    user = models.User(id=1, email="u@example.com", password_hash="x", role="admin")
    project = models.Project(id=1, name="P", organization_id=1, created_by_id=1, workspace_slug="p")
    db.add_all([org, user, project])
    db.add(models.OrganizationMembership(organization_id=1, user_id=1, role="admin", is_approver=True))
    db.add(models.Setting(key="openrouter_api_key", value="configured", encrypted=True))
    db.add(models.Instance(project_id=1, erp_type="pri_erp", url="https://admin.sh.prierp.com", environment="staging", status="ready", is_active=True, version_info={}))
    db.add(models.AgentSchedule(project_id=1, requested_by_id=1, prompt="check pri erp health", interval_seconds=3600, next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    db.commit(); db.close()

    assert run_dispatch(monkeypatch, Session) == 1
    schedule = Session().query(models.AgentSchedule).one()
    assert schedule.last_run_id is not None
    assert schedule.last_error is None

