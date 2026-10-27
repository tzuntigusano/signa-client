"""MODO DEMO de la app (python -m signa.app --demo): operaciones de EJEMPLO para ver las pantallas llenas, solo en
pantalla (nada va al servidor). Con todos los estados y motivos que da el motor del copiador, su cronología,
un precio simulado para el drawer «Trade» y lo que «haría» cada botón (sin mandar nada). También lo usan los
tests y las capturas."""


def _t(n, prov, acct, contract, qty, held, entry, exit_px, stop, status, reason, pnl, hm, days=0, closed_hm=None):
    from datetime import datetime, timedelta
    d = datetime.now().astimezone().replace(hour=hm[0], minute=hm[1], second=0, microsecond=0) - timedelta(days=days)
    closed = d.replace(hour=closed_hm[0], minute=closed_hm[1]) if closed_hm else None
    return {"order_ref": f"u:{prov}:{n}", "provider_id": prov, "account": acct, "contract": contract, "qty": qty,
            "held": held, "entry_px": entry, "exit_px": exit_px, "stop_px": stop, "status": status,
            "exit_reason": reason, "pnl": pnl, "opened_at": d.isoformat(),
            "closed_at": closed.isoformat() if closed else None}


# como las espeja el servidor desde los motores (signa_trades): TODOS los estados y motivos que da el motor del
# copiador (auto_engine): abiertas (enteras, con TP1 ya cobrado, en escalera), cerradas por TP1 / runner / SL /
# break-even / escalera / su salida / su aviso / su manage / fin de día / reinicio, no compradas (persecución,
# cara, sin precio, fuera de hora, ya dentro, reentrada, error del plan) y error
TRADES = [
    _t(1, "hacedor", "paper", "SPY 769C 20261027", 2, 2, 0.46, None, 0.23, "open", None, 0, (17, 31)),
    _t(2, "imperio", "paper", "QQQ 755C 20261027", 2, 1, 1.13, 2.27, 1.13, "open", "TP1 +100%", 114.0, (17, 2)),
    _t(3, "piposavage", "paper", "SPX 7610P 20261027", 1, 1, 0.70, None, 0.84, "open", "ladder", 0, (16, 48)),
    _t(4, "hacedor", "live", "SPY 768C 20261027", 2, 0, 0.44, 0.88, None, "closed", "TP1 +100%", 88.0, (16, 30),
       closed_hm=(16, 52)),
    _t(5, "hacedor", "paper", "QQQ 750P 20261027", 2, 0, 0.62, 2.48, None, "closed", "runner TP +300%", 372.0,
       (16, 5), closed_hm=(18, 40)),
    _t(6, "imperio", "paper", "AVGO 345C 20261030", 1, 0, 4.65, 2.32, None, "closed", "SL −50%", -233.0, (15, 9),
       closed_hm=(15, 47)),
    _t(7, "piposavage", "paper", "SPX 7620C 20261027", 1, 0, 1.10, 1.10, None, "closed", "stop at break-even", 0.0,
       (15, 41), closed_hm=(16, 2)),
    _t(8, "piposavage", "paper", "SPX 7600P 20261027", 1, 0, 0.85, 1.19, None, "closed", "ladder stop (+40%)", 34.0,
       (15, 35), closed_hm=(16, 20)),
    _t(9, "hacedor", "paper", "SPY 767P 20261027", 2, 0, 0.51, 0.74, None, "closed", "his exit", 46.0, (15, 50),
       closed_hm=(16, 3)),
    _t(10, "imperio", "paper", "SOFI 17C 20261106", 2, 0, 0.27, 0.22, None, "closed", "his warning", -10.0,
       (14, 24), closed_hm=(15, 1)),
    _t(11, "piposavage", "paper", "SPX 7630C 20261027", 1, 0, 0.95, 1.71, None, "closed", "his manage", 76.0,
       (15, 58), closed_hm=(16, 31)),
    _t(12, "imperio", "paper", "CBRS 120C 20261031", 1, 0, 2.95, 3.70, None, "closed", "end of day", 75.0, (16, 4),
       closed_hm=(21, 55)),
    _t(13, "hacedor", "paper", "SPY 766C 20261026", 2, 0, 0.58, None, None, "closed", "left open by a restart", 0.0,
       (16, 12), days=1),
    _t(14, "hacedor", "paper", "SPY 769C 20261027", 2, 0, None, None, None, "skipped",
       "chasing: ask 0.62 = +41% over his 0.44", None, (15, 0)),
    _t(15, "imperio", "paper", "NVDA 210C 20261031", 2, 0, None, None, None, "skipped",
       "too expensive: ask 6.40 = 640$ per contract", None, (14, 51)),
    _t(16, "piposavage", "paper", "SPX 7650C 20261027", 1, 0, None, None, None, "skipped", "no live quote", None,
       (14, 40)),
    _t(17, "imperio", "paper", "TSLA 460C 20261031", 2, 0, None, None, None, "skipped", "outside regular session",
       None, (13, 10)),
    _t(18, "hacedor", "paper", "SPY 768C 20261027", 2, 0, None, None, None, "skipped",
       "already holding that contract", None, (16, 31)),
    _t(19, "piposavage", "paper", "SPX 7610P 20261027", 1, 0, None, None, None, "skipped",
       "re-entry: same strike still open", None, (16, 55)),
    _t(20, "imperio", "paper", "BANG 768C 20261027", 2, 0, None, None, None, "skipped",
       "plan/quote: unknown contract", None, (14, 48)),
    _t(21, "hacedor", "live", "QQQ 756C 20261027", 2, 0, None, None, None, "error", "order rejected: 201 no funds",
       None, (17, 15)),
    _t(22, "hacedor", "paper", "SPY 765P 20261024", 2, 0, 0.70, 1.40, None, "closed", "TP1 +100%", 140.0, (15, 33),
       days=3, closed_hm=(15, 58)),
    _t(23, "imperio", "paper", "AMD 250C 20261024", 1, 0, 3.10, 1.55, None, "closed", "SL −50%", -155.0, (16, 0),
       days=3, closed_hm=(17, 20)),
    _t(24, "piposavage", "paper", "SPX 7580C 20261017", 1, 0, 1.30, 2.60, None, "closed", "TP1 +100%", 130.0,
       (15, 45), days=10, closed_hm=(16, 10)),
]

