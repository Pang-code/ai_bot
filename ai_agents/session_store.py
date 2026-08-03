from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class SessionRecord:
    session_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MessageRecord:
    message_id: UUID
    session_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class SessionRepository(Protocol):
    async def ensure_session(
        self,
        owner_id: UUID,
        session_id: UUID,
        title: str,
    ) -> bool: ...

    async def append_exchange(
        self,
        session_id: UUID,
        user_message: str,
        assistant_message: str,
    ) -> None: ...

    async def list_sessions(
        self,
        owner_id: UUID,
        limit: int = 100,
    ) -> list[SessionRecord]: ...

    async def get_session(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> SessionRecord | None: ...

    async def get_messages(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> list[MessageRecord] | None: ...

    async def delete_session(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> bool: ...


def make_session_title(message: str, max_length: int = 60) -> str:
    title = " ".join(message.split())
    if len(title) <= max_length:
        return title
    return f"{title[: max_length - 1]}…"


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[UUID, tuple[UUID, SessionRecord]] = {}
        self._messages: dict[UUID, list[MessageRecord]] = {}

    async def ensure_session(
        self,
        owner_id: UUID,
        session_id: UUID,
        title: str,
    ) -> bool:
        existing = self._sessions.get(session_id)
        if existing:
            return existing[0] == owner_id

        now = datetime.now(timezone.utc)
        self._sessions[session_id] = (
            owner_id,
            SessionRecord(
                session_id=session_id,
                title=title,
                created_at=now,
                updated_at=now,
            ),
        )
        self._messages[session_id] = []
        return True

    async def append_exchange(
        self,
        session_id: UUID,
        user_message: str,
        assistant_message: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        messages = self._messages[session_id]
        messages.extend(
            [
                MessageRecord(
                    message_id=uuid4(),
                    session_id=session_id,
                    role="user",
                    content=user_message,
                    created_at=now,
                ),
                MessageRecord(
                    message_id=uuid4(),
                    session_id=session_id,
                    role="assistant",
                    content=assistant_message,
                    created_at=now,
                ),
            ]
        )
        owner_id, session = self._sessions[session_id]
        self._sessions[session_id] = (
            owner_id,
            SessionRecord(
                session_id=session.session_id,
                title=session.title,
                created_at=session.created_at,
                updated_at=now,
            ),
        )

    async def list_sessions(
        self,
        owner_id: UUID,
        limit: int = 100,
    ) -> list[SessionRecord]:
        sessions = [
            session
            for session_owner, session in self._sessions.values()
            if session_owner == owner_id
        ]
        return sorted(
            sessions,
            key=lambda session: session.updated_at,
            reverse=True,
        )[:limit]

    async def get_messages(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> list[MessageRecord] | None:
        existing = self._sessions.get(session_id)
        if not existing or existing[0] != owner_id:
            return None
        return list(self._messages[session_id])

    async def get_session(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> SessionRecord | None:
        existing = self._sessions.get(session_id)
        if not existing or existing[0] != owner_id:
            return None
        return existing[1]

    async def delete_session(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> bool:
        existing = self._sessions.get(session_id)
        if not existing or existing[0] != owner_id:
            return False
        del self._sessions[session_id]
        del self._messages[session_id]
        return True
