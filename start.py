"""Arranque único para local y Cloudera AI Workbench."""
import os

import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("CDSW_APP_PORT", os.getenv("APP_PORT", "8000")))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port)
