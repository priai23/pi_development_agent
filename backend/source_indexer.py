"""stdlib-only Odoo 19 source indexer.

Parses addon source code using only ast, xml.etree.ElementTree, csv, tokenize,
and ast.literal_eval. No third-party dependencies.

Entry point: index_addon_roots(roots, snapshot_id, db)
"""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import logging
import tokenize
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SymbolRecord:
    snapshot_id: str
    module: str
    model: str | None
    kind: str          # model|field|method|view|action|rule|acl|cron|js_component|manifest
    name: str
    path: str | None
    line_start: int | None
    line_end: int | None
    digest: str | None
    payload: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Python source parsers (via ast)
# ---------------------------------------------------------------------------

def _source_excerpt(source_lines: list[str], start: int, end: int, max_lines: int = 50) -> str:
    """Return a capped slice of source lines (1-indexed, inclusive)."""
    return "\n".join(source_lines[start - 1 : min(end, start - 1 + max_lines)])


def _digest_excerpt(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _collect_decorators(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    decorators = []
    for dec in func_node.decorator_list:
        if isinstance(dec, ast.Name):
            decorators.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            parts = []
            n: ast.expr = dec
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name):
                parts.append(n.id)
            decorators.append(".".join(reversed(parts)))
        elif isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Attribute):
                parts = []
                n2: ast.expr = dec.func
                while isinstance(n2, ast.Attribute):
                    parts.append(n2.attr)
                    n2 = n2.value
                if isinstance(n2, ast.Name):
                    parts.append(n2.id)
                decorators.append(".".join(reversed(parts)))
            elif isinstance(dec.func, ast.Name):
                decorators.append(dec.func.id)
    return decorators


def _extract_string_value(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.List):
        return None  # handled separately for _inherit lists
    return None


