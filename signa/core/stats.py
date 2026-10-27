"""ANALÍTICA de operaciones (pantalla Stats). Lógica pura y compartida: el servidor la usa con las operaciones de Signa
(signa_trades) y la app con las de TU copiador local (sus bases sqlite, mismo motor), así ambas se miden igual.

Una operación = {provider_id, status (open|closed|skipped|error), pnl, opened_at (ISO con zona), exit_reason}."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
PERIODS = {"7d": 7, "30d": 30, "all": None}


def _when(ts: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(ts)
        return t if t.tzinfo else t.replace(tzinfo=NY)
    except (TypeError, ValueError):
        return None


def in_period(trades: list[dict], period: str, now: datetime | None = None) -> list[dict]:
    days = PERIODS.get(period)
    if days is None:
        return list(trades)
    since = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    return [t for t in trades if (w := _when(t.get("opened_at"))) and w >= since]


def summarize(trades: list[dict]) -> dict:
    """Métricas de un conjunto de operaciones (las cerradas cuentan para acierto/medias; el P&L incluye lo vendido
    de las abiertas)."""
    closed = [t for t in trades if t["status"] == "closed"]
    bought = [t for t in trades if t["status"] in ("open", "closed")]
    pnls = [float(t.get("pnl") or 0) for t in closed]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    total = round(sum(float(t.get("pnl") or 0) for t in bought), 2)
    # peor racha: la mayor caída del P&L acumulado (operaciones cerradas por orden de apertura)
    peak = cum = dd = 0.0
    for t in sorted(closed, key=lambda t: t.get("opened_at") or ""):
        cum += float(t.get("pnl") or 0)
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {
        "bought": len(bought), "closed": len(closed), "open": len(bought) - len(closed),
        "skipped": sum(1 for t in trades if t["status"] == "skipped"),
        "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else (None if not wins else float("inf")),
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "pnl": total, "max_drawdown": round(dd, 2),
        "best": round(max(pnls), 2) if pnls else None, "worst": round(min(pnls), 2) if pnls else None,
    }


def by(trades: list[dict], key) -> dict[str, dict]:
    """Métricas agrupadas por key(t) (proveedor, hora, motivo…)."""
    groups: dict[str, list] = {}
    for t in trades:
        k = key(t)
        if k is not None:
            groups.setdefault(k, []).append(t)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def hour_ny(t: dict) -> str | None:
    w = _when(t.get("opened_at"))
    return w.astimezone(NY).strftime("%H:00") if w and t["status"] in ("open", "closed") else None


def exit_kind(t: dict) -> str | None:
    """El motivo de salida, agrupado (sin los números: «SL −50%» y «SL −30%» son lo mismo)."""
    if t["status"] != "closed":
        return None
    r = (t.get("exit_reason") or "").lower()
    for word, name in (("ladder", "Ladder stop"), ("runner", "Runner TP"), ("tp", "Take profit"),
                       ("break-even", "Break-even"), ("end of day", "End of day"), ("warning", "His warning"),
                       ("manage", "His manage"), ("exit", "His exit"), ("your sell", "You sold"),
                       ("restart", "Lost track"), ("sl", "Stop loss")):
        if word in r:
            return name
    return "Other"


def report(trades: list[dict], period: str = "30d") -> dict:
    """Lo que pinta la pantalla Stats: total, por proveedor, por hora de entrada (ET) y por motivo de salida."""
    ts = in_period(trades, period)
    return {"period": period, "total": summarize(ts), "providers": by(ts, lambda t: t.get("provider_id")),
            "by_hour": by(ts, hour_ny), "by_exit": by(ts, exit_kind),
            "by_provider_exit": {p: by([t for t in ts if t.get("provider_id") == p], exit_kind)
                                 for p in {t.get("provider_id") for t in ts}}}


# --- las operaciones de un motor del copiador (su sqlite), en el mismo formato -----------------------
def engine_trades(path: Path, provider: str) -> list[dict]:
    """Las operaciones REALES (variante «instant») de una base del motor del copiador, SOLO LECTURA."""
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    out = []
    try:
        for tid, at, status, reason, realized in db.execute(
                "SELECT id, at, status, reason, realized FROM trades WHERE variant='instant'"):
            last = db.execute("SELECT reason FROM sales WHERE trade_id=? ORDER BY at DESC LIMIT 1", (tid,)).fetchone()
            st = {"stale": "closed"}.get(status, status if status in ("open", "closed", "skipped") else "error")
            out.append({"provider_id": provider, "status": st, "pnl": realized or 0, "opened_at": at,
                        "exit_reason": (last[0] if last else None) or reason})
    except sqlite3.Error:
        return out
    finally:
        db.close()
    return out
