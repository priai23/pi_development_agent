from datetime import datetime
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORMModel):
    id: int
    email: str
    role: str
    is_active: bool
    must_change_password: bool


class AuthState(BaseModel):
    user: UserOut
    csrf_token: str


class SetupStatusOut(BaseModel):
    needs_setup: bool
    user_count: int


class SetupAdminRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    role: Literal["admin", "member"] = "member"
    organization_ids: list[int] = []
    approver_organization_ids: list[int] = []


class AdminUserUpdate(BaseModel):
    role: Literal["admin", "member"] | None = None
    is_active: bool | None = None
    organization_ids: list[int] | None = None
    approver_organization_ids: list[int] | None = None


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class BudgetUpdate(BaseModel):
    monthly_budget_usd: float = Field(gt=0, le=1_000_000)
    budget_warning_percent: int = Field(default=80, ge=1, le=100)


class OrganizationOut(ORMModel):
    id: int
    name: str
    created_at: datetime
    monthly_budget_usd: float | None
    budget_warning_percent: int


class InstanceOut(ORMModel):
    id: int
    project_id: int
    erp_type: str
    url: str
    db_name: str | None
    username: str | None
    environment: str
    hosting_type: str
    auth_method: str
    status: str
    is_active: bool
    version_info: dict
    capabilities: dict
    bridge_status: str
    last_tested_at: datetime | None
    last_error: str | None
    created_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    organization_id: int


class ProjectOut(ORMModel):
    id: int
    name: str
    organization_id: int
    created_by_id: int
    workspace_slug: str
    phase: str
    created_at: datetime
    instances: list[InstanceOut] = []


class DetectRequest(BaseModel):
    url: AnyHttpUrl
    erp_type: Literal["odoo", "pi_erp"]


class InstanceCreate(BaseModel):
    erp_type: Literal["odoo", "pi_erp"]
    url: AnyHttpUrl
    db_name: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    environment: Literal["staging", "production"] = "staging"
    hosting_type: Literal["on_premise", "odoo_sh"] = "on_premise"
    auth_method: Literal["json2", "xmlrpc"] = "json2"
    project_id: int


class InstanceUpdate(BaseModel):
    url: AnyHttpUrl | None = None
    db_name: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    environment: Literal["staging", "production"] | None = None
    hosting_type: Literal["on_premise", "odoo_sh"] | None = None
    auth_method: Literal["json2", "xmlrpc"] | None = None
    is_active: bool | None = None


class InteractionOut(ORMModel):
    id: int
    project_id: int
    role: str
    content: str
    created_at: datetime


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class ActionDecision(BaseModel):
    decision: Literal["approve", "reject"]


class QuestionAnswer(BaseModel):
    answer: str


class AgentQuestionOut(ORMModel):
    id: str
    run_id: str
    question: str
    options: list[str]
    answer: str | None
    status: str
    created_at: datetime
    expires_at: datetime


class PendingActionOut(ORMModel):
    id: str
    run_id: str | None
    tool_name: str
    arguments: dict
    preview: dict
    risk_class: str
    status: str
    expires_at: datetime


class LLMSettingsOut(BaseModel):
    model_name: str
    api_key_configured: bool
    fallback_model_name: str | None = None
    timeout_seconds: int = 120
    max_output_tokens: int = 8000


class LLMSettingsUpdate(BaseModel):
    model_name: str = Field(min_length=1, max_length=200)
    openrouter_api_key: str | None = Field(default=None, min_length=10)
    fallback_model_name: str | None = None
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_output_tokens: int = Field(default=8000, ge=256, le=100_000)


class RunCreate(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    queue_if_busy: bool = False


class RunOut(ORMModel):
    id: str
    project_id: int
    requested_by_id: int
    status: str
    prompt: str
    support_id: str
    error_category: str | None
    error_message: str | None
    retryable: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    task_graph: list[Any] | None = None
    active_task_id: str | None = None
    planner_model: str | None = None
    fallback_model: str | None = None
    workspace_base_revision: str | None = None
    question: AgentQuestionOut | None = None


# ─── A2A Task Decomposition Schemas ───────────────────────────────────────────

class TaskGraphItem(BaseModel):
    """A single node in the Supervisor task graph."""
    task_id: str                                               # e.g. 'task_02_extend_models'
    title: str                                                 # human-readable label
    risk_class: int = 1                                        # 1=read, 2=reversible-write, 3=destructive
    depends_on: list[str] = []                                 # task_ids this task depends on
    status: Literal["pending", "in_progress", "blocked", "done", "failed", "cancelled"] = "pending"
    retry_count: int = 0
    max_retries: int = 2                                       # configurable per task
    context_bundle: dict[str, Any] = {}                        # scoped context slice passed to worker
    result: dict[str, Any] | None = None                      # result reported back to supervisor
    heartbeat_at: str | None = None                           # ISO timestamp of last sub-task heartbeat


class HandoffMessage(BaseModel):
    """Supervisor ↔ Worker message envelope."""
    task_id: str
    parent_run_id: str
    depends_on: list[str] = []
    status: Literal["pending", "in_progress", "blocked", "done", "failed", "cancelled"]
    context_bundle: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    heartbeat_at: str | None = None
    retry_count: int = 0


class ToolEventOut(ORMModel):
    id: int
    run_id: str
    sequence: int
    event_type: str
    payload: dict
    created_at: datetime


class HostPolicyCreate(BaseModel):
    hostname_pattern: str = Field(min_length=1, max_length=255)
    allow_private_network: bool = False
    require_https: bool = True


class HostPolicyOut(ORMModel):
    id: int
    hostname_pattern: str
    allow_private_network: bool
    require_https: bool
    is_active: bool
    created_by_id: int
    created_at: datetime


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12)


