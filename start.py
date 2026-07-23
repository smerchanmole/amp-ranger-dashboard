"""Arranque único para local y Cloudera AI Workbench."""
import os
from pathlib import Path

import uvicorn

from backend.config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    port = int(os.getenv("CDSW_APP_PORT", os.getenv("APP_PORT", "8000")))
    certfile = Path(settings.ssl_certfile)
    keyfile = Path(settings.ssl_keyfile)
    if not certfile.exists() or not keyfile.exists():
        raise SystemExit("Faltan los certificados HTTPS. Ejecuta: python -m scripts.generate_self_signed_cert")
    uvicorn.run(
        "backend.main:app", host=settings.server_ip, port=port,
        ssl_certfile=str(certfile), ssl_keyfile=str(keyfile),
    )
