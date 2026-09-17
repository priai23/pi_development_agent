import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import main
import schemas
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


def test_duplicate_project_name_rejected(db):
    admin = models.User(email="admin-dup@example.com", password_hash="unused", role="admin")
    organization = models.Organization(name="Dup Org")
    db.add_all([admin, organization]); db.flush()
    payload1 = schemas.ProjectCreate(name="Unique Name", organization_id=organization.id)
    main.create_project(payload1, user=admin, db=db)
    
    payload2 = schemas.ProjectCreate(name="unique name", organization_id=organization.id)
    with pytest.raises(HTTPException) as exc:
        main.create_project(payload2, user=admin, db=db)
    assert exc.value.status_code == 409
    assert "already exists" in exc.value.detail


def test_list_sessions_tags_current_session(db):
    from starlette.requests import Request
    from security import token_hash
    from auth import SESSION_COOKIE

    user = models.User(email="sess@example.com", password_hash="hash", role="member")
    db.add(user); db.flush()
    s1 = models.UserSession(id="s1", user_id=user.id, token_hash=token_hash("token-1"), csrf_hash=token_hash("csrf-1"), expires_at=models.utcnow(), last_seen_at=models.utcnow())
    s2 = models.UserSession(id="s2", user_id=user.id, token_hash=token_hash("token-2"), csrf_hash=token_hash("csrf-2"), expires_at=models.utcnow(), last_seen_at=models.utcnow())
    db.add_all([s1, s2]); db.commit()

    req = Request(scope={"type": "http", "headers": [(b"cookie", f"{SESSION_COOKIE}=token-1".encode())]})
    sessions = main.list_sessions(req, user=user, db=db)
    assert len(sessions) == 2
    sess1 = next(s for s in sessions if s.id == "s1")
    sess2 = next(s for s in sessions if s.id == "s2")
    assert sess1.is_current is True
    assert sess2.is_current is False


def test_change_password_preserves_current_session_and_revokes_others(db):
    from starlette.requests import Request
    from security import hash_password, token_hash
    from auth import SESSION_COOKIE

    user = models.User(email="pw@example.com", password_hash=hash_password("OldPassword123!"), role="member")
    db.add(user); db.flush()
    s_curr = models.UserSession(id="curr", user_id=user.id, token_hash=token_hash("token-curr"), csrf_hash=token_hash("csrf-c"), expires_at=models.utcnow(), last_seen_at=models.utcnow())
    s_other = models.UserSession(id="other", user_id=user.id, token_hash=token_hash("token-other"), csrf_hash=token_hash("csrf-o"), expires_at=models.utcnow(), last_seen_at=models.utcnow())
    db.add_all([s_curr, s_other]); db.commit()

    req = Request(scope={"type": "http", "headers": [(b"cookie", f"{SESSION_COOKIE}=token-curr".encode())]})
    payload = schemas.PasswordChange(current_password="OldPassword123!", new_password="NewPassword123!")
    main.change_password(payload, req, user=user, db=db)

    remaining = db.query(models.UserSession).filter(models.UserSession.user_id == user.id).all()
    assert len(remaining) == 1
    assert remaining[0].id == "curr"

