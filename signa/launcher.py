"""Punto de entrada de Signa.exe (empaquetado): sin argumentos, la app; con «--module X», esa pieza (el agente, el
ejecutor o el supervisor de un Gateway), que la app arranca así cuando está instalada."""
import runpy
import sys


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--module":
        module = sys.argv[2]
        sys.argv = [sys.argv[0], *sys.argv[3:]]
        try:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
        except Exception:  # una pieza de fondo no tiene ventana: sin esto PyInstaller saca un diálogo de error
            import traceback
            from signa.core.logs import HOME
            HOME.mkdir(parents=True, exist_ok=True)
            (HOME / f"crash-{module}.log").write_text(traceback.format_exc(), encoding="utf-8")
            sys.exit(1)
    else:
        runpy.run_module("signa.app", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
