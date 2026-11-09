import re

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from .. import db
from ..auth import create_token, hash_password, verify_password
from ..ratelimit import limit_auth

router = APIRouter(prefix="/api/auth", dependencies=[Depends(limit_auth)])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) > 254 or not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if not 8 <= len(v) <= 128:   # upper bound stops giant-password CPU abuse
            raise ValueError("Password must be 8-128 characters")
        return v


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: Credentials):
    pw_hash = await hash_password(body.password)
    try:
        user_id = await db.fetchval(
            "INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
            body.email, pw_hash,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "An account with that email already exists")
    return TokenOut(access_token=create_token(user_id))


@router.post("/login", response_model=TokenOut)
async def login(body: Credentials):
    row = await db.fetchrow("SELECT id, password_hash FROM users WHERE email = $1", body.email)
    # Same error (and same work) whether the email or the password was wrong, so
    # attackers can't use the response to discover which emails are registered.
    if row is None or not await verify_password(row["password_hash"], body.password):
        raise HTTPException(401, "Invalid email or password")
    return TokenOut(access_token=create_token(row["id"]))