def parse_python_file(
    path: Path,
    module: str,
    snapshot_id: str,
    rel_path: str,
) -> list[SymbolRecord]:
    """Extract model, field, and method symbols from a Python source file."""
    try:
        source_text = path.read_text(encoding="utf-8")
        tree = ast.parse(source_text, filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        logger.warning("parse_python_file: skipping %s: %s", path, exc)
        return []

    source_lines = source_text.splitlines()
    records: list[SymbolRecord] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        # Collect class-level assignments for _name, _inherit, _description, etc.
        class_attrs: dict[str, ast.expr] = {}
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        class_attrs[target.id] = item.value
            elif isinstance(item, (ast.AnnAssign,)) and isinstance(item.target, ast.Name):
                if item.value:
                    class_attrs[item.target.id] = item.value

        model_name_val = _extract_string_value(class_attrs.get("_name"))
        inherit_val = class_attrs.get("_inherit")
        inherit: list[str] = []
        if isinstance(inherit_val, ast.Constant) and isinstance(inherit_val.value, str):
            inherit = [inherit_val.value]
        elif isinstance(inherit_val, ast.List):
            inherit = [
                elt.value for elt in inherit_val.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]

        effective_model = model_name_val or (inherit[0] if inherit else None)
        if effective_model is None:
            continue  # not an Odoo model class

        description = _extract_string_value(class_attrs.get("_description"))
        order = _extract_string_value(class_attrs.get("_order"))
        excerpt = _source_excerpt(source_lines, node.lineno, node.end_lineno or node.lineno)

        records.append(SymbolRecord(
            snapshot_id=snapshot_id,
            module=module,
            model=effective_model,
            kind="model",
            name=effective_model,
            path=rel_path,
            line_start=node.lineno,
            line_end=node.end_lineno,
            digest=_digest_excerpt(excerpt),
            payload={
                "class": node.name,
                "_name": model_name_val,
                "_inherit": inherit,
                "_description": description,
                "_order": order,
            },
        ))

        # Fields
        FIELD_TYPES = {
            "Char", "Text", "Html", "Integer", "Float", "Monetary",
            "Boolean", "Date", "Datetime", "Binary", "Image",
            "Many2one", "One2many", "Many2many",
            "Selection", "Reference", "Serialized",
            "Id",
        }
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            if not isinstance(item.value, ast.Call):
                continue
            call = item.value
            # fields.Char(...) or just Char(...)
            call_name: str | None = None
            if isinstance(call.func, ast.Attribute) and call.func.attr in FIELD_TYPES:
                call_name = call.func.attr
            elif isinstance(call.func, ast.Name) and call.func.id in FIELD_TYPES:
                call_name = call.func.id
            if call_name is None:
                continue

            for target in item.targets:
                if not isinstance(target, ast.Name):
                    continue
                field_name = target.id

                kw: dict[str, str | bool | None] = {}
                for kw_node in call.keywords:
                    if kw_node.arg and isinstance(kw_node.value, ast.Constant):
                        kw[kw_node.arg] = kw_node.value.value
                    elif kw_node.arg and isinstance(kw_node.value, ast.Name):
                        kw[kw_node.arg] = kw_node.value.id

                comodel = None
                if isinstance(call.args, list) and call.args:
                    first = call.args[0]
                    if isinstance(first, ast.Constant):
                        comodel = first.value

                records.append(SymbolRecord(
                    snapshot_id=snapshot_id,
                    module=module,
                    model=effective_model,
                    kind="field",
                    name=field_name,
                    path=rel_path,
                    line_start=item.lineno,
                    line_end=item.end_lineno,
                    digest=None,
                    payload={
                        "field_type": call_name,
                        "comodel": comodel or kw.get("comodel_name"),
                        "compute": kw.get("compute"),
                        "inverse": kw.get("inverse"),
                        "search": kw.get("search"),
                        "store": kw.get("store"),
                        "related": kw.get("related"),
                        "required": kw.get("required"),
                        "readonly": kw.get("readonly"),
                        "string": kw.get("string"),
                    },
                ))

        # Methods
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = _collect_decorators(item)
            depends = []
            for dec in item.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "depends":
                    for dep_arg in dec.args:
                        if isinstance(dep_arg, ast.Constant):
                            depends.append(dep_arg.value)

            excerpt = _source_excerpt(source_lines, item.lineno, item.end_lineno or item.lineno)
            records.append(SymbolRecord(
                snapshot_id=snapshot_id,
                module=module,
                model=effective_model,
                kind="method",
                name=item.name,
                path=rel_path,
                line_start=item.lineno,
                line_end=item.end_lineno,
                digest=_digest_excerpt(excerpt),
                payload={
                    "decorators": decorators,
                    "depends": depends,
                    "is_async": isinstance(item, ast.AsyncFunctionDef),
                    "args": [a.arg for a in item.args.args],
                },
            ))

    return records


# ---------------------------------------------------------------------------
# Manifest parser (ast.literal_eval)
# ---------------------------------------------------------------------------

def parse_manifest(path: Path, module: str, snapshot_id: str, rel_path: str) -> list[SymbolRecord]:
    try:
        raw = path.read_text(encoding="utf-8")
        manifest = ast.literal_eval(raw)
    except Exception as exc:
        logger.warning("parse_manifest: %s: %s", path, exc)
        return []
    if not isinstance(manifest, dict):
        return []
    return [SymbolRecord(
        snapshot_id=snapshot_id,
        module=module,
        model=None,
        kind="manifest",
        name=module,
        path=rel_path,
        line_start=None,
        line_end=None,
        digest=hashlib.sha256(path.read_bytes()).hexdigest(),
        payload={
            "name": manifest.get("name"),
            "version": manifest.get("version"),
            "depends": manifest.get("depends", []),
            "data": manifest.get("data", []),
            "demo": manifest.get("demo", []),
            "auto_install": manifest.get("auto_install", False),
            "installable": manifest.get("installable", True),
            "license": manifest.get("license"),
        },
    )]


# ---------------------------------------------------------------------------
# XML record parser (ElementTree)
# ---------------------------------------------------------------------------

_XML_RECORD_TAGS = {"record", "template", "menuitem", "act_window", "report"}
_VIEW_MODELS = {"ir.ui.view"}
_ACTION_MODELS = {"ir.actions.act_window", "ir.actions.server", "ir.actions.report"}
_RULE_MODELS = {"ir.rule"}
_ACL_MODELS = {"ir.model.access"}
_CRON_MODELS = {"ir.cron"}


def parse_xml_file(path: Path, module: str, snapshot_id: str, rel_path: str) -> list[SymbolRecord]:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        logger.warning("parse_xml_file: %s: %s", path, exc)
        return []

    records: list[SymbolRecord] = []
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    root = tree.getroot()

    # Iterate all record/template elements with an id
    for elem in root.iter():
        tag = elem.tag
        xml_id = elem.get("id")
        if not xml_id:
            continue

        if tag == "record":
            model_attr = elem.get("model", "")
            name_field = next(
                (f.text for f in elem if f.get("name") == "name"), None
            ) or xml_id
            inherit_id = next(
                (f.get("ref") or f.text for f in elem if f.get("name") == "inherit_id"), None
            )

            if model_attr in _VIEW_MODELS:
                arch_elem = next((f for f in elem if f.get("name") == "arch"), None)
                arch_summary = ET.tostring(arch_elem, encoding="unicode")[:500] if arch_elem is not None else None
                records.append(SymbolRecord(
                    snapshot_id=snapshot_id,
                    module=module,
                    model=next((f.text or f.get("ref") for f in elem if f.get("name") == "model"), None),
                    kind="view",
                    name=xml_id,
                    path=rel_path,
                    line_start=None,
                    line_end=None,
                    digest=digest,
                    payload={"xml_id": xml_id, "inherit_id": inherit_id, "view_name": name_field, "arch_summary": arch_summary},
                ))
            elif model_attr in _ACTION_MODELS:
                records.append(SymbolRecord(
                    snapshot_id=snapshot_id, module=module, model=model_attr, kind="action",
                    name=xml_id, path=rel_path, line_start=None, line_end=None, digest=digest,
                    payload={"xml_id": xml_id, "action_model": model_attr, "action_name": name_field},
                ))
            elif model_attr in _RULE_MODELS:
                records.append(SymbolRecord(
                    snapshot_id=snapshot_id, module=module, model=model_attr, kind="rule",
                    name=xml_id, path=rel_path, line_start=None, line_end=None, digest=digest,
                    payload={"xml_id": xml_id, "rule_name": name_field},
                ))
            elif model_attr in _CRON_MODELS:
                records.append(SymbolRecord(
                    snapshot_id=snapshot_id, module=module, model=model_attr, kind="cron",
                    name=xml_id, path=rel_path, line_start=None, line_end=None, digest=digest,
                    payload={"xml_id": xml_id, "cron_name": name_field},
                ))
            else:
                # Generic external ID record
                records.append(SymbolRecord(
                    snapshot_id=snapshot_id, module=module, model=model_attr, kind="xml_id",
                    name=xml_id, path=rel_path, line_start=None, line_end=None, digest=digest,
                    payload={"xml_id": xml_id, "record_name": name_field, "model": model_attr},
                ))

        elif tag == "template":
            records.append(SymbolRecord(
                snapshot_id=snapshot_id, module=module, model=None, kind="view",
                name=xml_id, path=rel_path, line_start=None, line_end=None, digest=digest,
                payload={"xml_id": xml_id, "inherit_id": elem.get("inherit_id")},
            ))

    return records


# ---------------------------------------------------------------------------
# Security CSV parser
# ---------------------------------------------------------------------------

def parse_access_csv(path: Path, module: str, snapshot_id: str, rel_path: str) -> list[SymbolRecord]:
    try:
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
    except Exception as exc:
        logger.warning("parse_access_csv: %s: %s", path, exc)
        return []
    records = []
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for row in rows:
        xml_id = row.get("id", "").strip()
        if not xml_id:
            continue
        records.append(SymbolRecord(
            snapshot_id=snapshot_id, module=module,
            model=row.get("model_id:id", row.get("model_id", "")).strip() or None,
            kind="acl",
            name=xml_id,
            path=rel_path,
            line_start=None,
            line_end=None,
            digest=digest,
            payload={
                "xml_id": xml_id,
                "name": row.get("name", "").strip(),
                "group": row.get("group_id:id", row.get("group_id", "")).strip() or None,
                "perm_read": row.get("perm_read", "0").strip() == "1",
                "perm_write": row.get("perm_write", "0").strip() == "1",
                "perm_create": row.get("perm_create", "0").strip() == "1",
                "perm_unlink": row.get("perm_unlink", "0").strip() == "1",
            },
        ))
    return records


# ---------------------------------------------------------------------------
# JavaScript component parser (tokenize + regex, best-effort)
# ---------------------------------------------------------------------------

_JS_REGISTRY_PATTERN = re.compile(r'registry\s*\.\s*(?:category\([^)]+\)\s*\.\s*)?add\s*\(\s*["\']([^"\']+)["\']')
_JS_OWL_COMPONENT = re.compile(r'class\s+(\w+)\s+extends\s+(?:owl\.)?Component')
_JS_PATCH_PATTERN = re.compile(r'patch\s*\(\s*["\']([^"\']+)["\']')


def parse_js_file(path: Path, module: str, snapshot_id: str, rel_path: str) -> list[SymbolRecord]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    records = []
    digest = hashlib.sha256(text.encode()).hexdigest()
    for m in _JS_REGISTRY_PATTERN.finditer(text):
        records.append(SymbolRecord(
            snapshot_id=snapshot_id, module=module, model=None, kind="js_component",
            name=m.group(1), path=rel_path, line_start=None, line_end=None, digest=digest,
            payload={"kind": "registry_add", "key": m.group(1)},
        ))
    for m in _JS_OWL_COMPONENT.finditer(text):
        records.append(SymbolRecord(
            snapshot_id=snapshot_id, module=module, model=None, kind="js_component",
            name=m.group(1), path=rel_path, line_start=None, line_end=None, digest=digest,
            payload={"kind": "owl_component", "class_name": m.group(1)},
        ))
    for m in _JS_PATCH_PATTERN.finditer(text):
        records.append(SymbolRecord(
            snapshot_id=snapshot_id, module=module, model=None, kind="js_component",
            name=m.group(1), path=rel_path, line_start=None, line_end=None, digest=digest,
            payload={"kind": "patch", "target": m.group(1)},
        ))
    return records


# ---------------------------------------------------------------------------
# Module walker
# ---------------------------------------------------------------------------

_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules", ".venv", "venv"}


def _iter_addon_files(addon_root: Path) -> Iterator[Path]:
    for path in sorted(addon_root.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


def index_addon(addon_root: Path, snapshot_id: str) -> list[SymbolRecord]:
    """Parse all recognisable source files in one addon directory."""
    module = addon_root.name
    records: list[SymbolRecord] = []

    manifest_path = addon_root / "__manifest__.py"
    if not manifest_path.exists():
        logger.debug("index_addon: no __manifest__.py in %s; skipping", addon_root)
        return []

    records.extend(parse_manifest(manifest_path, module, snapshot_id, "__manifest__.py"))

    for path in _iter_addon_files(addon_root):
        rel = str(path.relative_to(addon_root))
        if path.suffix == ".py" and path.name != "__manifest__.py":
            records.extend(parse_python_file(path, module, snapshot_id, rel))
        elif path.suffix == ".xml":
            records.extend(parse_xml_file(path, module, snapshot_id, rel))
        elif path.suffix == ".csv" and "security" in path.parts:
            records.extend(parse_access_csv(path, module, snapshot_id, rel))
        elif path.suffix == ".js" and "static" in path.parts:
            records.extend(parse_js_file(path, module, snapshot_id, rel))

    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def index_addon_roots(
    roots: list[Path],
    snapshot_id: str,
    db,
) -> int:
    """Index all addon directories under the given roots and persist SourceSymbol rows.

    Returns the total number of symbols stored.

    ``db`` is a SQLAlchemy Session already open — the caller controls commit/rollback.
    """
    from models import SourceSymbol  # local import

    total = 0
    for root in roots:
        if not root.is_dir():
            logger.warning("index_addon_roots: root %s is not a directory; skipping", root)
            continue
        for addon_dir in sorted(root.iterdir()):
            if not addon_dir.is_dir():
                continue
            if not (addon_dir / "__manifest__.py").exists():
                continue
            try:
                symbols = index_addon(addon_dir, snapshot_id)
                for sym in symbols:
                    db.add(SourceSymbol(
                        snapshot_id=sym.snapshot_id,
                        module=sym.module,
                        model=sym.model,
                        kind=sym.kind,
                        name=sym.name,
                        path=sym.path,
                        line_start=sym.line_start,
                        line_end=sym.line_end,
                        digest=sym.digest,
                        payload=sym.payload,
                    ))
                total += len(symbols)
                logger.info("index_addon_roots: %s — %d symbols", addon_dir.name, len(symbols))
            except Exception as exc:
                logger.error("index_addon_roots: error indexing %s: %s", addon_dir, exc, exc_info=True)

    db.flush()
    return total


def search_symbols(db, snapshot_id: str, query: str, kind: str | None = None, limit: int = 25) -> list[dict]:
    """Search indexed symbols by name or model matching the query."""
    from models import SourceSymbol
    needle = f"%{query.strip()}%"
    q = db.query(SourceSymbol).filter(
        SourceSymbol.snapshot_id == snapshot_id,
        (SourceSymbol.name.ilike(needle) | SourceSymbol.model.ilike(needle)),
    )
    if kind:
        q = q.filter(SourceSymbol.kind == kind)
    rows = q.limit(limit).all()
    return [
        {
            "module": r.module,
            "model": r.model,
            "kind": r.kind,
            "name": r.name,
            "path": r.path,
            "line_start": r.line_start,
            "line_end": r.line_end,
            "payload": r.payload,
        }
        for r in rows
    ]


def dependency_graph(db, snapshot_id: str) -> dict[str, list[str]]:
    """Build a map of module dependencies from indexed manifest records."""
    from models import SourceSymbol
    manifests = db.query(SourceSymbol).filter(
        SourceSymbol.snapshot_id == snapshot_id,
        SourceSymbol.kind == "manifest",
    ).all()
    graph = {}
    for m in manifests:
        deps = (m.payload or {}).get("depends", [])
        graph[m.module] = deps
    return graph


def model_to_views(db, snapshot_id: str, model_name: str) -> list[dict]:
    """Find all views associated with a given model in the snapshot."""
    from models import SourceSymbol
    views = db.query(SourceSymbol).filter(
        SourceSymbol.snapshot_id == snapshot_id,
        SourceSymbol.kind == "view",
        SourceSymbol.model == model_name,
    ).all()
    return [
        {"name": v.name, "path": v.path, "line_start": v.line_start, "payload": v.payload}
        for v in views
    ]


def security_to_models(db, snapshot_id: str, model_name: str) -> dict:
    """Find all ACL entries and record rules for a given model."""
    from models import SourceSymbol
    acls = db.query(SourceSymbol).filter(
        SourceSymbol.snapshot_id == snapshot_id,
        SourceSymbol.kind == "acl",
        SourceSymbol.model == model_name,
    ).all()
    rules = db.query(SourceSymbol).filter(
        SourceSymbol.snapshot_id == snapshot_id,
        SourceSymbol.kind == "rule",
        SourceSymbol.model == model_name,
    ).all()
    return {
        "model": model_name,
        "acls": [{"name": a.name, "path": a.path, "payload": a.payload} for a in acls],
        "rules": [{"name": r.name, "path": r.path, "payload": r.payload} for r in rules],
    }


def change_impact(db, snapshot_id: str, changed_files: list[str]) -> dict:
    """Analyze which symbols, models, and views are impacted by changed files."""
    from models import SourceSymbol
    impacted_symbols = []
    impacted_models = set()
    for f in changed_files:
        clean_path = f.lstrip("./")
        syms = db.query(SourceSymbol).filter(
            SourceSymbol.snapshot_id == snapshot_id,
            SourceSymbol.path.ilike(f"%{clean_path}%"),
        ).all()
        for s in syms:
            impacted_symbols.append({"kind": s.kind, "name": s.name, "model": s.model, "path": s.path})
            if s.model:
                impacted_models.add(s.model)

    return {
        "changed_files": changed_files,
        "impacted_symbol_count": len(impacted_symbols),
        "impacted_symbols": impacted_symbols[:50],
        "impacted_models": sorted(impacted_models),
    }

