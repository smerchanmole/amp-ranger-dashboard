"""Adaptador único al AI Gateway compatible con OpenAI.

La aplicación desconoce si el alias se resuelve en OpenAI, Ollama u otro
proveedor. LiteLLM mantiene credenciales, routing y modelos fuera del código.
"""

from __future__ import annotations

import json
from typing import Any

import requests
import urllib3

from .chat import SYSTEM_CONTEXT
from .agent_runtime import AgentRuntime
from .config import Settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class GatewayError(RuntimeError):
    pass


class AIGatewayClient:
    def __init__(self, settings: Settings, runtime: AgentRuntime | None = None):
        self.settings = settings
        self.runtime = runtime or AgentRuntime(settings)

    def _headers(self, profile: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        token = profile["token"]
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _stream_completion(self, profile: dict[str, Any], payload: dict[str, Any]) -> str:
        payload = {**payload, "stream": True}
        if not profile["endpoint"]:
            raise GatewayError(f"Configura el endpoint del proveedor {profile['id']}")
        try:
            response = requests.post(
                f"{profile['endpoint'].rstrip('/')}/chat/completions",
                json=payload,
                headers=self._headers(profile),
                timeout=self.settings.ai_gateway_timeout_seconds,
                stream=True,
                verify=self.settings.ai_gateway_verify_ssl,
            )
            response.raise_for_status()
            parts: list[str] = []
            event_count = 0
            response_keys: set[str] = set()
            choice_keys: set[str] = set()
            finish_reasons: set[str] = set()
            reasoning_fragments = 0
            ignored_lines = 0
            for raw_line in response.iter_lines():
                line = (raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)).strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                elif line.startswith("{"):
                    # Algunos endpoints ignoran stream=true y devuelven un único
                    # objeto JSON compatible con OpenAI.
                    data = line
                else:
                    ignored_lines += 1
                    continue
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                event_count += 1
                response_keys.update(str(key) for key in chunk)
                choice = (chunk.get("choices") or [{}])[0]
                choice_keys.update(str(key) for key in choice)
                if choice.get("finish_reason"):
                    finish_reasons.add(str(choice["finish_reason"]))
                message = choice.get("delta") or choice.get("message") or {}
                if message.get("reasoning_content"):
                    reasoning_fragments += 1
                content = message.get("content") or choice.get("text")
                if content:
                    parts.append(str(content))
            if not parts:
                content_type = getattr(response, "headers", {}).get("content-type", "no indicado")
                details = (
                    f"HTTP correcto pero sin texto; tipo={content_type}, eventos={event_count}, "
                    f"claves_respuesta={sorted(response_keys) or ['ninguna']}, "
                    f"claves_choice={sorted(choice_keys) or ['ninguna']}, "
                    f"finish_reason={sorted(finish_reasons) or ['no indicado']}, "
                    f"fragmentos_razonamiento={reasoning_fragments}, "
                    f"líneas_ignoradas={ignored_lines}"
                )
                raise GatewayError(f"El modelo respondió sin contenido utilizable. {details}")
            return "".join(parts).strip()
        except GatewayError:
            raise
        except requests.exceptions.SSLError as exc:
            raise GatewayError(
                "No se pudo conectar con el modelo por un error de certificado SSL. "
                "Si el endpoint usa un certificado autofirmado, desactiva "
                "'Verificar certificado SSL del modelo' en Configuración > Modelo LLM. "
                f"Detalle: {exc}"
            ) from exc
        except (requests.RequestException, IndexError, TypeError, ValueError) as exc:
            raise GatewayError(f"Modelo no disponible: {exc}") from exc

    def probe(self) -> dict[str, Any]:
        """Comprueba autenticación y respuesta real con el mínimo de tokens."""
        profile = self.runtime.profile()
        model = profile["defaultModel"]
        self._stream_completion(profile, {
            "model": model,
            "messages": [{"role": "user", "content": "Responde únicamente OK"}],
            "max_tokens": 64,
            "temperature": 0.2,
            "top_p": 0.7,
        })
        return {"connected": True, "provider": profile["id"], "model": model, "tokenSource": profile["tokenSource"]}

    def explain(self, provider: str, model: str, question: str, result: dict[str, Any]) -> str:
        profile = self.runtime.profile(provider)
        if model not in profile["models"]:
            raise GatewayError(f"Modelo no permitido: {model}")
        evidence = {
            "resumen_determinista": result.get("answer"),
            "fuente_auditoria": result.get("auditSource"),
            "tabla": result.get("table", [])[:20],
            "grafica": result.get("chart"),
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_CONTEXT + "\nDevuelve primero un resumen breve y ejecutivo. Resume únicamente la evidencia proporcionada; no inventes datos ni acciones. La tabla y la gráfica se renderizan por separado."},
                {"role": "user", "content": f"Pregunta: {question}\nEvidencia calculada por Ranger Intelligence: {evidence}"},
            ],
        }
        payload.update({"temperature": 0.2, "top_p": 0.7, "max_tokens": 1024})
        return self._stream_completion(profile, payload)
