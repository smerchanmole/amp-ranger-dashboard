"""Autenticación local de una cuenta mediante cookie firmada.

La cookie contiene únicamente usuario, emisión, caducidad y un identificador
aleatorio. La firma HMAC impide alterarla; ``HttpOnly`` evita que React pueda
leerla. La contraseña se verifica con scrypt y nunca se recupera del hash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import HTTPException, Request, Response, status

from .config import Settings

_attempts: dict[str, deque[datetime]] = defaultdict(deque)
_attempt_lock = Lock()
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=5)
SAFE_USERNAME = re.compile(r"^[A-Za-z0-9._@+-]{1,200}$")


def cloudera_user(request: Request) -> str | None:
    """Lee la identidad autenticada que el proxy de CML entrega a la app."""
    for header in ("remote-user", "x-remote-user", "x-forwarded-user", "x-cdsw-user"):
        value = (request.headers.get(header) or "").strip()
        if value and SAFE_USERNAME.fullmatch(value):
            return value
    return None


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_password(password: str, encoded: str) -> bool:
    """Verifica un hash ``scrypt$n$r$p$salt$digest`` en tiempo constante."""
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=_decode(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest, _decode(expected))
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    """Genera un hash para administrar credenciales sin guardar texto claro."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    b64 = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return f"scrypt$16384$8$1${b64(salt)}${b64(digest)}"


def _sign(payload: str, secret: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def create_session(username: str, settings: Settings) -> str:
    """Crea un token firmado y autocontenido con expiración absoluta."""
    now = datetime.now(timezone.utc)
    body = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.app_session_hours)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    payload = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload, settings.app_session_secret)}"


def read_session(token: str | None, settings: Settings) -> str | None:
    """Valida firma, caducidad e identidad esperada."""
    if not token or not settings.app_session_secret:
        return None
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(payload, settings.app_session_secret)):
            return None
        body = json.loads(_decode(payload))
        if int(body["exp"]) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        username = str(body["sub"])
        return username if hmac.compare_digest(username, settings.app_auth_username) else None
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def set_session_cookie(response: Response, username: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.app_cookie_name,
        value=create_session(username, settings),
        max_age=settings.app_session_hours * 3600,
        httponly=True,
        secure=settings.app_cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.app_cookie_name, path="/", secure=settings.app_cookie_secure, httponly=True, samesite="strict"
    )


def require_user(request: Request) -> str:
    """Dependencia FastAPI que protege APIs y documentación."""
    from .main import settings  # import diferido para evitar un ciclo al arrancar

    transparent_user = cloudera_user(request)
    if transparent_user:
        return transparent_user
    username = read_session(request.cookies.get(settings.app_cookie_name), settings)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión no válida o caducada")
    return username


def login_allowed(client_id: str) -> bool:
    now = datetime.now(timezone.utc)
    with _attempt_lock:
        attempts = _attempts[client_id]
        while attempts and now - attempts[0] > FAILURE_WINDOW:
            attempts.popleft()
        return len(attempts) < MAX_FAILURES


def record_login_failure(client_id: str) -> None:
    with _attempt_lock:
        _attempts[client_id].append(datetime.now(timezone.utc))


def clear_login_failures(client_id: str) -> None:
    with _attempt_lock:
        _attempts.pop(client_id, None)
