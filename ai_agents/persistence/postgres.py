from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from ai_agents.config import Settings
from ai_agents.governance import AuditEvent, Role, TenantRecord, UserRecord
from ai_agents.auth import hash_password
from ai_agents.session_store import MessageRecord, SessionRecord


class PostgresSessionRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_id UUID PRIMARY KEY,
                    owner_id UUID NOT NULL,
                    title TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await connection.execute(
                "ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS tenant_id UUID"
            )
            await connection.execute(
                "ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS created_by UUID"
            )
            await connection.execute(
                """
                UPDATE agent_sessions SET created_by = owner_id
                WHERE created_by IS NULL
                """
            )
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS agent_sessions_tenant_creator_updated_idx
                ON agent_sessions (tenant_id, created_by, updated_at DESC)
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_messages (
                    sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    message_id UUID NOT NULL UNIQUE,
                    session_id UUID NOT NULL REFERENCES agent_sessions(session_id)
                        ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS agent_messages_session_sequence_idx
                ON agent_messages (session_id, sequence)
                """
            )

    async def ensure_session(
        self,
        tenant_id: UUID,
        created_by: UUID,
        session_id: UUID,
        title: str,
    ) -> bool:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_sessions (
                    session_id, owner_id, tenant_id, created_by, title
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id, created_by, tenant_id, created_by, title),
            )
            cursor = await connection.execute(
                """
                SELECT tenant_id, created_by
                FROM agent_sessions
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
            return bool(
                row
                and row["tenant_id"] == tenant_id
                and row["created_by"] == created_by
            )

    async def append_exchange(
        self,
        session_id: UUID,
        user_message: str,
        assistant_message: str,
    ) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO agent_messages (
                        message_id, session_id, role, content
                    )
                    VALUES (%s, %s, 'user', %s)
                    """,
                    (uuid4(), session_id, user_message),
                )
                await connection.execute(
                    """
                    INSERT INTO agent_messages (
                        message_id, session_id, role, content
                    )
                    VALUES (%s, %s, 'assistant', %s)
                    """,
                    (uuid4(), session_id, assistant_message),
                )
                await connection.execute(
                    """
                    UPDATE agent_sessions
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )

    async def list_sessions(
        self,
        tenant_id: UUID,
        created_by: UUID,
        limit: int = 100,
    ) -> list[SessionRecord]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, title, created_at, updated_at
                FROM agent_sessions
                WHERE tenant_id = %s AND created_by = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (tenant_id, created_by, limit),
            )
            rows = await cursor.fetchall()
        return [
            SessionRecord(
                session_id=row["session_id"],
                tenant_id=tenant_id,
                created_by=created_by,
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def get_messages(
        self,
        tenant_id: UUID,
        created_by: UUID,
        session_id: UUID,
    ) -> list[MessageRecord] | None:
        async with self._pool.connection() as connection:
            owner_cursor = await connection.execute(
                """
                SELECT 1
                FROM agent_sessions
                WHERE session_id = %s
                  AND tenant_id = %s AND created_by = %s
                """,
                (session_id, tenant_id, created_by),
            )
            if await owner_cursor.fetchone() is None:
                return None

            cursor = await connection.execute(
                """
                SELECT message_id, session_id, role, content, created_at
                FROM agent_messages
                WHERE session_id = %s
                ORDER BY sequence
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
        return [
            MessageRecord(
                message_id=row["message_id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def get_session(
        self,
        tenant_id: UUID,
        created_by: UUID,
        session_id: UUID,
    ) -> SessionRecord | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, title, created_at, updated_at
                FROM agent_sessions
                WHERE session_id = %s
                  AND tenant_id = %s AND created_by = %s
                """,
                (session_id, tenant_id, created_by),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            tenant_id=tenant_id,
            created_by=created_by,
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def delete_session(
        self,
        tenant_id: UUID,
        created_by: UUID,
        session_id: UUID,
    ) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM agent_sessions
                WHERE session_id = %s
                  AND tenant_id = %s AND created_by = %s
                RETURNING session_id
                """,
                (session_id, tenant_id, created_by),
            )
            return await cursor.fetchone() is not None


class PostgresGovernanceRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS users (
                user_id UUID PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS tenants (
                tenant_id UUID PRIMARY KEY, name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS tenant_memberships (
                tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
                user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('owner','admin','member','auditor')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS audit_events (
                event_id UUID PRIMARY KEY, actor_user_id UUID,
                tenant_id UUID, action TEXT NOT NULL, resource_type TEXT NOT NULL,
                resource_id TEXT, status TEXT NOT NULL, details JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                request_id TEXT NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS audit_events_tenant_created_idx
               ON audit_events (tenant_id, created_at DESC)""",
            """CREATE OR REPLACE FUNCTION prevent_audit_event_mutation()
               RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                   RAISE EXCEPTION 'audit_events is append-only';
               END; $$""",
            """DO $$ BEGIN
               IF NOT EXISTS (
                   SELECT 1 FROM pg_trigger
                   WHERE tgname = 'audit_events_append_only'
               ) THEN
                   CREATE TRIGGER audit_events_append_only
                   BEFORE UPDATE OR DELETE ON audit_events
                   FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
               END IF;
               END $$""",
        ]
        async with self._pool.connection() as connection:
            for statement in statements:
                await connection.execute(statement)

    async def register(
        self, email: str, password: str, tenant_name: str
    ) -> tuple[UserRecord, TenantRecord]:
        user_id, tenant_id = uuid4(), uuid4()
        normalized = email.strip().lower()
        async with self._pool.connection() as connection:
            async with connection.transaction():
                try:
                    await connection.execute(
                        """INSERT INTO users (user_id,email,password_hash)
                           VALUES (%s,%s,%s)""",
                        (user_id, normalized, hash_password(password)),
                    )
                except Exception as error:
                    if getattr(error, "sqlstate", None) == "23505":
                        raise ValueError("email_exists") from None
                    raise
                await connection.execute(
                    "INSERT INTO tenants (tenant_id,name) VALUES (%s,%s)",
                    (tenant_id, tenant_name),
                )
                await connection.execute(
                    """INSERT INTO tenant_memberships (tenant_id,user_id,role)
                       VALUES (%s,%s,'owner')""",
                    (tenant_id, user_id),
                )
        user = await self.get_user(user_id)
        assert user is not None
        return user, TenantRecord(
            tenant_id, tenant_name, "owner", user.created_at
        )

    @staticmethod
    def _user(row: dict[str, Any] | None) -> UserRecord | None:
        if row is None:
            return None
        return UserRecord(
            row["user_id"], row["email"], row["password_hash"], row["created_at"]
        )

    async def get_user_by_email(self, email: str) -> UserRecord | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM users WHERE email=%s", (email.strip().lower(),)
            )
            return self._user(await cursor.fetchone())

    async def get_user(self, user_id: UUID) -> UserRecord | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM users WHERE user_id=%s", (user_id,)
            )
            return self._user(await cursor.fetchone())

    async def list_tenants(self, user_id: UUID) -> list[TenantRecord]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT t.tenant_id,t.name,m.role,t.created_at
                   FROM tenants t JOIN tenant_memberships m USING (tenant_id)
                   WHERE m.user_id=%s ORDER BY t.created_at""",
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [
            TenantRecord(row["tenant_id"], row["name"], row["role"], row["created_at"])
            for row in rows
        ]

    async def get_role(self, user_id: UUID, tenant_id: UUID) -> Role | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT role FROM tenant_memberships
                   WHERE tenant_id=%s AND user_id=%s""",
                (tenant_id, user_id),
            )
            row = await cursor.fetchone()
        return row["role"] if row else None

    async def add_member(
        self, tenant_id: UUID, email: str, role: Role
    ) -> UserRecord | None:
        user = await self.get_user_by_email(email)
        if user is None:
            return None
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO tenant_memberships (tenant_id,user_id,role)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (tenant_id,user_id) DO UPDATE SET role=EXCLUDED.role""",
                (tenant_id, user.user_id, role),
            )
        return user

    async def audit(self, **kwargs: Any) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO audit_events (
                    event_id,actor_user_id,tenant_id,action,resource_type,
                    resource_id,status,details,request_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(), kwargs["actor_user_id"], kwargs["tenant_id"],
                    kwargs["action"], kwargs["resource_type"],
                    kwargs["resource_id"], kwargs["status"],
                    Jsonb(kwargs["details"]), kwargs["request_id"],
                ),
            )

    async def list_audit_events(
        self, tenant_id: UUID, limit: int = 100
    ) -> list[AuditEvent]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT * FROM audit_events WHERE tenant_id=%s
                   ORDER BY created_at DESC LIMIT %s""",
                (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            AuditEvent(
                row["event_id"], row["actor_user_id"], row["tenant_id"],
                row["action"], row["resource_type"], row["resource_id"],
                row["status"], row["details"], row["created_at"], row["request_id"],
            )
            for row in rows
        ]


@dataclass(frozen=True)
class PostgresPersistence:
    checkpointer: AsyncPostgresSaver
    sessions: PostgresSessionRepository
    governance: PostgresGovernanceRepository


def _create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=settings.require_database_url(),
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
        timeout=settings.postgres_pool_timeout_seconds,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        open=False,
    )


@asynccontextmanager
async def create_postgres_persistence(
    settings: Settings,
) -> AsyncIterator[PostgresPersistence]:
    pool = _create_pool(settings)
    async with pool:
        await pool.wait(timeout=settings.postgres_pool_timeout_seconds)
        checkpointer = AsyncPostgresSaver(pool)
        sessions = PostgresSessionRepository(pool)
        governance = PostgresGovernanceRepository(pool)
        await checkpointer.setup()
        await governance.setup()
        await sessions.setup()
        yield PostgresPersistence(
            checkpointer=checkpointer,
            sessions=sessions,
            governance=governance,
        )


@asynccontextmanager
async def create_postgres_checkpointer(
    settings: Settings,
) -> AsyncIterator[AsyncPostgresSaver]:
    async with create_postgres_persistence(settings) as persistence:
        yield persistence.checkpointer
