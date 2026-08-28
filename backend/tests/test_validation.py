from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from unittest.mock import patch

import main
from validation import package_module, validate_module, validate_module_full


def module(tmp_path: Path) -> Path:
    root = tmp_path / "sample_module"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "__manifest__.py").write_text(
        "{'name': 'Sample', 'version': '19.0.1.0.0', 'depends': ['base'], 'data': [], 'installable': True}",
        encoding="utf-8",
    )
    return root


def test_valid_odoo_19_module_passes_static_checks(tmp_path):
    root = module(tmp_path)
    report = validate_module(root)
    assert report["passed"] is True
    assert len(report["digest"]) == 64
    assert package_module(root) == package_module(root)


def test_deprecated_view_and_shell_are_rejected(tmp_path):
    root = module(tmp_path)
    (root / "models.py").write_text("import subprocess\nsubprocess.run(['whoami'])\n", encoding="utf-8")
    (root / "views.xml").write_text("<odoo><tree attrs=\"{}\"/></odoo>", encoding="utf-8")
    report = validate_module(root)
    assert report["passed"] is False
    failures = {check["name"] for check in report["checks"] if not check["passed"]}
    assert "shell execution" in failures
    assert "legacy tree view" in failures


def test_missing_manifest_file_and_models_init_are_rejected(tmp_path):
    root = module(tmp_path)
    (root / "__manifest__.py").write_text(
        "{'name': 'Sample', 'version': '19.0.1.0.0', 'depends': ['base'], "
        "'data': ['views/missing.xml'], 'installable': True}",
        encoding="utf-8",
    )
    models_dir = root / "models"
    models_dir.mkdir()
    (models_dir / "sample.py").write_text("from odoo import models\n", encoding="utf-8")

    report = validate_module(root)

    failures = {check["name"] for check in report["checks"] if not check["passed"]}
    assert "manifest path:views/missing.xml" in failures
    assert "models init" in failures


def test_full_validation_requires_disposable_odoo_success(tmp_path):
    root = module(tmp_path)
    with patch("validation.validate_module_runtime", return_value={
        "ok": True,
        "checks": [{"name": "odoo_exit_code", "passed": True, "message": "Odoo exited with 0"}],
        "test_count": 1,
        "error": None,
        "log": "",
    }):
        report = validate_module_full(root, "sample_module")

    assert report["passed"] is True
    assert report["static"]["passed"] is True
    assert report["runtime"]["ok"] is True


def test_full_validation_does_not_run_odoo_after_static_failure(tmp_path):
    root = module(tmp_path)
    (root / "__init__.py").unlink()
    with patch("validation.validate_module_runtime") as runtime:
        report = validate_module_full(root, "sample_module")

    assert report["passed"] is False
    runtime.assert_not_called()


def test_runtime_validation_derives_missing_postgres_config(tmp_path):
    root = module(tmp_path)
    captured = {}

    def validate(_job, config):
        captured.update(config)
        return {"ok": True, "checks": [], "test_count": 1, "error": None, "log": ""}

    with (
        patch("validation.settings.validation_postgres_admin_dsn", ""),
        patch("validation.settings.database_url", "postgresql+psycopg://agent:secret@localhost:5432/erp_agent"),
        patch("validation.platform.system", return_value="Darwin"),
        patch("validation.importlib.util.spec_from_file_location", return_value=SimpleNamespace(
            loader=SimpleNamespace(exec_module=lambda _module: None)
        )),
        patch("validation.importlib.util.module_from_spec", return_value=SimpleNamespace(
            run_validate_module=validate
        )),
    ):
        validate_module_full(root, "sample_module")

    assert captured["postgres_admin_dsn"] == "postgresql://agent:secret@localhost:5432/postgres"
    assert captured["postgres_host"] == "host.docker.internal"
    assert captured["postgres_user"] == "agent"
    assert captured["postgres_password"] == "secret"


def test_duplicate_xml_ids_and_missing_model_acl_are_rejected(tmp_path):
    root = module(tmp_path)
    models_dir = root / "models"
    models_dir.mkdir()
    (models_dir / "__init__.py").write_text("from . import sample\n", encoding="utf-8")
    (models_dir / "sample.py").write_text(
        "from odoo import models\nclass Sample(models.Model):\n    _name = 'sample.record'\n    _description = 'Sample'\n",
        encoding="utf-8",
    )
    (root / "one.xml").write_text("<odoo><record id='same' model='ir.ui.view'/></odoo>", encoding="utf-8")
    (root / "two.xml").write_text("<odoo><record id='same' model='ir.actions.act_window'/></odoo>", encoding="utf-8")

    report = validate_module(root)

    failures = {check["name"] for check in report["checks"] if not check["passed"]}
    assert "xml id:same" in failures
    assert "access control" in failures


def test_odoo19_obsolete_security_fields_are_rejected(tmp_path):
    root = module(tmp_path)
    (root / "security.xml").write_text(
        "<odoo><record id='g' model='res.groups'><field name='category_id'/></record>"
        "</odoo>",
        encoding="utf-8",
    )

    failures = {check["name"] for check in validate_module(root)["checks"] if not check["passed"]}

    assert "odoo19 res.groups fields:security.xml" in failures


def test_quick_deploy_uses_workspace_root(tmp_path):
    module(tmp_path)
    workspace = SimpleNamespace(root=tmp_path)
    failed_report = {"passed": False, "static": {"checks": []}, "runtime": {"checks": []}}

    with (
        patch("main.require_project", return_value=SimpleNamespace(workspace_slug="project")),
        patch("main.Workspace", return_value=workspace),
        patch("main.validate_module_full", return_value=failed_report),
    ):
        try:
            main.quick_deploy_module(1, SimpleNamespace(id=1), SimpleNamespace())
        except HTTPException as exc:
            assert exc.status_code == 409
        else:
            raise AssertionError("Expected failed validation response")
