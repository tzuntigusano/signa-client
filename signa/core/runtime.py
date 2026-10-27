"""Cómo se arranca cada pieza de Signa y lo que cambia entre Windows y Mac, en un solo sitio:

    instalada (Win):  Signa.exe [--module signa.executor …]
    instalada (Mac):  Signa.app/Contents/MacOS/Signa [--module …]
    código:           pythonw -m signa.app (Windows) · python -m signa.app (Mac)
"""
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parents[2]


def _python() -> str:
    """El intérprete sin consola: pythonw en Windows; en Mac/Linux no hay ventana de consola que esconder."""
    if IS_WIN:
        w = Path(sys.executable).with_name("pythonw.exe")
        return str(w if w.exists() else sys.executable)
    return sys.executable


def module_cmd(module: str, *args: str) -> list[str]:
    """La orden para arrancar `python -m module args` (sin consola)."""
    if FROZEN:
        return [sys.executable, "--module", module, *args]
    return [_python(), "-m", module, *args]


def app_argv(*args: str) -> list[str]:
    """La orden que abre la app (la usa el arranque con el sistema)."""
    if FROZEN:
        return [sys.executable, *args]
    return [_python(), str(ROOT / "Signa.pyw"), *args]


def app_cmd(*args: str) -> str:
    return " ".join(f'"{a}"' if not a.startswith("--") else a for a in app_argv(*args))


def bundle() -> Path | None:
    """Mac: la Signa.app en la que corre (…/Signa.app/Contents/MacOS/Signa); None si no."""
    if IS_MAC and FROZEN:
        p = Path(sys.executable).resolve()
        if len(p.parents) >= 3 and p.parents[2].suffix == ".app":
            return p.parents[2]
    return None


def detached() -> dict:
    """Un proceso que sigue vivo aunque se cierre quien lo lanzó, y sin ventana."""
    if IS_WIN:
        return {"creationflags": 0x00000008 | 0x00000200 | 0x08000000}  # DETACHED | NEW_GROUP | NO_WINDOW
    return {"start_new_session": True}


def no_window() -> dict:
    """Un proceso hijo sin ventana de consola (Windows); en Mac no hace falta nada."""
    return {"creationflags": 0x08000000} if IS_WIN else {}


def spawn(cmd: list[str], cwd: str | None = None):
    subprocess.Popen(cmd, cwd=cwd or str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **detached())
