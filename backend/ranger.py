"""Adaptador de solo lectura entre la aplicación y Apache Ranger Admin.

En términos de gobierno, esta clase es la frontera de confianza: conserva las
credenciales en servidor, limita la paginación y expone únicamente operaciones
GET. Ningún KPI ni herramienta MCP puede construir endpoints arbitrarios.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import requests
import urllib3

from .config import Settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RangerError(RuntimeError):
    pass


class RangerClient:
    """Cliente HTTP mínimo para auditorías y catálogo de políticas Ranger."""
    AUDIT_PATH = "/service/xaudit/access_audit"
    POLICY_PATH = "/service/public/v2/api/policy"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        if settings.ranger_auth_type.casefold() == "bearer" and settings.ranger_token:
            self.session.headers["Authorization"] = f"Bearer {settings.ranger_token}"
        elif settings.ranger_auth_type.casefold() == "basic":
            self.session.auth = (settings.ranger_user, settings.ranger_password)
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        try:
            response = self.session.get(
                f"{self.settings.ranger_url.rstrip('/')}{path}",
                params=params,
                verify=self.settings.ranger_verify_ssl,
                timeout=self.settings.ranger_timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RangerError(f"No se pudo consultar Apache Ranger: {exc}") from exc

    def access_audits(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        service: str | None = None,
        page_size: int | None = None,
        exclude_users: bool = True,
    ) -> list[dict[str, Any]]:
        """Lee auditorías paginadas con límite duro de 100.000 eventos.

        ``excludeUser`` reduce ruido en origen. El backend vuelve a aplicar el
        filtro como control compensatorio para versiones que ignoran listas.
        """
        requested = min(page_size or self.settings.ranger_audit_page_size, 100000)
        params: dict[str, Any] = {
            "pageSize": min(requested, 10000),
            "startIndex": 0,
        }
        if exclude_users and self.settings.excluded_users:
            params["excludeUser"] = ",".join(self.settings.excluded_users)
        if start_date:
            params["startDate"] = start_date.strftime("%m/%d/%Y")
        if end_date:
            params["endDate"] = end_date.strftime("%m/%d/%Y")
        if service:
            params["repositoryName"] = service
        results: list[dict[str, Any]] = []
        while len(results) < requested:
            params["startIndex"] = len(results)
            params["pageSize"] = min(10000, requested - len(results))
            payload = self._get(self.AUDIT_PATH, params)
            if not isinstance(payload, dict):
                break
            page = payload.get("vXAccessAudits", payload.get("accessAudits", []))
            if not page:
                break
            results.extend(page)
            total = int(payload.get("totalCount", len(results)))
            if len(results) >= total or len(page) < params["pageSize"]:
                break
        return results[:requested]

    def policies(self, service: str | None = None) -> list[dict[str, Any]]:
        """Obtiene políticas para análisis de postura; nunca las modifica."""
        params = {"serviceName": service} if service else None
        payload = self._get(self.POLICY_PATH, params)
        if isinstance(payload, list):
            return payload
        return payload.get("policies", []) if isinstance(payload, dict) else []

    def health(self) -> dict[str, Any]:
        payload = self._get(self.AUDIT_PATH, {"pageSize": 1})
        return {"connected": True, "totalAudits": payload.get("totalCount", 0)} if isinstance(payload, dict) else {"connected": True}
