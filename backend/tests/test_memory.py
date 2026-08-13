import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import main


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_agent_memory_model(db):
    org = models.Organization(name="Test Org")
    admin = models.User(email="admin@example.com", password_hash="hash", role="admin")
    db.add_all([org, admin])
    db.flush()

    project = models.Project(name="Test Proj", organization_id=org.id, created_by_id=admin.id, workspace_slug="test-proj")
    db.add(project)
    db.commit()

    mem = models.AgentMemory(
        project_id=project.id,
        category="schema_insight",
        key="res.partner:loyalty_tier",
        content="loyalty_tier is a Selection field (Standard, Silver, Gold, Platinum)",
        confidence=1.0,
    )
    db.add(mem)
    db.commit()

    saved = db.query(models.AgentMemory).filter_by(key="res.partner:loyalty_tier").first()
    assert saved is not None
    assert saved.category == "schema_insight"
    assert "Selection field" in saved.content
    assert saved.project_id == project.id


def test_agent_memory_endpoints(db):
    admin = models.User(email="admin@example.com", password_hash="unused", role="admin")
    org = models.Organization(name="Test Org")
    db.add_all([admin, org])
    db.flush()

    db.add(models.OrganizationMembership(user_id=admin.id, organization_id=org.id, role="owner", is_approver=True))
    project = models.Project(name="Test Proj", organization_id=org.id, created_by_id=admin.id, workspace_slug="test-proj-2")
    db.add(project)
    db.commit()

    # Override DB and current_user dependencies
    main.app.dependency_overrides[main.get_db] = lambda: db
    main.app.dependency_overrides[main.current_user] = lambda: admin

    try:
        client = TestClient(main.app, base_url="http://localhost")

        # Create Memory via API
        create_res = client.post(
            f"/projects/{project.id}/memories",
            json={
                "category": "user_preference",
                "key": "coding_style",
                "content": "Prefer ponytail minimal code style and complete Odoo 19 XML views",
                "confidence": 1.0,
            },
        )
        assert create_res.status_code == 201
        data = create_res.json()
        assert data["key"] == "coding_style"
        memory_id = data["id"]

        # List Memories via API
        list_res = client.get(f"/projects/{project.id}/memories")
        assert list_res.status_code == 200
        mems = list_res.json()
        assert len(mems) >= 1
        assert mems[0]["key"] == "coding_style"

        # Delete Memory via API
        del_res = client.delete(f"/memories/{memory_id}")
        assert del_res.status_code == 204
    finally:
        main.app.dependency_overrides.clear()
