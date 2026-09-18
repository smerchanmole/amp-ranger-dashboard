"""Capa semántica segura para preguntas de gobierno en lenguaje natural.

No convierte texto libre en URLs ni SQL. Clasifica la intención y ejecuta una
transformación permitida sobre la instantánea que FastAPI ya obtuvo de Ranger.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from .analytics import _allowed, _time, audit_row, resource_identity


SYSTEM_CONTEXT = """Eres el Analista de Seguridad de Apache Ranger. Trabajas únicamente con
la muestra de auditorías y políticas entregada por el backend y nunca inventas datos. Responde
en español claro. Selecciona el formato más útil: resumen textual, tabla de accesos y/o gráfica.
Incluye siempre el periodo y tamaño de muestra. Puedes analizar accesos permitidos o denegados,
usuarios, servicios, operaciones, recursos, tablas, columnas, IP y políticas. Cuando la evidencia
incluya base, tabla o columna, nómbralas de forma explícita. No ejecutas cambios en Ranger."""

AVAILABLE_APIS = [
    {
        "método": "GET",
        "api": "/service/xaudit/access_audit o /solr/ranger_audits/select",
        "función": "Consulta auditorías Ranger vía Knox o directamente en Solr; aplica fechas, muestra y exclusión de usuarios.",
    },
    {
        "método": "GET",
        "api": "/service/public/v2/api/policy",
        "función": "Consulta políticas para detectar comodines, permisos amplios y riesgos.",
    },
]


def _result(answer: str, intent: str, data: list[Any], table: list[dict[str, Any]] | None = None,
            chart: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"answer": answer, "intent": intent, "data": data, "table": table or [], "chart": chart}


def _counter_chart(counter: Counter[str], title: str, limit: int = 10) -> dict[str, Any]:
    return {"type": "bar", "title": title, "data": [{"name": name, "value": value} for name, value in counter.most_common(limit)]}


def _resource_parts(item: dict[str, Any]) -> dict[str, str]:
    """Interpreta recursos Ranger conservando la evidencia original.

    Hive representa normalmente una columna como ``base/tabla/columna`` y una
    tabla como ``base/tabla``. Las rutas HDFS no se reclasifican artificialmente.
    """
    raw = str(item.get("resourcePath") or item.get("resource") or "desconocido")
    resource_type = str(item.get("resourceType") or item.get("resType") or "").casefold()
    service = str(item.get("repoName") or item.get("repo") or item.get("serviceType") or "desconocido")
    parts = [part for part in raw.strip("/").split("/") if part]
    database = table = column = ""
    if "column" in resource_type and len(parts) >= 3:
        database, table, column = parts[0], parts[1], "/".join(parts[2:])
    elif "table" in resource_type and len(parts) >= 2:
        database, table = parts[0], "/".join(parts[1:])
    return {
        "servicio": service, "tipo": resource_type.lstrip("@") or "recurso",
        "base_datos": database, "tabla": table, "columna": column,
        "recurso_completo": raw,
    }


def _denied_objects(denied: list[dict[str, Any]], dimension: str) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Agrega denegaciones por tabla o columna con claves no ambiguas."""
    counts: Counter[tuple[str, ...]] = Counter()
    for event in denied:
        resource = _resource_parts(event)
        if dimension == "tabla" and resource["tabla"]:
            counts[(resource["servicio"], resource["base_datos"], resource["tabla"])] += 1
        elif dimension == "columna" and resource["columna"]:
            counts[(resource["servicio"], resource["base_datos"], resource["tabla"], resource["columna"])] += 1

    rows: list[dict[str, Any]] = []
    chart: Counter[str] = Counter()
    for key, count in counts.most_common(100):
        service, database, table, *rest = key
        row = {"servicio": service, "base_datos": database, "tabla": table}
        label = f"{database}.{table}"
        if dimension == "columna":
            row["columna"] = rest[0]
            label += f".{rest[0]}"
        row["denegaciones"] = count
        rows.append(row)
        chart[f"{service} · {label}"] = count
    return rows, chart


