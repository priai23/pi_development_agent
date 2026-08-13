from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import relationship

from database import Base


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
            "project_id",
            unique=True,
            postgresql_where=text("status IN ('running','awaiting_approval','cancelling')"),
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String(24), nullable=False, default="queued", index=True)
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
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
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
    status = Column(String(24), nullable=False, default="draft")
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


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
    category = Column(String(32), nullable=False, index=True)  # e.g., 'schema_insight', 'odoo_gotcha', 'user_preference', 'module_pattern'
    key = Column(String(128), nullable=False, index=True)
    content = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    usage_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    project = relationship("Project", back_populates="memories")