# lo que diría cada proveedor al cerrar (para el detalle de ejemplo)
_EXIT_SAYS = {"his exit": "Out, taking profits here", "his warning": "Careful, pull back if you're in",
              "his manage": "manageeee", "TP1 +100%": "Paying nicely 🔥", "runner TP +300%": "Runners still going 🚀"}


def demo_detail(t: dict) -> list[dict]:
    """Cronología de EJEMPLO de una operación (modo --demo): las mismas piezas que da el servidor de verdad —
    mensajes del proveedor (call, respuesta, charla) y lo que hizo el motor (lectura, órdenes, SL, ventas,
    tarjetas)—, montadas con los datos del trade."""
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(t["opened_at"])
    sym, strike = (t["contract"].split() + ["", ""])[:2]
    entry, qty, reason = t.get("entry_px"), t.get("qty") or 1, t.get("exit_reason") or ""
    his = round(entry * 0.97, 2) if entry else 0.44

    def at(sec: int) -> str:
        return (t0 + timedelta(seconds=sec)).isoformat()

    ev = [{"at": at(-2), "kind": "provider", "link": "entry", "text": f"{sym} {strike} {his:g}"}]
    if t["status"] == "skipped":
        ev.append({"at": at(0), "kind": "skip", "text": f"Not bought: {reason}"})
        if reason.startswith("chasing"):
            ev.append({"at": at(1800), "kind": "skip",
                       "text": f"Watched after skipping: 312 prices, lowest ask {his + 0.05:.2f} (his {his:g})"})
        ev.append({"at": at(240), "kind": "provider", "link": "chat", "text": "Loading more here"})
        return ev
    if t["status"] == "error":
        return ev + [{"at": at(0), "kind": "plan", "text": f"Entry {qty} at market (his {his:g})"},
                     {"at": at(1), "kind": "order", "text": f"BUY {qty} {sym} {strike} · market"},
                     {"at": at(2), "kind": "error", "text": f"BUY {qty} {sym} {strike} · market failed: {reason}"}]
    sl, tp1 = round(entry * 0.5, 2), round(entry * 2, 2)
    ev += [{"at": at(0), "kind": "plan", "text": f"Entry {qty} at {entry:.2f} (his {his:g}) · SL {sl:.2f} · TP1 "
            f"{tp1:.2f}"},
           {"at": at(1), "kind": "order", "text": f"BUY {qty} {sym} {strike} · market"},
           {"at": at(2), "kind": "result", "text": f"✅ #1 BUY {qty} {sym} {strike} · MKT → filled at {entry:.2f}"},
           {"at": at(2), "kind": "fill", "text": f"BUY filled: {qty} at {entry}"},
           {"at": at(3), "kind": "stop", "text": f"SL set at {sl:.2f}"},
           {"at": at(3), "kind": "result", "text": f"🛑 #2 STOP SELL at {sl:.2f} placed"},
           {"at": at(4), "kind": "tp", "text": f"TP set at {tp1:.2f}"},
           {"at": at(5), "kind": "card", "text": f"⚡ AUTO · bought {qty} {sym} {strike} at {entry:.2f} · SL {sl:.2f}"
            f" · TP1 {tp1:.2f}"},
           {"at": at(420), "kind": "provider", "link": "chat", "text": "Holding, watching the 9 EMA"}]
    if reason == "ladder" or reason.startswith("ladder stop"):
        for i, step in enumerate((0.0, 0.2, 0.4)):
            ev.append({"at": at(900 + 600 * i), "kind": "stop", "text": f"SL set at {entry * (1 + step):.2f}"})
            ev.append({"at": at(901 + 600 * i), "kind": "card",
                       "text": f"🪜 Ladder · SL raised to {entry * (1 + step):.2f} ({step:+.0%})"})
    if t.get("exit_px"):
        closed = datetime.fromisoformat(t["closed_at"]) if t.get("closed_at") else t0 + timedelta(minutes=20)
        sec = int((closed - t0).total_seconds())
        if says := _EXIT_SAYS.get(reason):
            ev.append({"at": at(sec - 3), "kind": "provider", "link": "reply", "text": says})
        sold = qty - (t.get("held") or 0)
        ev += [{"at": at(sec), "kind": "fill", "text": f"SL filled: {sold} at {t['exit_px']}"} if
               reason.startswith(("SL", "stop", "ladder stop")) else
               {"at": at(sec), "kind": "order", "text": f"SELL {sold} {sym} {strike} · market"},
               {"at": at(sec + 1), "kind": "sale", "text": f"Sold {sold} at {t['exit_px']:.2f} · {reason} · "
                f"{t.get('pnl') or 0:+.2f}"},
               {"at": at(sec + 2), "kind": "card", "text": f"💰 {sym} {strike} sold {sold} at {t['exit_px']:.2f}"
                f" · {reason}"}]
        if t["status"] == "open":  # TP1 cobrado: el resto a break-even
            ev += [{"at": at(sec + 3), "kind": "stop", "text": f"SL set at {entry:.2f}"},
                   {"at": at(sec + 4), "kind": "card", "text": f"🔒 Runner protected at break-even ({entry:.2f})"}]
    return ev


