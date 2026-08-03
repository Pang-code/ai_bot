from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from ai_agents.api.app import create_app


CLIENT_ID = "d84b34b5-2cf8-4e15-b3c4-3e8a027878ef"
CLIENT_HEADERS = {"X-Client-ID": CLIENT_ID}


class FakeAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.configs: list[dict[str, Any]] = []

    async def ainvoke(
        self,
        input_data: dict[str, Any],
        *,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.configs.append(config)
        if self.fail:
            raise RuntimeError("upstream failed")
        message = input_data["messages"][-1]["content"]
        return {"messages": [AIMessage(content=f"收到：{message}")]}


@pytest.mark.asyncio
async def test_chat_creates_session_and_returns_request_id() -> None:
    agent = FakeAgent()
    app = create_app(agent_override=agent)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/chat",
                json={"message": "你好"},
                headers={
                    **CLIENT_HEADERS,
                    "X-Request-ID": "request-123",
                },
            )

    assert response.status_code == 200
    body = response.json()
    UUID(body["session_id"])
    assert body["answer"] == "收到：你好"
    assert body["request_id"] == "request-123"
    assert response.headers["X-Request-ID"] == "request-123"


@pytest.mark.asyncio
async def test_chat_reuses_supplied_session() -> None:
    agent = FakeAgent()
    session_id = "94c6510d-8c07-44db-ab87-7a9623cb5d3c"
    app = create_app(agent_override=agent)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/chat",
                json={"message": "继续", "session_id": session_id},
                headers=CLIENT_HEADERS,
            )

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    assert agent.configs[0]["configurable"]["thread_id"] == session_id


@pytest.mark.asyncio
async def test_blank_message_uses_unified_error_format() -> None:
    app = create_app(agent_override=FakeAgent())

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/chat",
                json={"message": "   "},
                headers=CLIENT_HEADERS,
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"]


@pytest.mark.asyncio
async def test_agent_failure_does_not_expose_internal_error() -> None:
    app = create_app(agent_override=FakeAgent(fail=True))

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/chat",
                json={"message": "你好"},
                headers=CLIENT_HEADERS,
            )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "agent_execution_failed"
    assert "upstream failed" not in response.text


@pytest.mark.asyncio
async def test_cors_allows_vue_development_origin() -> None:
    app = create_app(agent_override=FakeAgent())

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.options(
                "/v1/chat",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                },
            )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "http://localhost:5173"
    )


@pytest.mark.asyncio
async def test_session_history_can_be_listed_reopened_and_deleted() -> None:
    app = create_app(agent_override=FakeAgent())

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            chat_response = await client.post(
                "/v1/chat",
                json={"message": "帮我搜索 LangGraph 的文档"},
                headers=CLIENT_HEADERS,
            )
            session_id = chat_response.json()["session_id"]

            list_response = await client.get(
                "/v1/sessions",
                headers=CLIENT_HEADERS,
            )
            detail_response = await client.get(
                f"/v1/sessions/{session_id}/messages",
                headers=CLIENT_HEADERS,
            )
            delete_response = await client.delete(
                f"/v1/sessions/{session_id}",
                headers=CLIENT_HEADERS,
            )
            missing_response = await client.get(
                f"/v1/sessions/{session_id}/messages",
                headers=CLIENT_HEADERS,
            )

    assert chat_response.status_code == 200
    assert list_response.status_code == 200
    assert list_response.json()["sessions"][0]["title"] == (
        "帮我搜索 LangGraph 的文档"
    )
    assert detail_response.status_code == 200
    assert [item["role"] for item in detail_response.json()["messages"]] == [
        "user",
        "assistant",
    ]
    assert delete_response.status_code == 204
    assert missing_response.status_code == 404
