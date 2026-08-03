from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from ai_agents.agent import build_agent
from ai_agents.api.exception_handlers import (
    handle_service_error,
    handle_unexpected_error,
    handle_validation_error,
)
from ai_agents.api.schemas import (
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthResponse,
    MessageResponse,
    SessionListResponse,
    SessionMessagesResponse,
    SessionSummary,
)
from ai_agents.config import Settings, get_settings
from ai_agents.errors import ServiceError, ServiceNotReadyError
from ai_agents.persistence.postgres import create_postgres_persistence
from ai_agents.service import AgentService
from ai_agents.session_store import InMemorySessionRepository


def create_app(
    *,
    agent_override: Any | None = None,
    settings_override: Settings | None = None,
) -> FastAPI:
    settings = settings_override or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if agent_override is not None:
            app.state.agent_service = AgentService(
                agent=agent_override,
                sessions=InMemorySessionRepository(),
                checkpointer=InMemorySaver(),
            )
            yield
            return

        async with create_postgres_persistence(settings) as persistence:
            agent = build_agent(
                settings,
                checkpointer=persistence.checkpointer,
            )
            app.state.agent_service = AgentService(
                agent=agent,
                sessions=persistence.sessions,
                checkpointer=persistence.checkpointer,
            )
            yield

    app = FastAPI(
        title="AI Agents API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ServiceError, handle_service_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        supplied_request_id = request.headers.get("X-Request-ID", "").strip()
        request_id = (
            supplied_request_id[:128] if supplied_request_id else str(uuid4())
        )
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health/live", response_model=HealthResponse)
    async def liveness() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    async def readiness(request: Request) -> HealthResponse:
        if not hasattr(request.app.state, "agent_service"):
            raise ServiceNotReadyError()
        return HealthResponse(status="ready")

    @app.post(
        "/v1/chat",
        response_model=ChatResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def chat_with_owner(
        payload: ChatRequest,
        request: Request,
        client_id: Annotated[UUID, Header(alias="X-Client-ID")],
    ) -> ChatResponse:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()

        session_id = payload.session_id or uuid4()
        answer = await service.chat(
            message=payload.message,
            session_id=session_id,
            owner_id=client_id,
        )
        return ChatResponse(
            session_id=session_id,
            request_id=request.state.request_id,
            answer=answer,
        )

    @app.get(
        "/v1/sessions",
        response_model=SessionListResponse,
    )
    async def list_sessions(
        request: Request,
        client_id: Annotated[UUID, Header(alias="X-Client-ID")],
    ) -> SessionListResponse:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()
        sessions = await service.list_sessions(client_id)
        return SessionListResponse(
            sessions=[
                SessionSummary(
                    session_id=session.session_id,
                    title=session.title,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
                for session in sessions
            ]
        )

    @app.get(
        "/v1/sessions/{session_id}/messages",
        response_model=SessionMessagesResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def get_session_messages(
        session_id: UUID,
        request: Request,
        client_id: Annotated[UUID, Header(alias="X-Client-ID")],
    ) -> SessionMessagesResponse:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()
        session, messages = await service.get_messages(client_id, session_id)
        return SessionMessagesResponse(
            session=SessionSummary(
                session_id=session.session_id,
                title=session.title,
                created_at=session.created_at,
                updated_at=session.updated_at,
            ),
            messages=[
                MessageResponse(
                    message_id=message.message_id,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in messages
            ],
        )

    @app.delete(
        "/v1/sessions/{session_id}",
        status_code=204,
        responses={404: {"model": ErrorResponse}},
    )
    async def delete_session(
        session_id: UUID,
        request: Request,
        client_id: Annotated[UUID, Header(alias="X-Client-ID")],
    ) -> Response:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()
        await service.delete_session(client_id, session_id)
        return Response(status_code=204)

    return app


app = create_app()
