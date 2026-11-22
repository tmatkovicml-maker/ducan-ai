"""Single-user authentication, session handling and CSRF protection.

One shared password (argon2 hash in ``ADMIN_PASSWORD_HASH``). Sessions live in a
signed, HttpOnly cookie. Every state-changing request must carry the session's
CSRF token (form field ``csrf_token`` or header ``X-CSRF-Token``).
"""

from __future__ import annotations

import secrets
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, status

_hasher = PasswordHasher()
MIN_PASSWORD_LENGTH = 14
SESSION_MAX_AGE = 12 * 3600
ACTOR = "owner"


class LoginRequired(Exception):
    """Raised by :func:`require_login`; handled by a redirect to the login page."""


def hash_password(password: str) -> str:
    """Return an argon2 hash; rejects short passwords."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Lozinka mora imati najmanje {MIN_PASSWORD_LENGTH} znakova.")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time password check."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def csrf_token(request: Request) -> str:
    """Return the session's CSRF token, creating one if needed."""
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def require_csrf(request: Request) -> None:
    """Dependency: reject unsafe requests without a valid CSRF token."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    expected = request.session.get("csrf")
    sent = request.headers.get("x-csrf-token")
    if not sent:
        form = await request.form()
        value = form.get("csrf_token")
        sent = value if isinstance(value, str) else None
    if not expected or not sent or not secrets.compare_digest(expected, sent):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Neispravan CSRF token.")


def require_login(request: Request) -> str:
    """Dependency: return the actor name or raise :class:`LoginRequired`."""
    if not request.session.get("auth"):
        raise LoginRequired
    if time.time() - float(request.session.get("login_at", 0)) > SESSION_MAX_AGE:
        request.session.clear()
        raise LoginRequired
    return ACTOR


def log_in(request: Request) -> None:
    """Start a fresh authenticated session (new CSRF token)."""
    request.session.clear()
    request.session["auth"] = True
    request.session["login_at"] = time.time()
    csrf_token(request)


class LoginThrottle:
    """In-memory limit of failed logins per client address."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 900) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str) -> deque[float]:
        q = self._failures.setdefault(key, deque())
        cutoff = time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def fail(self, key: str) -> None:
        self._recent(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
