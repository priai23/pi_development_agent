import ast
import base64
import csv
import hashlib
import re
import io
import importlib.util
import platform
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from sqlalchemy.engine import make_url

from config import settings


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

    root_init = module_root / "__init__.py"
    checks.append({
        "name": "module init",
        "passed": root_init.is_file(),
        "message": "__init__.py present" if root_init.is_file() else "__init__.py is required",
    })

    files = [path for path in module_root.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    digest = hashlib.sha256()
    xml_ids: dict[str, str] = {}
    new_models: set[str] = set()
    for path in sorted(files):
        digest.update(str(path.relative_to(module_root)).encode()); digest.update(path.read_bytes())
        if path.suffix == ".py":
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                checks.append({"name": f"python:{path.name}", "passed": True, "message": "Parsed"})
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ClassDef):
                        continue
                    for item in node.body:
                        if not isinstance(item, ast.Assign):
                            continue
                        if any(isinstance(target, ast.Name) and target.id == "_name" for target in item.targets):
                            if isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                                new_models.add(item.value.value)
            except SyntaxError as exc:
                checks.append({"name": f"python:{path.name}", "passed": False, "message": str(exc)})
        if path.suffix == ".xml":
            try:
                tree = ET.parse(path)
                checks.append({"name": f"xml:{path.name}", "passed": True, "message": "Parsed"})
                for elem in tree.iter():
                    xml_id = elem.get("id")
                    if not xml_id:
                        continue
                    prior = xml_ids.get(xml_id)
                    checks.append({
                        "name": f"xml id:{xml_id}",
                        "passed": prior is None,
                        "message": f"Declared in {path.name}" if prior is None else f"Duplicate XML ID in {prior} and {path.name}",
                    })
                    xml_ids.setdefault(xml_id, path.name)
                    if elem.tag == "record" and elem.get("model") == "res.groups":
                        obsolete = [field.get("name") for field in elem.findall("field") if field.get("name") in {"category_id", "users"}]
                        if obsolete:
                            checks.append({
                                "name": f"odoo19 res.groups fields:{path.name}",
                                "passed": False,
                                "message": f"Replace obsolete fields {', '.join(obsolete)} with privilege_id/user_ids",
                            })
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
        if isinstance(manifest, dict):
            dependencies = manifest.get("depends", [])
            dependency_ok = isinstance(dependencies, list) and all(
                isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_]*", item)
                for item in dependencies
            )
            checks.append({
                "name": "manifest dependencies",
                "passed": dependency_ok,
                "message": "Dependency names are valid" if dependency_ok else "depends must contain valid Odoo module names",
            })
            declared_paths = [
                item
                for key in ("data", "demo")
                for item in manifest.get(key, [])
                if isinstance(item, str)
            ]
            for relative in declared_paths:
                target = (module_root / relative).resolve(strict=False)
                path_ok = target.is_relative_to(module_root.resolve()) and target.is_file()
                checks.append({
                    "name": f"manifest path:{relative}",
                    "passed": path_ok,
                    "message": "Referenced file exists" if path_ok else "Referenced file is missing or outside the module",
                })
            assets = manifest.get("assets") or {}
            if not isinstance(assets, dict):
                checks.append({"name": "manifest assets", "passed": False, "message": "assets must be a mapping"})
                assets = {}
            for bundle, asset_paths in assets.items():
                if not isinstance(asset_paths, list):
                    checks.append({"name": f"asset bundle:{bundle}", "passed": False, "message": "Asset bundle must be a list"})
                    continue
                for asset in asset_paths:
                    relative = asset.removeprefix(f"{module_root.name}/") if isinstance(asset, str) else ""
                    asset_ok = bool(relative) and any(module_root.glob(relative))
                    checks.append({
                        "name": f"asset path:{asset}",
                        "passed": asset_ok,
                        "message": "Asset path matches files" if asset_ok else "Asset path does not match a module file",
                    })
    except Exception as exc:
        checks.append({"name": "manifest", "passed": False, "message": str(exc)})

    model_files = list((module_root / "models").glob("*.py")) if (module_root / "models").exists() else []
    if model_files:
        models_init = module_root / "models" / "__init__.py"
        checks.append({
            "name": "models init",
            "passed": models_init.is_file(),
            "message": "models/__init__.py present" if models_init.is_file() else "models/__init__.py is required",
        })
    access_file = module_root / "security" / "ir.model.access.csv"
    access_ok = not new_models or access_file.exists()
    if access_file.exists():
        try:
            rows = list(csv.DictReader(access_file.open(encoding="utf-8")))
            access_ok = bool(rows) and all("perm_unlink" in row for row in rows)
            for model_name in sorted(new_models):
                expected = f"model_{model_name.replace('.', '_')}"
                covered = any(row.get("model_id:id") == expected for row in rows)
                checks.append({
                    "name": f"acl coverage:{model_name}",
                    "passed": covered,
                    "message": "ACL row present" if covered else f"Missing ACL row for {expected}",
                })
        except Exception:
            access_ok = False
    checks.append({"name": "access control", "passed": access_ok, "message": "Access rules present" if access_ok else "Models require security/ir.model.access.csv"})
    return {"passed": all(check["passed"] for check in checks), "digest": digest.hexdigest(), "checks": checks, "file_count": len(files)}


def validate_module_runtime(module_root: Path, module_name: str, *, is_upgrade: bool = False) -> dict:
    runner_path = Path(__file__).parent.parent / "bridge_runner" / "runner.py"
    spec = importlib.util.spec_from_file_location("primacy_bridge_runner", runner_path)
    if not spec or not spec.loader:
        raise RuntimeError("Deployment runner module is unavailable")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    archive = package_module(module_root)
    digest = hashlib.sha256(archive).hexdigest()
    database_url = make_url(settings.database_url)
    derive_database_config = not settings.validation_postgres_admin_dsn
    postgres_host = database_url.host or "localhost"
    if derive_database_config and postgres_host in {"localhost", "127.0.0.1", "::1"} and platform.system() in {"Darwin", "Windows"}:
        postgres_host = "host.docker.internal"
    config = {
        "postgres_admin_dsn": settings.validation_postgres_admin_dsn or database_url.set(
            drivername="postgresql", database="postgres"
        ).render_as_string(hide_password=False),
        "postgres_host": postgres_host if derive_database_config else settings.validation_postgres_host,
        "postgres_port": settings.validation_postgres_port,
        "postgres_user": (database_url.username or "postgres") if derive_database_config else settings.validation_postgres_user,
        "postgres_password": (database_url.password or "") if derive_database_config else settings.validation_postgres_password,
        "odoo_docker_image": settings.validation_odoo_image,
        "test_timeout_seconds": settings.validation_timeout_seconds,
    }
    return runner.run_validate_module({
        "job_uuid": hashlib.sha256(f"{module_name}:{digest}".encode()).hexdigest()[:32],
        "module_name": module_name,
        "is_upgrade": is_upgrade,
        "artifact_zip_b64": base64.b64encode(archive).decode(),
        "artifact_digest": digest,
    }, config)


def validate_module_full(module_root: Path, module_name: str, *, is_upgrade: bool = False) -> dict:
    static = validate_module(module_root)
    runtime = validate_module_runtime(module_root, module_name, is_upgrade=is_upgrade) if static["passed"] else {
        "ok": False,
        "checks": [],
        "test_count": 0,
        "error": "Static validation failed",
        "log": "",
    }
    return {
        "passed": static["passed"] and runtime.get("ok") is True,
        "static": static,
        "runtime": runtime,
        "digest": hashlib.sha256(package_module(module_root)).hexdigest(),
    }
