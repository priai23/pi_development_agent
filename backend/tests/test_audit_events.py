"""
tests/test_audit_events.py — Unit tests for audit logging and event tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

import models


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_audit_event_creation(db_session):
    event = models.AuditEvent(
        event_type="permission.granted",
        risk_class="2",
        result="success",
        details={"resource": "workspace:addons/sale_custom/*", "decision": "allow"},
    )
    db_session.add(event)
    db_session.commit()

    saved = db_session.query(models.AuditEvent).filter_by(event_type="permission.granted").one()
    assert saved.risk_class == "2"
    assert saved.details["decision"] == "allow"
    assert saved.created_at is not None


def test_audit_event_filtering(db_session):
    org = models.Organization(name="Acme Corp")
    user = models.User(email="test@example.com", password_hash="hash")
    db_session.add_all([org, user])
    db_session.flush()

    p1 = models.Project(name="P1", organization_id=org.id, created_by_id=user.id, workspace_slug="p1")
    p2 = models.Project(name="P2", organization_id=org.id, created_by_id=user.id, workspace_slug="p2")
    db_session.add_all([p1, p2])
    db_session.flush()

    e1 = models.AuditEvent(project_id=p1.id, event_type="run.started", result="ok", details={})
    e2 = models.AuditEvent(project_id=p2.id, event_type="run.started", result="ok", details={})
    e3 = models.AuditEvent(project_id=p1.id, event_type="action.approved", result="ok", details={})
    db_session.add_all([e1, e2, e3])
    db_session.commit()

    p1_events = db_session.query(models.AuditEvent).filter(models.AuditEvent.project_id == p1.id).all()
    assert len(p1_events) == 2
    types = {ev.event_type for ev in p1_events}
    assert types == {"run.started", "action.approved"}