def demo_quote(t: dict, prev: dict | None) -> dict:
    """Precio de EJEMPLO para el drawer «Trade» en --demo: un paseo aleatorio alrededor de la entrada. Mismo formato
    que el que devuelve el servidor (manual.quote)."""
    import random
    entry = t.get("entry_px") or 0.5
    q = dict(prev) if prev else {
        "symbol": t["contract"].split()[0], "entry": t.get("entry_px"), "qty": t.get("qty"), "held": t.get("held"),
        "sl": t.get("stop_px"), "tp1": round(entry * 2, 2) if t["status"] == "open" else None, "tp2": None,
        "tp1_done": 0, "status": t["status"], "realized": t.get("pnl") or 0, "reason": t.get("exit_reason"),
        "account": t.get("account"), "bid": round(entry * 1.04, 2)}
    bid = max(0.01, round(q["bid"] * (1 + random.uniform(-0.025, 0.025)), 2))
    q.update(bid=bid, ask=round(bid + max(0.01, bid * 0.03), 2))
    q["unrealized"] = round((bid - q["entry"]) * 100 * q["held"], 2) if q.get("entry") and q.get("held") else None
    return q


def demo_action(q: dict, action: str, value) -> str:
    """Lo que «haría» cada botón en --demo: cambia la posición de ejemplo y lo dice. No manda NADA."""
    entry, bid = q.get("entry") or 0, q.get("bid") or 0
    v = value if isinstance(value, dict) else {}
    level = (entry if v.get("be") else v["price"] if "price" in v else entry * (1 + v.get("pct", 0) / 100)) \
        if action in ("stop", "tp") else None
    if action == "stop":
        if level >= bid:
            return f"⚠️ The stop ({level:.2f}) would be above the bid ({bid:.2f}): sell instead."
        q.update(sl=round(level, 2), reason="manual")
        return f"🛡 Stop → {level:.2f} ({level / entry - 1:+.0%}) · demo: nothing was sent"
    if action == "tp":
        q.update(tp1=round(level, 2), reason="manual")
        return f"🎯 Take profit → {level:.2f} ({level / entry - 1:+.0%}) · demo: nothing was sent"
    if action in ("stop_remove", "tp_remove"):
        q.update({"sl": None} if action == "stop_remove" else {"tp1": None, "tp2": None}, reason="manual")
        return f"{'Stop loss' if action == 'stop_remove' else 'Take profit'} removed · demo: nothing was sent"
    if action == "sell":
        n = int(value)
        q["realized"] = round((q.get("realized") or 0) + (bid - entry) * 100 * n, 2)
        q["held"] = q["held"] - n
        if not q["held"]:
            q["status"] = "closed"
        return f"✅ Sold {n} at {bid:.2f} · demo: nothing was sent"
    if action == "buy":
        n = v.get("contracts") or int(v.get("amount", 0) // ((q.get("bid") or 1) * 103))
        return f"✅ Bought {n} at {q.get('ask') or 0:.2f} · demo: nothing was sent"
    return "demo"
