"""Lo que la app hace en ESTE PC, sin el servidor: leer el estado que publica el ejecutor, arrancarlo, y conectar /
desconectar / reiniciar los IB Gateway con el IBC que lleva Signa (signa.gateway). Parar o reiniciar un Gateway
corta a TODO lo conectado a él (también el copiador): la pantalla lo pide confirmar."""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import gateway
from ..core import runtime
from ..core.logs import HOME, is_running

EXECUTOR_PORT, AGENT_PORT = 47691, 47690
STATUS = HOME / "executor_status.json"
STALE = 40  # s: un estado más viejo es de un ejecutor que ya no está


def module_running(module: str, port: int) -> bool:
    """¿Corre ya `python -m <module>`? Por su candado o, si es de antes del candado, por su proceso."""
    if is_running(port):
        return True
    import psutil
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = p.info["cmdline"] or []
        except (psutil.Error, KeyError):
            continue
        if module in cmd and "-m" in cmd:
            return True
    return False


def executor_running() -> bool:
    return is_running(EXECUTOR_PORT)


def read_status() -> dict | None:
    """Último estado del ejecutor, o None si no lo hay o es viejo."""
    try:
        st = json.loads(STATUS.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(st["at"])).total_seconds()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return st if age < STALE else None


def _spawn(module: str):
    runtime.spawn(runtime.module_cmd(module))


def start_executor():
    _spawn("signa.executor")


def start_services() -> list[str]:
    """Al abrir Signa (también al iniciar Windows): agente, ejecutor y los Gateway con login guardado que no estén
    ya en marcha. Devuelve lo arrancado y los avisos. BLOQUEA (escanea procesos): la app lo llama en un hilo."""
    done = []
    pieces = [("signa.executor", EXECUTOR_PORT, "executor")]
    # el agente lee las notificaciones de WINDOWS (la fuente de Hacedor): solo en el PC configurado como fuente
    if runtime.IS_WIN and (HOME / "agent.json").exists():
        pieces.insert(0, ("signa.agent", AGENT_PORT, "agent"))
    for module, port, what in pieces:
        if not module_running(module, port):
            _spawn(module)
            done.append(what)
    for account in gateway.ACCOUNTS:
        try:
            if gateway.credentials(account) and not gateway.status(account)["running"]:
                start_gateway(account)
                done.append(f"{account} Gateway")
        except Exception as e:  # un Gateway que no arranca no impide lo demás
            done.append(f"{account} Gateway failed: {e}")
    return done


def start_gateway(account: str):
    """Arranca el Gateway de esa cuenta con el IBC que lleva Signa (supervisor signa.gateway)."""
    gateway.start(account, runtime.module_cmd("signa.gateway", account), str(runtime.ROOT))


def stop_gateway(account: str) -> str:
    return gateway.stop(account)


def restart_gateway(account: str) -> str:
    """El de Signa: RESTART de IBC (sin volver a pedir login ni IB Key). Uno de fuera o sin control: cerrarlo y
    arrancarlo desde Signa. BLOQUEA hasta un minuto: la app lo llama en un hilo."""
    st = gateway.status(account)
    if st["managed"] == "signa" and st["control"]:
        gateway.send_command(account, "RESTART")
        return "restart"
    gateway.stop(account)
    for _ in range(60):  # que suelte el puerto de la API antes de arrancar otro
        if not gateway.port_open(gateway.ACCOUNTS[account]["api"]):
            break
        time.sleep(1)
    start_gateway(account)
    return "stop+start"


# --- tu copiador local (solo para comparar en Stats; solo lectura) ----------------------------------
COPIER_DIR = Path(os.environ.get("TRADE_COPIER", Path.home() / "trade-copier"))
COPIER_DBS = {"piposavage": "pipo_auto_copier.db", "imperio": "pipo_auto_imperio_imperio.db",
              "hacedor": "pipo_auto_imperio_optex.db"}  # proveedor -> base del motor del copiador


def copier_stats(period: str) -> dict | None:
    """Métricas del copiador de este PC por proveedor, del periodo; None si aquí no hay copiador."""
    from ..core import stats
    found = {p: COPIER_DIR / f for p, f in COPIER_DBS.items() if (COPIER_DIR / f).exists()}
    if not found:
        return None
    trades = [t for p, path in found.items() for t in stats.engine_trades(path, p)]
    return stats.by(stats.in_period(trades, period), lambda t: t["provider_id"])
