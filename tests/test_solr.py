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
        kerberos_user="smerchan", kerberos_realm="MOLE4.LOCAL",
        kerberos_password="kerberos-test-password",
        kerberos_ccache=tmp_path / "krb5cc",
    )

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return completed(command, returncode=1 if command[0] == "klist" else 0)

    KerberosTicket(settings, runner).ensure()
    kinit_command, kinit_kwargs = calls[1]
    assert kinit_command == ["kinit", "-c", str((tmp_path / "krb5cc").resolve()), "smerchan@MOLE4.LOCAL"]
    assert kinit_kwargs["input"] == "kerberos-test-password\n"
    assert kinit_kwargs["env"]["KRB5CCNAME"].startswith("FILE:")


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
