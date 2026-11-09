"""REGLAS EDITABLES de la estrategia de un proveedor (Ivan 09/11/2026): Providers › cada interruptor principal lleva
debajo sus reglas, cada una con su casilla y sus números. Lo comparten la app (pinta y valida) y el servidor (valida,
guarda y se lo pasa al motor), así que es la única fuente de los valores por defecto.

Se guarda en signa_subscriptions.rules (migración 009) SOLO lo que el usuario cambia respecto a Signa:
    {"chase": {"lo": 15}, "tp1": {"on": false}}
Lo que no está = lo de Signa, así un cambio de la estrategia llega a quien no lo tocó.

PRIORIDADES (de más a menos; ver tests/test_rules.py y tests/test_rules_engine.py):
  1. Lo que hagas TÚ en una operación (vender, poner SL/TP…) -> la llevas tú (reason='manual'): ninguna regla la toca
     hasta «Apply rules». El cierre del día también la cierra (como siempre).
  2. El interruptor principal apagado (Auto buy / protection / rules) -> sus reglas no actúan solas: tarjeta.
  3. Una regla desmarcada -> esa regla no actúa (las demás, sí).
  4. Los números de la regla. Cambiarlos vale YA también para lo abierto (en la siguiente vuelta del motor), pero un
     stop nunca baja y lo que ya pasó (la venta del TP, su aviso) no se repite.
"""
import datetime as dt
from fractions import Fraction

# id de la regla -> grupo (el interruptor del que cuelga), si viene marcada y sus campos (valor por defecto, tipo,
# mínimo, máximo). Tipos: pct (número), int (entero), frac («2/3»), time («15:55»)
SCHEMA: dict[str, dict] = {
    "chase": {"group": "auto_buy", "on": True,
              "fields": {"lo": (20.0, "pct", 0, 100), "hi": (10.0, "pct", 0, 100)}},
    "mismatch": {"group": "auto_buy", "on": True,
                 "fields": {"below": (50.0, "pct", 1, 99), "above": (80.0, "pct", 1, 500)}},
    # «1 contrato si duda»: apagado desde el 03/11 (siempre sus contratos); quien quiera, lo enciende
    "doubt": {"group": "auto_buy", "on": False, "fields": {"n": (1, "int", 1, 50)}},
    "warn": {"group": "auto_protect", "on": True,
             "fields": {"ref": (100.0, "pct", 1, 1000), "sl": (30.0, "pct", 1, 99)}},
    "tp1": {"group": "auto_rules", "on": True,
            "fields": {"pct": (25.0, "pct", 1, 1000), "frac": ("2/3", "frac", None, None)}},
    "double": {"group": "auto_rules", "on": True,
               "fields": {"pct": (100.0, "pct", 1, 1000), "trail": (30.0, "pct", 1, 99)}},
    "time": {"group": "auto_rules", "on": True,
             "fields": {"hold": (90, "int", 1, 390), "floor": (20.0, "pct", 0, 1000), "trail": (30.0, "pct", 1, 99)}},
    "eod": {"group": "auto_rules", "on": True, "fields": {"at": ("15:55", "time", "09:35", "15:55")}},
}
PROVIDERS = {"hacedor", "test"}  # los que llevan estas reglas (el motor de Hacedor: time_engine.py)
EOD_LAST = dt.time(15, 55)  # el cierre del motor del copiador: después no se puede (el 0DTE vence)


class RuleError(ValueError):
    pass


def has_rules(provider_id: str) -> bool:
    return provider_id in PROVIDERS


def defaults() -> dict:
    return {rid: {"on": r["on"], **{k: f[0] for k, f in r["fields"].items()}} for rid, r in SCHEMA.items()}


def parse_frac(v) -> Fraction:
    try:
        f = Fraction(str(v).strip().replace(" ", ""))
    except (ValueError, ZeroDivisionError):
        raise RuleError("the part to sell must be a fraction like 2/3") from None
    if not 0 < f < 1 or f.denominator > 10:
        raise RuleError("the part to sell must be between 0 and 1, like 1/2 or 2/3")
    return f


