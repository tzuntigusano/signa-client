"""Arranque de la app de escritorio: python -m signa.app
(--logout borra la clave del llavero: la próxima vez pide una invitación nueva; --startup = abierta por Windows al
iniciar sesión: arranca minimizada)."""
import sys

from PySide6.QtWidgets import QApplication

from ..core.logs import Log, single_instance
from . import brand, client, theme
from .window import MainWindow


LOCK_PORT = 47689  # una sola app por PC (agente 47690, ejecutor 47691, Gateways 47692-3)


def main() -> int:
    lock = single_instance(LOCK_PORT)
    if lock is None:
        Log("app")("app", "Signa is already open: exiting")
        return 0
    brand.claim_taskbar()  # la barra de tareas: «Signa» con su icono (no el de Python)
    app = QApplication(sys.argv)
    app.setWindowIcon(brand.icon())
    app.setApplicationName("Signa")
    app.setOrganizationName("Signa")
    app.setFont(theme.ui_font())
    if "--logout" in sys.argv:
        client.forget_key()
    demo = None
    if "--demo" in sys.argv:  # trades de ejemplo para ver la pantalla (solo aquí; nada va al servidor)
        from .demo import TRADES as demo
    win = MainWindow(app, services=True, demo=demo)
    if "--startup" in sys.argv:
        win.showMinimized()
    else:
        win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
