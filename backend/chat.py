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
usuarios, servicios, operaciones, recursos, IP y políticas. No ejecutas cambios en Ranger."""

AVAILABLE_APIS = [
    {
        "método": "GET",
        "api": "/service/xaudit/access_audit",
        "función": "Consulta auditorías de acceso; excluye la lista configurable de usuarios internos mediante excludeUser.",
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
    if any(token in q for token in ("última hora", "ultima hora", "últimos 60", "ultimos 60")):
        events = [item for item in audits if (when := _time(item)) and timedelta(0) <= now - when <= timedelta(hours=1)]
        ok = sum(_allowed(item) for item in events)
        ko = len(events) - ok
        chart = {"type": "bar", "title": "Accesos de la última hora", "data": [{"name": "Permitidos", "value": ok}, {"name": "Denegados", "value": ko}]}
        return _result(f"En la última hora hay {len(events)} accesos: {ok} permitidos y {ko} denegados (muestra disponible: {sample}).", "last_hour", events, [audit_row(x) for x in events[:100]], chart)

    if "servicio" in q and ("usuario" in q or "acced" in q):
        pairs = Counter((str(a.get("requestUser") or "desconocido"), str(a.get("repoName") or "desconocido")) for a in audits)
        table = [{"usuario": user, "servicio": service, "accesos": count} for (user, service), count in pairs.most_common(100)]
        services = Counter(str(a.get("repoName") or "desconocido") for a in audits)
        return _result(f"He agrupado {sample} accesos por usuario y servicio.", "users_services", table, table, _counter_chart(services, "Accesos por servicio"))

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
            _counter_chart(services, "Accesos por servicio"),
        )

    if any(word in q for word in ("operación", "operacion", "acción", "accion")):
        operations = Counter(str(a.get("accessType") or a.get("action") or "desconocida") for a in audits)
        data = [{"name": n, "value": v} for n, v in operations.most_common(20)]
        return _result(
            f"He agrupado {sample} accesos por tipo de operación, mostrando primero las más frecuentes.",
            "accesses_by_operation", data,
            [{"operación": x["name"], "accesos": x["value"]} for x in data],
            _counter_chart(operations, "Accesos por operación"),
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
