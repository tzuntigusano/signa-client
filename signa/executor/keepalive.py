"""Que la conexión con IBKR no se quede MUERTA sin saberlo (Paula, 02/11: tras dormir el Mac, la conexión con su IB
Gateway seguía «abierta» pero no contestaba nada; cada compra esperaba hasta que el servidor se rendía a los 45 s).

  watchdog   cada PING_EVERY s, a cada cuenta conectada una pregunta trivial (la hora del servidor de IBKR, que el
             Gateway contesta al momento hasta en paper). Si falla PING_STRIKES veces seguidas, la conexión está
             muerta: se cierra y se reconecta. NO pone límite a las órdenes ni a los precios (en paper tardan).
  Awake      en Mac, durante el horario del mercado (con margen), que el Mac no se duerma por inactividad mientras
             Signa esté abierta (`caffeinate -i`, como el propio macOS). Fuera de horario, nada. En WINDOWS (Ivan
             20/11, para dejar el PC solo en vacaciones) lo mismo con SetThreadExecutionState: solo frena la
             suspensión POR INACTIVIDAD; suspenderlo a propósito (una tarea programada, el botón) sigue funcionando.
"""
import asyncio
import subprocess
import sys
from datetime import datetime, time

from signa.core.market import NY

PING_EVERY = 15      # s (probado contra un Gateway paper: espaciadas, 12/12 al instante; dos SEGUIDAS sin pausa, IB
#                      ignora la segunda: nunca preguntar más a menudo)
PING_TIMEOUT = 10    # s: la hora del servidor la contesta el Gateway al instante; 10 s sin nada = muerta
PING_STRIKES = 2     # fallos seguidos antes de reconectar (un tropiezo suelto no corta nada)
AWAKE_FROM, AWAKE_TO = time(9, 0), time(16, 30)  # NY, días laborables: apertura y cierre con margen


async def ping(broker, timeout: float = PING_TIMEOUT) -> bool:
    try:
        await asyncio.wait_for(broker.ib.reqCurrentTimeAsync(), timeout)
        return True
    except Exception:
        return False


async def watchdog(ex, log, every: float = PING_EVERY, strikes: int = PING_STRIKES):
    """ex: el Executor (sus brokers, reconnect y reconnecting)."""
    misses: dict[str, int] = {}
    while True:
        await asyncio.sleep(every)
        for name, b in list(ex.brokers.items()):
            if not b.ib.isConnected() or name in ex.reconnecting:
                misses.pop(name, None)
                continue
            if await ping(b):
                misses.pop(name, None)
                continue
            misses[name] = misses.get(name, 0) + 1
            if misses[name] < strikes:
                continue
            misses.pop(name, None)
            log(f"IBKR {name}: IB Gateway isn't answering (connection dead, e.g. after the computer slept): "
                "reconnecting")
            try:
                b.ib.disconnect()
            except Exception:
                pass
            await ex.reconnect(name, b)


def awake_hours(now: datetime | None = None) -> bool:
    now = (now or datetime.now(NY)).astimezone(NY)
    return now.weekday() < 5 and AWAKE_FROM <= now.time() < AWAKE_TO


ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001


def _win_state(flags: int) -> int:
    import ctypes
    return ctypes.windll.kernel32.SetThreadExecutionState(flags)


class Awake:
    """Mac: `caffeinate -i -w <pid de Signa>` mientras sea horario de mercado (se acaba solo si Signa se cierra).
    Windows: SetThreadExecutionState(ES_SYSTEM_REQUIRED) desde el hilo del bucle (dura mientras viva el proceso)."""

    def __init__(self, log, platform: str = sys.platform, spawn=subprocess.Popen, win_state=_win_state):
        self.log, self.on, self.spawn = log, platform in ("darwin", "win32"), spawn
        self.win, self.win_state, self.win_awake = platform == "win32", win_state, False
        self.proc = None

    def tick(self, now: datetime | None = None, pid: int | None = None):
        if not self.on:
            return
        want = awake_hours(now)
        if self.win:
            if want != self.win_awake:
                try:
                    ok = self.win_state(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if want else 0))
                except Exception as e:
                    ok = 0
                    self.log(f"keep awake: {type(e).__name__}: {e}")
                if not ok:
                    self.on = False
                    return
                self.win_awake = want
                self.log("market hours: keeping this PC awake while Signa is open" if want
                         else "market closed: this PC can sleep again")
            return
        alive = self.proc is not None and self.proc.poll() is None
        if want and not alive:
            import os
            try:
                self.proc = self.spawn(["caffeinate", "-i", "-w", str(pid or os.getpid())])
                self.log("market hours: keeping this Mac awake while Signa is open")
            except OSError as e:
                self.log(f"caffeinate: {e}")
                self.on = False
        elif not want and alive:
            self.proc.terminate()
            self.proc = None
            self.log("market closed: this Mac can sleep again")

    async def run(self, every: float = 60):
        while self.on:
            self.tick()
            await asyncio.sleep(every)
