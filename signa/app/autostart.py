"""Arrancar Signa con el sistema (solo el usuario actual, sin permisos de administrador). En MAC, un LaunchAgent
(~/Library/LaunchAgents/com.signa.app.plist, RunAtLoad): ver el final del módulo. En WINDOWS, dos piezas, como hace
Windows con cualquier app de inicio:
  - HKCU\\...\\CurrentVersion\\Run\\Signa = la orden que abre Signa al iniciar sesión;
  - HKCU\\...\\Explorer\\StartupApproved\\Run\\Signa = «permitida» (lo que enseña y cambia el Administrador de tareas
    › Aplicaciones de inicio). Si estaba desactivada ahí, Windows ignoraría la primera: al marcarla en Signa se
    deja permitida.
Y los accesos de la carpeta Inicio que arrancan el IB Gateway con el IBC suelto (StartGateway*.bat) chocarían con
los de Signa (dos sesiones de la misma cuenta: IBKR echa a una): se pueden desactivar igual que desde el
Administrador de tareas (StartupApproved\\StartupFolder), sin borrarlos."""
import os
import plistlib
from pathlib import Path

from ..core import runtime

if runtime.IS_WIN:
    import winreg

NAME = "Signa"
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
ON, OFF = bytes([2] + [0] * 11), bytes([3] + [0] * 11)  # el 1er byte par = permitida, impar = desactivada


def command() -> str:
    return runtime.app_cmd("--startup")  # Signa.exe --startup instalada; pythonw Signa.pyw --startup desde el código


def _get(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _set(path: str, name: str, kind: int, value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        winreg.SetValueEx(k, name, 0, kind, value)


def _del(path: str, name: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def _allowed(value) -> bool:
    return not value or value[0] % 2 == 0  # sin valor: Windows la da por permitida


def is_enabled() -> bool:
    return bool(_get(RUN, NAME)) and _allowed(_get(APPROVED + r"\Run", NAME))


def enable():
    _set(RUN, NAME, winreg.REG_SZ, command())
    _set(APPROVED + r"\Run", NAME, winreg.REG_BINARY, ON)


def disable():
    _del(RUN, NAME)
    _del(APPROVED + r"\Run", NAME)


# --- accesos del IBC suelto en la carpeta Inicio --------------------------------------------------
def startup_folder() -> Path:
    return Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"


def old_gateway_shortcuts() -> list[str]:
    """Accesos de la carpeta Inicio que lanzan StartGateway*.bat y siguen ACTIVOS."""
    out = []
    for lnk in sorted(startup_folder().glob("*.lnk")):
        try:
            raw = lnk.read_bytes()
        except OSError:
            continue
        if (b"StartGateway" in raw or "StartGateway".encode("utf-16-le") in raw) and \
                _allowed(_get(APPROVED + r"\StartupFolder", lnk.name)):
            out.append(lnk.name)
    return out


def disable_shortcuts(names: list[str]):
    """Como «Deshabilitar» en el Administrador de tareas: el acceso se queda, Windows no lo abre."""
    for n in names:
        _set(APPROVED + r"\StartupFolder", n, winreg.REG_BINARY, OFF)


# --- Mac: un LaunchAgent del usuario (lo que usan las apps de Mac para abrirse al iniciar sesión) ---
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.signa.app.plist"


def mac_plist() -> dict:
    return {"Label": "com.signa.app", "ProgramArguments": runtime.app_argv("--startup"), "RunAtLoad": True,
            "ProcessType": "Interactive"}


if runtime.IS_MAC:
    def is_enabled() -> bool:  # noqa: F811 (la versión de Mac sustituye a la de Windows)
        return LAUNCH_AGENT.exists()

    def enable():  # noqa: F811
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT.write_bytes(plistlib.dumps(mac_plist()))

    def disable():  # noqa: F811
        LAUNCH_AGENT.unlink(missing_ok=True)

    def old_gateway_shortcuts() -> list[str]:  # noqa: F811 (en Mac no hay accesos del IBC suelto que chocar)
        return []

    def disable_shortcuts(names: list[str]):  # noqa: F811
        pass