def _value(rid: str, key: str, v):
    default, kind, lo, hi = SCHEMA[rid]["fields"][key]
    if kind == "frac":
        f = parse_frac(v)
        return f"{f.numerator}/{f.denominator}"
    if kind == "time":
        try:
            t = dt.time.fromisoformat(str(v).strip())
        except ValueError:
            raise RuleError(f"{rid}.{key} must be a time like 15:55") from None
        if not dt.time.fromisoformat(lo) <= t <= dt.time.fromisoformat(hi):
            raise RuleError(f"{rid}.{key} must be between {lo} and {hi} ET")
        return f"{t:%H:%M}"
    if isinstance(v, bool):
        raise RuleError(f"{rid}.{key} must be a number")
    try:
        num = float(str(v).replace(",", ".").replace("%", "").strip())
    except ValueError:
        raise RuleError(f"{rid}.{key} must be a number") from None
    if kind == "int":
        if num != int(num):
            raise RuleError(f"{rid}.{key} must be a whole number")
        num = int(num)
    if not lo <= num <= hi:
        raise RuleError(f"{rid}.{key} must be between {lo:g} and {hi:g}")
    return num


def normalize(raw: dict | None) -> dict:
    """Lo que manda la app -> lo que se guarda: validado y SOLO lo distinto de Signa (vacío = todo como Signa)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RuleError("rules must be an object")
    base, out = defaults(), {}
    for rid, vals in raw.items():
        if rid not in SCHEMA:
            raise RuleError(f"Unknown rule: {rid}")
        if not isinstance(vals, dict):
            raise RuleError(f"{rid} must be an object")
        diff = {}
        for k, v in vals.items():
            if k == "on":
                if not isinstance(v, bool):
                    raise RuleError(f"{rid}.on must be on or off")
            elif k not in SCHEMA[rid]["fields"]:
                raise RuleError(f"Unknown setting: {rid}.{k}")
            else:
                v = _value(rid, k, v)
            if v != base[rid][k]:
                diff[k] = v
        if diff:
            out[rid] = diff
    return out


def effective(saved: dict | None, chase_pct: float | None = None, chase_default: float | None = None) -> dict:
    """Las reglas completas que usa el motor: las de Signa con las del usuario encima. chase_pct = el «Max chase» de
    antes (un solo número): vale para las dos franjas mientras no las haya cambiado una a una. chase_default = el que
    haya puesto el admin para todo el proveedor (engine_config)."""
    out = defaults()
    if chase_default is not None:
        out["chase"]["lo"] = out["chase"]["hi"] = float(chase_default)
    saved = saved or {}
    if chase_pct is not None and not {"lo", "hi"} & set(saved.get("chase", {})):
        out["chase"]["lo"] = out["chase"]["hi"] = float(chase_pct)
    for rid, vals in saved.items():
        if rid in out:
            out[rid].update({k: v for k, v in vals.items() if k == "on" or k in SCHEMA[rid]["fields"]})
    return out


def tp1_qty(held: int, frac: str = "2/3") -> int:
    """Los que se venden en el TP parcial: la parte elegida redondeando hacia arriba, pero siempre queda al menos uno
    corriendo (con 2 y 2/3, 1; con 1, ninguno). Con 2/3 es la regla de siempre."""
    if held <= 1:
        return 0
    return min(held - 1, -(-held * parse_frac(frac).numerator // parse_frac(frac).denominator))


def chase_check(r: dict, limit: float | None, ask: float | None) -> str | None:
    """Las reglas de compra «Don't chase» y «Wrong-contract guard» sobre su precio: el motivo para NO comprar o
    None. Desmarcadas, no frenan nada."""
    if not limit or not ask:
        return None
    d = ask / limit - 1
    mm = r["mismatch"]
    if mm["on"] and (d <= -mm["below"] / 100 or d >= mm["above"] / 100):
        return f"ask {ask:.2f} doesn't match his {limit:.2f} (another expiry?)"
    ch = r["chase"]
    cap = ch["hi"] if limit >= 2 else ch["lo"]
    if ch["on"] and d > cap / 100 + 1e-9:
        return f"chasing: ask {ask:.2f} = {d:+.0%} over his {limit:.2f} (max {cap:g}%)"
    return None
