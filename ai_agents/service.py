import logging
from typing import Any
from uuid import UUID

from ai_agents.errors import AgentExecutionError, SessionNotFoundError
from ai_agents.session_store import (
    MessageRecord,
    SessionRecord,
    SessionRepository,
    make_session_title,
)


logger = logging.getLogger(__name__)


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
        owner_id: UUID,
    ) -> str:
        can_access = await self._sessions.ensure_session(
            owner_id=owner_id,
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
            answer = _message_text(result["messages"][-1].content)
            await self._sessions.append_exchange(
                session_id=session_id,
                user_message=message,
                assistant_message=answer,
            )
            return answer
        except Exception:
            logger.exception(
                "Agent execution failed",
                extra={"session_id": str(session_id)},
            )
            raise AgentExecutionError() from None

    async def list_sessions(self, owner_id: UUID) -> list[SessionRecord]:
        return await self._sessions.list_sessions(owner_id)

    async def get_messages(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> tuple[SessionRecord, list[MessageRecord]]:
        session = await self._sessions.get_session(owner_id, session_id)
        messages = await self._sessions.get_messages(owner_id, session_id)
        if session is None or messages is None:
            raise SessionNotFoundError()
        return session, messages

    async def delete_session(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> None:
        messages = await self._sessions.get_messages(owner_id, session_id)
        if messages is None:
            raise SessionNotFoundError()

        await self._checkpointer.adelete_thread(str(session_id))
        deleted = await self._sessions.delete_session(owner_id, session_id)
        if not deleted:
            raise SessionNotFoundError()