def answer(question: str, audits: list[dict[str, Any]], policies: list[dict[str, Any]]) -> dict[str, Any]:
    q = question.lower().strip()
    sample = len(audits)
    if not q:
        return _result("Escribe una pregunta sobre accesos, usuarios, servicios, recursos, IP o políticas.", "empty", [])

    if any(token in q for token in ("qué api", "que api", "apis", "endpoints", "qué puedes llamar", "que puedes llamar")):
        return _result(
            "Puedo llamar únicamente a estas APIs GET de Apache Ranger. No tengo ninguna herramienta de escritura o modificación de políticas.",
            "available_apis", AVAILABLE_APIS, AVAILABLE_APIS,
        )

    denied = [item for item in audits if not _allowed(item)]
    allowed = [item for item in audits if _allowed(item)]
    now = datetime.now(timezone.utc)

    if any(token in q for token in ("evolución", "evolucion", "tendencia", "por día", "por dia", "temporal")):
        timeline: dict[str, dict[str, int]] = {}
        for item in audits:
            when = _time(item)
            if not when:
                continue
            day = when.astimezone(timezone.utc).date().isoformat()
            row = timeline.setdefault(day, {"name": day, "allowed": 0, "denied": 0})
            row["allowed" if _allowed(item) else "denied"] += 1
        rows = [timeline[day] for day in sorted(timeline)]
        table = [{"fecha": row["name"], "permitidos": row["allowed"], "denegados": row["denied"]} for row in rows]
        return _result(
            f"He resumido la evolución diaria de {sample} accesos en {len(rows)} días con datos.",
            "access_timeline", rows, table,
            {"type": "line", "title": "Evolución de accesos", "data": rows,
             "series": [{"key": "allowed", "name": "Permitidos"}, {"key": "denied", "name": "Denegados"}]},
        )

    asks_denied = any(token in q for token in ("deneg", "rechaz", "bloque", " ko", "fall"))
    asks_table = any(token in q for token in ("tabla", "tablas"))
    asks_column = any(token in q for token in ("columna", "columnas", "campo", "campos"))
    if asks_denied and (asks_table or asks_column):
        dimension = "columna" if asks_column and not asks_table else "tabla"
        rows, chart_counts = _denied_objects(denied, dimension)
        if not rows:
            return _result(
                f"No hay denegaciones con metadatos de {dimension} en la muestra de {sample} accesos.",
                f"denied_{dimension}s", [], [],
            )
        leader = rows[0]
        qualified = f'{leader["base_datos"]}.{leader["tabla"]}'
        if dimension == "columna":
            qualified += f'.{leader["columna"]}'
        return _result(
            f"La {dimension} con más accesos rechazados es {qualified}, del servicio "
            f'{leader["servicio"]}, con {leader["denegaciones"]} denegaciones dentro de una muestra de {sample} accesos.',
            f"denied_{dimension}s", rows, rows,
            _counter_chart(chart_counts, f"{dimension.capitalize()}s con más denegaciones"),
        )

    if any(token in q for token in ("última hora", "ultima hora", "últimos 60", "ultimos 60")):
        events = [item for item in audits if (when := _time(item)) and timedelta(0) <= now - when <= timedelta(hours=1)]
        ok = sum(_allowed(item) for item in events)
        ko = len(events) - ok
        chart = {"type": "column", "title": "Accesos de la última hora", "data": [{"name": "Permitidos", "value": ok}, {"name": "Denegados", "value": ko}]}
        return _result(f"En la última hora hay {len(events)} accesos: {ok} permitidos y {ko} denegados (muestra disponible: {sample}).", "last_hour", events, [audit_row(x) for x in events[:100]], chart)

    if "servicio" in q and ("usuario" in q or "acced" in q):
        pairs = Counter((str(a.get("requestUser") or "desconocido"), str(a.get("repoName") or "desconocido")) for a in audits)
        table = [{"usuario": user, "servicio": service, "accesos": count} for (user, service), count in pairs.most_common(100)]
        services = Counter(str(a.get("repoName") or "desconocido") for a in audits)
        stacked: dict[str, dict[str, Any]] = {}
        service_names = [name for name, _ in services.most_common(6)]
        for (user, service), count in pairs.items():
            if service in service_names:
                stacked.setdefault(user, {"name": user})[service] = count
        chart = {"type": "stacked-column", "title": "Accesos por usuario y servicio", "data": list(stacked.values())[:12],
                 "series": [{"key": name, "name": name} for name in service_names]}
        return _result(f"He agrupado {sample} accesos por usuario y servicio.", "users_services", table, table, chart)

    if "comod" in q or ("polític" in q and "riesgo" in q):
        matches = [p for p in policies if '"*"' in str(p) or "public" in str(p).lower()][:100]
        table = [{"id": p.get("id"), "nombre": p.get("name"), "servicio": p.get("service"), "activa": p.get("isEnabled")} for p in matches]
        return _result(f"He encontrado {len(matches)} políticas potencialmente amplias entre {len(policies)} políticas.", "risky_policies", matches, table)

    if "deneg" in q and ("usuario" in q or "quién" in q or "quien" in q):
        top = Counter(str(a.get("requestUser") or "desconocido") for a in denied)
        data = [{"name": n, "value": v} for n, v in top.most_common(20)]
        return _result(f"Hay {len(denied)} denegaciones en una muestra de {sample} accesos. Los usuarios con más denegaciones aparecen en la tabla.", "denied_users", data, [{"usuario": x["name"], "denegaciones": x["value"]} for x in data], _counter_chart(top, "Denegaciones por usuario"))

    if "usuario" in q and any(word in q for word in ("acceso", "actividad", "número", "numero", "desglos", "agrup", "repart", "por usuario", "usuarios")):
        users = Counter(str(a.get("requestUser") or "desconocido") for a in audits)
        data = [{"name": n, "value": v} for n, v in users.most_common(20)]
        leader, leader_count = users.most_common(1)[0] if users else ("—", 0)
        share = round(leader_count * 100 / sample, 1) if sample else 0
        return _result(
            f"La muestra contiene {sample} accesos de {len(users)} usuarios. {leader} es el usuario con más actividad: {leader_count} accesos ({share}% de la muestra).",
            "accesses_by_user", data,
            [{"usuario": x["name"], "accesos": x["value"], "porcentaje": round(x["value"] * 100 / sample, 1) if sample else 0} for x in data],
            _counter_chart(users, "Accesos por usuario"),
        )

    if "servicio" in q and any(word in q for word in ("desglos", "agrup", "repart", "por servicio", "acceso", "actividad")):
        services = Counter(str(a.get("repoName") or "desconocido") for a in audits)
        data = [{"name": n, "value": v} for n, v in services.most_common(20)]
        return _result(
            f"He distribuido los {sample} accesos entre {len(services)} servicios, ordenados de mayor a menor actividad.",
            "accesses_by_service", data,
            [{"servicio": x["name"], "accesos": x["value"], "porcentaje": round(x["value"] * 100 / sample, 1) if sample else 0} for x in data],
            {"type": "donut", "title": "Distribución por servicio", "data": data[:10]},
        )

    if any(word in q for word in ("operación", "operacion", "acción", "accion")):
        operations = Counter(str(a.get("accessType") or a.get("action") or "desconocida") for a in audits)
        data = [{"name": n, "value": v} for n, v in operations.most_common(20)]
        return _result(
            f"He agrupado {sample} accesos por tipo de operación, mostrando primero las más frecuentes.",
            "accesses_by_operation", data,
            [{"operación": x["name"], "accesos": x["value"]} for x in data],
            {"type": "column", "title": "Accesos por operación", "data": data[:12]},
        )

    if "recurso" in q and any(word in q for word in ("solicit", "acced", "usad", "consult")):
        resources = Counter(
            (
                str(a.get("repoName") or a.get("serviceType") or "desconocido"),
                str(a.get("resourcePath") or a.get("resource") or "desconocido"),
            )
            for a in audits
        )
        rows = []
        for (service, raw), count in resources.most_common(100):
            name, context = resource_identity(raw)
            rows.append({"servicio": service, "recurso": name, "base_o_ruta": context, "accesos": count})
        chart_data = [{"name": f'{x["servicio"]} · {x["recurso"]}', "value": x["accesos"]} for x in rows[:10]]
        return _result(f"Estos son los recursos más solicitados en la muestra de {sample} accesos.", "top_resources", rows, rows, {"type": "bar", "title": "Recursos más solicitados", "data": chart_data})

    user = re.search(r"usuario\s+([\w.@-]+)", q)
    if user and ("por qué" in q or "porque" in q or "deneg" in q):
        name = user.group(1)
        events = [a for a in denied if str(a.get("requestUser", "")).lower() == name]
        reasons = Counter(str(a.get("resultReason") or "Ranger no informó del motivo") for a in events)
        return _result(f"Encontré {len(events)} denegaciones para {name}. " + "; ".join(f"{r} ({n})" for r, n in reasons.most_common(5)), "user_denials", events, [audit_row(x) for x in events[:100]], _counter_chart(reasons, "Motivos de denegación"))

    if any(word in q for word in ("últimos accesos", "ultimos accesos", "accesos recientes", "accesos ok", "accesos permitidos")):
        selected = denied if "deneg" in q or " ko" in f" {q}" else allowed
        rows = [audit_row(x) for x in sorted(selected, key=lambda x: _time(x) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:100]]
        return _result(f"Muestro los {len(rows)} accesos {'denegados' if selected is denied else 'permitidos'} más recientes.", "recent_accesses", selected, rows)

    services = Counter(str(a.get("repoName") or "desconocido") for a in audits)
    return _result(
        f"He analizado una muestra de {sample} accesos. Puedo desglosarlos por hora, usuario, servicio, recurso, IP, resultado u operación. Reformula indicando qué dimensión quieres comparar.",
        "overview", [],
        [{"servicio": name, "accesos": value} for name, value in services.most_common()],
        _counter_chart(services, "Accesos por servicio"),
    )
