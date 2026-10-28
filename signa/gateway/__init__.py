"""IB GATEWAY gestionado por Signa con IBC (signa/ibc/IBC.jar, GPL-3.0, sin modificar).

IBC no es un programa que «maneje» el Gateway desde fuera: es una clase Java (ibcalpha.ibc.IbcGateway) que arranca
DENTRO de la JVM del Gateway y rellena el login. Así que Signa hace lo que hacían los .bat de IBC
(StartGateway.bat → StartIBC.bat), en Python:
  - monta la línea de Java: jars del Gateway + IBC.jar, sus opciones de JVM y las de install4j;
  - la relanza como el bucle de StartIBC.bat: reinicio diario del Gateway SIN volver a pedir login (fichero
    `autorestart` → -Drestart=…), reinicio en frío, y reintento si caduca el 2FA o el diálogo de login;
  - su config.ini lo genera Signa SIN credenciales (usuario y contraseña viven en el Administrador de
    credenciales de Windows y se le pasan a IBC al arrancar), con el servidor de comandos de IBC abierto SOLO en
    127.0.0.1 para STOP / RESTART.

Este módulo es la lógica; el proceso que vigila cada Gateway es `python -m signa.gateway paper|live`."""
import json
import os
import random
import re
import socket
from pathlib import Path

from ..core import runtime
from ..core.logs import HOME

IBC_DIR = Path(__file__).resolve().parents[1] / "ibc"
IBC_JAR = IBC_DIR / "IBC.jar"
# dónde instala IBKR el Gateway y sus ajustes, y dónde suele estar un IBC suelto (para importar el login)
if runtime.IS_WIN:
    JTS_DEFAULT, OLD_IBC_DEFAULT = Path(r"C:\Jts"), Path(r"C:\IBC")
else:  # Mac (y Linux): ~/Jts para los ajustes; el programa, en ~/Applications o /Applications (Mac)
    JTS_DEFAULT, OLD_IBC_DEFAULT = Path.home() / "Jts", Path.home() / "ibc"
JTS = Path(os.environ.get("SIGNA_JTS_DIR", JTS_DEFAULT))
OLD_IBC = Path(os.environ.get("SIGNA_IBC_DIR", OLD_IBC_DEFAULT))
STATE = HOME / "gateway"
ENTRY = "ibcalpha.ibc.IbcGateway"
KEYRING = "Signa IBKR"
# puerto de la API (el de siempre) y del servidor de comandos de IBC; candado del supervisor
ACCOUNTS = {"paper": {"api": 4002, "cmd": 7462, "lock": 47692},
            "live": {"api": 4001, "cmd": 7463, "lock": 47693}}
E_2FA_TIMEOUT, E_LOGIN_TIMEOUT = 1111, 1112  # códigos de salida de IBC (StartIBC.bat)
SECRET_KEYS = ("IbLoginId", "IbPassword", "FIXLoginId", "FIXPassword")


class GatewayError(Exception):
    """Fallo con un texto que se puede enseñar tal cual."""
    friendly = True


# --- instalación del Gateway ----------------------------------------------------------------------
def _version(name: str) -> tuple:
    """«1051», «10.51», «IB Gateway 10.37» -> (10, 51) para ordenar."""
    nums = re.findall(r"\d+", name)
    if len(nums) == 1 and len(nums[0]) >= 3:  # 1051 (Windows)
        return int(nums[0][:2]), int(nums[0][2:])
    return tuple(int(n) for n in nums)


def install() -> Path:
    """Carpeta del IB Gateway instalado más nuevo (la que tiene jars/):
    Windows C:\\Jts\\ibgateway\\<versión> · Mac ~/Applications o /Applications/IB Gateway <versión>."""
    if runtime.IS_WIN:
        places = [JTS / "ibgateway"]
        found = [d for p in places if p.is_dir() for d in p.glob("*") if d.name.isdigit()]
    else:
        places = [Path.home() / "Applications", Path("/Applications"), JTS / "ibgateway"]
        found = [d for p in places if p.is_dir() for d in p.glob("*") if d.name.startswith("IB Gateway")
                 or d.name.isdigit()]
    found = [d for d in found if (d / "jars").is_dir()]
    if not found:
        where = " or ".join(str(p) for p in places)
        raise GatewayError(f"IB Gateway isn't installed (nothing in {where}). Install it from interactivebrokers.com.")
    return max(found, key=lambda d: _version(d.name))


