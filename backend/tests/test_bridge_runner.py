import base64
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).parents[2]))
from bridge_runner.runner import RunnerError, canonical_job, safe_extract, verify_job  # noqa: E402


def signed_job():
    private = Ed25519PrivateKey.generate()
    job = {
        "job_uuid": "job-1", "operation": "upgrade", "module_name": "sample_module",
        "module_version": "19.0.1.0.0", "artifact_url": "https://artifacts.example/module.zip",
        "artifact_digest": "0" * 64, "nonce": "unique-nonce",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    job["signature"] = base64.b64encode(private.sign(canonical_job(job))).decode()
    public = private.public_key()
    return job, public


def test_signed_job_and_replay_protection():
    job, public = signed_job()
    verify_job(job, public, set())
    with pytest.raises(RunnerError, match="already used"):
        verify_job(job, public, {job["nonce"]})


def test_signature_covers_module_name():
    job, public = signed_job()
    job["module_name"] = "other_module"
    with pytest.raises(Exception):
        verify_job(job, public, set())


def test_expired_signed_job_is_rejected():
    private = Ed25519PrivateKey.generate()
    job = {
        "job_uuid": "expired", "operation": "upgrade", "module_name": "sample_module",
        "module_version": "19.0.1.0.0", "artifact_url": "https://artifacts.example/module.zip",
        "artifact_digest": "0" * 64, "nonce": "expired-nonce",
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    job["signature"] = base64.b64encode(private.sign(canonical_job(job))).decode()
    with pytest.raises(RunnerError, match="expired"):
        verify_job(job, private.public_key(), set())


def test_archive_rejects_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape", "bad")
    with pytest.raises(RunnerError, match="unsafe path"):
        safe_extract(archive, tmp_path / "out", "sample_module")


def test_archive_requires_expected_manifest(tmp_path):
    archive = tmp_path / "module.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("sample_module/__manifest__.py", json.dumps({"name": "Sample"}))
    extracted = safe_extract(archive, tmp_path / "out", "sample_module")
    assert extracted.name == "sample_module"
