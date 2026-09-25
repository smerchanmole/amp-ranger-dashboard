import requests

from backend.config import Settings
from backend.ranger import RangerClient, RangerError


class FakeResponse:
    def __init__(self, status=200, body="", content_type="application/json", reason="", url="https://ranger.example/final", headers=None):
        self.status_code = status
        self.text = body
        self.headers = {"content-type": content_type, **(headers or {})}
        self.reason = reason
        self.history = []
        self.url = url

    def json(self):
        if not self.text:
            raise requests.exceptions.JSONDecodeError("empty", "", 0)
        return {"totalCount": 1}


def error_from(response=None, exception=None):
    client = RangerClient(Settings(ranger_url="https://ranger.example"))

    def fake_get(*args, **kwargs):
        if exception:
            raise exception
        return response

    client.session.get = fake_get
    try:
        client.health()
    except RangerError as exc:
        return str(exc)
    raise AssertionError("La comprobación debía fallar")


def test_ranger_reports_unreachable_url():
    message = error_from(exception=requests.ConnectionError("Name or service not known"))
    assert message.startswith("No se llegó a la URL de Ranger")
    assert "DNS" in message


def test_ranger_reports_invalid_credentials():
    message = error_from(FakeResponse(status=401, reason="Unauthorized"))
    assert "Se llegó a la URL de Ranger" in message
    assert "usuario o contraseña incorrectos" in message


def test_ranger_reports_forbidden_user():
    message = error_from(FakeResponse(status=403, reason="Forbidden"))
    assert "usuario autenticado sin permisos suficientes" in message


def test_ranger_reports_api_http_error():
    message = error_from(FakeResponse(status=500, body="<html>Proxy failure</html>", content_type="text/html", reason="Server Error"))
    assert "llamada a la API /service/xaudit/access_audit" in message
    assert "HTTP 500" in message
    assert "Proxy failure" in message


def test_ranger_accepts_complete_audit_endpoint_without_duplicating_path():
    configured = "https://gateway.example/env/cdp-proxy-token/ranger/service/xaudit/access_audit"
    client = RangerClient(Settings(ranger_url=configured))
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResponse(status=200, body='{"totalCount": 1}', url=url)

    client.session.get = fake_get
    assert client.health()["connected"] is True
    assert captured["url"] == configured


def test_ranger_allows_self_signed_certificates_when_ssl_verification_is_disabled():
    client = RangerClient(Settings(ranger_url="https://ranger.example", ranger_verify_ssl=False))
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(status=200, body='{"totalCount": 1}', url=url)

    client.session.get = fake_get
    assert client.health()["connected"] is True
    assert captured["verify"] is False


def test_ranger_ssl_error_explains_how_to_allow_self_signed_certificates():
    message = error_from(exception=requests.exceptions.SSLError("self-signed certificate"))
    assert "certificado autofirmado" in message
    assert "Verificar certificado SSL" in message


def test_ranger_complete_endpoint_is_normalized_for_policy_calls():
    configured = "https://gateway.example/env/cdp-proxy-token/ranger/service/xaudit/access_audit?ignored=true"
    client = RangerClient(Settings(ranger_url=configured))
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResponse(status=200, body="[]", url=url)

    client.session.get = fake_get
    client.policies()
    assert captured["url"] == "https://gateway.example/env/cdp-proxy-token/ranger/service/public/v2/api/policy"


def test_ranger_404_explains_that_status_is_real_but_knox_may_mask_auth():
    message = error_from(FakeResponse(status=404, reason="Not Found"))
    assert "realmente HTTP 404" in message
    assert "no se ha convertido desde 401/403" in message
    assert "Knox puede ocultar" in message
    assert "URL solicitada:" in message
    assert "URL final:" in message


def test_ranger_404_with_authenticate_header_flags_authentication():
    response = FakeResponse(status=404, headers={"www-authenticate": 'Bearer realm="knox"'})
    message = error_from(response)
    assert "problema de autenticación" in message


def test_ranger_reports_empty_success_response():
    message = error_from(FakeResponse(status=200, body="", content_type="text/html"))
    assert "servidor aceptó la llamada (HTTP 200)" in message
    assert "no devolvió JSON válido" in message
    assert "respuesta vacía" in message
