"""La TARJETA DE PRUEBA del bot (Telegram › Send test): una call FALSA con la misma pinta y los mismos botones que una
tarjeta de verdad del modo automático (plegados tras «Show buttons»), para que quien prueba el bot vea cómo funciona.
Los botones responden de verdad, pero NO operan: no hay orden ni IBKR, solo cambia la tarjeta. Los atiende el
ejecutor de este PC sin pasar por el servidor (sus callback_data empiezan por «x»).

Puro: estado -> (texto, botones). El ejecutor guarda el estado de cada tarjeta por su message_id."""
import html
import random
from datetime import datetime

PREFIX = "x"  # callback_data «x…:0»: de la tarjeta de prueba (el ejecutor no lo manda al servidor)
SL_PCTS = (20, 30, 40, 50)


def new(provider: str) -> dict:
    return {"provider": provider, "contract": "SPY 600C", "his": 0.95, "entry": 1.00, "qty": 2, "held": 2,
            "sl": 0.40, "bid": 1.00, "realized": 0.0, "open": False, "menu": None, "n": 2}


def is_test(action: str) -> bool:
    return action.startswith(PREFIX)


def _b(text: str, action: str) -> dict:
    return {"text": text, "callback_data": f"{PREFIX}{action}:0"}


def render(st: dict, note: str = "") -> tuple[str, list]:
    e = st["entry"]
    head = ("🧪 <b>TEST CARD</b> · a fake call to show how Signa's cards work. The buttons respond, but nothing "
            "is sent to IBKR.\n\n"
            f"⚡ <b>{html.escape(st['provider'].upper())} AUTO · BOUGHT</b>\n<b>{st['contract']}</b> (test)\n"
            f"{st['qty']} contracts @ <b>{e:.2f}</b>  <i>(his {st['his']:.2f}, {e / st['his'] - 1:+.0%})</i>\n"
            f"🛑 SL {0.40:.2f} (−60%)")
    if note:
        head = f"{note}\n\n{head}"
    if not st["open"]:
        return head, [[_b("🔘 Show buttons", "shw")]]
    bid = st["bid"]
    hide = [[_b("🔼 Hide buttons", "hid")]]
    if not st["held"]:  # cerrada: volver a comprar (como las de verdad)
        lines = [f"✅ Closed · {st['realized']:+,.0f}$ (test)",
                 f"🟢 <b>Buy again {st['contract']}?</b> {st['n']} at market", f"Ask <b>{bid + 0.02:.2f}</b> · bid "
                 f"{bid:.2f}"]
        return f"{head}\n\n" + "\n".join(lines), [[_b("➖", "rbm"), _b(f"🟢 Buy {st['n']} at market", "rbb"),
                                                    _b("➕", "rbp")], [_b("🔄 Refresh", "srf")]] + hide
    if st["menu"] == "buy":
        return (f"{head}\n\n🟢 <b>Buy more</b> at market: how many?",
                [[_b("➖", "bmm"), _b(f"🟢 Buy {st['n']} more at market", "bmb"), _b("➕", "bmp")],
                 [_b("⬅️ Back", "bmx")]])
    lines = []
    lines.append(f"📍 You hold <b>{st['held']}</b> @ {e:.2f}")
    if st["sl"]:
        lines.append(f"🛑 Stop now {st['sl']:.2f}")
    lines.append(f"💵 Bid <b>{bid:.2f}</b> → <b>{bid / e - 1:+.0%}</b> ({(bid - e) * 100 * st['held']:+.0f}$)"
                 f" · ask {bid + 0.02:.2f}")
    lines.append(f"🕒 <i>{datetime.now():%H:%M:%S}</i>")
    if st["menu"] == "sl":
        lines.insert(-1, f"🛑 <b>Choose your stop loss</b> (from your entry {e:.2f}):")
        btns = [_b(f"−{pc}% · {e * (1 - pc / 100):.2f}", f"sl{pc}") for pc in SL_PCTS if e * (1 - pc / 100) < bid]
        rows = [btns[i:i + 2] for i in range(0, len(btns), 2)] + [[_b("⬅️ Back", "slb")]]
    else:
        rows = [[_b("🔴 Sell all (market)", "ssa"), _b("½ Sell half", "ssh")],
                [_b("🛡 SL → BE", "sbe"), _b("🛑 Stop loss", "slm")], [_b("🟢 Buy more", "bmo")],
                [_b("🔄 Refresh", "srf")]]
    return f"{head}\n\n" + "\n".join(lines), rows + hide


def press(st: dict, action: str, rnd: random.Random | None = None) -> str:
    """Aplica el botón a la tarjeta (sin operar) y devuelve la nota que sale arriba."""
    rnd = rnd or random
    a = action[len(PREFIX):] if is_test(action) else action
    # el precio se mueve un poco en cada pulsación, como el de verdad
    st["bid"] = round(max(0.05, st["bid"] * (1 + rnd.uniform(-0.06, 0.08))), 2)
    sell = {"ssa": st["held"], "ssh": max(1, st["held"] // 2)}.get(a)
    if a in ("shw", "hid"):
        st["open"], st["menu"] = a == "shw", None
        return ""
    st["open"] = True
    if a == "srf":
        return ""
    if a in ("rbm", "rbp", "bmm", "bmp"):
        st["n"] = max(1, st["n"] + (1 if a.endswith("p") else -1))
        return ""
    if a in ("rbb", "bmb"):  # comprar (otra vez, o más): a «ask», en la tarjeta
        ask, n, h = st["bid"] + 0.02, st["n"], st["held"]
        st["entry"] = round((st["entry"] * h + ask * n) / (h + n), 2) if h else round(ask, 2)
        st["held"] += n
        st["qty"] = st["held"]
        st["menu"] = None
        return f"🧪 Test: bought {n}{' more' if h else ''} at {ask:.2f} · nothing was sent"
    if a == "bmo":
        st["menu"], st["n"] = "buy", 1
        return ""
    if a == "bmx":
        st["menu"] = None
        return ""
    if not st["held"]:
        return "ℹ️ That position is already closed (test)."
    if sell:
        pnl = (st["bid"] - st["entry"]) * 100 * sell
        st["n"] = st["held"]  # si lo vende todo, «Buy again» ofrece lo mismo que tenía
        st["held"] -= sell
        st["realized"] += pnl
        return f"🧪 Test: sold {sell} at {st['bid']:.2f} ({st['bid'] / st['entry'] - 1:+.0%}, {pnl:+.0f}$) · nothing " \
               "was sent"
    if a == "sbe":
        st["sl"] = st["entry"]
        return f"🧪 Test: stop loss → {st['entry']:.2f} (break-even) · nothing was sent"
    if a == "slm":
        st["menu"] = "sl"
        return ""
    if a == "slb":
        st["menu"] = None
        return ""
    if a.startswith("sl") and a[2:].isdigit():
        st["menu"] = None
        st["sl"] = round(st["entry"] * (1 - int(a[2:]) / 100), 2)
        return f"🧪 Test: stop loss → {st['sl']:.2f} (−{a[2:]}%) · nothing was sent"
    return ""
