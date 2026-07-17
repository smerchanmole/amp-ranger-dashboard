"""Servidor MCP de solo lectura para agentes externos de gobierno.

MCP no sustituye a Ranger ni evita sus controles. Publica un conjunto cerrado
de herramientas que reutiliza exactamente el mismo cliente, filtros y límites
que la web. De este modo un LLM puede razonar sobre evidencias sin recibir
credenciales y sin capacidad para cambiar políticas.

Ejecución local por stdio::

    python -m backend.mcp_server

Para Streamable HTTP::

    MCP_TRANSPORT=streamable-http python -m backend.mcp_server
"""

from __future__ import annotations

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .analytics import dashboard
from .main import client, load, settings

Period = Literal["24h", "7d", "30d", "3m", "6m"]

mcp = FastMCP(
    "Apache Ranger Governance",
    instructions=(
        "Herramientas de solo lectura para auditoría y gobierno Apache Ranger. "
        "Nunca inventes eventos ni presentes una muestra como el universo completo."
    ),
    stateless_http=True,
    json_response=True,
)


def _snapshot(period: Period, sample_size: int, exclude_internal: bool):
    """Aplica los límites de gobierno antes de entregar contexto al modelo."""
    safe_size = max(100, min(sample_size, 100000))
    audits, policies = load(period, exclude_internal, safe_size)
    return audits, policies, dashboard(audits, policies, settings.services)


@mcp.tool()
def ranger_access_kpis(
    period: Period = "7d", sample_size: int = 5000, exclude_internal: bool = True
) -> dict:
    """Devuelve KPIs OK/KO, evolución, usuarios y servicios para el periodo."""
    _, _, view = _snapshot(period, sample_size, exclude_internal)
    return {
        "scope": {"period": period, "sampleSize": view["sampleSize"], "excludeInternal": exclude_internal},
        "summary": view["summary"],
        "timeline": view["timeline"],
        "accessesByUser": view["accessesByUser"],
        "serviceDistribution": view["serviceDistribution"],
    }


@mcp.tool()
def ranger_top_resources(
    period: Period = "7d", sample_size: int = 5000, exclude_internal: bool = True, limit: int = 20
) -> dict:
    """Lista activos más usados con servicio, nombre, base/ruta y accesos."""
    _, _, view = _snapshot(period, sample_size, exclude_internal)
    safe_limit = max(1, min(limit, 100))
    return {"sampleSize": view["sampleSize"], "resources": view["resourceTable"][:safe_limit]}


@mcp.tool()
def ranger_recent_denials(
    period: Period = "24h", sample_size: int = 5000, exclude_internal: bool = True, limit: int = 100
) -> dict:
    """Devuelve denegaciones recientes para investigación, sin mutar Ranger."""
    _, _, view = _snapshot(period, sample_size, exclude_internal)
    safe_limit = max(1, min(limit, 100))
    return {"sampleSize": view["sampleSize"], "denials": view["recentDenied"][:safe_limit]}


@mcp.tool()
def ranger_policy_inventory(service: str | None = None) -> dict:
    """Consulta el inventario de políticas, opcionalmente por servicio Ranger."""
    policies = client.policies(service)
    return {"service": service, "count": len(policies), "policies": policies[:500]}


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
