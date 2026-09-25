"""Composición FastAPI de la capa de gobierno y la interfaz web.

Las rutas `/api` son el contrato estable del producto. La aplicación React no
recibe credenciales Ranger; solo consume agregados y evidencias ya gobernadas.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hmac
from pathlib import Path
from threading import Lock
from time import monotonic, perf_counter
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analytics import dashboard
from .agent_runtime import AgentConfigError, AgentRuntime
from .auth import (
    clear_login_failures, clear_session_cookie, cloudera_user, login_allowed, read_session,
    record_login_failure, require_user, set_session_cookie, verify_password,
)
from .audit_log import JsonlAuditLog
from .chat import answer
from .config import get_settings
from .geolocation import GeoDatabase
from .llm import AIGatewayClient, GatewayError
from .ranger import RangerClient, RangerError
from .solr import SolrAuditClient, SolrError

settings = get_settings()
client = RangerClient(settings)
audit_client = SolrAuditClient(settings) if settings.audit_source.casefold() == "solr" else client
log = JsonlAuditLog(settings.audit_log_path)
geo = GeoDatabase(settings.geo_csv_path, settings.geo_db_path)
agent_runtime = AgentRuntime(settings)
gateway = AIGatewayClient(settings, agent_runtime)
app = FastAPI(title="Ranger Security Intelligence", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_methods=["GET", "POST"], allow_headers=["*"])
_cache: dict[str, tuple[float, tuple[list[dict], list[dict]]]] = {}
_cache_lock = Lock()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    period: Literal["24h", "7d", "30d", "3m", "6m"] = "7d"
    exclude_internal: bool = True
    sample_size: int = Field(default=5000, ge=100, le=100000)
    model: str | None = Field(default=None, max_length=100)
    provider: Literal["litellm", "cloudera"] | None = None


class AgentConfigRequest(BaseModel):
    provider: Literal["litellm", "cloudera"]
    endpoint: str = Field(min_length=8, max_length=1000)
    token: str | None = Field(default=None, max_length=8000)
    models: list[str] = Field(min_length=1, max_length=100)
    default_model: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


class RuntimeConfigRequest(BaseModel):
    """Campos operativos editables; los secretos vacíos conservan su valor."""
    ranger_url: str = Field(min_length=8, max_length=1000)
    ranger_auth_type: Literal["basic", "bearer", "none"] = "basic"
    ranger_user: str = Field(default="", max_length=200)
    ranger_password: str = Field(default="", max_length=4000)
    ranger_token: str = Field(default="", max_length=8000)
    ranger_verify_ssl: bool = False
    audit_source: Literal["ranger", "solr"] = "ranger"
    solr_server: str = Field(default="", max_length=500)
    solr_port: int = Field(default=8995, ge=1, le=65535)
    solr_collection: str = Field(default="ranger_audits", max_length=200)
    solr_url: str = Field(default="", max_length=1500)
    solr_auth_type: Literal["none", "basic"] = "none"
    solr_user: str = Field(default="", max_length=200)
    solr_password: str = Field(default="", max_length=4000)
    solr_verify_ssl: bool = False
    kerberos_enabled: bool = False
    kerberos_user: str = Field(default="", max_length=200)
    kerberos_realm: str = Field(default="", max_length=200)
    kerberos_kdc: str = Field(default="", max_length=500)
    kerberos_admin_server: str = Field(default="", max_length=500)
    kerberos_password: str = Field(default="", max_length=4000)
    kerberos_keytab: str = Field(default="", max_length=1000)
    kerberos_ccache: str = Field(default="data/krb5cc_ranger_solr", max_length=1000)
    kerberos_config_file: str = Field(default="data/krb5_ranger_solr.conf", max_length=1000)
    ai_gateway_api_url: str = Field(min_length=8, max_length=1000)
    ai_gateway_token: str = Field(default="", max_length=8000)
    ai_gateway_verify_ssl: bool = False
    cdp_token: str = Field(default="", max_length=8000)
    use_cml_jwt: bool = True
    ai_gateway_models: str = Field(min_length=1, max_length=1000)
    ai_gateway_default_model: str = Field(min_length=1, max_length=200)


def dates(period: str) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(days={"24h": 1, "7d": 7, "30d": 30, "3m": 90, "6m": 180}.get(period, 7)), end


def load(period: str, exclude_internal: bool = True, sample_size: int = 5000):
    """Carga una instantánea coherente de auditorías y políticas.

    La clave de caché incluye periodo, filtro y muestra para que dos vistas con
    distinto alcance nunca compartan accidentalmente el mismo universo de datos.
    """
    # Ranger can take several minutes to aggregate xaudit. A short shared cache
    # prevents the dashboard, map and chat from repeating the same heavy query.
    with _cache_lock:
        sample_size = max(100, min(sample_size, 100000))
        cache_key = f"{period}:{exclude_internal}:{sample_size}"
        cached = _cache.get(cache_key)
        if cached and monotonic() - cached[0] < 120:
            return cached[1]
        start, end = dates(period)
        audits = audit_client.access_audits(start, end, page_size=sample_size, exclude_users=exclude_internal)
        # Algunas versiones aceptan excludeUser pero no lo aplican correctamente
        # con listas. Este filtro local garantiza el comportamiento del selector.
        if exclude_internal:
            excluded = {user.casefold() for user in settings.excluded_users}
            audits = [item for item in audits if str(item.get("requestUser") or "").casefold() not in excluded]
        try:
            policies = client.policies()
        except RangerError:
            policies = []
        value = (audits, policies)
        _cache[cache_key] = (monotonic(), value)
        return value


def _probe_service(check) -> dict:
    started = perf_counter()
    try:
        detail = check()
        return {
            "ok": True,
            "latencyMs": round((perf_counter() - started) * 1000),
            "detail": detail,
        }
    except Exception as exc:
        return {
            "ok": False,
            "latencyMs": round((perf_counter() - started) * 1000),
            "error": str(exc)[:500],
        }


def connection_diagnostics() -> dict:
    """Valida API, Solr y modelo de forma independiente y concurrente."""
    probe_settings = settings.model_copy(
        update={
            "ranger_timeout_seconds": min(settings.ranger_timeout_seconds, 15),
            "solr_timeout_seconds": min(settings.solr_timeout_seconds, 15),
            "ai_gateway_timeout_seconds": min(settings.ai_gateway_timeout_seconds, 20),
        }
    )
    checks = {
        "api": lambda: RangerClient(probe_settings).health(),
        "solr": lambda: SolrAuditClient(probe_settings).health(),
        "model": lambda: AIGatewayClient(probe_settings).probe(),
    }
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="diagnostic") as executor:
        futures = {name: executor.submit(_probe_service, check) for name, check in checks.items()}
        results = {name: future.result() for name, future in futures.items()}
    return {"services": results, "checkedAt": datetime.now(timezone.utc).isoformat()}


@app.post("/api/auth/login")
def login(credentials: LoginRequest, request: Request, response: Response):
    """Intercambia credenciales por cookie; jamás registra la contraseña."""
    transparent_user = cloudera_user(request)
    if transparent_user:
        return {"authenticated": True, "username": f"Cloudera: {transparent_user}", "source": "cloudera"}
    if not settings.app_auth_password_hash:
        raise HTTPException(503, "No se detectó usuario de Cloudera y el acceso local no está configurado")
    client_id = request.client.host if request.client else "unknown"
    if not login_allowed(client_id):
        log.append({"type": "login_blocked", "client": client_id})
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados intentos. Espera cinco minutos.")
    valid_user = hmac.compare_digest(credentials.username, settings.app_auth_username)
    valid_password = verify_password(credentials.password, settings.app_auth_password_hash)
    if not (valid_user and valid_password):
        record_login_failure(client_id)
        log.append({"type": "login_failed", "username": credentials.username, "client": client_id})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos")
    clear_login_failures(client_id)
    set_session_cookie(response, settings.app_auth_username, settings)
    log.append({"type": "login_success", "username": settings.app_auth_username, "client": client_id})
    return {"authenticated": True, "username": f"Local: {settings.app_auth_username}", "source": "local"}


@app.get("/api/auth/session")
def session(request: Request):
    transparent_user = cloudera_user(request)
    if transparent_user:
        return {"authenticated": True, "username": f"Cloudera: {transparent_user}", "source": "cloudera"}
    username = read_session(request.cookies.get(settings.app_cookie_name), settings)
    if not username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión no válida o caducada")
    return {"authenticated": True, "username": f"Local: {username}", "source": "local"}


@app.post("/api/auth/logout")
def logout(response: Response, username: str = Depends(require_user)):
    clear_session_cookie(response, settings)
    log.append({"type": "logout", "username": username})
    return {"authenticated": False}


@app.get("/api/health")
def health(username: str = Depends(require_user)):
    try:
        return {**audit_client.health(), "geoDatabase": geo.available(), "services": settings.services}
    except (RangerError, SolrError) as exc:
        return {"connected": False, "source": settings.audit_source, "error": str(exc), "geoDatabase": geo.available(), "services": settings.services}


@app.get("/api/diagnostics")
def diagnostics(username: str = Depends(require_user)):
    return connection_diagnostics()


@app.get("/api/config")
def public_runtime_config(username: str = Depends(require_user)):
    """Expone valores editables y solo indica si hay secretos configurados."""
    return {
        "serverIp": settings.server_ip,
        "ranger": {
            "url": settings.ranger_url,
            "authType": settings.ranger_auth_type,
            "user": settings.ranger_user,
            "hasPassword": bool(settings.ranger_password),
            "hasToken": bool(settings.ranger_token),
            "verifySsl": settings.ranger_verify_ssl,
        },
        "audit": {
            "source": settings.audit_source,
            "server": settings.solr_server,
            "port": settings.solr_port,
            "collection": settings.solr_collection,
            "url": settings.solr_url,
            "selectUrl": settings.solr_select_url,
            "authType": settings.solr_auth_type,
            "user": settings.solr_user,
            "hasPassword": bool(settings.solr_password),
            "verifySsl": settings.solr_verify_ssl,
        },
        "kerberos": {
            "enabled": settings.kerberos_enabled,
            "user": settings.kerberos_user,
            "realm": settings.kerberos_realm,
            "kdc": settings.kerberos_kdc,
            "adminServer": settings.kerberos_admin_server,
            "hasPassword": bool(settings.kerberos_password),
            "keytab": str(settings.kerberos_keytab or ""),
            "ccache": str(settings.kerberos_ccache),
            "configFile": str(settings.kerberos_config_file),
        },
        "llm": {
            "apiUrl": settings.ai_gateway_api_url,
            "verifySsl": settings.ai_gateway_verify_ssl,
            "models": settings.gateway_models,
            "defaultModel": settings.ai_gateway_default_model,
            "hasToken": bool(settings.ai_gateway_token),
            "hasCdpToken": bool(settings.cdp_token),
            "useCmlJwt": settings.use_cml_jwt,
            "cmlJwtAvailable": settings.cml_jwt_path.is_file(),
            "tokenSource": settings.effective_ai_token[1],
        },
        "agent": agent_runtime.public(),
    }


@app.post("/api/agent/config")
def update_agent_config(payload: AgentConfigRequest, username: str = Depends(require_user)):
    """Aplica un perfil temporal sin devolver ni registrar el secreto."""
    try:
        config = agent_runtime.update(
            payload.provider, payload.endpoint, payload.token,
            payload.models, payload.default_model,
        )
        log.append({
            "type": "agent_config", "username": username,
            "provider": payload.provider, "endpoint": payload.endpoint,
            "models": payload.models, "defaultModel": payload.default_model,
            "tokenUpdated": bool(payload.token and payload.token.strip()),
        })
        return config
    except AgentConfigError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@app.post("/api/config")
def update_runtime_config(payload: RuntimeConfigRequest, username: str = Depends(require_user)):
    """Aplica configuración a este proceso CML sin escribir secretos en disco."""
    global client, audit_client, agent_runtime, gateway
    values = payload.model_dump()
    requested_models = [item.strip() for item in payload.ai_gateway_models.split(",") if item.strip()]
    if payload.ai_gateway_default_model not in requested_models:
        raise HTTPException(422, "El modelo predeterminado debe estar incluido en la lista de modelos")
    if payload.kerberos_enabled:
        required = {
            "usuario": payload.kerberos_user,
            "realm": payload.kerberos_realm,
            "KDC": payload.kerberos_kdc,
            "caché de credenciales": payload.kerberos_ccache,
            "fichero krb5.conf": payload.kerberos_config_file,
        }
        missing = [label for label, value in required.items() if not value.strip()]
        if missing:
            raise HTTPException(422, f"Kerberos requiere: {', '.join(missing)}")
        if not (payload.kerberos_password or payload.kerberos_keytab or settings.kerberos_password):
            raise HTTPException(422, "Kerberos requiere una contraseña o la ruta de un keytab")
    if payload.audit_source == "solr":
        if payload.solr_url and not payload.solr_url.startswith(("https://", "http://")):
            raise HTTPException(422, "La URL de Solr debe comenzar por https:// o http://")
        if not payload.solr_url.strip() and not payload.solr_server.strip():
            raise HTTPException(422, "Solr requiere una URL completa o un servidor")
        if not payload.kerberos_enabled and payload.solr_auth_type == "basic" and not payload.solr_user.strip():
            raise HTTPException(422, "Solr Basic requiere un usuario")
        if not payload.kerberos_enabled and payload.solr_auth_type == "basic" and not (payload.solr_password or settings.solr_password):
            raise HTTPException(422, "Solr Basic requiere una contraseña")
    for secret in ("ranger_password", "ranger_token", "solr_password", "kerberos_password", "ai_gateway_token", "cdp_token"):
        if not values[secret]:
            values.pop(secret)
    for path_field in ("kerberos_keytab", "kerberos_ccache", "kerberos_config_file"):
        values[path_field] = Path(values[path_field]) if values[path_field] else None
    for key, value in values.items():
        setattr(settings, key, value)
    client = RangerClient(settings)
    audit_client = SolrAuditClient(settings) if settings.audit_source == "solr" else client
    agent_runtime = AgentRuntime(settings)
    gateway = AIGatewayClient(settings, agent_runtime)
    with _cache_lock:
        _cache.clear()
    log.append({"type": "runtime_config_updated", "username": username, "auditSource": settings.audit_source})
    return public_runtime_config(username)


@app.get("/api/dashboard")
def get_dashboard(period: Literal["24h", "7d", "30d", "3m", "6m"] = "7d", exclude_internal: bool = True, sample_size: int = Query(5000, ge=100, le=100000), username: str = Depends(require_user)):
    try:
        audits, policies = load(period, exclude_internal, sample_size)
        return dashboard(audits, policies, settings.services)
    except (RangerError, SolrError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/map")
def get_map(period: Literal["24h", "7d", "30d", "3m", "6m"] = "7d", exclude_internal: bool = True, sample_size: int = Query(5000, ge=100, le=100000), username: str = Depends(require_user)):
    try:
        audits, _ = load(period, exclude_internal, sample_size)
        counts = Counter(str(item.get("clientIP")) for item in audits if item.get("clientIP"))
        return {"points": geo.locate(counts.most_common(200)), "databaseReady": geo.available()}
    except (RangerError, SolrError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/chat")
def chat(request: ChatRequest, username: str = Depends(require_user)):
    try:
        audits, policies = load(request.period, request.exclude_internal, request.sample_size)
        result = answer(request.question, audits, policies)
        result["auditSource"] = settings.audit_source
        profile = agent_runtime.profile(request.provider)
        provider = profile["id"]
        model = request.model or profile["defaultModel"]
        if model not in profile["models"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Modelo no permitido para el proveedor seleccionado")
        gateway_status = "ok"
        try:
            result["answer"] = gateway.explain(provider, model, request.question, result)
        except GatewayError as exc:
            # El cálculo gobernado sigue disponible aunque el LLM local esté cargando.
            gateway_status = "fallback"
            result["gatewayWarning"] = str(exc)
        result["model"] = model
        result["provider"] = provider
        log.append({"type": "chat", "username": username, "provider": provider, "model": model, "gatewayStatus": gateway_status, "question": request.question, "period": request.period, "excludeInternal": request.exclude_internal, "sampleSize": request.sample_size, "intent": result["intent"], "answer": result["answer"], "resultCount": len(result["data"])})
        return result
    except AgentConfigError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except (RangerError, SolrError) as exc:
        log.append({"type": "chat_error", "question": request.question, "error": str(exc)})
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/logs")
def logs(limit: int = Query(100, ge=1, le=500), username: str = Depends(require_user)):
    return {"items": log.recent(limit)}


@app.post("/api/admin/build-geo-index")
def build_geo_index(username: str = Depends(require_user)):
    try:
        return {"rows": geo.build(), "databaseReady": True}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/openapi.json", include_in_schema=False)
def protected_openapi(username: str = Depends(require_user)):
    return app.openapi()


@app.get("/docs", include_in_schema=False)
def protected_docs(username: str = Depends(require_user)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Ranger Security Intelligence API")


# En producción FastAPI sirve el bundle React. Se registra después de /api para
# no interceptar las rutas del backend. Durante desarrollo Vite usa su proxy.
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def single_page_app(full_path: str):
        requested = frontend_dist / full_path
        if full_path and requested.is_file() and frontend_dist in requested.resolve().parents:
            return FileResponse(requested)
        return FileResponse(frontend_dist / "index.html")
