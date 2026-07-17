"""Composición FastAPI de la capa de gobierno y la interfaz web.

Las rutas `/api` son el contrato estable del producto. La aplicación React no
recibe credenciales Ranger; solo consume agregados y evidencias ya gobernadas.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analytics import dashboard
from .audit_log import JsonlAuditLog
from .chat import answer
from .config import get_settings
from .geolocation import GeoDatabase
from .ranger import RangerClient, RangerError

settings = get_settings()
client = RangerClient(settings)
log = JsonlAuditLog(settings.audit_log_path)
geo = GeoDatabase(settings.geo_csv_path, settings.geo_db_path)
app = FastAPI(title="Ranger Security Intelligence", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_methods=["GET", "POST"], allow_headers=["*"])
_cache: dict[str, tuple[float, tuple[list[dict], list[dict]]]] = {}
_cache_lock = Lock()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    period: Literal["24h", "7d", "30d", "3m", "6m"] = "7d"
    exclude_internal: bool = True
    sample_size: int = Field(default=5000, ge=100, le=100000)


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
        audits = client.access_audits(start, end, page_size=sample_size, exclude_users=exclude_internal)
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


@app.get("/api/health")
def health():
    try:
        return {**client.health(), "geoDatabase": geo.available(), "services": settings.services}
    except RangerError as exc:
        return {"connected": False, "error": str(exc), "geoDatabase": geo.available(), "services": settings.services}


@app.get("/api/dashboard")
def get_dashboard(period: Literal["24h", "7d", "30d", "3m", "6m"] = "7d", exclude_internal: bool = True, sample_size: int = Query(5000, ge=100, le=100000)):
    try:
        audits, policies = load(period, exclude_internal, sample_size)
        return dashboard(audits, policies, settings.services)
    except RangerError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/map")
def get_map(period: Literal["24h", "7d", "30d", "3m", "6m"] = "7d", exclude_internal: bool = True, sample_size: int = Query(5000, ge=100, le=100000)):
    try:
        audits, _ = load(period, exclude_internal, sample_size)
        counts = Counter(str(item.get("clientIP")) for item in audits if item.get("clientIP"))
        return {"points": geo.locate(counts.most_common(200)), "databaseReady": geo.available()}
    except RangerError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/chat")
def chat(request: ChatRequest):
    try:
        audits, policies = load(request.period, request.exclude_internal, request.sample_size)
        result = answer(request.question, audits, policies)
        log.append({"type": "chat", "question": request.question, "period": request.period, "excludeInternal": request.exclude_internal, "sampleSize": request.sample_size, "intent": result["intent"], "answer": result["answer"], "resultCount": len(result["data"])})
        return result
    except RangerError as exc:
        log.append({"type": "chat_error", "question": request.question, "error": str(exc)})
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/logs")
def logs(limit: int = Query(100, ge=1, le=500)):
    return {"items": log.recent(limit)}


@app.post("/api/admin/build-geo-index")
def build_geo_index():
    try:
        return {"rows": geo.build(), "databaseReady": True}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


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
