from fastapi.testclient import TestClient

from backend.main import app


def test_diagnostics_returns_three_independent_services(monkeypatch):
    expected = {
        "services": {
            "api": {"ok": True, "latencyMs": 12, "detail": {"connected": True}},
            "solr": {"ok": False, "latencyMs": 15, "error": "sin conexión"},
            "model": {"ok": True, "latencyMs": 20, "detail": {"model": "nemotron"}},
        },
        "checkedAt": "2026-07-30T10:00:00+00:00",
    }
    monkeypatch.setattr("backend.main.connection_diagnostics", lambda: expected)
    client = TestClient(app, headers={"REMOTE-USER": "cml-admin"})
    response = client.get("/api/diagnostics")
    assert response.status_code == 200
    assert response.json() == expected
