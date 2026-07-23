"""Genera un certificado local con SAN para localhost y 127.0.0.1."""

from __future__ import annotations

import subprocess

from backend.config import get_settings

settings = get_settings()
settings.ssl_certfile.parent.mkdir(parents=True, exist_ok=True)
settings.ssl_keyfile.parent.mkdir(parents=True, exist_ok=True)

command = [
    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
    "-keyout", str(settings.ssl_keyfile), "-out", str(settings.ssl_certfile),
    "-days", "365", "-subj", f"/CN={settings.server_ip}",
    "-addext", f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{settings.server_ip}",
]
subprocess.run(command, check=True)
settings.ssl_keyfile.chmod(0o600)
print(f"Certificado generado: {settings.ssl_certfile}")
