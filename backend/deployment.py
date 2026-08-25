import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import models
from config import settings
from database import SessionLocal
from security import decrypt_secret, new_token


BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,180}$")


def request_deployment(db, project: models.Project, instance: models.Instance, artifact: models.Artifact, requested_by_id: int, rollback_plan: str = "") -> models.Deployment:
    if instance.project_id != project.id or artifact.project_id != project.id:
        raise ValueError("Instance and artifact must belong to this project")
    if instance.environment not in {"staging", "production"}:
        raise ValueError("Deployment target must be staging or production")
    if not instance.is_active or not instance.deployment_config_encrypted:
        raise ValueError("Configure an active deployment bridge or Odoo.sh repository first")
    validation = db.query(models.ValidationRun).filter(
        models.ValidationRun.artifact_id == artifact.id,
        models.ValidationRun.status == "passed",
    ).order_by(models.ValidationRun.created_at.desc()).first()
    if not validation or artifact.status != "validated":
        raise ValueError("Only a runtime-validated immutable artifact can be deployed")
    if instance.environment == "production":
        staged = db.query(models.Deployment).filter(
            models.Deployment.artifact_id == artifact.id,
            models.Deployment.environment == "staging",
            models.Deployment.status == "succeeded",
        ).first()
        uat = db.query(models.UATEvidence).filter(models.UATEvidence.artifact_id == artifact.id).first()
        if not staged or not uat or project.phase not in {"uat", "ready_for_production"}:
            raise ValueError("Production requires the exact artifact to pass staging and have UAT evidence")
        if not rollback_plan.strip():
            raise ValueError("Production deployment requires a rollback plan")
    deployment = models.Deployment(
        project_id=project.id,
        instance_id=instance.id,
        artifact_id=artifact.id,
        validation_id=validation.id,
        environment=instance.environment,
        requested_by_id=requested_by_id,
        rollback_plan=rollback_plan,
        status="pending_approval",
    )
    db.add(deployment)
    db.flush()
    return deployment


def canonical_job(job: dict) -> bytes:
    return json.dumps(job, sort_keys=True, separators=(",", ":")).encode()


def rpc(url: str, token: str, endpoint: str, params: dict) -> dict | None:
    response = httpx.post(
        f"{url.rstrip('/')}{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "method": "call", "params": params, "id": 1},
        timeout=30,
        follow_redirects=False,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError("Deployment bridge rejected the request")
    return body.get("result")


def execute_bridge(deployment: models.Deployment, instance: models.Instance, artifact: models.Artifact, config: dict, db) -> None:
    job = {
        "job_uuid": deployment.id,
        "operation": "install" if artifact.version.endswith(".0.0") else "upgrade",
        "module_name": artifact.name,
        "module_version": artifact.version,
        "artifact_url": f"{settings.public_base_url.rstrip('/')}/deployments/{deployment.id}/artifact",
        "artifact_digest": artifact.digest,
        "nonce": new_token(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
    }
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(config["signing_private_key"]))
    signed = {**job, "signature": base64.b64encode(private.sign(canonical_job(job))).decode()}
    rpc(config["bridge_url"], config["bridge_token"], "/primacy/bridge/v1/jobs", signed)
    deployment.external_job_id = deployment.id
    deployment.status = "deploying"


def execute_odoo_sh(deployment: models.Deployment, project: models.Project, artifact: models.Artifact, config: dict) -> None:
    branch_template = config["production_branch"] if deployment.environment == "production" else config["staging_branch"]
    branch = branch_template.format(project=project.id, release=deployment.id[:8])
    if not BRANCH_RE.fullmatch(branch) or branch.startswith("-"):
        raise RuntimeError("Configured Odoo.sh branch name is invalid")
    workspace = (settings.workspace_root / project.workspace_slug).resolve(strict=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as key_file:
        key_file.write(config["git_deploy_key"])
        key_path = key_file.name
    try:
        os.chmod(key_path, 0o600)
        environment = {**os.environ, "GIT_SSH_COMMAND": f"ssh -i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"}
        subprocess.run(["git", "-C", str(workspace), "remote", "remove", "primacy-odoo-sh"], capture_output=True, timeout=15)
        subprocess.run(["git", "-C", str(workspace), "remote", "add", "primacy-odoo-sh", config["repository_url"]], check=True, capture_output=True, timeout=15)
        subprocess.run(["git", "-C", str(workspace), "push", "primacy-odoo-sh", f"{artifact.commit_hash}:refs/heads/{branch}"], env=environment, check=True, capture_output=True, text=True, timeout=300)
    finally:
        os.unlink(key_path)
    deployment.external_job_id = branch
    deployment.status = "deploying"


def execute_deployment(deployment_id: str) -> None:
    with SessionLocal() as db:
        deployment = db.get(models.Deployment, deployment_id)
        if not deployment or deployment.status not in {"approved", "queued"}:
            return
        instance = db.get(models.Instance, deployment.instance_id)
        artifact = db.get(models.Artifact, deployment.artifact_id)
        project = db.get(models.Project, deployment.project_id)
        config = json.loads(decrypt_secret(instance.deployment_config_encrypted or ""))
        deployment.status = "executing"
        db.commit()
        try:
            if instance.hosting_type == "odoo_sh":
                execute_odoo_sh(deployment, project, artifact, config)
            else:
                execute_bridge(deployment, instance, artifact, config, db)
            db.commit()
        except Exception as exc:
            deployment.status = "failed"
            deployment.logs = f"{type(exc).__name__}: {exc}"[:100_000]
            deployment.finished_at = datetime.now(timezone.utc)
            db.commit()
            raise


def refresh_bridge_deployment(deployment: models.Deployment, instance: models.Instance) -> None:
    config = json.loads(decrypt_secret(instance.deployment_config_encrypted or ""))
    result = rpc(config["bridge_url"], config["bridge_token"], "/primacy/bridge/v1/jobs/status", {"job_uuid": deployment.external_job_id})
    mapping = {"queued": "deploying", "running": "deploying", "succeeded": "succeeded", "failed": "failed", "rolled_back": "rolled_back"}
    deployment.status = mapping[result["state"]]
    deployment.logs = result.get("logs", "")[-100_000:]
    if deployment.status in {"succeeded", "failed", "rolled_back"}:
        deployment.finished_at = datetime.now(timezone.utc)


def generate_signing_config() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()
