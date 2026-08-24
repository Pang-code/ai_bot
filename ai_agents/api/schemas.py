from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    session_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message 不能为空")
        return value


class ChatResponse(BaseModel):
    session_id: UUID
    request_id: str
    status: Literal["completed", "pending_approval"]
    answer: str | None = None
    pending_approval: "PendingApprovalResponse | None" = None


class PendingApprovalResponse(BaseModel):
    tool_name: str
    arguments: dict[str, object]
    description: str


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    tenant_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserResponse(BaseModel):
    user_id: UUID
    email: str


class TenantResponse(BaseModel):
    tenant_id: UUID
    name: str
    role: Literal["owner", "admin", "member", "auditor"]


class TenantListResponse(BaseModel):
    tenants: list[TenantResponse]


class AddMemberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["owner", "admin", "member", "auditor"]


class AuditEventResponse(BaseModel):
    event_id: UUID
    actor_user_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    status: str
    details: dict[str, object]
    created_at: datetime
    request_id: str


class AuditListResponse(BaseModel):
    events: list[AuditEventResponse]


class SessionSummary(BaseModel):
    session_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]


class MessageResponse(BaseModel):
    message_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class SessionMessagesResponse(BaseModel):
    session: SessionSummary
    messages: list[MessageResponse]


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
