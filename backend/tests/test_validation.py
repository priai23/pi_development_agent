from pathlib import Path

from validation import package_module, validate_module


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
