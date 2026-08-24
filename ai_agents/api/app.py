from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
import secrets
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from ai_agents.agent import build_agent
from ai_agents.api.exception_handlers import (
    handle_service_error,
    handle_unexpected_error,
    handle_validation_error,
)
from ai_agents.api.schemas import (
    ChatRequest,
    ChatResponse,
    PendingApprovalResponse,
    ApprovalRequest,
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    UserResponse,
    TenantResponse,
    TenantListResponse,
    AddMemberRequest,
    AuditEventResponse,
    AuditListResponse,
    ErrorResponse,
    HealthResponse,
    MessageResponse,
    SessionListResponse,
    SessionMessagesResponse,
    SessionSummary,
)
from ai_agents.config import Settings, get_settings
from ai_agents.auth import TokenUser, create_access_token, decode_access_token, verify_password
from ai_agents.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ResourceNotFoundError,
    ServiceError,
    ServiceNotReadyError,
)
from ai_agents.governance import GovernanceRepository, InMemoryGovernanceRepository, Role
from ai_agents.persistence.postgres import create_postgres_persistence
from ai_agents.service import AgentService
from ai_agents.session_store import InMemorySessionRepository


def create_app(
    *,
    agent_override: Any | None = None,
    settings_override: Settings | None = None,
) -> FastAPI:
    settings = settings_override or get_settings()
    if agent_override is not None and settings.jwt_secret is None:
        settings.jwt_secret = SecretStr(secrets.token_urlsafe(48))
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.require_jwt_secret()
        if agent_override is not None:
            app.state.governance = InMemoryGovernanceRepository()
            app.state.agent_service = AgentService(
                agent=agent_override,
                sessions=InMemorySessionRepository(),
                checkpointer=InMemorySaver(),
            )
            app.state.settings = settings
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
            app.state.governance = persistence.governance
            app.state.settings = settings
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

    def governance(request: Request) -> GovernanceRepository:
        repository = getattr(request.app.state, "governance", None)
        if repository is None:
            raise ServiceNotReadyError()
        return repository

    async def current_user(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(bearer)
        ],
    ) -> TokenUser:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AuthenticationError()
        return decode_access_token(credentials.credentials, request.app.state.settings)

    async def tenant_context(
        request: Request,
        user: Annotated[TokenUser, Depends(current_user)],
        header_tenant_id: Annotated[UUID, Header(alias="X-Tenant-ID")],
    ) -> tuple[TokenUser, UUID, Role]:
        role = await governance(request).get_role(
            user.user_id, header_tenant_id
        )
        if role is None:
            raise AuthorizationError("不是该租户的成员。")
        return user, header_tenant_id, role

    @app.post("/v1/auth/register", response_model=TokenResponse, status_code=201)
    async def register(payload: RegisterRequest, request: Request) -> TokenResponse:
        repo = governance(request)
        try:
            user, tenant = await repo.register(
                payload.email, payload.password, payload.tenant_name.strip()
            )
        except ValueError:
            raise ConflictError("email_exists", "该邮箱已注册。") from None
        await repo.audit(
            actor_user_id=user.user_id,
            tenant_id=tenant.tenant_id,
            action="auth.register",
            resource_type="user",
            resource_id=str(user.user_id),
            status="success",
            details={"email": user.email},
            request_id=request.state.request_id,
        )
        return TokenResponse(
            access_token=create_access_token(user.user_id, user.email, settings)
        )

    @app.post("/v1/auth/login", response_model=TokenResponse)
    async def login(payload: LoginRequest, request: Request) -> TokenResponse:
        repo = governance(request)
        user = await repo.get_user_by_email(payload.email)
        if user is None or not verify_password(payload.password, user.password_hash):
            await repo.audit(
                actor_user_id=user.user_id if user else None,
                tenant_id=None,
                action="auth.login",
                resource_type="user",
                resource_id=str(user.user_id) if user else None,
                status="failure",
                details={"email": payload.email.strip().lower()},
                request_id=request.state.request_id,
            )
            raise AuthenticationError("邮箱或密码错误。")
        await repo.audit(
            actor_user_id=user.user_id,
            tenant_id=None,
            action="auth.login",
            resource_type="user",
            resource_id=str(user.user_id),
            status="success",
            details={},
            request_id=request.state.request_id,
        )
        return TokenResponse(
            access_token=create_access_token(user.user_id, user.email, settings)
        )

    @app.get("/v1/auth/me", response_model=UserResponse)
    async def me(user: Annotated[TokenUser, Depends(current_user)]) -> UserResponse:
        return UserResponse(user_id=user.user_id, email=user.email)

    @app.get("/v1/tenants", response_model=TenantListResponse)
    async def list_tenants(
        request: Request, user: Annotated[TokenUser, Depends(current_user)]
    ) -> TenantListResponse:
        tenants = await governance(request).list_tenants(user.user_id)
        return TenantListResponse(
            tenants=[
                TenantResponse(tenant_id=item.tenant_id, name=item.name, role=item.role)
                for item in tenants
            ]
        )

    @app.post("/v1/tenants/{tenant_id}/members", response_model=UserResponse)
    async def add_member(
        tenant_id: UUID,
        payload: AddMemberRequest,
        request: Request,
        context: Annotated[tuple[TokenUser, UUID, Role], Depends(tenant_context)],
    ) -> UserResponse:
        user, header_tenant_id, role = context
        if tenant_id != header_tenant_id or role not in {"owner", "admin"}:
            raise AuthorizationError()
        member = await governance(request).add_member(
            tenant_id, payload.email, payload.role
        )
        if member is None:
            raise ResourceNotFoundError("该邮箱尚未注册。")
        await governance(request).audit(
            actor_user_id=user.user_id,
            tenant_id=tenant_id,
            action="tenant.member.add",
            resource_type="user",
            resource_id=str(member.user_id),
            status="success",
            details={"email": member.email, "role": payload.role},
            request_id=request.state.request_id,
        )
        return UserResponse(user_id=member.user_id, email=member.email)

    @app.get("/v1/audit-events", response_model=AuditListResponse)
    async def audit_events(
        request: Request,
        context: Annotated[tuple[TokenUser, UUID, Role], Depends(tenant_context)],
    ) -> AuditListResponse:
        _, tenant_id, role = context
        if role not in {"owner", "admin", "auditor"}:
            raise AuthorizationError()
        events = await governance(request).list_audit_events(tenant_id)
        return AuditListResponse(
            events=[
                AuditEventResponse(
                    event_id=item.event_id,
                    actor_user_id=item.actor_user_id,
                    action=item.action,
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    status=item.status,
                    details=item.details,
                    created_at=item.created_at,
                    request_id=item.request_id,
                )
                for item in events
            ]
        )

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
        context: Annotated[tuple[TokenUser, UUID, Role], Depends(tenant_context)],
    ) -> ChatResponse:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()

        session_id = payload.session_id or uuid4()
        user, tenant_id, _ = context
        is_new = payload.session_id is None
        result = await service.chat(
            message=payload.message,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user.user_id,
        )
        if is_new:
            await governance(request).audit(
                actor_user_id=user.user_id,
                tenant_id=tenant_id,
                action="session.create",
                resource_type="session",
                resource_id=str(session_id),
                status="success",
                details={},
                request_id=request.state.request_id,
            )
        return ChatResponse(
            session_id=session_id,
            request_id=request.state.request_id,
            status="pending_approval" if result.pending_approval else "completed",
            answer=result.answer,
            pending_approval=(
                PendingApprovalResponse(
                    tool_name=result.pending_approval.tool_name,
                    arguments=result.pending_approval.arguments,
                    description=result.pending_approval.description,
                )
                if result.pending_approval
                else None
            ),
        )

    @app.post(
        "/v1/sessions/{session_id}/approval", response_model=ChatResponse
    )
    async def resume_approval(
        session_id: UUID,
        payload: ApprovalRequest,
        request: Request,
        context: Annotated[tuple[TokenUser, UUID, Role], Depends(tenant_context)],
    ) -> ChatResponse:
        user, tenant_id, _ = context
        service = request.app.state.agent_service
        result = await service.resume(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user.user_id,
            decision=payload.decision,
        )
        pending = result.pending_approval
        await governance(request).audit(
            actor_user_id=user.user_id,
            tenant_id=tenant_id,
            action=f"tool.approval.{payload.decision}",
            resource_type="session",
            resource_id=str(session_id),
            status="pending" if pending else "completed",
            details={
                "tool_name": pending.tool_name if pending else "get_ip_location",
                "arguments": {},
            },
            request_id=request.state.request_id,
        )
        return ChatResponse(
            session_id=session_id,
            request_id=request.state.request_id,
            status="pending_approval" if pending else "completed",
            answer=result.answer,
            pending_approval=(
                PendingApprovalResponse(
                    tool_name=pending.tool_name,
                    arguments=pending.arguments,
                    description=pending.description,
                )
                if pending else None
            ),
        )

    @app.get(
        "/v1/sessions",
        response_model=SessionListResponse,
    )
    async def list_sessions(
        request: Request,
        context: Annotated[tuple[TokenUser, UUID, Role], Depends(tenant_context)],
    ) -> SessionListResponse:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()
        user, tenant_id, _ = context
        sessions = await service.list_sessions(tenant_id, user.user_id)
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
        context: Annotated[tuple[TokenUser, UUID, Role], Depends(tenant_context)],
    ) -> SessionMessagesResponse:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()
        user, tenant_id, _ = context
        session, messages = await service.get_messages(
            tenant_id, user.user_id, session_id
        )
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
        context: Annotated[tuple[TokenUser, UUID, Role], Depends(tenant_context)],
    ) -> Response:
        service = getattr(request.app.state, "agent_service", None)
        if service is None:
            raise ServiceNotReadyError()
        user, tenant_id, _ = context
        await service.delete_session(tenant_id, user.user_id, session_id)
        await governance(request).audit(
            actor_user_id=user.user_id,
            tenant_id=tenant_id,
            action="session.delete",
            resource_type="session",
            resource_id=str(session_id),
            status="success",
            details={},
            request_id=request.state.request_id,
        )
        return Response(status_code=204)

    return app


app = create_app()
