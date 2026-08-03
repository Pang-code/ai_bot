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
    answer: str


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
