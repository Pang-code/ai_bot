from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from ai_agents.config import Settings
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
                """
                CREATE INDEX IF NOT EXISTS agent_sessions_owner_updated_idx
                ON agent_sessions (owner_id, updated_at DESC)
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
        owner_id: UUID,
        session_id: UUID,
        title: str,
    ) -> bool:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_sessions (session_id, owner_id, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id, owner_id, title),
            )
            cursor = await connection.execute(
                """
                SELECT owner_id
                FROM agent_sessions
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = await cursor.fetchone()
            return bool(row and row["owner_id"] == owner_id)

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
        owner_id: UUID,
        limit: int = 100,
    ) -> list[SessionRecord]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, title, created_at, updated_at
                FROM agent_sessions
                WHERE owner_id = %s
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (owner_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            SessionRecord(
                session_id=row["session_id"],
                title=row["title"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def get_messages(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> list[MessageRecord] | None:
        async with self._pool.connection() as connection:
            owner_cursor = await connection.execute(
                """
                SELECT 1
                FROM agent_sessions
                WHERE session_id = %s AND owner_id = %s
                """,
                (session_id, owner_id),
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
        owner_id: UUID,
        session_id: UUID,
    ) -> SessionRecord | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT session_id, title, created_at, updated_at
                FROM agent_sessions
                WHERE session_id = %s AND owner_id = %s
                """,
                (session_id, owner_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def delete_session(
        self,
        owner_id: UUID,
        session_id: UUID,
    ) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM agent_sessions
                WHERE session_id = %s AND owner_id = %s
                RETURNING session_id
                """,
                (session_id, owner_id),
            )
            return await cursor.fetchone() is not None


@dataclass(frozen=True)
class PostgresPersistence:
    checkpointer: AsyncPostgresSaver
    sessions: PostgresSessionRepository


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
        await checkpointer.setup()
        await sessions.setup()
        yield PostgresPersistence(
            checkpointer=checkpointer,
            sessions=sessions,
        )


@asynccontextmanager
async def create_postgres_checkpointer(
    settings: Settings,
) -> AsyncIterator[AsyncPostgresSaver]:
    async with create_postgres_persistence(settings) as persistence:
        yield persistence.checkpointer
