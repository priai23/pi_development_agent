"""Structured Technical Specification compiler (Phase 6).

compile_specification() converts a user prompt into an immutable RunSpecification
plus a set of AcceptanceCheck rows before any code generation begins.

This is entirely deterministic on the backend — the model cannot choose success.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Acceptance check kinds — exhaustive enumeration
# ---------------------------------------------------------------------------

VALID_CHECK_KINDS = {
    "source_reuse",
    "module_install",
    "module_upgrade",
    "artifact_digest",
    "model_field",
    "xml_id",
    "view_load",
    "acl",
    "record_rule",
    "python_test",
    "business_scenario",
}

TASK_IMPLEMENT_VIEWS = "implement_views"
TASK_IMPLEMENT_SECURITY = "implement_security"
TASK_GENERATE_TESTS = "generate_tests"
TASK_VALIDATE_AND_VERIFY = "validate_and_verify"
CHECK_GATED_TASKS = {
    TASK_VALIDATE_AND_VERIFY,
}


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

@dataclass
class CheckSpec:
    """Blueprint for a single AcceptanceCheck."""
    kind: str
    spec_target: dict
    required: bool = True
    task_id: str | None = None

    def __post_init__(self):
        if self.kind not in VALID_CHECK_KINDS:
            raise ValueError(f"Unknown acceptance check kind: {self.kind!r}")


@dataclass
class RequirementSpec:
    id: str
    title: str
    description: str
    implementation_targets: list[str]  # model/view/rule names affected
    check_specs: list[CheckSpec] = field(default_factory=list)


@dataclass
class SpecificationResult:
    """Returned by compile_specification() on success."""
    specification_id: int
    run_id: str
    requirements: list[RequirementSpec]
    check_ids: list[str]
    digest: str


# ---------------------------------------------------------------------------
# Keyword extractors  (no LLM — pure heuristic for deterministic grounding)
# ---------------------------------------------------------------------------

def _extract_model_targets(prompt: str, snapshot_symbols: list) -> list[str]:
    """Find model names mentioned or inferable from the prompt."""
    prompt_lower = prompt.lower()
    found = []
    seen = set()
    for sym in snapshot_symbols:
        if sym.kind == "model" and sym.name:
            name_lower = sym.name.lower().replace(".", " ").replace("_", " ")
            simple = sym.name.split(".")[-1].replace("_", " ")
            if name_lower in prompt_lower or simple in prompt_lower:
                if sym.name not in seen:
                    found.append(sym.name)
                    seen.add(sym.name)
    return found[:10]


def _extract_field_targets(prompt: str, model_names: list[str], snapshot_symbols: list) -> list[str]:
    """Find field names for the identified models that appear in the prompt."""
    prompt_lower = prompt.lower()
    found = []
    seen = set()
    for sym in snapshot_symbols:
        if sym.kind == "field" and sym.model in model_names:
            field_label = sym.payload.get("string", "") or sym.name
            if sym.name in prompt_lower or field_label.lower() in prompt_lower:
                key = f"{sym.model}.{sym.name}"
                if key not in seen:
                    found.append(key)
                    seen.add(key)
    return found[:20]


_FEATURE_KEYWORDS = {
    "approval": ["workflow", "approve", "approval", "state", "stage"],
    "notification": ["notify", "notification", "mail", "email", "chatter"],
    "report": ["report", "pdf", "print", "qweb"],
    "access": ["access", "permission", "group", "security", "rule"],
    "view": ["form", "list", "view", "tree", "kanban", "dashboard"],
    "cron": ["scheduled", "cron", "automatic", "daily", "nightly"],
    "integration": ["sync", "integrate", "api", "webhook"],
}


def _classify_feature_areas(prompt: str) -> list[str]:
    prompt_lower = prompt.lower()
    areas = []
    for area, keywords in _FEATURE_KEYWORDS.items():
        if any(kw in prompt_lower for kw in keywords):
            areas.append(area)
    return areas


# ---------------------------------------------------------------------------
# Acceptance check builders — one per concern
# ---------------------------------------------------------------------------

def _build_module_checks(module_name: str, is_upgrade: bool) -> list[CheckSpec]:
    checks = [
        CheckSpec(
            kind="module_install" if not is_upgrade else "module_upgrade",
            spec_target={"module": module_name},
            required=True,
            task_id=TASK_VALIDATE_AND_VERIFY,
        ),
        CheckSpec(
            kind="artifact_digest",
            spec_target={"module": module_name},
            required=True,
            task_id=TASK_VALIDATE_AND_VERIFY,
        ),
    ]
    return checks


def _build_model_field_checks(model_name: str, field_names: list[str]) -> list[CheckSpec]:
    return [
        CheckSpec(
            kind="model_field",
            spec_target={"model": model_name, "field": f},
            required=True,
            task_id=TASK_VALIDATE_AND_VERIFY,
        )
        for f in field_names
    ]


def _build_view_checks(model_name: str, xml_ids: list[str]) -> list[CheckSpec]:
    checks = []
    for xml_id in xml_ids:
        checks.append(CheckSpec(
            kind="xml_id",
            spec_target={"xml_id": xml_id},
            required=True,
            task_id=TASK_IMPLEMENT_VIEWS,
        ))
        checks.append(CheckSpec(
            kind="view_load",
            spec_target={"xml_id": xml_id, "model": model_name},
            required=True,
            task_id=TASK_VALIDATE_AND_VERIFY,
        ))
    return checks


def _build_security_checks(model_name: str) -> list[CheckSpec]:
    return [
        CheckSpec(
            kind="acl",
            spec_target={"model": model_name},
            required=True,
            task_id=TASK_IMPLEMENT_SECURITY,
        ),
    ]


def _build_test_check(scenario: str) -> CheckSpec:
    return CheckSpec(
        kind="business_scenario",
        spec_target={"scenario": scenario},
        required=True,
        task_id=TASK_VALIDATE_AND_VERIFY,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compile_specification(
    run_id: str,
    project_id: int,
    snapshot_id: str | None,
    prompt: str,
    module_name: str,
    is_upgrade: bool,
    snapshot_symbols: list,
    db: "Session",
) -> SpecificationResult:
    """Derive a structured RunSpecification and AcceptanceCheck rows from the prompt.

    Args:
        run_id: The AgentRun.id this spec belongs to.
        project_id: The owning project.
        snapshot_id: The SourceSnapshot.id used for symbol resolution.
        prompt: The original user request.
        module_name: The Odoo addon name to build/upgrade.
        is_upgrade: True if the module already exists on the instance.
        snapshot_symbols: List of SourceSymbol ORM objects (may be empty when snapshot
            is not yet indexed — checks will be minimal).
        db: Open SQLAlchemy session.

    Returns:
        SpecificationResult with persisted IDs.
    """
    import models as m

    now = datetime.now(timezone.utc)
    # ---- heuristic analysis ----
    model_targets = _extract_model_targets(prompt, snapshot_symbols)
    field_targets = _extract_field_targets(prompt, model_targets, snapshot_symbols)
    feature_areas = _classify_feature_areas(prompt)

    # ---- build requirements ----
    requirements: list[RequirementSpec] = []

    # Requirement 1: module lifecycle
    r_module = RequirementSpec(
        id=f"req_{uuid4().hex[:8]}",
        title=f"{'Upgrade' if is_upgrade else 'Install'} module {module_name}",
        description=f"The module must {'upgrade cleanly' if is_upgrade else 'install'} on the target Odoo 19 instance.",
        implementation_targets=[module_name],
        check_specs=_build_module_checks(module_name, is_upgrade),
    )
    requirements.append(r_module)

    # Requirement 2: model/field presence (for each identified model)
    for model_name in model_targets:
        model_fields = [
            f.split(".", 1)[1] for f in field_targets if f.startswith(f"{model_name}.")
        ]
        if model_fields:
            r_fields = RequirementSpec(
                id=f"req_{uuid4().hex[:8]}",
                title=f"Model {model_name} has required fields",
                description=f"Fields {model_fields} must be present on {model_name} after installation.",
                implementation_targets=[model_name],
                check_specs=_build_model_field_checks(model_name, model_fields),
            )
            requirements.append(r_fields)

    # Requirement 3: view/UI checks
    if "view" in feature_areas or model_targets:
        for model_name in model_targets[:2]:
            safe = model_name.replace(".", "_")
            xml_ids = [f"view_{safe}_form", f"view_{safe}_list"]
            r_views = RequirementSpec(
                id=f"req_{uuid4().hex[:8]}",
                title=f"Views for {model_name} load without error",
                description=f"Form and list views for {model_name} must be loadable.",
                implementation_targets=[model_name],
                check_specs=_build_view_checks(model_name, xml_ids),
            )
            requirements.append(r_views)

    # Requirement 4: security
    for model_name in model_targets[:3]:
        r_sec = RequirementSpec(
            id=f"req_{uuid4().hex[:8]}",
            title=f"Access control rules for {model_name}",
            description="ACL rows must be present for every new model.",
            implementation_targets=[model_name],
            check_specs=_build_security_checks(model_name),
        )
        requirements.append(r_sec)

    # Requirement 5: business scenario test
    scenario_label = prompt[:120].replace("\n", " ").strip()
    r_test = RequirementSpec(
        id=f"req_{uuid4().hex[:8]}",
        title="Automated scenario test passes",
        description=f"An odoo.tests.common test covering the core scenario must pass: {scenario_label}",
        implementation_targets=[module_name],
        check_specs=[_build_test_check(scenario_label)],
    )
    requirements.append(r_test)

    # ---- collect all check IDs ----
    all_checks: list[CheckSpec] = []
    for req in requirements:
        all_checks.extend(req.check_specs)

    # ---- compute spec digest ----
    spec_payload = {
        "run_id": run_id,
        "module": module_name,
        "is_upgrade": is_upgrade,
        "requirements": [
            {
                "id": r.id,
                "title": r.title,
                "targets": r.implementation_targets,
                "check_count": len(r.check_specs),
            }
            for r in requirements
        ],
    }
    digest = hashlib.sha256(
        json.dumps(spec_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    # ---- persist RunSpecification ----
    check_ids_placeholder: list[str] = []  # will fill after check insert
    spec = m.RunSpecification(
        run_id=run_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        requirements=[
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "targets": r.implementation_targets,
            }
            for r in requirements
        ],
        changes=[
            {"kind": "model", "target": t}
            for t in model_targets
        ] + [
            {"kind": "field", "target": f}
            for f in field_targets
        ],
        acceptance_check_ids=check_ids_placeholder,
        digest=digest,
        status="draft",
        created_at=now,
    )
    db.add(spec)
    db.flush()

    # ---- persist AcceptanceCheck rows ----
    check_ids = []
    for cs in all_checks:
        check = m.AcceptanceCheck(
            id=str(uuid4()),
            run_id=run_id,
            task_id=cs.task_id,
            kind=cs.kind,
            spec_target=cs.spec_target,
            required=cs.required,
            status="pending",
            evidence=[],
            created_at=now,
        )
        db.add(check)
        db.flush()
        check_ids.append(check.id)

    # Back-fill acceptance_check_ids now that we have real IDs
    spec.acceptance_check_ids = check_ids
    db.flush()

    logger.info(
        "compile_specification: run=%s module=%s requirements=%d checks=%d digest=%s",
        run_id, module_name, len(requirements), len(check_ids), digest[:12],
    )

    return SpecificationResult(
        specification_id=spec.id,
        run_id=run_id,
        requirements=requirements,
        check_ids=check_ids,
        digest=digest,
    )


def get_specification_summary(run_id: str, db: "Session") -> dict | None:
    """Return a JSON-serialisable summary of the spec for API responses."""
    import models as m
    spec = db.query(m.RunSpecification).filter(m.RunSpecification.run_id == run_id).first()
    if spec is None:
        return None
    checks = db.query(m.AcceptanceCheck).filter(m.AcceptanceCheck.run_id == run_id).all()
    return {
        "id": spec.id,
        "run_id": run_id,
        "snapshot_id": spec.snapshot_id,
        "requirements": spec.requirements,
        "changes": spec.changes,
        "digest": spec.digest,
        "status": spec.status,
        "created_at": spec.created_at.isoformat(),
        "acceptance_checks": [
            {
                "id": c.id,
                "kind": c.kind,
                "task_id": c.task_id,
                "spec_target": c.spec_target,
                "required": c.required,
                "status": c.status,
                "result_detail": c.result_detail,
                "evidence": c.evidence,
                "evaluated_at": c.evaluated_at.isoformat() if c.evaluated_at else None,
            }
            for c in checks
        ],
    }
