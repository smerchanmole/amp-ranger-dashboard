"""Fuente de auditoría Ranger basada directamente en Solr Kerberizado.

Solr usa nombres de campo distintos a la API de Ranger. Este adaptador aplica
filtros en origen y normaliza cada documento al contrato interno de la
aplicación. Así los KPIs, el mapa, el chat y MCP permanecen desacoplados de la
tecnología que almacena las auditorías.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from .config import Settings

Runner = Callable[..., subprocess.CompletedProcess[str]]
SAFE_TERM = re.compile(r"^[A-Za-z0-9_.@-]+$")


class SolrError(RuntimeError):
    """Error controlado de ticket Kerberos, curl o respuesta Solr."""


class KerberosTicket:
    """Mantiene un credential cache específico para el proceso de la app."""

    def __init__(self, settings: Settings, runner: Runner = subprocess.run):
        self.settings = settings
        self.runner = runner
        self._lock = Lock()

    @property
    def cache_path(self) -> Path:
        return self.settings.kerberos_ccache.resolve()

    @property
    def environment(self) -> dict[str, str]:
        return {**os.environ, "KRB5CCNAME": f"FILE:{self.cache_path}"}

    def _valid(self) -> bool:
        try:
            result = self.runner(
                ["klist", "-s", "-c", str(self.cache_path)],
                env=self.environment, text=True, capture_output=True, check=False,
            )
            return result.returncode == 0
        except OSError as exc:
            raise SolrError("No se encontró klist; instala el cliente Kerberos") from exc

    def ensure(self) -> None:
        """Obtiene un TGT solo cuando el cache no contiene uno válido."""
        with self._lock:
            if self._valid():
                return
            if not self.settings.kerberos_password:
                raise SolrError("KERBEROS_PASSWORD no está configurada en .env")
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                result = self.runner(
                    ["kinit", "-c", str(self.cache_path), self.settings.kerberos_principal],
                    input=f"{self.settings.kerberos_password}\n",
                    env=self.environment, text=True, capture_output=True, check=False,
                )
            except OSError as exc:
                raise SolrError("No se encontró kinit; instala el cliente Kerberos") from exc
            if result.returncode != 0:
                raise SolrError(f"kinit falló para {self.settings.kerberos_principal}: {result.stderr.strip()}")


class SolrAuditClient:
    """Consulta `ranger_audits` con SPNEGO y devuelve eventos normalizados."""

    def __init__(self, settings: Settings, runner: Runner = subprocess.run):
        self.settings = settings
        self.runner = runner
        self.ticket = KerberosTicket(settings, runner)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    @staticmethod
    def _normalize(doc: dict[str, Any]) -> dict[str, Any]:
        """Traduce el esquema Solr al vocabulario histórico de RangerClient."""
        return {
            "id": doc.get("id"),
            "accessResult": doc.get("result", 0),
            "accessType": doc.get("access") or doc.get("action"),
            "aclEnforcer": doc.get("enforcer"),
            "agentId": doc.get("agent"),
            "clientIP": doc.get("cliIP"),
            "policyId": doc.get("policy"),
            "repoName": doc.get("repo"),
            "repoType": doc.get("repoType"),
            "serviceType": doc.get("agent"),
            "resultReason": doc.get("reason"),
            "eventTime": doc.get("evtTime"),
            "requestUser": doc.get("reqUser"),
            "action": doc.get("action"),
            "resourcePath": doc.get("resource"),
            "resourceType": doc.get("resType"),
            "clusterName": doc.get("cluster"),
            "agentHost": doc.get("agentHost"),
            "eventCount": doc.get("event_count"),
            "eventDuration": doc.get("event_dur_ms"),
            "requestData": doc.get("reqData"),
        }

    def _curl(self, params: list[tuple[str, str]]) -> dict[str, Any]:
        self.ticket.ensure()
        command = [
            "curl", "--silent", "--show-error", "--fail-with-body",
            "--negotiate", "-u", ":", "-G",
            "--max-time", str(self.settings.solr_timeout_seconds),
        ]
        if not self.settings.solr_verify_ssl:
            command.append("-k")
        command.append(self.settings.solr_select_url)
        for key, value in params:
            command.extend(["--data-urlencode", f"{key}={value}"])
        try:
            result = self.runner(
                command, env=self.ticket.environment, text=True,
                capture_output=True, check=False,
            )
        except OSError as exc:
            raise SolrError("No se encontró curl con soporte SPNEGO") from exc
        if result.returncode != 0:
            raise SolrError(f"Consulta Solr fallida: {result.stderr.strip()}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SolrError("Solr no devolvió JSON válido") from exc
        if int(payload.get("responseHeader", {}).get("status", 0)) != 0:
            raise SolrError(f"Solr devolvió un error: {payload.get('error', payload)}")
        return payload

    def access_audits(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        service: str | None = None,
        page_size: int | None = None,
        exclude_users: bool = True,
    ) -> list[dict[str, Any]]:
        """Lee hasta 100.000 documentos, ordenados del más reciente al más antiguo."""
        requested = min(page_size or self.settings.ranger_audit_page_size, 100000)
        filters: list[str] = []
        if exclude_users:
            users = [user for user in self.settings.excluded_users if SAFE_TERM.fullmatch(user)]
            if users:
                filters.append(f"-reqUser:({' OR '.join(users)})")
        if start_date and end_date:
            filters.append(f"evtTime:[{self._iso(start_date)} TO {self._iso(end_date)}]")
        if service:
            if not SAFE_TERM.fullmatch(service):
                raise SolrError("Nombre de servicio no válido")
            filters.append(f'repo:"{service}"')

        results: list[dict[str, Any]] = []
        total = requested
        while len(results) < min(requested, total):
            rows = min(10000, requested - len(results))
            params = [
                ("q", "*:*"), ("start", str(len(results))), ("rows", str(rows)),
                ("sort", "evtTime desc"), ("wt", "json"),
            ]
            params.extend(("fq", value) for value in filters)
            payload = self._curl(params)
            response = payload.get("response", {})
            total = int(response.get("numFound", 0))
            docs = response.get("docs", [])
            if not docs:
                break
            results.extend(self._normalize(doc) for doc in docs)
        return results[:requested]

    def health(self) -> dict[str, Any]:
        payload = self._curl([("q", "*:*"), ("rows", "0"), ("wt", "json")])
        return {
            "connected": True,
            "source": "solr",
            "totalAudits": int(payload.get("response", {}).get("numFound", 0)),
            "zkConnected": bool(payload.get("responseHeader", {}).get("zkConnected")),
        }
