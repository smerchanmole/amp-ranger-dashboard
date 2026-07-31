"""Adaptador de solo lectura entre la aplicación y Apache Ranger Admin.

En términos de gobierno, esta clase es la frontera de confianza: conserva las
credenciales en servidor, limita la paginación y expone únicamente operaciones
GET. Ningún KPI ni herramienta MCP puede construir endpoints arbitrarios.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    KNOWN_ENDPOINTS = (AUDIT_PATH, POLICY_PATH)

    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        if settings.ranger_auth_type.casefold() == "bearer" and settings.ranger_token:
            self.session.headers["Authorization"] = f"Bearer {settings.ranger_token}"
        elif settings.ranger_auth_type.casefold() == "basic":
            self.session.auth = (settings.ranger_user, settings.ranger_password)
        self.session.headers.update({"Accept": "application/json"})

    @classmethod
    def normalize_base_url(cls, configured_url: str) -> str:
        """Acepta la base de Ranger o cualquiera de sus endpoints conocidos."""
        parsed = urlsplit(configured_url.strip())
        path = parsed.path.rstrip("/")
        for endpoint in cls.KNOWN_ENDPOINTS:
            if path.endswith(endpoint):
                path = path[:-len(endpoint)].rstrip("/")
                break
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
        url = f"{self.normalize_base_url(self.settings.ranger_url)}{path}"
        try:
            response = self.session.get(
                url,
                params=params,
                verify=self.settings.ranger_verify_ssl,
                timeout=self.settings.ranger_timeout_seconds,
            )
        except requests.exceptions.InvalidURL as exc:
            raise RangerError(f"No se puede usar la URL de Ranger: la dirección no es válida ({url})") from exc
        except requests.exceptions.SSLError as exc:
            raise RangerError(f"No se llegó a la URL de Ranger por un error de certificado SSL: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise RangerError(
                f"No se llegó a la URL de Ranger: la conexión agotó el tiempo de espera "
                f"({self.settings.ranger_timeout_seconds} s)"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise RangerError(f"No se llegó a la URL de Ranger: error de red, DNS o conexión ({exc})") from exc
        except requests.RequestException as exc:
            raise RangerError(f"No se llegó a la URL de Ranger: error HTTP de conexión ({exc})") from exc

        status = response.status_code
        if status in (401, 403):
            reason = "usuario o contraseña incorrectos" if status == 401 else "usuario autenticado sin permisos suficientes"
            raise RangerError(
                f"Se llegó a la URL de Ranger, pero la autenticación falló: {reason} (HTTP {status}). "
                f"{self._response_route(response, url)}"
            )
        if status >= 400:
            auth_hint = ""
            if status == 404:
                if response.headers.get("www-authenticate"):
                    auth_hint = (
                        " La respuesta incluye WWW-Authenticate: el gateway está señalando "
                        "un problema de autenticación aunque haya usado HTTP 404."
                    )
                else:
                    auth_hint = (
                        " El estado recibido por el cliente es realmente HTTP 404; no se ha "
                        "convertido desde 401/403. Knox puede ocultar un recurso no autorizado "
                        "como 404, por lo que una respuesta vacía no permite distinguir entre "
                        "ruta inexistente y autorización ocultada."
                    )
            raise RangerError(
                f"Se llegó a la URL de Ranger, pero la llamada a la API {path} devolvió "
                f"HTTP {status} {response.reason or ''}. {self._response_route(response, url)} "
                f"{self._safe_response_summary(response)}{auth_hint}"
            )
        try:
            return response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as exc:
            redirected = bool(response.history)
            redirect_note = (
                " La petición fue redirigida; revisa si Knox ha enviado la llamada a una página de login."
                if redirected else ""
            )
            raise RangerError(
                f"Se llegó a la URL de Ranger y el servidor aceptó la llamada (HTTP {status}), "
                f"pero la API {path} no devolvió JSON válido.{redirect_note} "
                f"{self._response_route(response, url)} {self._safe_response_summary(response)}"
            ) from exc

    @staticmethod
    def _response_route(response: requests.Response, requested_url: str) -> str:
        final_url = getattr(response, "url", None) or requested_url
        history = [str(item.status_code) for item in getattr(response, "history", [])]
        chain = " -> ".join([*history, str(response.status_code)])
        return f"URL solicitada: {requested_url}; URL final: {final_url}; cadena HTTP: {chain}."

    @staticmethod
    def _safe_response_summary(response: requests.Response) -> str:
        """Describe la respuesta sin volcar cabeceras, cookies ni cuerpos completos."""
        content_type = response.headers.get("content-type", "no indicado").split(";")[0]
        text = re.sub(r"<[^>]+>", " ", response.text or "")
        text = re.sub(r"\s+", " ", text).strip()
        sample = text[:180] if text else "respuesta vacía"
        return f"Tipo de contenido: {content_type}; respuesta: {sample}"

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
