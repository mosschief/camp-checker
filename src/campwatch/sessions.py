"""
Authenticated-session material, behind an interface so the source can be
swapped later (e.g. programmatic login that self-refreshes) without touching
the hold path.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Dict, Optional

from pydantic import BaseModel

from campwatch.config import session_env_keys


class SessionMaterial(BaseModel):
    name: str
    cookie: str
    csrf: str


class SessionError(Exception):
    """Session material is missing or unusable."""


class SessionProvider(ABC):
    @abstractmethod
    def get_session(self, name: str) -> SessionMaterial:
        """Return live session material for the named session, or raise SessionError."""


class EnvSessionProvider(SessionProvider):
    """
    Reads SESSION_<NAME>_COOKIE / SESSION_<NAME>_CSRF from the environment
    (populated from .env). The simplest thing that works; replace with a
    self-refreshing provider behind the same interface when/if login
    automation lands.
    """

    def __init__(self, environ: Optional[Dict[str, str]] = None):
        self._environ = environ if environ is not None else os.environ

    def get_session(self, name: str) -> SessionMaterial:
        keys = session_env_keys(name)
        cookie = self._environ.get(keys["cookie"], "").strip()
        csrf = self._environ.get(keys["csrf"], "").strip()
        if not cookie or not csrf:
            raise SessionError(
                f"session {name!r} is not configured: set {keys['cookie']} and "
                f"{keys['csrf']} in .env (see README: session refresh)"
            )
        return SessionMaterial(name=name, cookie=cookie, csrf=csrf)
