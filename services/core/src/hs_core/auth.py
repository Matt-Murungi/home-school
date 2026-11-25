"""Verification of callers' Appwrite JWTs and role guards (labels are alphanumeric, ADR 0012)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import Depends, Request

from .errors import AppError
from .roles.model import ROLES, clean_labels

MAX_TOKEN_LEN = 4096  # Appwrite JWTs are well under this


class InvalidToken(Exception): ...


class AuthUnavailable(Exception): ...


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: list[str] = field(default_factory=list)


def roles_from_labels(labels: Any) -> list[str]:
    """Exact, case-sensitive match against known labels; anything else is ignored (A03: the one
    definition lives in `roles.model.clean_labels`)."""
    return sorted(clean_labels(labels))


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class FakeTokenVerifier:
    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, list[str], bool]] = {}

    def add(self, token: str, user_id: str, labels: list[str], expired: bool = False) -> None:
        self._tokens[token] = (user_id, labels, expired)

    def verify(self, token: str) -> Principal:
        entry = self._tokens.get(token)
        if entry is None or entry[2]:
            raise InvalidToken
        return Principal(entry[0], roles_from_labels(entry[1]))


def _field(obj: Any, name: str, alt: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(alt, obj.get(name))
    return getattr(obj, name, None)


class AppwriteJwtVerifier:
    """Calls Appwrite `GET /account` with the caller's JWT; Appwrite checks signature and expiry."""

    def __init__(self, endpoint: str, project_id: str, account_factory: Callable | None = None):
        self._endpoint, self._project = endpoint, project_id
        self._factory = account_factory or self._default_factory

    def _default_factory(self, jwt: str):
        from appwrite.client import Client
        from appwrite.services.account import Account

        client = Client().set_endpoint(self._endpoint).set_project(self._project).set_jwt(jwt)
        return client, Account(client)

    def verify(self, token: str) -> Principal:
        from appwrite.exception import AppwriteException

        if not token or len(token) > MAX_TOKEN_LEN:
            raise InvalidToken
        try:
            _, account = self._factory(token)
            user = account.get()
        except AppwriteException as e:
            if e.code in (400, 401, 403):
                raise InvalidToken from None
            raise AuthUnavailable from None
        except OSError:
            raise AuthUnavailable from None
        uid = _field(user, "id", "$id")
        if not uid or _field(user, "status", "status") is False:
            raise InvalidToken
        return Principal(uid, roles_from_labels(_field(user, "labels", "labels") or []))


def _unauth() -> AppError:
    return AppError(
        401, "unauthenticated", "Authentication required", {"WWW-Authenticate": "Bearer"}
    )


def current_principal(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauth()
    try:
        return request.app.state.verifier.verify(token.strip())
    except InvalidToken:
        raise _unauth() from None
    except AuthUnavailable:
        raise AppError(503, "auth_unavailable", "Authentication temporarily unavailable") from None
    except Exception:
        raise AppError(503, "auth_unavailable", "Authentication temporarily unavailable") from None


def require_roles(*allowed: str) -> Callable[..., Principal]:
    assert set(allowed) <= set(ROLES), "unknown role"

    def guard(p: Principal = Depends(current_principal)) -> Principal:  # noqa: B008
        if not set(p.roles) & set(allowed):
            raise AppError(403, "forbidden", "Insufficient role")
        return p

    return guard
