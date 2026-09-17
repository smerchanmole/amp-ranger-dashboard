from backend.analytics import dashboard, resource_identity
from backend.chat import answer
from backend.config import Settings
from backend.ranger import RangerClient
from backend.main import dates
from backend.geolocation import GeoDatabase
import sqlite3


AUDITS = [
    {"accessResult": 1, "requestUser": "ana", "clientIP": "8.8.8.8", "repoName": "cm_hdfs", "resourcePath": "/data/a", "eventTime": "2026-07-17T10:00:00Z"},
    {"accessResult": 0, "requestUser": "bob", "clientIP": "1.1.1.1", "repoName": "cm_hdfs", "resourcePath": "/data/a", "eventTime": "2026-07-17T11:00:00Z", "resultReason": "policy denied"},
]


def test_dashboard_counts_and_rankings():
    result = dashboard(AUDITS, [], ["cm_hdfs"])
    assert result["summary"]["allowed"] == 1
    assert result["summary"]["denied"] == 1
    assert result["summary"]["denialRate"] == 50
    assert result["topResources"][0] == {"name": "cm_hdfs · a", "service": "cm_hdfs", "context": "data", "value": 2}
    assert result["resourceTable"][0]["service"] == "cm_hdfs"


def test_resources_with_same_name_are_separated_by_service():
    audits = AUDITS + [{**AUDITS[0], "repoName": "cm_hive"}]
    result = dashboard(audits, [], ["cm_hdfs", "cm_hive"])
    rows = result["resourceTable"]
    assert {(row["service"], row["value"]) for row in rows} == {("cm_hdfs", 2), ("cm_hive", 1)}
    assert [(group["name"], group["total"]) for group in result["resourcesByService"]] == [
        ("cm_hdfs", 2), ("cm_hive", 1),
    ]


def test_resource_widgets_are_grouped_by_user_with_complete_totals():
    audits = AUDITS + [
        {**AUDITS[0], "resourcePath": "/data/b"},
        {**AUDITS[0], "resourcePath": "/data/c"},
    ]
    groups = dashboard(audits, [], ["cm_hdfs"])["resourcesByUser"]
    ana = next(group for group in groups if group["name"] == "ana")

    assert ana["total"] == 3
    assert {resource["name"] for resource in ana["resources"]} == {"a", "b", "c"}
    assert {resource["service"] for resource in ana["resources"]} == {"cm_hdfs"}


def test_chat_is_limited_to_known_intents():
    result = answer("¿Qué usuarios tuvieron más accesos denegados?", AUDITS, [])
    assert result["intent"] == "denied_users"
    assert result["data"][0]["name"] == "bob"
    assert result["table"][0]["usuario"] == "bob"
    assert result["chart"]["type"] == "bar"


def test_resource_identity_extracts_name_and_context():
    assert resource_identity("hdfs://cluster/user/data/orders.parquet") == ("orders.parquet", "user/data")
    assert resource_identity("sales/customers") == ("customers", "sales")


def test_chat_lists_only_read_apis():
    result = answer("¿Qué APIs puedes llamar?", AUDITS, [])
    assert result["intent"] == "available_apis"
    assert all(row["método"] == "GET" for row in result["table"])


def test_chat_understands_natural_user_breakdown():
    result = answer("desglosamos por usuario", AUDITS, [])
    assert result["intent"] == "accesses_by_user"
    assert result["table"][0]["usuario"] in {"ana", "bob"}
    assert "porcentaje" in result["table"][0]


def test_chat_finds_table_with_most_denied_column_accesses():
    audits = [
        {"accessResult": 0, "repoName": "cm_hive", "resourceType": "@column", "resourcePath": "ventas/pedidos/importe"},
        {"accessResult": 0, "repoName": "cm_hive", "resourceType": "@column", "resourcePath": "ventas/pedidos/cliente_id"},
        {"accessResult": 0, "repoName": "cm_hive", "resourceType": "@column", "resourcePath": "ventas/clientes/email"},
        {"accessResult": 1, "repoName": "cm_hive", "resourceType": "@column", "resourcePath": "ventas/clientes/email"},
    ]
    result = answer("¿Qué tabla tiene más accesos rechazados?", audits, [])
    assert result["intent"] == "denied_tablas"
    assert result["table"][0] == {
        "servicio": "cm_hive", "base_datos": "ventas", "tabla": "pedidos", "denegaciones": 2,
    }


def test_chat_finds_most_denied_column():
    audits = [
        {"accessResult": 0, "repoName": "cm_hive", "resourceType": "@column", "resourcePath": "ventas/personas/email"},
        {"accessResult": 0, "repoName": "cm_hive", "resourceType": "@column", "resourcePath": "ventas/personas/email"},
        {"accessResult": 0, "repoName": "cm_hive", "resourceType": "@column", "resourcePath": "ventas/personas/dni"},
    ]
    result = answer("¿Qué columna se está rechazando más?", audits, [])
    assert result["intent"] == "denied_columnas"
    assert result["table"][0]["columna"] == "email"
    assert result["table"][0]["denegaciones"] == 2


def test_ranger_excludes_service_users(monkeypatch):
    client = RangerClient(Settings(ranger_password="test", ranger_exclude_users="hive,spark"))
    captured = {}
    monkeypatch.setattr(client, "_get", lambda path, params: captured.update(params) or {"vXAccessAudits": []})
    client.access_audits()
    assert captured["excludeUser"] == "hive,spark"
    assert client.AUDIT_PATH == "/service/xaudit/access_audit"


def test_long_dashboard_periods():
    start_3m, end_3m = dates("3m")
    start_6m, end_6m = dates("6m")
    assert (end_3m - start_3m).days == 90
    assert (end_6m - start_6m).days == 180


def test_ranger_paginates_up_to_30000(monkeypatch):
    client = RangerClient(Settings(ranger_password="test", ranger_audit_page_size=30000))
    calls = []
    def fake_get(path, params):
        calls.append((params["startIndex"], params["pageSize"]))
        return {"totalCount": 25000, "vXAccessAudits": [{"id": i} for i in range(params["pageSize"])]}
    monkeypatch.setattr(client, "_get", fake_get)
    result = client.access_audits()
    assert len(result) == 30000 or len(result) == 25000
    assert calls == [(0, 10000), (10000, 10000), (20000, 10000)]


def test_private_ips_are_grouped_in_embajadores(tmp_path):
    db_path = tmp_path / "geo.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE ipv4 (start INTEGER, end INTEGER, country TEXT, city TEXT, latitude REAL, longitude REAL)")
    connection.commit(); connection.close()
    points = GeoDatabase(tmp_path / "unused.csv", db_path).locate([("192.168.1.10", 7), ("10.0.0.2", 3)])
    assert points == [{"ip": "Red local", "count": 10, "country": "España", "city": "Calle Embajadores 181, Madrid", "lat": 40.3912, "lng": -3.69233, "local": True}]


def test_sample_size_is_capped_at_100000(monkeypatch):
    client = RangerClient(Settings(ranger_password="test"))
    calls = []
    def empty_page(path, params):
        calls.append(params["pageSize"])
        return {"totalCount": 0, "vXAccessAudits": []}
    monkeypatch.setattr(client, "_get", empty_page)
    client.access_audits(page_size=200000)
    assert calls == [10000]