def java(gw: Path) -> Path:
    """El Java que trae el Gateway (IBC lo usa igual que StartIBC.bat / gatewaystartmacos.sh)."""
    exe = "java.exe" if runtime.IS_WIN else "java"
    i4j = gw / ".install4j"
    pref = i4j / "pref_jre.cfg"
    if pref.exists():
        j = Path(pref.read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()) / "bin" / exe
        if j.exists():
            return j
    for j in (gw / "jre" / "bin" / exe, i4j.parent / "jre" / "bin" / exe):
        if j.exists():
            return j
    if not runtime.IS_WIN:  # Mac: dentro del .app del Gateway (…/Contents/PlugIns/jre/Contents/Home/bin/java)
        for j in sorted(gw.rglob(f"Contents/Home/bin/{exe}")):
            return j
    raise GatewayError(f"Can't find the Java that comes with IB Gateway in {gw}")


def classpath(gw: Path) -> str:
    jars = sorted(str(j) for j in (gw / "jars").glob("*.jar"))
    return os.pathsep.join([*jars, str(gw / ".install4j" / "i4jruntime.jar"), str(ibc_jar())])


def ibc_jar() -> Path:
    """IBC.jar COPIADO a la carpeta de datos (un nombre por contenido). Java lo tiene abierto mientras el Gateway
    funciona: si lo cargara de la carpeta de la app, Windows no dejaría actualizar Signa con el Gateway en marcha."""
    import hashlib
    data = IBC_JAR.read_bytes()
    dst = STATE / f"IBC-{hashlib.sha256(data).hexdigest()[:12]}.jar"
    if not dst.exists():
        STATE.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)
    return dst


def vm_options(gw: Path) -> list[str]:
    """ibgateway.vmoptions (sin comentarios) + las opciones de install4j (i4jparams.conf), como StartIBC.bat."""
    opts = []
    f = gw / "ibgateway.vmoptions"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            tok = line.strip().split(" ")[0] if line.strip() else ""
            if tok and not tok.startswith("#"):
                opts.append(tok)
    conf = gw / ".install4j" / "i4jparams.conf"
    if conf.exists():
        m = re.search(r'<variable name="javaOptions" value="([^"]*)"', conf.read_text(encoding="utf-8",
                                                                                         errors="ignore"))
        if m:
            opts = m.group(1).split() + opts
    return opts


def settings_dir(account: str) -> Path:
    d = JTS / account  # los ajustes del Gateway de siempre (puertos de la API, IPs de confianza…)
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- config.ini de IBC (sin credenciales) ---------------------------------------------------------
def _ini(text: str) -> list[tuple[str, str | None]]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9]*)=(.*)$", line)
        out.append((m.group(1), m.group(2)) if m else (line, None))
    return out


def config_text(account: str, base: str, creds: tuple[str, str] | None = None) -> str:
    """config.ini para IBC a partir de `base` (el de la instalación anterior o la plantilla de IBC). Fija modo,
    puerto de la API y el servidor de comandos solo en local. Credenciales SOLO si se pasan `creds`: el supervisor
    las pone justo antes de arrancar y las quita en cuanto el Gateway ha entrado (nunca en la línea de comandos,
    que cualquier programa del PC puede leer en la lista de procesos)."""
    port = ACCOUNTS[account]
    secrets_ = {k: "" for k in SECRET_KEYS}
    if creds:
        secrets_.update(IbLoginId=creds[0], IbPassword=creds[1])
    force = {"TradingMode": account, "OverrideTwsApiPort": str(port["api"]), "CommandServerPort": str(port["cmd"]),
             "BindAddress": "127.0.0.1", "ControlFrom": "", "ReloginAfterSecondFactorAuthenticationTimeout": "yes",
             **secrets_}
    seen, lines = set(), []
    for key, val in _ini(base):
        if val is None:
            lines.append(key)
            continue
        seen.add(key)
        lines.append(f"{key}={force.get(key, val)}")
    lines += [f"{k}={v}" for k, v in force.items() if k not in seen]
    return "\n".join(lines) + "\n"


