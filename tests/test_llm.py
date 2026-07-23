from backend.config import Settings
from backend.llm import AIGatewayClient, GatewayError


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "Resumen gobernado"}}]}


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
    answer = AIGatewayClient(settings).explain("qwen-local", "pregunta", {"answer": "base", "table": [], "chart": None})
    assert answer == "Resumen gobernado"
    assert captured["url"] == "http://gateway:4000/v1/chat/completions"
    assert captured["json"]["model"] == "qwen-local"
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_gateway_rejects_models_not_published_in_env():
    settings = Settings(ai_gateway_models="topito,qwen-local")
    try:
        AIGatewayClient(settings).explain("direct-provider-model", "pregunta", {})
    except GatewayError as exc:
        assert "no permitido" in str(exc)
    else:
        raise AssertionError("El gateway debía rechazar un modelo fuera del catálogo")
