"""Adaptador único al AI Gateway compatible con OpenAI.

La aplicación desconoce si el alias se resuelve en OpenAI, Ollama u otro
proveedor. LiteLLM mantiene credenciales, routing y modelos fuera del código.
"""

from __future__ import annotations

from typing import Any

import requests

from .chat import SYSTEM_CONTEXT
from .config import Settings


class GatewayError(RuntimeError):
    pass


class AIGatewayClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def explain(self, model: str, question: str, result: dict[str, Any]) -> str:
        if model not in self.settings.gateway_models:
            raise GatewayError(f"Modelo no permitido: {model}")
        headers = {"Content-Type": "application/json"}
        if self.settings.ai_gateway_token:
            headers["Authorization"] = f"Bearer {self.settings.ai_gateway_token}"
        evidence = {
            "resumen_determinista": result.get("answer"),
            "tabla": result.get("table", [])[:20],
            "grafica": result.get("chart"),
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_CONTEXT + "\nResume únicamente la evidencia proporcionada; no inventes datos ni acciones."},
                {"role": "user", "content": f"Pregunta: {question}\nEvidencia calculada por Ranger Intelligence: {evidence}"},
            ],
        }
        try:
            response = requests.post(
                f"{self.settings.ai_gateway_api_url.rstrip('/')}/chat/completions",
                json=payload, headers=headers, timeout=self.settings.ai_gateway_timeout_seconds,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"]).strip()
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            raise GatewayError(f"AI Gateway no disponible: {exc}") from exc
