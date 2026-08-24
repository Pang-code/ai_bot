from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from ai_agents.config import Settings
from ai_agents.errors import AuthenticationError


_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _password_hash.verify(password, encoded)
    except Exception:
        return False


@dataclass(frozen=True)
class TokenUser:
    user_id: UUID
    email: str


def create_access_token(user_id: UUID, email: str, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_minutes),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(
        payload,
        settings.require_jwt_secret(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str, settings: Settings) -> TokenUser:
    try:
        payload = jwt.decode(
            token,
            settings.require_jwt_secret(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
        return TokenUser(
            user_id=UUID(payload["sub"]),
            email=str(payload["email"]),
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise AuthenticationError() from None
