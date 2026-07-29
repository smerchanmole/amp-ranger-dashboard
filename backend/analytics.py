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


def _resource_groups(
    counters: dict[str, Counter[Any]],
    *,
    resource_limit: int = 6,
    include_service: bool = False,
) -> list[dict[str, Any]]:
    """Serializa recursos por dimensión sin perder el denominador.

    Cada widget recibe el total completo de su servicio/usuario y los recursos
    principales. La categoría ``Otros`` conserva los accesos que quedan fuera
    del ranking, evitando que el toro visual sugiera una cobertura del 100 %
    cuando solo se muestran los primeros elementos.
    """
    groups = []
    for owner, counter in sorted(counters.items(), key=lambda item: sum(item[1].values()), reverse=True):
        total = sum(counter.values())
        resources = []
        visible_total = 0
        for identity, count in counter.most_common(resource_limit):
            if include_service:
                service, raw = identity
            else:
                service, raw = None, identity
            name, context = resource_identity(raw)
            resources.append({
                "name": name, "service": service, "context": context,
                "raw": raw, "value": count,
            })
            visible_total += count
        if total > visible_total:
            resources.append({
                "name": "Otros", "service": None, "context": "Resto de recursos",
                "raw": "", "value": total - visible_total,
            })
        groups.append({"name": owner or "(vacío)", "total": total, "resources": resources})
    return groups


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
    resources_by_service: dict[str, Counter[str]] = defaultdict(Counter)
    resources_by_user: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for item in audits:
        raw_resource = str(item.get("resourcePath") or item.get("resource") or "desconocido")
        service = str(item.get("repoName") or item.get("serviceType") or "desconocido")
        user = str(item.get("requestUser") or "desconocido")
        resources_by_service[service][raw_resource] += 1
        resources_by_user[user][(service, raw_resource)] += 1
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
        "resourcesByService": _resource_groups(resources_by_service),
        "resourcesByUser": _resource_groups(resources_by_user, include_service=True),
        "accessesByUser": _top(accesses_by_user, 15),
        "serviceDistribution": _top(service_distribution, max(8, len(services))),
        "operations": _top(operations),
        "sampleSize": len(audits),
        "configuredServices": services,
        "recentAllowed": [audit_row(item) for item in sorted_audits if _allowed(item)][:100],
        "recentDenied": [audit_row(item) for item in sorted_audits if not _allowed(item)][:100],
    }
