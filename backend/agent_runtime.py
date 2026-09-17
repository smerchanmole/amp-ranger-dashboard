"""Perfiles LLM seguros para LiteLLM y Cloudera AI Inference.

La configuración de ``.env`` es persistente. El panel web aplica cambios en
memoria y nunca devuelve los secretos al navegador ni los escribe en el log.
"""

from __future__ import annotations

from threading import RLock
from urllib.parse import urlparse

from .config import Settings


class AgentConfigError(ValueError):
    pass


class AgentRuntime:
    PROVIDERS = {
        "litellm": ("AI Gateway / LiteLLM", "Bearer token"),
        "cloudera": ("Cloudera AI Inference", "CDP Token, CML JWT o API key"),
    }

    def __init__(self, settings: Settings):
        self._lock = RLock()
        effective_token, token_source = settings.effective_ai_token
        cloudera_endpoint = settings.cloudera_ai_api_url or settings.ai_gateway_api_url
        cloudera_models = settings.cloudera_models or settings.gateway_models
        cloudera_default = settings.cloudera_ai_default_model or settings.ai_gateway_default_model
        self._profiles = {
            "litellm": {
                "endpoint": settings.ai_gateway_api_url,
                "token": settings.ai_gateway_token,
                "tokenSource": "api_key" if settings.ai_gateway_token else "none",
                "models": settings.gateway_models,
                "defaultModel": settings.ai_gateway_default_model,
            },
            "cloudera": {
                "endpoint": cloudera_endpoint,
                "token": settings.cloudera_ai_token or effective_token,
                "tokenSource": "cloudera_token" if settings.cloudera_ai_token else token_source,
                "models": cloudera_models,
                "defaultModel": cloudera_default,
            },
        }
        self._active = settings.agent_provider if settings.agent_provider in self.PROVIDERS else "cloudera"

    def profile(self, provider: str | None = None) -> dict:
        selected = provider or self._active
        if selected not in self.PROVIDERS:
            raise AgentConfigError("Proveedor de agente no permitido")
        with self._lock:
            return {"id": selected, **self._profiles[selected]}

    def public(self) -> dict:
        with self._lock:
            providers = []
            for provider, (label, auth_type) in self.PROVIDERS.items():
                profile = self._profiles[provider]
                providers.append({
                    "id": provider,
                    "label": label,
                    "authType": auth_type,
                    "endpoint": profile["endpoint"],
                    "models": profile["models"],
                    "defaultModel": profile["defaultModel"],
                    "tokenConfigured": bool(profile["token"]),
                    "tokenSource": profile["tokenSource"],
                })
            return {"activeProvider": self._active, "providers": providers, "mode": "hybrid-governed"}

    def update(self, provider: str, endpoint: str, token: str | None,
               models: list[str], default_model: str) -> dict:
        if provider not in self.PROVIDERS:
            raise AgentConfigError("Proveedor de agente no permitido")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AgentConfigError("El endpoint debe ser una URL HTTP(S) válida")
        clean_models = list(dict.fromkeys(model.strip() for model in models if model.strip()))
        if not clean_models or default_model not in clean_models:
            raise AgentConfigError("El modelo predeterminado debe pertenecer al catálogo")
        with self._lock:
            previous = self._profiles[provider]
            new_token = token.strip() if token and token.strip() else previous["token"]
            self._profiles[provider] = {
                "endpoint": endpoint.rstrip("/"),
                "token": new_token,
                "tokenSource": "web_override" if token and token.strip() else previous["tokenSource"],
                "models": clean_models,
                "defaultModel": default_model,
            }
            self._active = provider
        return self.public()
