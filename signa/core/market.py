"""Horario del mercado de EE. UU. (sesión regular), para avisos de la app. Sin festivos: es solo para avisar."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def market_open(now: datetime | None = None) -> bool:
    now = (now or datetime.now(NY)).astimezone(NY)
    return now.weekday() < 5 and time(9, 30) <= now.time() < time(16, 0)
