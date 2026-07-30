"""Arranque autocontenido para una Web App de Cloudera Machine Learning."""
import os
import subprocess
import sys
from pathlib import Path

def find_project_root() -> Path:
    """Localiza el proyecto tanto importado desde Git como cargado en carpeta."""
    cml_root = Path(os.getenv("CDSW_PROJECT_HOME", os.getcwd())).resolve()
    candidates = [cml_root, Path.cwd().resolve()]
    candidates.extend(
        requirements.parent
        for requirements in cml_root.glob("*/requirements.txt")
    )
    candidates.extend(
        requirements.parent
        for requirements in cml_root.glob("*/*/requirements.txt")
    )
    for candidate in candidates:
        if (
            (candidate / "requirements.txt").is_file()
            and (candidate / "backend" / "main.py").is_file()
            and (candidate / "frontend").is_dir()
        ):
            return candidate
    checked = ", ".join(str(path) for path in candidates)
    raise SystemExit(
        "No se encontró la carpeta de Ranger Intelligence. "
        f"Directorios comprobados: {checked}"
    )


ROOT = find_project_root()
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    # CML crea un contenedor limpio en cada arranque. La instalación forma parte
    # deliberadamente del mismo comando que levanta el servidor.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt")],
        check=True,
    )
    frontend_dist = ROOT / "frontend" / "dist" / "index.html"
    if not frontend_dist.exists():
        npm = os.getenv("NPM_BIN", "npm")
        subprocess.run([npm, "install", "--prefix", str(ROOT / "frontend")], check=True)
        subprocess.run([npm, "run", "build", "--prefix", str(ROOT / "frontend")], check=True)

    port = int(os.getenv("CDSW_APP_PORT", os.getenv("APP_PORT", "8000")))
    # CML ejecuta el fichero dentro de IPython, que ya tiene un event loop.
    # Uvicorn se aísla en otro proceso para que pueda crear su propio asyncio.
    subprocess.run(
        [
            sys.executable, "-m", "uvicorn", "backend.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=ROOT,
        check=True,
    )
