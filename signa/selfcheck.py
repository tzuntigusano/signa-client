"""Comprobación del empaquetado (la usa el workflow de Mac): la app instalada carga TODO lo que necesita (Qt, IBKR, el
código del copiador, IBC, el llavero) y sale con 0. No conecta a nada.

    Signa --module signa.selfcheck"""
import importlib
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

MODULES = ["signa.app.window", "signa.executor.__main__", "signa.gateway", "keyring", "ib_async", "aiohttp",
           "cryptography"]


def main() -> int:
    for m in MODULES:
        importlib.import_module(m)
    from signa import gateway
    from signa.app import brand
    for f in (gateway.IBC_JAR, brand.ICO.with_suffix(".png")):  # IBC y el icono van como datos: ¿están dentro?
        if not f.exists():
            print(f"missing: {f}")
            return 1
    print("selfcheck OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
