from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cyberlab.auth import Principal, current_principal
from cyberlab.db import User
from cyberlab.registry import Registry


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_db(request: Request) -> Iterator[Session]:
    yield from request.app.state.db.session()


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def current_user(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> User:
    """Resolve (or lazily create) the local user record for the authenticated subject."""
    user = db.scalar(select(User).where(User.subject == principal.subject))
    if user is None:
        user = User(subject=principal.subject, email=principal.email)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # A parallel request (e.g. the dashboard loading several panels at once) created
            # this user first; use theirs.
            db.rollback()
            user = db.scalar(select(User).where(User.subject == principal.subject))
    return user
