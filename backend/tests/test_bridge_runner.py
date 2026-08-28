import base64
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2]))
from bridge_runner.runner import RunnerError, _parse_odoo_log, backup_database, canonical_job, restore_database, run_odoo_module_command, run_validate_module, safe_extract, verify_job  # noqa: E402


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


@pytest.mark.parametrize(("operation", "flag"), [("install", "-i"), ("upgrade", "-u")])
def test_odoo_module_command_uses_fixed_operation_flag(operation, flag):
    with patch("bridge_runner.runner.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "ok"
        run.return_value.stderr = ""

        assert run_odoo_module_command(["odoo-bin", "-d", "test", "--stop-after-init"], operation, "sample_module") == "ok"

    assert run.call_args.args[0][-2:] == [flag, "sample_module"]
    assert run.call_args.kwargs["check"] is False


def test_odoo_module_command_fails_closed():
    with patch("bridge_runner.runner.subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stdout = ""
        run.return_value.stderr = "registry failed"
        with pytest.raises(RunnerError, match="registry failed"):
            run_odoo_module_command(["odoo-bin", "-d", "test"], "install", "sample_module")

    with pytest.raises(RunnerError, match="module name"):
        run_odoo_module_command(["odoo-bin"], "install", "../escape")


def test_disposable_validation_requires_zero_odoo_exit_code(tmp_path):
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("sample_module/__init__.py", "")
        bundle.writestr("sample_module/__manifest__.py", "{'name': 'Sample'}")
    payload = archive.read_bytes()
    job = {
        "job_uuid": "validation-1",
        "module_name": "sample_module",
        "artifact_zip_b64": base64.b64encode(payload).decode(),
        "artifact_digest": __import__("hashlib").sha256(payload).hexdigest(),
    }
    config = {"postgres_admin_dsn": None}

    with patch("bridge_runner.runner._run_docker_odoo", return_value=(1, "quiet failure")):
        result = run_validate_module(job, config)

    assert result["ok"] is False
    assert any(check["name"] == "odoo_exit_code" and not check["passed"] for check in result["checks"])


def test_disposable_validation_runs_only_target_module_tests(tmp_path):
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("sample_module/__init__.py", "")
        bundle.writestr("sample_module/__manifest__.py", "{'name': 'Sample'}")
    payload = archive.read_bytes()
    job = {
        "job_uuid": "validation-tags",
        "module_name": "sample_module",
        "artifact_zip_b64": base64.b64encode(payload).decode(),
        "artifact_digest": __import__("hashlib").sha256(payload).hexdigest(),
    }

    with patch("bridge_runner.runner._run_docker_odoo", return_value=(0, "Ran 1 test errors=0 failures=0")) as run:
        result = run_validate_module(job, {"postgres_admin_dsn": None})

    assert run.call_args.args[0][-2:] == ["--test-tags", "/sample_module"]
    assert result["test_count"] == 1


def test_odoo19_test_result_is_counted():
    parsed = _parse_odoo_log("0 failed, 0 error(s) of 6 tests when loading database 'test'")

    assert parsed["test_count"] == 6
    assert any(check["name"] == "tests_pass" and check["passed"] for check in parsed["checks"])


def test_database_backup_and_restore_use_fixed_commands(tmp_path):
    destination = tmp_path / "database.dump"

    def create_backup(command, **kwargs):
        destination.write_bytes(b"backup")

    with patch("bridge_runner.runner.subprocess.run", side_effect=create_backup) as run:
        backup_database(["pg_dump", "--format=custom"], destination)
    assert run.call_args.args[0][-1] == f"--file={destination}"

    with patch("bridge_runner.runner.subprocess.run") as run:
        restore_database(["pg_restore", "--clean"], destination)
    assert run.call_args.args[0][-1] == str(destination)


def test_upgrade_validation_installs_baseline_before_update(tmp_path):
    archive = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("sample_module/__init__.py", "")
        bundle.writestr("sample_module/__manifest__.py", "{'name': 'Sample'}")
    payload = archive.read_bytes()
    job = {
        "job_uuid": "upgrade-validation",
        "module_name": "sample_module",
        "is_upgrade": True,
        "artifact_zip_b64": base64.b64encode(payload).decode(),
        "artifact_digest": __import__("hashlib").sha256(payload).hexdigest(),
    }

    with patch("bridge_runner.runner._run_docker_odoo", side_effect=[(0, "baseline"), (0, "Ran 1 test errors=0 failures=0")]) as run:
        result = run_validate_module(job, {"postgres_admin_dsn": None})

    assert run.call_args_list[0].args[0][2:4] == ["-i", "sample_module"]
    assert run.call_args_list[1].args[0][2:4] == ["-u", "sample_module"]
    assert result["ok"] is True
