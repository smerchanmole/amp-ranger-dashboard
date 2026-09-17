from backend.agent_runtime import AgentRuntime
from backend.config import Settings
from backend.llm import AIGatewayClient, GatewayError


class FakeResponse:
    headers = {"content-type": "text/event-stream"}

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return [
            b'data: {"choices":[{"delta":{"content":"Resumen "}}]}',
            b'data: {"choices":[{"delta":{"content":"gobernado"}}]}',
            b"data: [DONE]",
        ]


def test_gateway_uses_alias_and_openai_compatible_endpoint(monkeypatch):
    settings = Settings(
        ai_gateway_api_url="http://gateway:4000/v1",
        ai_gateway_token="token",
        ai_gateway_models="topito,qwen-local",
    )
    captured = {}
    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse()
    monkeypatch.setattr("backend.llm.requests.post", fake_post)
    answer = AIGatewayClient(settings).explain("litellm", "qwen-local", "pregunta", {"answer": "base", "table": [], "chart": None})
    assert answer == "Resumen gobernado"
    assert captured["url"] == "http://gateway:4000/v1/chat/completions"
    assert captured["json"]["model"] == "qwen-local"
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_gateway_rejects_models_not_published_in_env():
    settings = Settings(ai_gateway_models="topito,qwen-local")
    try:
        AIGatewayClient(settings).explain("litellm", "direct-provider-model", "pregunta", {})
    except GatewayError as exc:
        assert "no permitido" in str(exc)
    else:
        raise AssertionError("El gateway debía rechazar un modelo fuera del catálogo")


def test_gateway_probe_performs_minimal_real_completion(monkeypatch):
    settings = Settings(
        ai_gateway_api_url="https://model.example/v1",
        ai_gateway_token="token",
        ai_gateway_default_model="nemotron",
        ai_gateway_models="nemotron",
    )
    captured = {}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr("backend.llm.requests.post", fake_post)
    result = AIGatewayClient(settings).probe()
    assert result == {"connected": True, "provider": "cloudera", "model": "nemotron", "tokenSource": "api_key"}
    assert captured["url"] == "https://model.example/v1/chat/completions"
    assert captured["json"]["max_tokens"] == 64


def test_gateway_empty_stream_reports_safe_response_structure(monkeypatch):
    class EmptyResponse(FakeResponse):
        def iter_lines(self):
            return [
                b'data: {"id":"x","choices":[{"delta":{},"finish_reason":"length"}]}',
                b"data: [DONE]",
            ]

    monkeypatch.setattr("backend.llm.requests.post", lambda *args, **kwargs: EmptyResponse())
    settings = Settings(ai_gateway_default_model="nemotron", ai_gateway_models="nemotron")
    try:
        AIGatewayClient(settings).probe()
    except GatewayError as exc:
        message = str(exc)
        assert "eventos=1" in message
        assert "finish_reason=['length']" in message
        assert "text/event-stream" in message
    else:
        raise AssertionError("Un stream vacío debía devolver diagnóstico estructural")


def test_cml_jwt_is_preferred_over_manual_api_key(tmp_path):
    jwt = tmp_path / "jwt"
    jwt.write_text('{"access_token":"fresh-cdp-token"}', encoding="utf-8")
    settings = Settings(
        cdp_token="",
        use_cml_jwt=True,
        cml_jwt_path=jwt,
        ai_gateway_token="older-api-key",
    )
    assert settings.effective_ai_token == ("fresh-cdp-token", "cml_jwt")


def test_explicit_cdp_token_has_highest_priority(tmp_path):
    jwt = tmp_path / "jwt"
    jwt.write_text('{"access_token":"automatic-token"}', encoding="utf-8")
    settings = Settings(cdp_token="explicit-token", cml_jwt_path=jwt, ai_gateway_token="api-key")
    assert settings.effective_ai_token == ("explicit-token", "cdp_token")


def test_cloudera_runtime_never_exposes_secret():
    runtime = AgentRuntime(Settings(cdp_token="cdp-secret", ai_gateway_models="nemotron", ai_gateway_default_model="nemotron"))
    assert "cdp-secret" not in str(runtime.public())
    assert runtime.profile("cloudera")["token"] == "cdp-secret"


def test_runtime_update_keeps_existing_secret_when_token_is_blank():
    runtime = AgentRuntime(Settings(ai_gateway_token="existing"))
    public = runtime.update("litellm", "https://gateway.example/v1", "", ["topito"], "topito")
    assert public["activeProvider"] == "litellm"
    assert runtime.profile()["token"] == "existing"
