from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import relationship

from database import Base


# ---------------------------------------------------------------------------
# Pipeline foundation models (Phase 1)
# ---------------------------------------------------------------------------


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(320), unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String(16), nullable=False, default="member")
    is_active = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    memberships = relationship("OrganizationMembership", back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    monthly_budget_usd = Column(Float, nullable=True)
    budget_warning_percent = Column(Integer, nullable=False, default=80)

    projects = relationship("Project", back_populates="organization", cascade="all, delete-orphan")
    memberships = relationship("OrganizationMembership", back_populates="organization", cascade="all, delete-orphan")


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(16), nullable=False, default="member")
    is_approver = Column(Boolean, nullable=False, default=False)

    user = relationship("User", back_populates="memberships")
    organization = relationship("Organization", back_populates="memberships")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    csrf_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    user = relationship("User")


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    workspace_slug = Column(String, unique=True, nullable=False)
    phase = Column(String(32), nullable=False, default="discovery")
    monthly_budget_usd = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    organization = relationship("Organization", back_populates="projects")
    created_by = relationship("User")
    instances = relationship("Instance", back_populates="project", cascade="all, delete-orphan")
    interactions = relationship("Interaction", back_populates="project", cascade="all, delete-orphan")
    memories = relationship("AgentMemory", back_populates="project", cascade="all, delete-orphan")
    schedules = relationship("AgentSchedule", back_populates="project", cascade="all, delete-orphan")



