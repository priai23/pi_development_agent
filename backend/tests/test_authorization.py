import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import main
from auth import require_project


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_member_cannot_read_another_organization_project(db):
    member = models.User(email="member@example.com", password_hash="unused", role="member")
    first = models.Organization(name="First")
    second = models.Organization(name="Second")
    db.add_all([member, first, second]); db.flush()
    db.add(models.OrganizationMembership(user_id=member.id, organization_id=first.id))
    project = models.Project(name="Private", organization_id=second.id, created_by_id=member.id, workspace_slug="private")
    db.add(project); db.commit()
    with pytest.raises(HTTPException) as denied:
        require_project(db, member, project.id)
    assert denied.value.status_code == 404


def test_admin_can_read_any_project(db):
    admin = models.User(email="admin@example.com", password_hash="unused", role="admin")
    organization = models.Organization(name="Company")
    db.add_all([admin, organization]); db.flush()
    project = models.Project(name="Visible", organization_id=organization.id, created_by_id=admin.id, workspace_slug="visible")
    db.add(project); db.commit()
    assert require_project(db, admin, project.id).id == project.id


def test_class_d_requires_a_different_organization_approver(db):
    requester = models.User(email="requester@example.com", password_hash="unused", role="member")
    approver = models.User(email="approver@example.com", password_hash="unused", role="member")
    organization = models.Organization(name="Company")
    db.add_all([requester, approver, organization]); db.flush()
    db.add_all([
        models.OrganizationMembership(user_id=requester.id, organization_id=organization.id),
        models.OrganizationMembership(user_id=approver.id, organization_id=organization.id, is_approver=True),
    ])
    project = models.Project(name="ERP", organization_id=organization.id, created_by_id=requester.id, workspace_slug="erp")
    db.add(project); db.flush()
    action = models.PendingAction(
        project_id=project.id, requested_by_id=requester.id, thread_id="thread", tool_call_id="call",
        tool_name="configure_inventory", arguments={}, preview={}, risk_class="D",
        expires_at=models.utcnow(),
    )
    db.add(action); db.commit()
    assert main.can_approve(db, requester, action) is False
    assert main.can_approve(db, approver, action) is True


def test_class_c_is_decided_by_requester(db):
    requester = models.User(email="requester-c@example.com", password_hash="unused", role="member")
    other = models.User(email="other-c@example.com", password_hash="unused", role="admin")
    organization = models.Organization(name="Company C")
    db.add_all([requester, other, organization]); db.flush()
    project = models.Project(name="ERP C", organization_id=organization.id, created_by_id=requester.id, workspace_slug="erp-c")
    db.add(project); db.flush()
    action = models.PendingAction(
        project_id=project.id, requested_by_id=requester.id, thread_id="thread-c", tool_call_id="call-c",
        tool_name="write_file", arguments={}, preview={}, risk_class="C", expires_at=models.utcnow(),
    )
    db.add(action); db.commit()
    assert main.can_approve(db, requester, action) is True
    assert main.can_approve(db, other, action) is False
