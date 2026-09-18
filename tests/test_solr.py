import json
import subprocess
from datetime import datetime, timezone

from backend.config import Settings
from backend.solr import KerberosTicket, SolrAuditClient


def completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_kerberos_uses_dedicated_cache_and_password_via_stdin(tmp_path):
    calls = []
    settings = Settings(
        kerberos_enabled=True,
        kerberos_user="smerchan", kerberos_realm="MOLE4.LOCAL",
        kerberos_kdc="base1.mole4.local",
        kerberos_admin_server="base1.mole4.local",
        kerberos_password="kerberos-test-password",
        kerberos_ccache=tmp_path / "krb5cc",
        kerberos_config_file=tmp_path / "krb5.conf",
    )

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(command, returncode=1 if command[0] == "klist" else 0)

    KerberosTicket(settings, runner).ensure()
    kinit_command, kinit_kwargs = calls[1]
    assert kinit_command == [
        "kinit", "-c", str((tmp_path / "krb5cc").resolve()), "smerchan@MOLE4.LOCAL",
    ]
    assert kinit_kwargs["input"] == "kerberos-test-password\n"
    assert kinit_kwargs["env"]["KRB5CCNAME"].startswith("FILE:")
    assert kinit_kwargs["env"]["KRB5_CONFIG"] == str((tmp_path / "krb5.conf").resolve())
    generated_config = (tmp_path / "krb5.conf").read_text(encoding="utf-8")
    assert "default_realm = MOLE4.LOCAL" in generated_config
    assert "kdc = base1.mole4.local" in generated_config


def test_disabled_kerberos_does_not_run_kinit_or_add_spnego(tmp_path):
    calls = []
    payload = {"responseHeader": {"status": 0}, "response": {"numFound": 0, "docs": []}}
    settings = Settings(kerberos_enabled=False, kerberos_ccache=tmp_path / "krb5cc")

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(command, stdout=json.dumps(payload))

    SolrAuditClient(settings, runner).health()
    assert len(calls) == 1
    assert calls[0][0][0] == "curl"
    assert "--negotiate" not in calls[0][0]
    assert "env" not in calls[0][1]


def test_kerberos_principal_only_adds_realm_when_configured():
    assert Settings(kerberos_user="smerchan", kerberos_realm="").kerberos_principal == "smerchan"
    assert (
        Settings(kerberos_user="smerchan", kerberos_realm="MOLE4.LOCAL").kerberos_principal
        == "smerchan@MOLE4.LOCAL"
    )


def test_knox_url_is_normalized_and_basic_credentials_use_stdin(tmp_path):
    calls = []
    payload = {"responseHeader": {"status": 0}, "response": {"numFound": 0, "docs": []}}
    settings = Settings(
        solr_url="https://h12cdpmp01x.salud.madrid.org:8443/gateway/cdp-proxy-api/solr/ranger_audits/select#/",
        solr_auth_type="basic", solr_user="audit-reader", solr_password='secret"value',
        kerberos_enabled=False, kerberos_ccache=tmp_path / "krb5cc",
    )

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(command, stdout=json.dumps(payload))

    SolrAuditClient(settings, runner).health()
    command, kwargs = calls[0]
    assert settings.solr_select_url == "https://h12cdpmp01x.salud.madrid.org:8443/gateway/cdp-proxy-api/solr/ranger_audits/select"
    assert "--basic" in command
    assert "--config" in command
    assert "audit-reader" not in " ".join(command)
    assert kwargs["input"] == 'user = "audit-reader:secret\\"value"\n'


def test_knox_base_url_adds_solr_collection_and_select():
    settings = Settings(
        solr_url="https://gateway.example:8443/gateway/cdp-proxy-api",
        solr_collection="ranger_audits",
    )
    assert settings.solr_select_url.endswith("/gateway/cdp-proxy-api/solr/ranger_audits/select")


def test_solr_filters_users_dates_and_normalizes_response(monkeypatch, tmp_path):
    captured = {}
    solr_doc = {
        "id": "event-1", "access": "append", "enforcer": "ranger-acl",
        "agent": "hdfs", "repo": "cm_hdfs", "reqUser": "mioti",
        "resource": "/user/mioti/file.csv", "cliIP": "192.168.2.106",
        "result": 1, "policy": 132, "resType": "path", "action": "write",
        "evtTime": "2026-06-28T17:20:38.116Z", "reason": "/user/mioti/file.csv",
    }
    payload = {"responseHeader": {"status": 0, "zkConnected": True}, "response": {"numFound": 1, "docs": [solr_doc]}}
    settings = Settings(
        solr_server="base2.mole4.local", solr_port=8995,
        ranger_exclude_users="hdfs,hive,impala,kafka,nifi,spark,yarn,hue",
        kerberos_ccache=tmp_path / "krb5cc",
    )

    def runner(command, **kwargs):
        captured["command"] = command
        return completed(command, stdout=json.dumps(payload))

    client = SolrAuditClient(settings, runner)
    monkeypatch.setattr(client.ticket, "ensure", lambda: None)
    events = client.access_audits(
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        page_size=10,
    )

    command = captured["command"]
    encoded = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--data-urlencode"]
    assert "fq=-reqUser:(hdfs OR hive OR impala OR kafka OR nifi OR spark OR yarn OR hue)" in encoded
    assert any(value.startswith("fq=evtTime:[2026-06-01") for value in encoded)
    assert "sort=evtTime desc" in encoded
    assert events[0]["requestUser"] == "mioti"
    assert events[0]["repoName"] == "cm_hdfs"
    assert events[0]["resourcePath"] == "/user/mioti/file.csv"
    assert events[0]["eventTime"] == "2026-06-28T17:20:38.116Z"
