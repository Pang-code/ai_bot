import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ai_agents.errors import AgentExecutionError, SessionNotFoundError
from ai_agents.session_store import (
    MessageRecord,
    SessionRecord,
    SessionRepository,
    make_session_title,
)
from langgraph.types import Command


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingApproval:
    tool_name: str
    arguments: dict[str, Any]
    description: str


@dataclass(frozen=True)
class ChatResult:
    answer: str | None = None
    pending_approval: PendingApproval | None = None


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return str(content)


class AgentService:
    def __init__(
        self,
        agent: Any,
        sessions: SessionRepository,
        checkpointer: Any,
    ) -> None:
        self._agent = agent
        self._sessions = sessions
        self._checkpointer = checkpointer

    async def chat(
        self,
        message: str,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
    ) -> ChatResult:
        can_access = await self._sessions.ensure_session(
            tenant_id=tenant_id,
            created_by=user_id,
            session_id=session_id,
            title=make_session_title(message),
        )
        if not can_access:
            raise SessionNotFoundError()

        config = {"configurable": {"thread_id": str(session_id)}}
        try:
            result = await self._agent.ainvoke(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
            )
            pending = self._pending_approval(result)
            if pending is not None:
                return ChatResult(pending_approval=pending)
            answer = _message_text(result["messages"][-1].content)
            await self._sessions.append_exchange(
                session_id=session_id,
                user_message=message,
                assistant_message=answer,
            )
            return ChatResult(answer=answer)
        except Exception:
            logger.exception(
                "Agent execution failed",
                extra={"session_id": str(session_id)},
            )
            raise AgentExecutionError() from None

    async def resume(
        self,
        *,
        session_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        decision: str,
    ) -> ChatResult:
        session = await self._sessions.get_session(
            tenant_id, user_id, session_id
        )
        if session is None:
            raise SessionNotFoundError()
        config = {"configurable": {"thread_id": str(session_id)}}
        payload: dict[str, Any] = {"type": decision}
        if decision == "reject":
            payload["message"] = "用户拒绝了 IP 定位请求。"
        try:
            result = await self._agent.ainvoke(
                Command(resume={"decisions": [payload]}),
                config=config,
            )
            pending = self._pending_approval(result)
            if pending is not None:
                return ChatResult(pending_approval=pending)
            answer = _message_text(result["messages"][-1].content)
            await self._sessions.append_exchange(
                session_id=session_id,
                user_message="（审批后恢复会话）",
                assistant_message=answer,
            )
            return ChatResult(answer=answer)
        except Exception:
            logger.exception("Agent resume failed", extra={"session_id": str(session_id)})
            raise AgentExecutionError() from None

    @staticmethod
    def _pending_approval(result: dict[str, Any]) -> PendingApproval | None:
        interrupts = result.get("__interrupt__") or ()
        if not interrupts:
            return None
        value = getattr(interrupts[0], "value", interrupts[0])
        actions = value.get("action_requests", []) if isinstance(value, dict) else []
        if not actions:
            return None
        action = actions[0]
        return PendingApproval(
            tool_name=str(action.get("name", "")),
            arguments=dict(action.get("args", {})),
            description=str(action.get("description", "")),
        )

    async def list_sessions(
        self, tenant_id: UUID, user_id: UUID
    ) -> list[SessionRecord]:
        return await self._sessions.list_sessions(tenant_id, user_id)

    async def get_messages(
        self,
        tenant_id: UUID,
        user_id: UUID,
        session_id: UUID,
    ) -> tuple[SessionRecord, list[MessageRecord]]:
        session = await self._sessions.get_session(tenant_id, user_id, session_id)
        messages = await self._sessions.get_messages(tenant_id, user_id, session_id)
        if session is None or messages is None:
            raise SessionNotFoundError()
        return session, messages

    async def delete_session(
        self,
        tenant_id: UUID,
        user_id: UUID,
        session_id: UUID,
    ) -> None:
        messages = await self._sessions.get_messages(
            tenant_id, user_id, session_id
        )
        if messages is None:
            raise SessionNotFoundError()

        await self._checkpointer.adelete_thread(str(session_id))
        deleted = await self._sessions.delete_session(
            tenant_id, user_id, session_id
        )
        if not deleted:
            raise SessionNotFoundError()
