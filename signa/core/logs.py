"""Registro de los procesos de Signa en el PC (agente, ejecutor): a la consola si la hay y SIEMPRE a un fichero en
%APPDATA%\\Signa\\<nombre>.log (sin consola, p. ej. arrancado con pythonw o por una tarea de Windows, es la única
pista). Se rota al pasar de 5 MB (se guarda un .1)."""
import json
import os
import platform
import re
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


# --- diagnóstico para el ADMIN de Signa (panel Admin › usuario › Status & logs) ------------------------------------
DIAG_TAIL = 64 * 1024  # de cada log, lo último
# lo que nunca sale del PC aunque esté en un log: tokens de bots de Telegram, claves y contraseñas
SECRET_RES = (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),  # token de bot de Telegram
              re.compile(r"(?i)\b(key|token|password|passwd|secret|authorization|bearer)(\W{1,3})\S+"),
              re.compile(r"\b[A-Za-z0-9_-]{40,}\b"))  # cualquier cosa con pinta de clave larga
MONEY = {"available", "buying_power", "net_liq"}  # el dinero de su cuenta: al admin no le hace falta


def redact(text: str) -> str:
    text = SECRET_RES[0].sub("[telegram bot]", text)
    text = SECRET_RES[1].sub(lambda m: f"{m.group(1)}{m.group(2)}[hidden]", text)
    return SECRET_RES[2].sub("[hidden]", text)


def diagnostics(with_logs: bool = False, tail: int = DIAG_TAIL) -> dict:
    """Lo que ve el admin del PC de un usuario: versión, sistema, el estado del ejecutor (conexiones con IBKR y bots,
    sin el dinero) y, si se pide, el final de cada log de Signa con los secretos tapados."""
    from .. import __version__
    try:
        status = json.loads((HOME / "executor_status.json").read_text(encoding="utf-8"))
        for acc in (status.get("accounts") or {}).values():
            for k in MONEY & set(acc):
                acc.pop(k)
    except (OSError, ValueError):
        status = None
    out = {"version": __version__, "platform": f"{platform.system()} {platform.release()}",
           "at": datetime.now().astimezone().isoformat(timespec="seconds"), "status": status}
    if with_logs:
        logs = {}
        for f in sorted(HOME.glob("*.log")):
            try:
                with f.open("rb") as fh:
                    size = fh.seek(0, 2)
                    fh.seek(max(0, size - tail))
                    data = fh.read().decode("utf-8", "replace")
            except OSError as e:
                data = f"(can't read: {e})"
                size = 0
            if size > tail:
                data = data.split("\n", 1)[-1]  # sin la primera línea a medias
            logs[f.name] = {"size": size, "text": redact(data)}
        out["logs"] = logs
    return out
