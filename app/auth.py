"""Passwords (argon2) and JWT access tokens.

JWT in one paragraph: after login the server signs a small JSON blob
({"sub": user_id, "exp": expiry}) with a secret key and hands it to the client.
The client sends it back on every request (Authorization: Bearer <token>). The
server only has to verify the signature - no session lookup in the database -
which is why JWTs scale well. Trade-off: a token can't be revoked before it
expires, so we keep the lifetime short (60 min by default).
"""

import asyncio
import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)
_ALGO = "HS256"


# argon2 is deliberately slow (~50ms of CPU) so password guessing is expensive.
# Run it in a thread so it doesn't block the event loop for other requests.
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str, password: str) -> bool:
    def _check() -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False
    return await asyncio.to_thread(_check)


def create_token(user_id: int) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "iat": now, "exp": now + settings.jwt_minutes * 60}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def _decode(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})


async def optional_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> int | None:
    """No token -> anonymous (None). A token that is present but bad -> 401."""
    return _decode(creds.credentials) if creds else None


async def require_user(user_id: int | None = Depends(optional_user)) -> int:
    if user_id is None:
        raise HTTPException(401, "Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    return user_id
