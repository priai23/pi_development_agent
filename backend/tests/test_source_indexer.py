"""Tests for the stdlib-only Odoo source indexer (backend/source_indexer.py).

These tests run without Odoo, a database, or any third-party packages —
just the indexer and the fixture addon in tests/fixtures/test_addon/.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from source_indexer import (
    index_addon,
    parse_python_file,
    parse_manifest,
    parse_xml_file,
    parse_access_csv,
    SymbolRecord,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "test_addon"
ADDON_ROOT = FIXTURE_ROOT / "primacy_test_fixture"
SNAPSHOT_ID = "test-snapshot-001"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

class TestManifestParser:
    def test_parses_manifest_fields(self):
        records = parse_manifest(
            ADDON_ROOT / "__manifest__.py",
            module="primacy_test_fixture",
            snapshot_id=SNAPSHOT_ID,
            rel_path="__manifest__.py",
        )
        assert len(records) == 1
        r = records[0]
        assert r.kind == "manifest"
        assert r.name == "primacy_test_fixture"
        assert r.payload["version"] == "19.0.1.0.0"
        assert "base" in r.payload["depends"]
        assert r.payload["installable"] is True

    def test_digest_is_sha256_hex(self):
        records = parse_manifest(
            ADDON_ROOT / "__manifest__.py",
            module="primacy_test_fixture",
            snapshot_id=SNAPSHOT_ID,
            rel_path="__manifest__.py",
        )
        digest = records[0].digest
        assert digest is not None and len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


# ---------------------------------------------------------------------------
# Python model indexer
# ---------------------------------------------------------------------------

class TestPythonParser:
    @pytest.fixture(scope="class")
    def records(self):
        return parse_python_file(
            ADDON_ROOT / "models" / "leave_request.py",
            module="primacy_test_fixture",
            snapshot_id=SNAPSHOT_ID,
            rel_path="models/leave_request.py",
        )

    def test_finds_model_symbol(self, records):
        models = [r for r in records if r.kind == "model"]
        assert any(r.name == "primacy.leave.request" for r in models), \
            f"Expected model 'primacy.leave.request', got: {[r.name for r in models]}"

    def test_model_payload(self, records):
        model_sym = next(r for r in records if r.kind == "model" and r.name == "primacy.leave.request")
        assert model_sym.payload["_description"] == "Leave Request"
        assert model_sym.payload["_order"] == "date_from desc"
        assert "mail.thread" in model_sym.payload["_inherit"]

    def test_finds_fields(self, records):
        fields = [r for r in records if r.kind == "field"]
        field_names = {r.name for r in fields}
        assert "name" in field_names
        assert "employee_id" in field_names
        assert "date_from" in field_names
        assert "state" in field_names
        assert "duration_days" in field_names

    def test_many2one_comodel(self, records):
        employee_field = next(r for r in records if r.kind == "field" and r.name == "employee_id")
        assert employee_field.payload["field_type"] == "Many2one"
        assert employee_field.payload["comodel"] == "hr.employee"

    def test_compute_field_attributes(self, records):
        dur = next(r for r in records if r.kind == "field" and r.name == "duration_days")
        assert dur.payload["compute"] == "_compute_duration"

    def test_required_field_attribute(self, records):
        name_field = next(r for r in records if r.kind == "field" and r.name == "name")
        assert name_field.payload["required"] is True

    def test_finds_methods(self, records):
        methods = [r for r in records if r.kind == "method"]
        method_names = {r.name for r in methods}
        assert "action_submit" in method_names
        assert "action_approve" in method_names
        assert "_check_dates" in method_names
        assert "_compute_duration" in method_names

    def test_constrains_decorator(self, records):
        check = next(r for r in records if r.kind == "method" and r.name == "_check_dates")
        assert any("constrains" in d for d in check.payload["decorators"])

    def test_depends_decorator(self, records):
        compute = next(r for r in records if r.kind == "method" and r.name == "_compute_duration")
        assert any("depends" in d for d in compute.payload["decorators"])
        assert "date_from" in compute.payload["depends"]
        assert "date_to" in compute.payload["depends"]

    def test_model_line_range(self, records):
        model_sym = next(r for r in records if r.kind == "model")
        assert model_sym.line_start is not None and model_sym.line_start > 0
        assert model_sym.line_end is not None and model_sym.line_end >= model_sym.line_start

    def test_method_has_digest(self, records):
        method = next(r for r in records if r.kind == "method" and r.name == "action_submit")
        assert method.digest is not None and len(method.digest) == 64


# ---------------------------------------------------------------------------
# XML record indexer
# ---------------------------------------------------------------------------

class TestXmlParser:
    @pytest.fixture(scope="class")
    def records(self):
        return parse_xml_file(
            ADDON_ROOT / "views" / "leave_request_views.xml",
            module="primacy_test_fixture",
            snapshot_id=SNAPSHOT_ID,
            rel_path="views/leave_request_views.xml",
        )

    def test_finds_form_view(self, records):
        views = [r for r in records if r.kind == "view"]
        assert any(r.name == "view_leave_request_form" for r in views)

    def test_finds_list_view(self, records):
        views = [r for r in records if r.kind == "view"]
        assert any(r.name == "view_leave_request_list" for r in views)

    def test_finds_action(self, records):
        actions = [r for r in records if r.kind == "action"]
        assert any(r.name == "action_leave_request" for r in actions)

    def test_view_model_field(self, records):
        form = next(r for r in records if r.name == "view_leave_request_form")
        # Model is stored at SymbolRecord.model (extracted from XML), not in payload["model"]
        assert form.model == "primacy.leave.request"

    def test_all_symbols_have_module(self, records):
        assert all(r.module == "primacy_test_fixture" for r in records)


# ---------------------------------------------------------------------------
# Security CSV indexer
# ---------------------------------------------------------------------------

class TestAccessCsvParser:
    @pytest.fixture(scope="class")
    def records(self):
        return parse_access_csv(
            ADDON_ROOT / "security" / "ir.model.access.csv",
            module="primacy_test_fixture",
            snapshot_id=SNAPSHOT_ID,
            rel_path="security/ir.model.access.csv",
        )

    def test_finds_two_rules(self, records):
        assert len(records) == 2

    def test_user_rule_permissions(self, records):
        user_rule = next(r for r in records if "user" in r.name)
        assert user_rule.payload["perm_read"] is True
        assert user_rule.payload["perm_write"] is True
        assert user_rule.payload["perm_create"] is True
        assert user_rule.payload["perm_unlink"] is False

    def test_manager_rule_permissions(self, records):
        mgr_rule = next(r for r in records if "manager" in r.name)
        assert mgr_rule.payload["perm_unlink"] is True

    def test_model_references(self, records):
        for r in records:
            assert r.model is not None
            assert "primacy_leave_request" in (r.model or "")


# ---------------------------------------------------------------------------
# Full addon index
# ---------------------------------------------------------------------------

class TestAddonIndexer:
    @pytest.fixture(scope="class")
    def all_records(self):
        return index_addon(ADDON_ROOT, SNAPSHOT_ID)

    def test_total_symbol_count_reasonable(self, all_records):
        assert len(all_records) > 10, f"Expected >10 symbols, got {len(all_records)}"

    def test_all_kinds_present(self, all_records):
        kinds = {r.kind for r in all_records}
        assert "model" in kinds
        assert "field" in kinds
        assert "method" in kinds
        assert "view" in kinds
        assert "acl" in kinds
        assert "manifest" in kinds

    def test_all_records_have_snapshot_id(self, all_records):
        assert all(r.snapshot_id == SNAPSHOT_ID for r in all_records)

    def test_all_records_have_module(self, all_records):
        assert all(r.module == "primacy_test_fixture" for r in all_records)

    def test_no_records_from_skip_dirs(self, all_records):
        for r in all_records:
            if r.path:
                assert "__pycache__" not in r.path

    def test_model_record_references_correct_model_name(self, all_records):
        model_records = [r for r in all_records if r.kind == "model"]
        names = {r.name for r in model_records}
        assert "primacy.leave.request" in names

    def test_missing_manifest_dir_returns_empty(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "not_an_addon"
            empty.mkdir()
            result = index_addon(empty, SNAPSHOT_ID)
            assert result == []
