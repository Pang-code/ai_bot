from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from ai_agents.auth import hash_password


Role = Literal["owner", "admin", "member", "auditor"]


@dataclass(frozen=True)
class UserRecord:
    user_id: UUID
    email: str
    password_hash: str
    created_at: datetime


@dataclass(frozen=True)
class TenantRecord:
    tenant_id: UUID
    name: str
    role: Role
    created_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    event_id: UUID
    actor_user_id: UUID | None
    tenant_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    status: str
    details: dict[str, Any]
    created_at: datetime
    request_id: str


class GovernanceRepository(Protocol):
    async def register(
        self, email: str, password: str, tenant_name: str
    ) -> tuple[UserRecord, TenantRecord]: ...

    async def get_user_by_email(self, email: str) -> UserRecord | None: ...

    async def get_user(self, user_id: UUID) -> UserRecord | None: ...

    async def list_tenants(self, user_id: UUID) -> list[TenantRecord]: ...

    async def get_role(self, user_id: UUID, tenant_id: UUID) -> Role | None: ...

    async def add_member(
        self, tenant_id: UUID, email: str, role: Role
    ) -> UserRecord | None: ...

    async def audit(
        self,
        *,
        actor_user_id: UUID | None,
        tenant_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        status: str,
        details: dict[str, Any],
        request_id: str,
    ) -> None: ...

    async def list_audit_events(
        self, tenant_id: UUID, limit: int = 100
    ) -> list[AuditEvent]: ...


class InMemoryGovernanceRepository:
    def __init__(self) -> None:
        self.users: dict[UUID, UserRecord] = {}
        self.user_ids_by_email: dict[str, UUID] = {}
        self.tenants: dict[UUID, tuple[str, datetime]] = {}
        self.memberships: dict[tuple[UUID, UUID], Role] = {}
        self.events: list[AuditEvent] = []

    async def register(
        self, email: str, password: str, tenant_name: str
    ) -> tuple[UserRecord, TenantRecord]:
        normalized = email.strip().lower()
        if normalized in self.user_ids_by_email:
            raise ValueError("email_exists")
        now = datetime.now(timezone.utc)
        user = UserRecord(uuid4(), normalized, hash_password(password), now)
        tenant_id = uuid4()
        self.users[user.user_id] = user
        self.user_ids_by_email[normalized] = user.user_id
        self.tenants[tenant_id] = (tenant_name, now)
        self.memberships[(tenant_id, user.user_id)] = "owner"
        return user, TenantRecord(tenant_id, tenant_name, "owner", now)

    async def get_user_by_email(self, email: str) -> UserRecord | None:
        user_id = self.user_ids_by_email.get(email.strip().lower())
        return self.users.get(user_id) if user_id else None

    async def get_user(self, user_id: UUID) -> UserRecord | None:
        return self.users.get(user_id)

    async def list_tenants(self, user_id: UUID) -> list[TenantRecord]:
        result = []
        for (tenant_id, member_id), role in self.memberships.items():
            if member_id == user_id:
                name, created_at = self.tenants[tenant_id]
                result.append(TenantRecord(tenant_id, name, role, created_at))
        return sorted(result, key=lambda item: item.created_at)

    async def get_role(self, user_id: UUID, tenant_id: UUID) -> Role | None:
        return self.memberships.get((tenant_id, user_id))

    async def add_member(
        self, tenant_id: UUID, email: str, role: Role
    ) -> UserRecord | None:
        user = await self.get_user_by_email(email)
        if user is None:
            return None
        self.memberships[(tenant_id, user.user_id)] = role
        return user

    async def audit(self, **kwargs: Any) -> None:
        self.events.append(
            AuditEvent(
                event_id=uuid4(),
                created_at=datetime.now(timezone.utc),
                **kwargs,
            )
        )

    async def list_audit_events(
        self, tenant_id: UUID, limit: int = 100
    ) -> list[AuditEvent]:
        events = [event for event in self.events if event.tenant_id == tenant_id]
        return list(reversed(events[-limit:]))
