import base64
import sys
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).parents[1]))
import deployment
import models
from config import settings


def test_execute_bridge_creates_signed_job():
    """
    Regression test to ensure execute_bridge correctly builds and signs a job
    without crashing (e.g. from new_token argument errors).
    """
    private = Ed25519PrivateKey.generate()
    config = {
        "signing_private_key": base64.b64encode(private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )).decode(),
        "bridge_url": "http://localhost",
        "bridge_token": "token",
    }
    
    dep = models.Deployment(id="deployment-1", external_job_id=None, status="pending")
    inst = models.Instance(id=1)
    art = models.Artifact(name="test_mod", version="19.0.1.0.0", digest="0"*64)

    with patch("deployment.rpc") as mock_rpc:
        deployment.execute_bridge(dep, inst, art, config, db=None)
        
    assert dep.external_job_id == "deployment-1"
    assert dep.status == "deploying"
    assert mock_rpc.called


def test_shared_deployment_policy_requires_runtime_validation_and_production_evidence():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = models.User(email="owner@example.com", password_hash="hash", role="admin")
        org = models.Organization(name="Org")
        db.add_all([user, org]); db.flush()
        project = models.Project(name="ERP", organization_id=org.id, created_by_id=user.id, workspace_slug="erp", phase="uat")
        db.add(project); db.flush()
        artifact = models.Artifact(
            project_id=project.id, artifact_type="odoo_module", name="sample", version="19.0.1.0.0",
            commit_hash="a" * 40, digest="b" * 64, path="sample", status="static_validated", created_by_id=user.id,
        )
        staging = models.Instance(
            project_id=project.id, erp_type="odoo", url="https://staging.example", environment="staging",
            deployment_config_encrypted="configured",
        )
        db.add_all([artifact, staging]); db.flush()
        validation = models.ValidationRun(project_id=project.id, artifact_id=artifact.id, status="static_passed", report={})
        db.add(validation); db.flush()

        with pytest.raises(ValueError, match="runtime-validated"):
            deployment.request_deployment(db, project, staging, artifact, user.id)

        artifact.status = "validated"
        validation.status = "passed"
        requested = deployment.request_deployment(db, project, staging, artifact, user.id)
        assert requested.status == "pending_approval"

        production = models.Instance(
            project_id=project.id, erp_type="odoo", url="https://prod.example", environment="production",
            deployment_config_encrypted="configured",
        )
        db.add(production); db.flush()
        with pytest.raises(ValueError, match="UAT evidence"):
            deployment.request_deployment(db, project, production, artifact, user.id, "restore backup")
    finally:
        db.close()


def test_run_smoke_tests(monkeypatch):
    class MockResp:
        def __init__(self, status):
            self.status_code = status

    def mock_get(url, timeout):
        if "health" in url:
            return MockResp(200)
        if "version_info" in url:
            return MockResp(200)
        return MockResp(404)

    monkeypatch.setattr("httpx.get", mock_get)
    inst = models.Instance(url="https://odoo.example.com")
    dep = models.Deployment(id="dep-123")
    res = deployment.run_smoke_tests(dep, inst)
    assert res["ok"] is True
    assert len(res["checks"]) == 2
    assert all(c["ok"] for c in res["checks"])


def test_execute_rollback_with_prior_artifact():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = models.User(email="admin@example.com", password_hash="hash", role="admin")
        org = models.Organization(name="Org")
        db.add_all([user, org]); db.flush()
        project = models.Project(name="ERP", organization_id=org.id, created_by_id=user.id, workspace_slug="erp")
        db.add(project); db.flush()
        art1 = models.Artifact(
            project_id=project.id, artifact_type="odoo_module", name="sample", version="19.0.1.0.0",
            commit_hash="1" * 40, digest="1" * 64, path="sample", status="validated", created_by_id=user.id,
        )
        art2 = models.Artifact(
            project_id=project.id, artifact_type="odoo_module", name="sample", version="19.0.1.0.1",
            commit_hash="2" * 40, digest="2" * 64, path="sample", status="validated", created_by_id=user.id,
        )
        db.add_all([art1, art2]); db.flush()
        val = models.ValidationRun(project_id=project.id, artifact_id=art2.id, status="passed", report={})
        db.add(val); db.flush()

        inst = models.Instance(
            project_id=project.id, erp_type="odoo", url="https://staging.example", environment="staging",
            deployment_config_encrypted=None,
        )
        db.add(inst); db.flush()

        dep = models.Deployment(
            project_id=project.id, instance_id=inst.id, artifact_id=art2.id, validation_id=val.id,
            environment="staging", requested_by_id=user.id, prior_artifact_id=art1.id, status="failed",
        )
        db.add(dep); db.flush()

        # When deployment_config_encrypted is None/empty, rollback gracefully marks unrecoverable
        ok = deployment.execute_rollback(dep, db)
        assert ok is False
        assert dep.recovery_state == "unrecoverable"
    finally:
        db.close()

