"""Public response contracts; authorization remains in the query/command layer."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

Role = Literal['employee', 'hr', 'admin', 'it']
Category = Literal['MATERIAL_EXCEPTION', 'ACCESS_DEVICE', 'GENERAL_ONBOARDING']
CaseStatus = Literal['new', 'assigned', 'in_progress', 'waiting_employee', 'resolved', 'closed', 'cancelled']


class ExtensibleResponse(BaseModel):
    model_config = ConfigDict(extra='allow')


class EmployeeProfile(ExtensibleResponse):
    id: str
    name: str
    city: str | None
    employment_type: str | None
    planned_start_date: str | None


class Account(EmployeeProfile):
    role: Role
    company: str
    group_name: str | None


class SessionState(BaseModel):
    user: Account
    csrf_token: str


class SimpleResult(BaseModel):
    ok: bool


class TaskContext(ExtensibleResponse):
    id: str | None = None
    name: str | None = None
    city: str | None = None
    planned_start_date: str | None = None
    instruction: str


class TaskResponse(ExtensibleResponse):
    id: str
    case_id: str
    type: Literal['IT_ACCESS', 'IT_DEVICE', 'MATERIAL_FOLLOWUP']
    assignee_id: str
    status: Literal['pending', 'completed', 'cancelled']
    required: bool
    dependency_task_ids: list[str]
    due_at: str
    completed_at: str | None
    minimal_context: TaskContext
    row_version: int


class MaterialResponse(BaseModel):
    id: str
    case_id: str
    required_item_code: str
    provided: bool
    format_check_status: str
    supplied_at: str | None


class MessageResponse(BaseModel):
    id: str
    case_id: str
    actor_id: str
    body: str
    visibility: Literal['public', 'internal']
    created_at: str


class EventResponse(ExtensibleResponse):
    id: str
    kind: str
    actor_id: str
    case_id: str | None
    metadata: dict[str, Any]
    visibility: Literal['public', 'internal']
    occurred_at: str
    data_origin: str
    schema_version: int


class CaseResponse(ExtensibleResponse):
    id: str
    case_number: str
    requester_id: str
    category: Category
    status: CaseStatus
    assigned_group: str
    assigned_to: str
    description: str
    structured_fields: dict[str, Any]
    policy_snapshot_refs: list[dict[str, Any]]
    context_source: dict[str, Any]
    created_at: str
    updated_at: str
    first_human_response_at: str | None
    due_at: str
    latest_resolved_at: str | None
    closed_at: str | None
    cancelled_at: str | None
    resolution_code: str | None
    reopen_count: int
    idempotency_key: str
    row_version: int
    sla_hours: float
    data_origin: str


class CaseDetail(CaseResponse):
    requester: EmployeeProfile
    messages: list[MessageResponse]
    events: list[EventResponse]
    tasks: list[TaskResponse]
    materials: list[MaterialResponse]
    profile_changed: bool


class RequestDraft(ExtensibleResponse):
    id: str
    requester_id: str
    category: Category
    draft_version: int
    context_source: dict[str, Any]
    fields: dict[str, Any]
    created_at: str
    updated_at: str
    case_id: str | None
    confirmation_token: str
    existing_cases: list[CaseResponse]


class ReplyDraft(BaseModel):
    body: str
    citation_refs: list[dict[str, Any]]
    provider_mode: Literal['offline', 'offline_fallback', 'remote']
    published: bool


class ProfileChangeResponse(BaseModel):
    id: str
    planned_start_date: str
    message: str
