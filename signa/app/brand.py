"""La marca de Signa: su logo con los colores fijos (el icono oficial no cambia con el tema claro/oscuro) y cómo
hacer que Windows enseñe ESE icono en la barra de tareas (y no el de Python cuando la app corre desde el código)."""
import sys
from pathlib import Path

# el logo de la app (widgets.logo) con el azul de la marca
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="6" '
       'fill="#2455c3"/><path d="M5 13h3l2.2-5 3.6 9 2.2-4H19" fill="none" stroke="#ffffff" stroke-width="1.8" '
       'stroke-linecap="round" stroke-linejoin="round"/></svg>')
ICO = Path(__file__).with_name("assets") / "signa.ico"
APP_ID = "Signa.App"  # AppUserModelID: Windows agrupa la barra de tareas por esto


def claim_taskbar():
    """Antes de abrir ventanas: que Windows trate este proceso como «Signa» (su icono, su grupo en la barra de
    tareas) y no como pythonw.exe."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass


def icon():
    from PySide6.QtGui import QIcon
    return QIcon(str(ICO)) if ICO.exists() else QIcon()
