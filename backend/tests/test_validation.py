from pathlib import Path

from unittest.mock import patch

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
