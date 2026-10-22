"""Password hashing and session tokens.

Local development identity uses scrypt password hashes and HS256 tokens;
production deployments validate OIDC access tokens (RS256 via the provider's
JWKS) and map the subject to a Parallel user.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from .settings import Settings, get_settings

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("passwords must have at least 8 characters")
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, n, r, p, salt, dk = stored.split("$")
    calc = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32
    )
    return hmac.compare_digest(calc, base64.b64decode(dk))


class TokenError(ValueError):
    pass


def issue_token(user_id: str, settings: Settings | None = None, extra: dict[str, Any] | None = None) -> str:
    s = settings or get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_minutes),
        "iss": "parallel-local",
        **(extra or {}),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


_jwks_clients: dict[str, jwt.PyJWKClient] = {}


def decode_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    s = settings or get_settings()
    try:
        if s.auth_mode == "oidc":
            if not (s.oidc_jwks_url and s.oidc_issuer):
                raise TokenError("OIDC is not configured")
            client = _jwks_clients.setdefault(s.oidc_jwks_url, jwt.PyJWKClient(s.oidc_jwks_url))
            key = client.get_signing_key_from_jwt(token).key
            return jwt.decode(
                token, key, algorithms=["RS256", "ES256"], audience=s.oidc_audience, issuer=s.oidc_issuer
            )
        return jwt.decode(token, s.secret_key, algorithms=["HS256"], issuer="parallel-local")
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
