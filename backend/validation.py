import ast
import csv
import hashlib
import re
import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


BANNED_PATTERNS = {
    "legacy attrs syntax": re.compile(r"\battrs\s*="),
    "legacy tree view": re.compile(r"<tree\b|</tree>"),
    "deprecated name_get": re.compile(r"\bdef\s+name_get\s*\("),
    "raw SQL execution": re.compile(r"\.(?:execute|executemany)\s*\("),
    "shell execution": re.compile(r"\b(?:subprocess|os\.system|popen)\b"),
    "embedded secret": re.compile(r"(?i)(?:password|api[_-]?key|secret)\s*=\s*['\"][^'\"]+"),
}


def package_module(module_root: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(item for item in module_root.rglob("*") if item.is_file()):
            relative = Path(module_root.name) / path.relative_to(module_root)
            if any(part in {"__pycache__", ".pytest_cache", ".git"} for part in relative.parts):
                continue
            info = zipfile.ZipInfo(str(relative), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, path.read_bytes())
    return output.getvalue()


def validate_module(module_root: Path) -> dict:
    checks: list[dict] = []
    if not module_root.is_dir() or not (module_root / "__manifest__.py").is_file():
        return {"passed": False, "checks": [{"name": "module structure", "passed": False, "message": "__manifest__.py is required"}]}

    files = [path for path in module_root.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(module_root)).encode()); digest.update(path.read_bytes())
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                checks.append({"name": f"python:{path.name}", "passed": True, "message": "Parsed"})
            except SyntaxError as exc:
                checks.append({"name": f"python:{path.name}", "passed": False, "message": str(exc)})
        if path.suffix == ".xml":
            try:
                tree = ET.parse(path)
                checks.append({"name": f"xml:{path.name}", "passed": True, "message": "Parsed"})
                # Odoo 19 search view group filter check: filters with group_by in context must specify domain
                text = path.read_text(encoding="utf-8")
                if "<group" in text and "group_by" in text:
                    for elem in tree.iter("filter"):
                        ctx = elem.get("context", "")
                        domain = elem.get("domain")
                        if "group_by" in ctx and domain is None:
                            checks.append({
                                "name": f"xml_group_filter_domain:{path.name}",
                                "passed": False,
                                "message": f"Filter '{elem.get('name', 'unknown')}' inside group passing group_by context MUST explicitly specify domain='[]'"
                            })
            except ET.ParseError as exc:
                checks.append({"name": f"xml:{path.name}", "passed": False, "message": str(exc)})
        if path.suffix in {".py", ".xml", ".js", ".csv"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in BANNED_PATTERNS.items():
                if pattern.search(text):
                    checks.append({"name": label, "passed": False, "message": str(path.relative_to(module_root))})

    try:
        manifest = ast.literal_eval((module_root / "__manifest__.py").read_text(encoding="utf-8"))
        valid = isinstance(manifest, dict) and str(manifest.get("version", "")).startswith("19.0") and manifest.get("installable") is True
        checks.append({"name": "manifest", "passed": valid, "message": "Valid Odoo 19 manifest" if valid else "Version must start 19.0 and installable must be true"})
    except Exception as exc:
        checks.append({"name": "manifest", "passed": False, "message": str(exc)})

    model_files = list((module_root / "models").glob("*.py")) if (module_root / "models").exists() else []
    access_file = module_root / "security" / "ir.model.access.csv"
    access_ok = not model_files or access_file.exists()
    if access_file.exists():
        try:
            rows = list(csv.DictReader(access_file.open(encoding="utf-8")))
            access_ok = bool(rows) and all("perm_unlink" in row for row in rows)
        except Exception:
            access_ok = False
    checks.append({"name": "access control", "passed": access_ok, "message": "Access rules present" if access_ok else "Models require security/ir.model.access.csv"})
    return {"passed": all(check["passed"] for check in checks), "digest": digest.hexdigest(), "checks": checks, "file_count": len(files)}
