"""Paso de objetos del motor por la conexión servidor <-> ejecutor: calls, planes de orden y contratos de IBKR, a JSON y
de vuelta. Lo usan el servidor (broker remoto) y el ejecutor del PC; los dos con las MISMAS clases del copiador."""
import dataclasses
from datetime import date, datetime


def _plain(v):
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {k: _plain(x) for k, x in dataclasses.asdict(v).items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and v != v:  # NaN no es JSON
        return None
    return v


def contract_to(c) -> dict | None:
    if c is None:
        return None
    d = {k: v for k, v in dataclasses.asdict(c).items() if v not in (None, "", [], 0, 0.0) or k == "conId"}
    if "comboLegs" in d:
        d["comboLegs"] = [_plain(x) for x in c.comboLegs]
    return d


def contract_from(d: dict | None):
    if d is None:
        return None
    from ib_async import ComboLeg, Contract
    d = dict(d)
    legs = d.pop("comboLegs", None)
    d.pop("deltaNeutralContract", None)
    c = Contract.create(**d)
    if legs:
        c.comboLegs = [ComboLeg(**x) for x in legs]
    return c


def call_to(c) -> dict:
    return _plain(c)


def call_from(d: dict, Call, Leg):
    d = dict(d)
    d["legs"] = [Leg(**x) for x in d.get("legs") or []]
    return Call(**d)


def call_update(c, d: dict, Leg):
    """El ejecutor puede tocar la call (p. ej. su vencimiento): se copian los cambios a la del motor."""
    for k, v in d.items():
        setattr(c, k, [Leg(**x) for x in v] if k == "legs" else v)


def plan_to(p) -> dict:
    return {"contract": contract_to(p.contract), "action": p.action, "qty": p.qty, "desc": p.desc,
            "legs": [[contract_to(c), s] for c, s in p.legs], "info": _plain(p.info)}


def plan_from(d: dict, Plan):
    return Plan(contract_from(d["contract"]), d["action"], d["qty"], d["desc"],
                [(contract_from(c), s) for c, s in d["legs"]], dict(d.get("info") or {}))
