"""Punto de entrada sencillo para iniciar la API desde Windows."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def usar_entorno_virtual() -> None:
    """Reinicia el script con el Python de .venv cuando sea necesario."""
    raiz = Path(__file__).resolve().parent
    python_venv = raiz / ".venv" / "Scripts" / "python.exe"

    if sys.prefix == sys.base_prefix and python_venv.exists():
        resultado = subprocess.run(
            [str(python_venv), str(Path(__file__).resolve()), *sys.argv[1:]],
            cwd=raiz,
            check=False,
        )
        raise SystemExit(resultado.returncode)


if __name__ == "__main__":
    usar_entorno_virtual()

    import uvicorn

    puerto = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=puerto,
        reload=True,
    )