class Instance(Base):
    __tablename__ = "instances"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    erp_type = Column(String(16), nullable=False)
    url = Column(String, nullable=False)
    db_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    password_encrypted = Column(Text, nullable=True)
    api_key_encrypted = Column(Text, nullable=True)
    environment = Column(String(16), nullable=False, default="staging")
    hosting_type = Column(String(16), nullable=False, default="on_premise")
    auth_method = Column(String(16), nullable=False, default="xmlrpc")
    status = Column(String(16), nullable=False, default="connected")
    is_active = Column(Boolean, nullable=False, default=True)
    version_info = Column(JSON, nullable=False, default=dict)
    capabilities = Column(JSON, nullable=False, default=dict)
    bridge_status = Column(String(24), nullable=False, default="not_configured")
    deployment_config_encrypted = Column(Text, nullable=True)
    last_tested_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    project = relationship("Project", back_populates="instances")


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    project = relationship("Project", back_populates="interactions")


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
    encrypted = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    thread_id = Column(String, nullable=False, index=True)
    tool_call_id = Column(String, unique=True, nullable=False)
    tool_name = Column(String, nullable=False)
    arguments = Column(JSON, nullable=False)
    preview = Column(JSON, nullable=False)
    risk_class = Column(String(1), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    decided_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    execution_started_at = Column(DateTime(timezone=True), nullable=True)
    idempotency_key = Column(String(128), unique=True, nullable=True)


class PermissionGrant(Base):
    """A narrowly scoped allow/deny rule for an agent resource."""
    __tablename__ = "permission_grants"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", "resource", name="uq_permission_grant_scope"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    resource = Column(String(512), nullable=False)
    decision = Column(String(8), nullable=False, default="ask")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class TerminalSession(Base):
    """Auditable one-shot terminal execution owned by a project user."""
    __tablename__ = "terminal_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    requested_by_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    command = Column(Text, nullable=False)
    cwd = Column(String(512), nullable=False, default=".")
    status = Column(String(24), nullable=False, default="awaiting_approval")
    output = Column(Text, nullable=False, default="")
    exit_code = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type = Column(String, nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    risk_class = Column(String(1), nullable=True)
    result = Column(String(24), nullable=True)
    support_id = Column(String(36), nullable=True, index=True)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class HostPolicy(Base):
    __tablename__ = "host_policies"

    id = Column(Integer, primary_key=True)
    hostname_pattern = Column(String, unique=True, nullable=False)
    allow_private_network = Column(Boolean, nullable=False, default=False)
    require_https = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index(
            "uq_agent_run_active_project",
            "project_id", "intent",
            unique=True,
            postgresql_where=text("status IN ('running','awaiting_question','awaiting_approval','cancelling') AND intent = 'write'"),
            sqlite_where=text("status IN ('running','awaiting_question','awaiting_approval','cancelling') AND intent = 'write'"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(24), nullable=False, default="queued", index=True)
    # Read-only investigations may run concurrently; write runs remain serialized per project.
    intent = Column(String(16), nullable=False, default="write", index=True)
    prompt = Column(Text, nullable=False)
    thread_id = Column(String, nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    retry_of_id = Column(String(36), ForeignKey("agent_runs.id"), nullable=True)
    support_id = Column(String(36), nullable=False, default=lambda: str(uuid4()), index=True)
    error_category = Column(String(32), nullable=True)
    error_message = Column(String, nullable=True)
    error_detail = Column(Text, nullable=True)
    retryable = Column(Boolean, nullable=False, default=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # A2A task decomposition — Supervisor task graph
    task_graph = Column(JSON, nullable=True)             # list[TaskGraphItem] — ordered task plan
    active_task_id = Column(String(64), nullable=True)   # currently executing sub-task id
    subtask_heartbeat_at = Column(DateTime(timezone=True), nullable=True)  # per-task heartbeat
    task_retries = Column(JSON, nullable=True)            # {task_id: retry_count}
    planner_model = Column(String(64), nullable=True)     # model to use for planning
    fallback_model = Column(String(64), nullable=True)    # fallback model
    workspace_base_revision = Column(String(40), nullable=True)
    workspace_slug = Column(String(200), nullable=True, index=True)
    # Pipeline grounding (Phase 1)
    source_snapshot_id = Column(String(36), ForeignKey("source_snapshots.id", ondelete="SET NULL"), nullable=True)
    specification_id = Column(Integer, ForeignKey("run_specifications.id", ondelete="SET NULL"), nullable=True)
    stage = Column(String(32), nullable=False, default="queued")  # queued|grounding|indexing|planning|implementing|validating|staging|done
    last_progress_at = Column(DateTime(timezone=True), nullable=True)
    current_operation = Column(String(128), nullable=True)        # human-readable current op description
    operation_deadline_at = Column(DateTime(timezone=True), nullable=True)
    module_name = Column(String(128), nullable=True)
    question = relationship("AgentQuestion", back_populates="run", uselist=False, cascade="all, delete-orphan")
    subtasks = relationship("AgentSubtask", back_populates="parent_run", cascade="all, delete-orphan")


class AgentSubtask(Base):
    """A persisted child-agent execution owned by a supervisor run."""
    __tablename__ = "agent_subtasks"
    __table_args__ = (
        UniqueConstraint("parent_run_id", "task_id", name="uq_agent_subtask_parent_task"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    parent_run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(String(64), nullable=False)
    title = Column(String(300), nullable=False)
    role = Column(String(64), nullable=False, default="implementation_specialist")
    thread_id = Column(String(200), nullable=False, unique=True)
    status = Column(String(24), nullable=False, default="queued", index=True)
    prompt = Column(Text, nullable=False, default="")
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    parent_run = relationship("AgentRun", back_populates="subtasks")


class AgentSchedule(Base):
    """Durable recurring prompt owned by a project."""
    __tablename__ = "agent_schedules"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    prompt = Column(Text, nullable=False)
    interval_seconds = Column(Integer, nullable=False, default=3600)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    next_run_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True)
    last_error = Column(Text, nullable=True)
    # Reliability features (Phase 11)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)  # sha256 stamped at dispatch
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    retry_backoff_seconds = Column(Integer, nullable=False, default=300)
    missed_run_policy = Column(String(16), nullable=False, default="skip")   # skip | run_once | run_all
    concurrency_policy = Column(String(16), nullable=False, default="skip")  # skip | queue | cancel_prior
    timeout_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="schedules")


class AgentQuestion(Base):
    __tablename__ = "agent_questions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(32), nullable=False, default="agent")
    question = Column(Text, nullable=False)
    options = Column(JSON, nullable=False, default=list)
    answer = Column(Text, nullable=True)
    status = Column(String(24), nullable=False, default="pending", index=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    run = relationship("AgentRun", back_populates="question")


class ToolEvent(Base):
    __tablename__ = "tool_events"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(32), nullable=False)
    aggregate_id = Column(String(36), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    claimed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    completed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Requirement(Base):
    __tablename__ = "requirements"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False, default="")
    acceptance_criteria = Column(Text, nullable=False, default="")
    status = Column(String(24), nullable=False, default="draft")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ProjectTask(Base):
    __tablename__ = "project_tasks"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_id = Column(Integer, ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True)
    title = Column(String, nullable=False)
    status = Column(String(24), nullable=False, default="backlog")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class DiscoveryFinding(Base):
    __tablename__ = "discovery_findings"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(64), nullable=False)
    title = Column(String, nullable=False)
    evidence = Column(JSON, nullable=False, default=dict)
    verified = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class TechnicalSpecification(Base):
    __tablename__ = "technical_specifications"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_id = Column(Integer, ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    risk_assessment = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="draft")
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class UATEvidence(Base):
    __tablename__ = "uat_evidence"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id = Column(String(36), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    notes = Column(Text, nullable=False)
    accepted_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requirement_id = Column(Integer, ForeignKey("requirements.id", ondelete="SET NULL"), nullable=True)
    artifact_type = Column(String(24), nullable=False)
    name = Column(String, nullable=False)
    version = Column(String, nullable=False)
    commit_hash = Column(String(64), nullable=False)
    digest = Column(String(64), nullable=False)
    path = Column(String, nullable=False)
    # The run-owned workspace that produced this artifact. Nullable keeps
    # existing artifacts and project-local artifacts backward compatible.
    workspace_slug = Column(String(200), nullable=True, index=True)
    status = Column(String(24), nullable=False, default="draft")
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    # Build traceability (Phase 1)
    spec_digest = Column(String(64), nullable=True)              # SHA-256 of RunSpecification at build time
    source_snapshot_digest = Column(String(64), nullable=True)   # fingerprint of the SourceSnapshot used
    build_fingerprint = Column(JSON, nullable=True)              # {odoo_version, edition, depends, files}


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id = Column(String(36), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(24), nullable=False, default="queued")
    report = Column(JSON, nullable=False, default=dict)
    logs = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    instance_id = Column(Integer, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False)
    artifact_id = Column(String(36), ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    validation_id = Column(String(36), ForeignKey("validation_runs.id"), nullable=False)
    environment = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False, default="pending_approval")
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    approved_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rollback_plan = Column(Text, nullable=False, default="")
    logs = Column(Text, nullable=False, default="")
    external_job_id = Column(String(128), nullable=True, index=True)
    # Post-deploy verification (Phase 10)
    smoke_test_result = Column(JSON, nullable=True)               # {ok, checks: [{name, ok, detail}]}
    prior_artifact_id = Column(String(36), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    recovery_state = Column(String(24), nullable=True)            # recovering | recovered | unrecoverable
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    category = Column(String(32), nullable=False, index=True)  # e.g., 'user_preference', 'verified_fact'; legacy: 'schema_insight', 'odoo_gotcha', 'module_pattern'
    key = Column(String(128), nullable=False, index=True)
    content = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    usage_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    # Evidence backing (Phase 1 / Phase 14)
    evidence_type = Column(String(32), nullable=True)        # 'acceptance_check' | 'source_symbol' | 'user_event'
    evidence_ref_id = Column(String(64), nullable=True)      # ID of the backing AcceptanceCheck or SourceSymbol
    snapshot_scope_id = Column(String(36), nullable=True)    # SourceSnapshot.id this fact is scoped to
    verified_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # None = never expires

    project = relationship("Project", back_populates="memories")


# ---------------------------------------------------------------------------
# New pipeline tables
# ---------------------------------------------------------------------------


class SourceSnapshot(Base):
    """Immutable point-in-time fingerprint of the connected Odoo environment."""
    __tablename__ = "source_snapshots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    instance_id = Column(Integer, ForeignKey("instances.id", ondelete="CASCADE"), nullable=False, index=True)
    odoo_version = Column(String(16), nullable=False)        # e.g. '19.0'
    odoo_edition = Column(String(16), nullable=False)        # 'community' | 'enterprise'
    db_uuid = Column(String(64), nullable=True)              # Odoo ir.config_parameter database.uuid
    server_serial = Column(String(64), nullable=True)        # Odoo server serial / git hash
    installed_modules = Column(JSON, nullable=False, default=dict)   # {module_name: version}
    addon_digests = Column(JSON, nullable=False, default=dict)       # {module_name: sha256}
    fingerprint = Column(String(64), nullable=False, index=True)     # SHA-256 of the above for fast equality
    runner_identity = Column(String(128), nullable=True)     # public key thumbprint of runner that built this
    status = Column(String(24), nullable=False, default="pending_index")  # pending_index|indexing|indexed|failed
    symbol_count = Column(Integer, nullable=False, default=0)
    index_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    indexed_at = Column(DateTime(timezone=True), nullable=True)


class SourceSymbol(Base):
    """A parsed symbol from an indexed SourceSnapshot — read-only, never modified after insert."""
    __tablename__ = "source_symbols"
    __table_args__ = (
        Index("ix_source_symbols_snapshot_kind", "snapshot_id", "kind"),
        Index("ix_source_symbols_module_model", "module", "model"),
    )

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(String(36), ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    module = Column(String(128), nullable=False, index=True)
    model = Column(String(128), nullable=True, index=True)       # None for non-model symbols
    kind = Column(String(32), nullable=False, index=True)        # model|field|method|view|action|rule|acl|cron|js_component|manifest
    name = Column(String(256), nullable=False, index=True)
    path = Column(String(512), nullable=True)                    # relative path within addon
    line_start = Column(Integer, nullable=True)
    line_end = Column(Integer, nullable=True)
    digest = Column(String(64), nullable=True)                   # SHA-256 of the symbol source excerpt
    payload = Column(JSON, nullable=False, default=dict)         # kind-specific structured data


class RunSpecification(Base):
    """Immutable structured specification derived from the user prompt before any code generation."""
    __tablename__ = "run_specifications"

    id = Column(Integer, primary_key=True)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id = Column(String(36), ForeignKey("source_snapshots.id", ondelete="SET NULL"), nullable=True)
    requirements = Column(JSON, nullable=False, default=list)    # list[{id, title, targets, check_ids}]
    changes = Column(JSON, nullable=False, default=list)         # list[{kind, model, field/view/rule, description}]
    acceptance_check_ids = Column(JSON, nullable=False, default=list)  # [AcceptanceCheck.id, ...]
    digest = Column(String(64), nullable=False)                  # SHA-256 of canonical JSON
    status = Column(String(24), nullable=False, default="draft")  # draft|approved|superseded
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AcceptanceCheck(Base):
    """A single verification gate that must pass before task success is declared."""
    __tablename__ = "acceptance_checks"
    __table_args__ = (
        Index("ix_acceptance_checks_run_kind", "run_id", "kind"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(String(64), nullable=True)                  # which task owns this check
    kind = Column(String(32), nullable=False)                    # source_reuse|module_install|module_upgrade|
                                                                 # artifact_digest|model_field|xml_id|view_load|
                                                                 # acl|record_rule|python_test|business_scenario
    spec_target = Column(JSON, nullable=False, default=dict)     # kind-specific expectation (model, field, xml_id, etc.)
    required = Column(Boolean, nullable=False, default=True)
    status = Column(String(16), nullable=False, default="pending")  # pending|running|passed|failed|skipped
    evidence = Column(JSON, nullable=False, default=list)         # [{kind, ref, digest, summary}]
    result_detail = Column(Text, nullable=True)                  # failure reason or pass note
    evaluated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class ToolExecution(Base):
    """Exactly-once receipt for every side-effecting tool call."""
    __tablename__ = "tool_executions"

    operation_id = Column(String(64), primary_key=True)          # SHA-256(run_id + task_id + tool_call_id)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = Column(String(64), nullable=True)
    tool_call_id = Column(String(128), nullable=False)
    tool_name = Column(String(128), nullable=False)
    args_digest = Column(String(64), nullable=False)             # SHA-256 of canonicalised args JSON
    status = Column(String(16), nullable=False, default="preparing")  # preparing|executing|succeeded|failed
    structured_result = Column(JSON, nullable=True)              # stored ToolResult for idempotent replay
    retryable = Column(Boolean, nullable=False, default=False)
    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Durable attachment storage (Phase 6)
# ---------------------------------------------------------------------------


class RunAttachment(Base):
    """Durable attachment uploaded to a run — stores original + extracted text."""
    __tablename__ = "run_attachments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    uploader_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # File metadata
    original_filename = Column(String(512), nullable=False)
    mime_type = Column(String(128), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)  # SHA-256 for dedup

    # Storage
    storage_path = Column(String(1024), nullable=False)
    storage_backend = Column(String(16), nullable=False, default="local")  # "local" | "s3"

    # Processing pipeline state
    processing_state = Column(
        String(24), nullable=False, default="pending",
    )  # pending | scanning | extracting | indexed | failed
    scan_result = Column(
        String(16), nullable=True,
    )  # clean | malicious | error | skipped

    # Extracted content
    extracted_text = Column(Text, nullable=True)
    extraction_error = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)

    # Evidence linkage
    evidence_refs = Column(JSON, nullable=False, default=list)  # [{run_id, artifact_id, page}]

    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    processed_at = Column(DateTime(timezone=True), nullable=True)