class PasswordResetComplete(BaseModel):
    token: str
    new_password: str = Field(min_length=12)


class SessionOut(ORMModel):
    id: str
    expires_at: datetime
    created_at: datetime
    last_seen_at: datetime


class RequirementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    acceptance_criteria: str = ""
    owner_id: int | None = None


class RequirementOut(ORMModel):
    id: int
    project_id: int
    title: str
    description: str
    acceptance_criteria: str
    status: str
    owner_id: int | None
    approved_by_id: int | None
    created_at: datetime


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    requirement_id: int | None = None
    owner_id: int | None = None


class TaskOut(ORMModel):
    id: int
    project_id: int
    requirement_id: int | None
    title: str
    status: str
    owner_id: int | None
    created_at: datetime


class DiscoveryCreate(BaseModel):
    category: Literal["instance", "version", "modules", "configuration", "security", "data"]
    title: str = Field(min_length=1, max_length=300)
    evidence: dict = {}
    verified: bool = False


class SpecificationCreate(BaseModel):
    requirement_id: int
    content: str = Field(min_length=20)
    risk_assessment: str = Field(min_length=10)


class UATEvidenceCreate(BaseModel):
    artifact_id: str
    notes: str = Field(min_length=10)


class PhaseUpdate(BaseModel):
    phase: Literal["discovery", "requirements", "design", "build", "validate", "uat", "ready_for_production"]


class AuditOut(ORMModel):
    id: int
    project_id: int | None
    user_id: int | None
    organization_id: int | None
    event_type: str
    risk_class: str | None
    result: str | None
    support_id: str | None
    details: dict
    created_at: datetime


class ArtifactCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    version: str = Field(pattern=r"^19\.0\.\d+\.\d+\.\d+$")
    path: str
    requirement_id: int | None = None


class ArtifactOut(ORMModel):
    id: str
    project_id: int
    requirement_id: int | None
    artifact_type: str
    name: str
    version: str
    commit_hash: str
    digest: str
    path: str
    status: str
    created_by_id: int
    created_at: datetime


class ValidationOut(ORMModel):
    id: str
    project_id: int
    artifact_id: str
    status: str
    report: dict
    logs: str
    created_at: datetime
    finished_at: datetime | None


class DeploymentConfigUpdate(BaseModel):
    bridge_url: AnyHttpUrl | None = None
    bridge_token: str | None = None
    repository_url: str | None = None
    staging_branch: str | None = None
    production_branch: str | None = None
    module_directory: str | None = None
    git_deploy_key: str | None = None


class DeploymentConfigOut(BaseModel):
    configured: bool
    public_key_base64: str | None = None
    bridge_url: str | None = None
    repository_configured: bool = False


class DeploymentCreate(BaseModel):
    instance_id: int
    artifact_id: str
    rollback_plan: str = Field(min_length=10, max_length=10_000)


class DeploymentOut(ORMModel):
    id: str
    project_id: int
    instance_id: int
    artifact_id: str
    validation_id: str
    environment: str
    status: str
    requested_by_id: int
    approved_by_id: int | None
    rollback_plan: str
    logs: str
    external_job_id: str | None
    created_at: datetime
    finished_at: datetime | None


class MemoryCreate(BaseModel):
    category: str = Field(min_length=2, max_length=32)
    key: str = Field(min_length=2, max_length=128)
    content: str = Field(min_length=1, max_length=10_000)
    confidence: float = 1.0


class MemoryOut(ORMModel):
    id: int
    project_id: int | None
    category: str
    key: str
    content: str
    confidence: float
    usage_count: int
    created_at: datetime
    updated_at: datetime
