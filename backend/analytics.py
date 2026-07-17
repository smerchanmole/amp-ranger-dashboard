"""Transformaciones de gobierno: de eventos Ranger a indicadores explicables.

Este módulo es deliberadamente puro: no conoce credenciales ni hace llamadas de
red. Esa separación permite auditar cómo se calcula cada KPI y probarlo con una
muestra controlada, una propiedad importante en plataformas Cloudera reguladas.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse


def _allowed(item: dict[str, Any]) -> bool:
    return str(item.get("accessResult", "")).lower() in {"1", "true", "allowed", "allow"}


def _time(item: dict[str, Any]) -> datetime | None:
    raw = item.get("eventTime")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _top(counter: Counter[str], limit: int = 8) -> list[dict[str, Any]]:
    return [{"name": name or "(vacío)", "value": value} for name, value in counter.most_common(limit)]


def resource_identity(raw: Any) -> tuple[str, str]:
    """Convierte recursos Ranger (URL, Hive o HDFS) en nombre y contexto legibles."""
    value = unquote(str(raw or "desconocido")).strip()
    parsed = urlparse(value if "://" in value else f"resource://{value.lstrip('/')}")
    clean = parsed.path or parsed.netloc or value
    parts = [part for part in clean.replace("\\", "/").split("/") if part]
    if not parts:
        return value, "—"
    name = parts[-1]
    context = "/".join(parts[:-1]) or parsed.netloc or "—"
    # Algunos plugins serializan database/table/column con @ o puntos.
    if "@" in name:
        name, suffix = name.split("@", 1)
        context = suffix or context
    return name, context


def audit_row(item: dict[str, Any]) -> dict[str, Any]:
    raw_resource = item.get("resourcePath") or item.get("resource") or "desconocido"
    name, context = resource_identity(raw_resource)
    return {
        "date": item.get("eventTime"),
        "user": item.get("requestUser") or "desconocido",
        "ip": item.get("clientIP") or "desconocida",
        "service": item.get("repoName") or item.get("serviceType") or "desconocido",
        "operation": item.get("accessType") or item.get("action") or "desconocida",
        "resource": name,
        "context": context,
        "allowed": _allowed(item),
    }


def dashboard(audits: list[dict[str, Any]], policies: list[dict[str, Any]], services: list[str]) -> dict[str, Any]:
    """Construye la vista gobernada que consumen dashboard y agente.

    Un recurso se agrupa junto con su servicio Ranger: una tabla ``ventas`` en
    Hive y un tópico homónimo en Kafka no son el mismo activo de gobierno.
    """
    allowed = sum(_allowed(item) for item in audits)
    denied = len(audits) - allowed
    rate = round(denied * 100 / len(audits), 2) if audits else 0
    users = Counter(str(a.get("requestUser") or "desconocido") for a in audits if not _allowed(a))
    resources = Counter(
        (
            str(a.get("repoName") or a.get("serviceType") or "desconocido"),
            str(a.get("resourcePath") or a.get("resource") or "desconocido"),
        )
        for a in audits
    )
    accesses_by_user = Counter(str(a.get("requestUser") or "desconocido") for a in audits)
    ips = Counter(str(a.get("clientIP") or "desconocida") for a in audits if not _allowed(a))
    denied_services = Counter(str(a.get("repoName") or "desconocido") for a in audits if not _allowed(a))
    service_distribution = Counter(str(a.get("repoName") or "desconocido") for a in audits)
    operations = Counter(str(a.get("accessType") or a.get("action") or "desconocida") for a in audits)
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"allowed": 0, "denied": 0})
    for item in audits:
        when = _time(item)
        if when:
            daily[when.astimezone(timezone.utc).date().isoformat()]["allowed" if _allowed(item) else "denied"] += 1

    now = datetime.now(timezone.utc)
    today = [item for item in audits if (when := _time(item)) and when.astimezone(timezone.utc).date() == now.date()]
    last_hour = [item for item in audits if (when := _time(item)) and 0 <= (now - when).total_seconds() <= 3600]
    sorted_audits = sorted(audits, key=lambda item: _time(item) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    resource_rows = []
    for (service, raw), count in resources.most_common(100):
        name, context = resource_identity(raw)
        resource_rows.append({"service": service, "name": name, "context": context, "raw": raw, "value": count})

    risky = 0
    for policy in policies:
        text = str(policy)
        if '"*"' in text or "public" in text.lower() or "delegateAdmin': True" in text:
            risky += 1

    return {
        "summary": {
            "total": len(audits), "allowed": allowed, "denied": denied, "denialRate": rate,
            "uniqueUsers": len(accesses_by_user), "policies": len(policies), "riskyPolicies": risky,
            "todayAllowed": sum(_allowed(item) for item in today), "todayDenied": sum(not _allowed(item) for item in today),
            "hourAllowed": sum(_allowed(item) for item in last_hour), "hourDenied": sum(not _allowed(item) for item in last_hour),
        },
        "timeline": [{"date": day, **daily[day]} for day in sorted(daily)],
        "topDeniedUsers": _top(users),
        "topDeniedIps": _top(ips),
        "topDeniedServices": _top(denied_services),
        "topResources": [{"name": f'{row["service"]} · {row["name"]}', "service": row["service"], "context": row["context"], "value": row["value"]} for row in resource_rows[:10]],
        "resourceTable": resource_rows,
        "accessesByUser": _top(accesses_by_user, 15),
        "serviceDistribution": _top(service_distribution, max(8, len(services))),
        "operations": _top(operations),
        "sampleSize": len(audits),
        "configuredServices": services,
        "recentAllowed": [audit_row(item) for item in sorted_audits if _allowed(item)][:100],
        "recentDenied": [audit_row(item) for item in sorted_audits if not _allowed(item)][:100],
    }