def write_config(account: str, creds: tuple[str, str] | None = None) -> Path:
    old = OLD_IBC / account / "config.ini"
    base = old if old.exists() else IBC_DIR / "config.default.ini"
    STATE.mkdir(parents=True, exist_ok=True)  # en %APPDATA% del usuario: otros usuarios del PC no lo leen
    f = STATE / f"{account}.ini"
    f.write_text(config_text(account, base.read_text(encoding="utf-8-sig", errors="ignore"), creds),
                 encoding="utf-8")
    return f


# --- credenciales (Administrador de credenciales de Windows) --------------------------------------
def credentials(account: str) -> tuple[str, str] | None:
    import keyring
    raw = keyring.get_password(KEYRING, account)
    if not raw:
        return None
    d = json.loads(raw)
    return d["user"], d["password"]


def save_credentials(account: str, user: str, password: str):
    import keyring
    if not user.strip() or not password:
        raise GatewayError("Enter both your IBKR username and password.")
    keyring.set_password(KEYRING, account, json.dumps({"user": user.strip(), "password": password}))


def forget_credentials(account: str):
    import keyring
    try:
        keyring.delete_password(KEYRING, account)
    except keyring.errors.PasswordDeleteError:
        pass


def import_credentials(account: str) -> str:
    """Copia usuario y contraseña del config.ini de la instalación de IBC anterior al llavero. Devuelve el usuario.
    (Solo lo hace el código al pulsar el botón; nunca se enseñan.)"""
    f = OLD_IBC / account / "config.ini"
    if not f.exists():
        raise GatewayError(f"No IBC settings for {account} in {OLD_IBC}")
    vals = {k: v for k, v in _ini(f.read_text(encoding="utf-8-sig", errors="ignore")) if v is not None}
    if not vals.get("IbLoginId") or not vals.get("IbPassword"):
        raise GatewayError("That IBC file has no username and password saved.")
    save_credentials(account, vals["IbLoginId"], vals["IbPassword"])
    return vals["IbLoginId"]


def masked(user: str) -> str:
    return user[:2] + "•" * max(len(user) - 2, 3)


# --- línea de Java y bucle de StartIBC.bat ---------------------------------------------------------
def new_session() -> str:
    return f"{random.randint(0, 32767)}{random.randint(0, 32767)}"


def restart_option(settings: Path) -> tuple[str | None, bool]:
    """GetAutoRestartOption de StartIBC.bat: el Gateway deja settings/<carpeta>/autorestart al reiniciarse solo.
    (opción -Drestart, ¿hay que relanzar?). Varios ficheros = no se sabe cuál: se borran y login completo."""
    found = [f for f in settings.glob("*/autorestart") if f.is_file()]
    if len(found) == 1:
        return f"-Drestart={found[0].parent.name}", True
    for f in found:
        f.unlink(missing_ok=True)
    return None, bool(found)


def command_line(account: str, gw: Path, config: Path, session: str, restart: str | None) -> list[str]:
    """Como StartIBC.bat SIN usuario/contraseña en la línea (los lee IBC de su config.ini): config y modo."""
    settings = settings_dir(account)
    opts = vm_options(gw) + [
        "-Dtwslaunch.autoupdate.serviceImpl=com.ib.tws.twslaunch.install4j.Install4jAutoUpdateService",
        "-Dchannel=latest", "-Dexe4j.isInstall4j=true", "-Dinstall4jType=standalone",
        f"-DjtsConfigDir={settings}", f"-Dibcsessionid={session}"]
    return [str(java(gw)), "-cp", classpath(gw), *opts, *([restart] if restart else []), ENTRY, str(config),
            account]


