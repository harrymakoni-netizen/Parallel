"""Request dependencies: database session, authenticated user, pagination."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from parallel_core.db import SessionLocal
from parallel_core.models import User
from parallel_core.security import TokenError, decode_token
from parallel_core.settings import get_settings

from .errors import ApiError


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Function scope: the transaction commits (or fails) before the response is sent. With the default request
# scope the commit ran after the client already had its 201, so a fast follow-up request could miss the
# write, and a failing commit would follow a success response. Event streams open their own sessions.
DB = Annotated[Session, Depends(get_db, scope="function")]


def current_user(db: DB, authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "Sign in required")
    try:
        claims = decode_token(authorization.split(" ", 1)[1])
    except TokenError as exc:
        raise ApiError(401, "invalid_token", str(exc)) from exc
    if get_settings().auth_mode == "oidc":
        user = db.scalar(select(User).where(User.oidc_subject == claims["sub"]))
    else:
        user = db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise ApiError(401, "unknown_user", "User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


class Page:
    def __init__(self, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        self.limit, self.offset = limit, offset


Paging = Annotated[Page, Depends()]
