"""Registro de los procesos de Signa en el PC (agente, ejecutor): a la consola si la hay y SIEMPRE a un fichero en
%APPDATA%\\Signa\\<nombre>.log (sin consola, p. ej. arrancado con pythonw o por una tarea de Windows, es la única
pista). Se rota al pasar de 5 MB (se guarda un .1)."""
import os
import socket
import sys
from datetime import datetime
from pathlib import Path

def _home() -> Path:
    """Donde vive lo de Signa en este PC (logs, config, estado): %APPDATA%\\Signa en Windows, Application Support en
    Mac, ~/.local/share en Linux."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Signa"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Signa"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Signa"


HOME = _home()
MAX_BYTES = 5 * 1024 * 1024


class Log:
    def __init__(self, name: str):
        HOME.mkdir(parents=True, exist_ok=True)
        self.path = HOME / f"{name}.log"

    def __call__(self, prefix: str, msg: str):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{prefix}] {msg}"
        if sys.stdout is not None:
            try:
                print(line, flush=True)
            except (OSError, ValueError, UnicodeEncodeError):
                pass
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                self.path.replace(self.path.with_suffix(".log.1"))
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


def single_instance(port: int) -> socket.socket | None:
    """Candado: devuelve el socket si somos los únicos (hay que guardarlo vivo), None si ya hay otro corriendo."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def is_running(port: int) -> bool:
    """¿Hay un proceso de Signa con ese candado? (agente 47690, ejecutor 47691). Se mira intentando ocupar el
    puerto: llamar a la puerta no vale, el candado escucha pero nunca acepta y la cola se llena."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()