def next_step(code: int, settings: Path, session: str, twofa_restart: bool = True) -> str | None:
    """Qué hacer cuando la JVM termina (Handle IBC exit conditions de StartIBC.bat): 'restart' (con login),
    'autorestart' (sin login), 'cold' o None (se acabó)."""
    if code == E_2FA_TIMEOUT:
        return "restart" if twofa_restart else None
    if code == E_LOGIN_TIMEOUT:
        return "restart"
    _opt, needed = restart_option(settings)
    if needed:
        pause = settings / f"PAUSE{session}"
        if pause.exists():
            pause.unlink(missing_ok=True)
            return None
        return "autorestart"
    cold = settings / f"COLDRESTART{session}"
    if cold.exists():
        cold.unlink(missing_ok=True)
        return "cold"
    return None


# --- estado y control ------------------------------------------------------------------------------
def port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def processes(account: str) -> list:
    """Procesos Java de IBC/Gateway de esa cuenta (los de Signa y los de una instalación de IBC suelta). Se
    reconocen por la clase de IBC y la cuenta; la línea de comandos lleva la contraseña: no se guarda ni enseña."""
    import psutil
    out = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            cmd = p.info["cmdline"] or []
        except (psutil.Error, KeyError):
            continue
        if ENTRY in cmd and (cmd[-1:] == [account] or any(c.endswith(f"\\{account}\\config.ini") or
                                                          c.endswith(f"\\{account}.ini") for c in cmd)):
            out.append(p)
    return out


def supervisor_running(account: str) -> bool:
    from ..core.logs import is_running
    return is_running(ACCOUNTS[account]["lock"])


def status(account: str) -> dict:
    procs = processes(account)
    signa = supervisor_running(account)
    return {"api": port_open(ACCOUNTS[account]["api"]), "running": bool(procs) or signa,
            "managed": "signa" if signa else ("external" if procs else None),
            "control": port_open(ACCOUNTS[account]["cmd"]), "credentials": _has_credentials(account)}


def _has_credentials(account: str) -> bool:
    try:
        return credentials(account) is not None
    except Exception:
        return False


def send_command(account: str, cmd: str, timeout: float = 5) -> str:
    """Servidor de comandos de IBC (solo 127.0.0.1): STOP, RESTART, RECONNECTACCOUNT…"""
    with socket.create_connection(("127.0.0.1", ACCOUNTS[account]["cmd"]), timeout=timeout) as s:
        s.sendall(f"{cmd}\n".encode())
        try:
            reply = s.recv(1024).decode(errors="ignore").strip()
        except OSError:
            reply = ""
        try:
            s.sendall(b"EXIT\n")
        except OSError:
            pass
    return reply


def start(account: str, cmd: list[str], cwd: str):
    """Arranca el supervisor de esa cuenta (que lanza el Gateway con IBC y lo mantiene vivo)."""
    if account not in ACCOUNTS:
        raise GatewayError(f"unknown account {account!r}")
    if processes(account) or supervisor_running(account):
        raise GatewayError(f"The {account} Gateway is already running.")
    if credentials(account) is None:
        raise GatewayError("Save your IBKR username and password first.")
    install()  # que falle aquí, con un mensaje claro, y no dentro del supervisor
    runtime.spawn(cmd, cwd)  # cmd: runtime.module_cmd("signa.gateway", cuenta)


def stop(account: str, wait: float = 60) -> str:
    """Cierra el Gateway de esa cuenta. El de Signa, con STOP de IBC (limpio); uno de fuera (sin servidor de
    comandos), terminando su proceso. Devuelve cómo se hizo."""
    import psutil
    how = "none"
    if port_open(ACCOUNTS[account]["cmd"]):
        send_command(account, "STOP")
        how = "stop"
    procs = processes(account)
    if how == "stop":
        _gone, procs = psutil.wait_procs(procs, timeout=wait)
    for p in procs:  # de fuera, o no respondió al STOP
        try:
            for c in p.children(recursive=True):
                c.kill()
            p.kill()
            how = "kill" if how == "none" else "stop+kill"
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)
    return how
