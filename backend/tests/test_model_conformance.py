"""Tests for Model Conformance Gate (backend/model_conformance.py)."""
from __future__ import annotations

import pytest
from model_conformance import (
    validate_python_code,
    validate_manifest_content,
    validate_xml_views,
)


def test_validate_python_code_valid():
    code = """
from odoo import fields, models

class CustomModel(models.Model):
    _name = "custom.model"
    _description = "Custom Model"

    name = fields.Char(string="Name", required=True)
"""
    res = validate_python_code(code)
    assert res.valid is True
    assert len(res.errors) == 0


def test_validate_python_code_syntax_error():
    code = "def bad_syntax(: pass"
    res = validate_python_code(code)
    assert res.valid is False
    assert res.category == "PythonSyntaxError"
    assert len(res.errors) > 0


def test_validate_python_code_forbidden_openerp():
    code = "from openerp import models, fields"
    res = validate_python_code(code)
    assert res.valid is False
    assert any("openerp" in e for e in res.errors)


def test_validate_python_code_deprecated_columns():
    code = """
from odoo import models

class OldModel(models.Model):
    _name = "old.model"
    _columns = {
        'name': fields.char('Name', size=64),
    }
"""
    res = validate_python_code(code)
    assert res.valid is False
    assert any("_columns" in e for e in res.errors)


def test_validate_manifest_valid():
    manifest = """{
    'name': 'Test Module',
    'version': '19.0.1.0.0',
    'depends': ['base', 'mail'],
    'installable': True,
}"""
    res = validate_manifest_content(manifest)
    assert res.valid is True
    assert len(res.errors) == 0


def test_validate_manifest_missing_keys():
    manifest = "{'name': 'Incomplete'}"
    res = validate_manifest_content(manifest)
    assert res.valid is False
    assert any("version" in e for e in res.errors)
    assert any("depends" in e for e in res.errors)


def test_validate_xml_views_valid():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_test_form" model="ir.ui.view">
        <field name="name">test.form</field>
        <field name="model">test.model</field>
        <field name="arch" type="xml">
            <form string="Test"><field name="name"/></form>
        </field>
    </record>
</odoo>
"""
    res = validate_xml_views(xml)
    assert res.valid is True
    assert len(res.errors) == 0


def test_validate_xml_views_missing_model():
    xml = """<odoo>
    <record id="broken_record">
        <field name="name">Test</field>
    </record>
</odoo>"""
    res = validate_xml_views(xml)
    assert res.valid is False
    assert any("model" in e for e in res.errors)
