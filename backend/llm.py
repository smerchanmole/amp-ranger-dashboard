"""Adaptador único al AI Gateway compatible con OpenAI.

La aplicación desconoce si el alias se resuelve en OpenAI, Ollama u otro
proveedor. LiteLLM mantiene credenciales, routing y modelos fuera del código.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from .chat import SYSTEM_CONTEXT
from .config import Settings


class GatewayError(RuntimeError):
    pass


class AIGatewayClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token, _ = self.settings.effective_ai_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _stream_completion(self, payload: dict[str, Any]) -> str:
        payload = {**payload, "stream": True}
        try:
            response = requests.post(
                f"{self.settings.ai_gateway_api_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self.settings.ai_gateway_timeout_seconds,
                stream=True,
            )
            response.raise_for_status()
            parts: list[str] = []
            for raw_line in response.iter_lines():
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                choice = (chunk.get("choices") or [{}])[0]
                content = (choice.get("delta") or choice.get("message") or {}).get("content")
                if content:
                    parts.append(str(content))
            if not parts:
                raise GatewayError("El modelo respondió sin contenido en el stream")
            return "".join(parts).strip()
        except GatewayError:
            raise
        except (requests.RequestException, IndexError, TypeError, ValueError) as exc:
            raise GatewayError(f"Modelo no disponible: {exc}") from exc

    def probe(self) -> dict[str, Any]:
        """Comprueba autenticación y respuesta real con el mínimo de tokens."""
        model = self.settings.ai_gateway_default_model
        _, token_source = self.settings.effective_ai_token
        self._stream_completion({
            "model": model,
            "messages": [{"role": "user", "content": "Responde únicamente OK"}],
            "max_tokens": 2,
            "temperature": 0.2,
            "top_p": 0.7,
        })
        return {"connected": True, "model": model, "tokenSource": token_source}

    def explain(self, model: str, question: str, result: dict[str, Any]) -> str:
        if model not in self.settings.gateway_models:
            raise GatewayError(f"Modelo no permitido: {model}")
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
        payload.update({"temperature": 0.2, "top_p": 0.7, "max_tokens": 1024})
        return self._stream_completion(payload)
