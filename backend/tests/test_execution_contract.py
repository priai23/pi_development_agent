import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent import SupervisorPlanner, module_label_matches, tool_outcome, validate_inspection_url
from permissions import active_grant, resource_for
import models


@pytest.fixture
def permission_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_read_only_intent_wins_over_negated_write_words():
    prompt = "Inspect the connected database and find Training Centre. Do not create or modify any files."
    assert SupervisorPlanner.is_read_only_request(prompt)


def test_inspection_url_is_limited_to_connected_host():
    assert validate_inspection_url("https://erp.example.com/web", "https://erp.example.com").geturl() == "https://erp.example.com/web"
    with pytest.raises(ValueError, match="connected ERP host"):
        validate_inspection_url("https://attacker.example/web", "https://erp.example.com")


def test_failed_structured_check_is_not_reported_as_success():
    assert tool_outcome("run_project_check", '{"ok": false, "exit_code": 1}') == "failed"
    assert tool_outcome("run_project_check", '{"ok": true, "exit_code": 0}') == "succeeded"


def test_module_lookup_matches_display_name_and_returns_technical_identity():
    row = {"name": "hr_skills", "shortdesc": "Skills Management"}
    assert module_label_matches(row, "skills management")
    assert module_label_matches(row, "HR Skills")


def test_permission_matcher_prefers_specific_project_rule(permission_db):
    user_id, project_id = 1, 1
    global_rule = models.PermissionGrant(
        project_id=None, user_id=None, created_by_id=user_id,
        resource="workspace:*", decision="allow",
    )
    project_rule = models.PermissionGrant(
        project_id=project_id, user_id=user_id, created_by_id=user_id,
        resource="workspace:models/*", decision="allow",
    )
    permission_db.add_all([global_rule, project_rule]); permission_db.commit()
    grant = active_grant(permission_db, project_id, user_id, resource_for("write_file", {"path": "models/x.py"}))
    assert grant is project_rule


def test_permission_deny_cannot_be_shadowed_by_allow(permission_db):
    permission_db.add_all([
        models.PermissionGrant(
            project_id=None, user_id=None, created_by_id=1,
            resource="command:*", decision="deny",
        ),
        models.PermissionGrant(
            project_id=1, user_id=1, created_by_id=1,
            resource="command:python *", decision="allow",
        ),
    ])
    permission_db.commit()

    assert active_grant(permission_db, 1, 1, "command:python -V").decision == "deny"
