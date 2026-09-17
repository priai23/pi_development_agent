"""Model Conformance Gate (Phase 13).

Validates model outputs, JSON schemas, tool arguments, and manifest files
against strict Odoo 19 architectural and syntactic rules before execution.
"""
from __future__ import annotations

import ast
import re
from defusedxml import ElementTree as ET
from dataclasses import dataclass
from typing import Any


@dataclass
class ConformanceResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    category: str | None = None


# ---------------------------------------------------------------------------
# Python model & manifest validation rules
# ---------------------------------------------------------------------------

_FORBIDDEN_PATTERNS = [
    (re.compile(r"\bfrom\s+openerp\b"), "Odoo 19 uses 'odoo', not legacy 'openerp'"),
    (re.compile(r"\bos\.system\s*\("), "Direct os.system execution is prohibited in Odoo modules"),
    (re.compile(r"\bexec\s*\("), "exec() calls are prohibited in Odoo modules"),
    (re.compile(r"\beval\s*\("), "Unsafe eval() is prohibited; use ast.literal_eval or safe_eval"),
]

_DEPRECATED_ODOO_FIELDS = {
    "_columns": "Use Python 3 class-attribute fields (e.g. name = fields.Char(...)) instead of _columns",
    "_defaults": "Use default=... keyword parameter on fields instead of _defaults dictionary",
}


def validate_python_code(code: str, path: str = "<unknown>") -> ConformanceResult:
    """Check Python code syntax and Odoo 19 conventions using ast."""
    errors = []
    warnings = []

    # 1. Parse AST
    try:
        tree = ast.parse(code, filename=path)
    except SyntaxError as exc:
        return ConformanceResult(
            valid=False,
            errors=[f"SyntaxError at line {exc.lineno}: {exc.msg}"],
            warnings=[],
            category="PythonSyntaxError",
        )

    # 2. Check forbidden patterns
    for pattern, msg in _FORBIDDEN_PATTERNS:
        if pattern.search(code):
            errors.append(msg)

    # 3. Check Odoo model conventions via AST
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            if target.id in _DEPRECATED_ODOO_FIELDS:
                                errors.append(f"{target.id} in class {node.name}: {_DEPRECATED_ODOO_FIELDS[target.id]}")

    return ConformanceResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        category="ModelConformanceError" if errors else None,
    )


def validate_manifest_content(code: str) -> ConformanceResult:
    """Ensure __manifest__.py evaluates to a valid dictionary with required keys."""
    try:
        data = ast.literal_eval(code)
    except Exception as exc:
        return ConformanceResult(
            valid=False,
            errors=[f"Invalid manifest syntax: {exc}"],
            warnings=[],
            category="ManifestSyntaxError",
        )

    if not isinstance(data, dict):
        return ConformanceResult(
            valid=False,
            errors=["Manifest must be a Python dictionary"],
            warnings=[],
            category="ManifestStructureError",
        )

    errors = []
    warnings = []

    required_keys = ["name", "version", "depends"]
    for k in required_keys:
        if k not in data:
            errors.append(f"Missing required manifest key: '{k}'")

    version = data.get("version", "")
    if version and not (version.startswith("19.0.") or version.startswith("1.0")):
        warnings.append(f"Manifest version '{version}' does not follow Odoo 19 format ('19.0.x.y.z')")

    if not isinstance(data.get("depends", []), list):
        errors.append("'depends' in manifest must be a list of module name strings")

    return ConformanceResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        category="ManifestConformanceError" if errors else None,
    )


def validate_xml_views(content: str, path: str = "<unknown>") -> ConformanceResult:
    """Validate Odoo XML view structure using ElementTree."""
    errors = []
    warnings = []

    try:
        # Strip xml declaration if present before parsing
        clean = content.strip()
        if clean.startswith("<?xml"):
            clean = clean.split("\n", 1)[-1]
        root = ET.fromstring(clean)
    except ET.ParseError as exc:
        return ConformanceResult(
            valid=False,
            errors=[f"XML ParseError: {exc}"],
            warnings=[],
            category="XmlSyntaxError",
        )

    if root.tag != "odoo" and root.tag != "openerp":
        errors.append(f"Root tag must be <odoo>, got <{root.tag}>")

    for record in root.findall(".//record"):
        rec_id = record.get("id")
        rec_model = record.get("model")
        if not rec_id:
            errors.append("XML <record> is missing required 'id' attribute")
        if not rec_model:
            errors.append(f"XML <record id='{rec_id}'> is missing required 'model' attribute")

    return ConformanceResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        category="XmlConformanceError" if errors else None,
    )
