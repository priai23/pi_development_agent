import ast
import base64
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("PRIMACY_RUNNER_CONFIG", "config.json")
BRIDGE_TOKEN = os.environ.get("PRIMACY_BRIDGE_TOKEN")


class RunnerError(Exception):
    pass


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def canonical_job(job: dict) -> bytes:
    clean = {key: value for key, value in job.items() if key != "signature"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_job(job: dict, public_key: Ed25519PublicKey, seen_nonces: set) -> None:
    nonce = job.get("nonce")
    if not nonce or nonce in seen_nonces:
        raise RunnerError("Job nonce already used")
    try:
        signature = base64.b64decode(job["signature"], validate=True)
        public_key.verify(signature, canonical_job(job))
    except (KeyError, ValueError, InvalidSignature) as exc:
        raise RunnerError("Invalid job signature") from exc
    expiry = datetime.fromisoformat(str(job["expires_at"]).replace("Z", "+00:00"))
    if expiry <= datetime.now(timezone.utc):
        raise RunnerError("Job has expired")
    seen_nonces.add(nonce)


def verify_digest(payload_bytes: bytes, expected_digest: str) -> bool:
    return hashlib.sha256(payload_bytes).hexdigest() == expected_digest


def download_artifact(url: str, allowed_hosts: list[str]) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in {host.lower() for host in allowed_hosts}:
        raise RunnerError("Artifact URL is not an allowed HTTPS host")
    response = requests.get(url, timeout=30, allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise RunnerError("Artifact redirects are not allowed")
    response.raise_for_status()
    return response.content


def safe_extract(zip_path: str | Path, extract_to: str | Path, expected_module_name: str) -> Path:
    destination = Path(extract_to).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    module_root = (destination / expected_module_name).resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise RunnerError(f"unsafe path traversal in ZIP: {member.filename}")
            if member.is_dir():
                continue
            mode = (member.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise RunnerError(f"Symlinks are not allowed in deployment packages: {member.filename}")
        archive.extractall(destination)
    manifest = module_root / "__manifest__.py"
    if not module_root.is_dir() or not manifest.is_file():
        raise RunnerError(f"Expected Odoo module manifest not found for {expected_module_name}")
    return module_root


def bridge_rpc(bridge_url: str, endpoint: str, params: dict | None = None) -> dict | None:
    response = requests.post(
        f"{bridge_url.rstrip('/')}{endpoint}",
        headers={"Authorization": f"Bearer {BRIDGE_TOKEN}"},
        json={"jsonrpc": "2.0", "method": "call", "params": params or {}, "id": 1},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RunnerError("Deployment bridge rejected the request")
    return body.get("result")


def update_job_status(bridge_url: str, job_uuid: str, state: str, logs: str = "") -> None:
    bridge_rpc(bridge_url, f"/primacy/bridge/v1/jobs/{job_uuid}/result", {"state": state, "logs": logs[-100_000:]})

def save_terminal_status(config: dict, job_uuid: str, state: str, logs: str = "") -> None:
    reports_dir = Path(config.get("reports_path", "pending_reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    temp_file = reports_dir / f"{job_uuid}.tmp"
    final_file = reports_dir / f"{job_uuid}.json"
    temp_file.write_text(json.dumps({"job_uuid": job_uuid, "state": state, "logs": logs}))
    temp_file.replace(final_file)

def flush_pending_results(config: dict) -> None:
    reports_dir = Path(config.get("reports_path", "pending_reports"))
    if not reports_dir.exists():
        return
    for report_file in reports_dir.glob("*.json"):
        try:
            report = json.loads(report_file.read_text())
            update_job_status(config["bridge_url"], report["job_uuid"], report["state"], report["logs"])
            report_file.unlink(missing_ok=True)
        except Exception as exc:
            logger.error("Failed to flush report %s: %s", report_file.name, exc)


def restart_odoo(command: list[str]) -> None:
    if not command or any(not isinstance(item, str) or not item for item in command):
        raise RunnerError("Invalid restart command")
    subprocess.run(command, check=True, timeout=120)


# ---------------------------------------------------------------------------
# Phase 5 — Source index operation (stdlib-only, no backend import)
# ---------------------------------------------------------------------------

_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "node_modules"}
_FIELD_TYPES = {
    "Char", "Text", "Html", "Integer", "Float", "Monetary",
    "Boolean", "Date", "Datetime", "Binary", "Image",
    "Many2one", "One2many", "Many2many", "Selection", "Reference",
}
_JS_REGISTRY = re.compile(r'registry\s*\.\s*(?:category\([^)]+\)\s*\.\s*)?add\s*\(\s*["\']([^"\']+)["\']')


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _index_one_addon(addon_root: Path) -> list[dict]:
    """Inline minimal source index for one addon — returns list of symbol dicts."""
    module = addon_root.name
    symbols: list[dict] = []

    manifest_path = addon_root / "__manifest__.py"
    if not manifest_path.exists():
        return []
    try:
        manifest = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
        symbols.append({
            "module": module, "model": None, "kind": "manifest", "name": module,
            "path": "__manifest__.py", "line_start": None, "line_end": None,
            "digest": _sha256(manifest_path.read_bytes()),
            "payload": {
                "version": manifest.get("version"), "depends": manifest.get("depends", []),
                "installable": manifest.get("installable", True),
            },
        })
    except Exception:
        pass

    for py_file in sorted(addon_root.rglob("*.py")):
        if any(p in _SKIP_DIRS for p in py_file.parts) or py_file.name == "__manifest__.py":
            continue
        rel = str(py_file.relative_to(addon_root))
        try:
            src = py_file.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py_file))
        except Exception:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            attrs: dict[str, ast.expr] = {}
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for tgt in item.targets:
                        if isinstance(tgt, ast.Name) and item.value:
                            attrs[tgt.id] = item.value
            name_val = attrs["_name"].value if "_name" in attrs and isinstance(attrs["_name"], ast.Constant) else None
            inherit: list[str] = []
            if "_inherit" in attrs:
                iv = attrs["_inherit"]
                if isinstance(iv, ast.Constant):
                    inherit = [iv.value]
                elif isinstance(iv, ast.List):
                    inherit = [e.value for e in iv.elts if isinstance(e, ast.Constant)]
            model = name_val or (inherit[0] if inherit else None)
            if not model:
                continue
            end = node.end_lineno or node.lineno
            symbols.append({
                "module": module, "model": model, "kind": "model", "name": model,
                "path": rel, "line_start": node.lineno, "line_end": end,
                "digest": _sha256("\n".join(lines[node.lineno - 1:end]).encode()),
                "payload": {"_name": name_val, "_inherit": inherit, "class": node.name},
            })
            for item in node.body:
                if not isinstance(item, ast.Assign) or not isinstance(item.value, ast.Call):
                    continue
                call = item.value
                fname = None
                if isinstance(call.func, ast.Attribute) and call.func.attr in _FIELD_TYPES:
                    fname = call.func.attr
                elif isinstance(call.func, ast.Name) and call.func.id in _FIELD_TYPES:
                    fname = call.func.id
                if not fname:
                    continue
                for tgt in item.targets:
                    if isinstance(tgt, ast.Name):
                        kw: dict = {k.arg: k.value.value for k in call.keywords if k.arg and isinstance(k.value, ast.Constant)}
                        comodel = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else kw.get("comodel_name")
                        symbols.append({
                            "module": module, "model": model, "kind": "field", "name": tgt.id,
                            "path": rel, "line_start": item.lineno, "line_end": item.end_lineno,
                            "digest": None,
                            "payload": {"field_type": fname, "comodel": comodel, "compute": kw.get("compute"), "string": kw.get("string")},
                        })

    for xml_file in sorted(addon_root.rglob("*.xml")):
        if any(p in _SKIP_DIRS for p in xml_file.parts):
            continue
        rel = str(xml_file.relative_to(addon_root))
        try:
            tree = ET.parse(xml_file)
            dg = _sha256(xml_file.read_bytes())
        except ET.ParseError:
            continue
        for elem in tree.getroot().iter():
            xml_id = elem.get("id")
            if not xml_id or elem.tag != "record":
                continue
            model_attr = elem.get("model", "")
            symbols.append({
                "module": module, "model": model_attr or None,
                "kind": "view" if "view" in model_attr else "xml_id",
                "name": xml_id, "path": rel, "line_start": None, "line_end": None, "digest": dg,
                "payload": {"xml_id": xml_id, "model": model_attr},
            })

    access = addon_root / "security" / "ir.model.access.csv"
    if access.exists():
        try:
            dg = _sha256(access.read_bytes())
            for row in csv.DictReader(access.open(encoding="utf-8")):
                xid = row.get("id", "").strip()
                if xid:
                    symbols.append({
                        "module": module, "model": row.get("model_id:id", "").strip() or None,
                        "kind": "acl", "name": xid, "path": "security/ir.model.access.csv",
                        "line_start": None, "line_end": None, "digest": dg,
                        "payload": {"xml_id": xid, "group": row.get("group_id:id", "").strip()},
                    })
        except Exception:
            pass

    for js_file in sorted(addon_root.rglob("*.js")):
        if "static" not in js_file.parts:
            continue
        try:
            text = js_file.read_text(encoding="utf-8", errors="replace")
            dg = _sha256(text.encode())
            for m in _JS_REGISTRY.finditer(text):
                symbols.append({
                    "module": module, "model": None, "kind": "js_component",
                    "name": m.group(1), "path": str(js_file.relative_to(addon_root)),
                    "line_start": None, "line_end": None, "digest": dg,
                    "payload": {"kind": "registry_add", "key": m.group(1)},
                })
        except Exception:
            pass

    return symbols


def run_source_index(job: dict, config: dict) -> dict:
    """Index one or more addon roots and return a full symbol list as JSON."""
    addon_roots = job.get("addon_roots") or config.get("source_index_roots", [])
    snapshot_id = job.get("snapshot_id", "")
    all_symbols: list[dict] = []
    errors: list[str] = []
    for root_str in addon_roots:
        root = Path(root_str)
        if not root.is_dir():
            errors.append(f"root not found: {root_str}")
            continue
        for addon_dir in sorted(root.iterdir()):
            if not addon_dir.is_dir() or not (addon_dir / "__manifest__.py").exists():
                continue
            try:
                syms = _index_one_addon(addon_dir)
                for s in syms:
                    s["snapshot_id"] = snapshot_id
                all_symbols.extend(syms)
            except Exception as exc:
                errors.append(f"{addon_dir.name}: {exc}")
    return {
        "ok": True,
        "snapshot_id": snapshot_id,
        "symbol_count": len(all_symbols),
        "symbols": all_symbols,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Phase 7 — Disposable Odoo validation via Docker
# ---------------------------------------------------------------------------

# Override with the full digest in production config:
#   "odoo_docker_image": "odoo@sha256:<digest>"
_ODOO19_IMAGE = "odoo:19.0"


def _run_docker_odoo(
    args: list[str],
    addon_path: str,
    db_host: str,
    db_port: int,
    db_user: str,
    db_password: str,
    config: dict,
    timeout: int = 300,
) -> tuple[int, str]:
    image = config.get("odoo_docker_image", _ODOO19_IMAGE)
    cmd = [
        "docker", "run", "--rm",
        "--network", "host",
        "-v", f"{addon_path}:/mnt/extra-addons:ro",
        "-e", f"HOST={db_host}",
        "-e", f"PORT={db_port}",
        "-e", f"USER={db_user}",
        "-e", f"PASSWORD={db_password}",
        image,
        "odoo",
        "--addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons",
        "--no-http",
        "--stop-after-init",
    ] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, start_new_session=True)
        return result.returncode, ((result.stdout or "") + (result.stderr or ""))[-50_000:]
    except subprocess.TimeoutExpired as exc:
        return -1, f"TIMEOUT after {timeout}s\n" + ((exc.stdout or "") + (exc.stderr or ""))[-50_000:]
    except FileNotFoundError:
        return -2, "Docker not found on runner host"


def _parse_odoo_log(output: str) -> dict:
    failed = any(m in output for m in ["CRITICAL", "ERROR odoo.modules", "Installation failed", "OperationalError"])
    test_ok = "Ran " in output and "errors=0" in output and "failures=0" in output
    if "At least one test failed" in output or "FAIL:" in output or "ERROR:" in output:
        test_ok = False
    m = re.search(r"Ran (\d+) tests?", output)
    test_count = int(m.group(1)) if m else 0
    checks = [{"name": "install_clean", "passed": not failed,
               "message": "No critical errors" if not failed else "Critical error detected"}]
    if test_count > 0:
        checks.append({"name": "tests_pass", "passed": test_ok,
                       "message": f"{test_count} tests ran" + ("" if test_ok else " — failures present")})
    return {"checks": checks, "test_count": test_count, "install_failed": failed, "log_excerpt": output[-8_000:]}


def run_validate_module(job: dict, config: dict) -> dict:
    """Install/upgrade module in a disposable Docker+Postgres environment and return pass/fail."""
    module_name = job["module_name"]
    job_uuid = job["job_uuid"]
    is_upgrade = job.get("is_upgrade", False)
    db_host = job.get("db_host") or config.get("postgres_host", "localhost")
    db_port = int(job.get("db_port") or config.get("postgres_port", 5432))
    db_user = job.get("db_user") or config.get("postgres_user", "odoo")
    db_password = job.get("db_password") or config.get("postgres_password", "")
    admin_dsn = config.get("postgres_admin_dsn")
    test_db = f"{config.get('test_db_prefix', 'primacy_test_')}{job_uuid.replace('-', '_')}"
    timeout = int(config.get("test_timeout_seconds", 300))

    checks: list[dict] = []
    log_combined = ""

    zip_bytes = base64.b64decode(job["artifact_zip_b64"])
    actual_digest = _sha256(zip_bytes)
    digest_ok = actual_digest == job.get("artifact_digest", actual_digest)
    checks.append({"name": "artifact_digest", "passed": digest_ok,
                   "message": "Digest verified" if digest_ok else f"Mismatch: {actual_digest}"})
    if not digest_ok:
        return {"ok": False, "checks": checks, "log": "", "error": "Artifact digest mismatch"}

    with tempfile.TemporaryDirectory(prefix="primacy_val_") as tmpdir:
        tmp = Path(tmpdir)
        zip_path = tmp / f"{module_name}.zip"
        zip_path.write_bytes(zip_bytes)
        try:
            safe_extract(zip_path, tmp / "addons", module_name)
            addon_root = str(tmp / "addons")
        except RunnerError as exc:
            return {"ok": False, "checks": checks + [{"name": "extract", "passed": False, "message": str(exc)}], "log": ""}

        db_created = False
        if admin_dsn:
            try:
                subprocess.run(["psql", admin_dsn, "-c", f'CREATE DATABASE "{test_db}"'],
                               check=True, capture_output=True, timeout=30)
                db_created = True
                checks.append({"name": "db_create", "passed": True, "message": f"Database {test_db} created"})
            except Exception as exc:
                checks.append({"name": "db_create", "passed": False, "message": str(exc)})
                return {"ok": False, "checks": checks, "log": ""}
        else:
            checks.append({"name": "db_create", "passed": True, "message": "Skipped (no admin_dsn)"})

        try:
            flag = "-i" if not is_upgrade else "-u"
            args = ["-d", test_db, flag, module_name, "--test-enable"]
            rc, output = _run_docker_odoo(args, addon_root, db_host, db_port, db_user, db_password, config, timeout)
            log_combined += output
            parsed = _parse_odoo_log(output)
            checks.extend(parsed["checks"])
        finally:
            if db_created and admin_dsn:
                try:
                    subprocess.run(["psql", admin_dsn, "-c", f'DROP DATABASE IF EXISTS "{test_db}"'],
                                   check=True, capture_output=True, timeout=30)
                except Exception:
                    pass

    all_passed = all(c["passed"] for c in checks)
    return {
        "ok": all_passed,
        "module_name": module_name,
        "is_upgrade": is_upgrade,
        "checks": checks,
        "log": log_combined[-8_000:],
        "error": None if all_passed else "One or more validation checks failed",
    }


# ---------------------------------------------------------------------------
# Legacy deploy operation (v1 unchanged)
# ---------------------------------------------------------------------------

def run_deploy(job: dict, config: dict, seen_nonces: set) -> None:
    job_uuid = job["job_uuid"]
    existing = None
    backup = None
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(config["public_key_base64"], validate=True))
        verify_job(job, public_key, seen_nonces)
        nonce_store = Path(config.get("nonce_store_path", "nonce-ledger.txt"))
        nonce_store.parent.mkdir(parents=True, exist_ok=True)
        with nonce_store.open("a", encoding="utf-8") as handle:
            handle.write(f"{job['nonce']}\n")
        update_job_status(config["bridge_url"], job_uuid, "running", "Runner started deployment")
        artifact = download_artifact(job["artifact_url"], config["allowed_artifact_hosts"])
        if not verify_digest(artifact, job["artifact_digest"]):
            raise RunnerError("SHA-256 digest verification failed")

        addon_root = Path(config["custom_addons_path"]).resolve()
        backup_root = Path(config.get("backup_path", str(addon_root / ".primacy-backups"))).resolve()
        backup_root.mkdir(parents=True, exist_ok=True)
        existing = addon_root / job["module_name"]
        backup = backup_root / f"{job_uuid}-{job['module_name']}"
        if existing.exists():
            if backup.exists():
                shutil.rmtree(backup)
            shutil.move(str(existing), str(backup))

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            handle.write(artifact)
            archive_path = handle.name
        try:
            safe_extract(archive_path, addon_root, job["module_name"])
        finally:
            Path(archive_path).unlink(missing_ok=True)

        try:
            restart_odoo(config["restart_command"])
        except Exception as restart_error:
            if backup and backup.exists():
                if existing.exists():
                    shutil.rmtree(existing)
                shutil.move(str(backup), str(existing))
                try:
                    restart_odoo(config["restart_command"])
                    save_terminal_status(config, job_uuid, "rolled_back", f"Restart failed; previous artifact restored: {restart_error}")
                except Exception as rollback_error:
                    save_terminal_status(config, job_uuid, "failed", f"Restart failed AND rollback restart failed! Manual recovery required. Original error: {restart_error}. Rollback error: {rollback_error}")
                return
            raise
        save_terminal_status(config, job_uuid, "succeeded", "Deployment completed successfully")
    except Exception as exc:
        try:
            if backup and backup.exists() and existing:
                if existing.exists():
                    shutil.rmtree(existing)
                shutil.move(str(backup), str(existing))
                try:
                    restart_odoo(config["restart_command"])
                    save_terminal_status(config, job_uuid, "rolled_back", f"Deployment failed; previous artifact restored: {exc}")
                except Exception as rollback_error:
                    save_terminal_status(config, job_uuid, "failed", f"Deployment failed AND rollback restart failed! Manual recovery required. Original error: {exc}. Rollback error: {rollback_error}")
            else:
                save_terminal_status(config, job_uuid, "failed", f"{type(exc).__name__}: {exc}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Job dispatcher
# ---------------------------------------------------------------------------

def process_job(job: dict, config: dict, seen_nonces: set | None = None) -> None:
    """Route a job to the correct operation handler.

    operation field values:
        "deploy"          — legacy module deployment (default, backward-compatible)
        "source_index"    — index addon roots, POST symbols to callback_url
        "validate_module" — disposable Docker install/upgrade, POST result to callback_url
    """
    seen_nonces = seen_nonces if seen_nonces is not None else set()
    operation = job.get("operation", "deploy")

    def _post_callback(result: dict) -> None:
        callback_url = job.get("callback_url")
        if callback_url:
            try:
                requests.post(
                    callback_url,
                    headers={"Authorization": f"Bearer {BRIDGE_TOKEN}"},
                    json=result,
                    timeout=60,
                )
            except Exception as exc:
                logger.error("process_job[%s]: callback failed: %s", operation, exc)

    if operation == "source_index":
        _post_callback(run_source_index(job, config))
        return

    if operation == "validate_module":
        _post_callback(run_validate_module(job, config))
        return

    run_deploy(job, config, seen_nonces)


def main():
    if not BRIDGE_TOKEN:
        raise SystemExit("PRIMACY_BRIDGE_TOKEN environment variable is missing")
    config = load_config()
    runner_id = config.get("runner_id", os.uname().nodename)
    nonce_store = Path(config.get("nonce_store_path", "nonce-ledger.txt"))
    seen_nonces: set[str] = set(nonce_store.read_text(encoding="utf-8").splitlines()) if nonce_store.exists() else set()
    while True:
        flush_pending_results(config)
        try:
            job = bridge_rpc(config["bridge_url"], "/primacy/bridge/v1/jobs/next", {"runner_id": runner_id})
            if job:
                process_job(job, config, seen_nonces)
        except requests.RequestException as exc:
            print(f"Connection error: {exc}")
        except RunnerError as exc:
            print(f"Runner error: {exc}")
        time.sleep(10)


if __name__ == "__main__":
    main()
