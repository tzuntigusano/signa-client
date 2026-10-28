"""INSTALADOR de Signa (SignaSetup.exe; también hace de desinstalador y de actualizador). Sin permisos de
administrador: todo en la carpeta del usuario.

    SignaSetup.exe              instala (ventana): copia Signa en %LOCALAPPDATA%\\Programs\\Signa, acceso en el menú
                                Inicio, desinstalador en «Aplicaciones instaladas» y abre Signa
    SignaSetup.exe /update      lo lanza la app al actualizarse: espera a que se cierre, cierra sus procesos (agente,
                                ejecutor, supervisores), copia la versión nueva y vuelve a abrir Signa
    Uninstall.exe /uninstall    quita Signa (la copia de este exe que queda en la carpeta de instalación)

La app empaquetada viaja DENTRO de este exe (Signa.zip, añadido al construirlo: tools/build.py)."""
import os
import shutil
import socket
import subprocess
import sys
import time
import winreg
import zipfile
from pathlib import Path

APP = "Signa"
DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / APP
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP}"
START_MENU = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs"
APP_LOCK = 47689  # el candado de la app (signa.app.__main__)
DETACHED = 0x00000008 | 0x00000200


def payload() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "Signa.zip"


def version() -> str:
    try:
        return (Path(getattr(sys, "_MEIPASS", "")) / "VERSION").read_text().strip()
    except OSError:
        return "?"


def app_running() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", APP_LOCK))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _uses_dir(p) -> bool:
    """¿El proceso corre desde la carpeta de Signa o la usa? (los IB Gateway de versiones hasta la 0.1.3 cargaban el
    IBC.jar de dentro: con ellos abiertos Windows no deja cambiar la carpeta)."""
    here = str(DIR.resolve()).lower()
    exe = p.info.get("exe")
    if exe and Path(exe).resolve().is_relative_to(DIR.resolve()):
        return True
    return any(here in str(a).lower() for a in (p.info.get("cmdline") or []))


def stop_signa(wait: float = 30):
    """Cierra lo que corre desde la carpeta de Signa (la app ya se cerró sola; quedan agente, ejecutor y
    supervisores). Un IB Gateway solo si usa algo de la carpeta (los antiguos); Signa los vuelve a abrir."""
    import psutil
    end = time.time() + wait
    while app_running() and time.time() < end:
        time.sleep(0.5)
    mine = []
    for p in psutil.process_iter(["exe", "cmdline"]):
        try:
            if p.pid != os.getpid() and _uses_dir(p):
                mine.append(p)
        except (psutil.Error, OSError, ValueError):
            continue
    for p in mine:
        try:
            p.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(mine, timeout=10)


def copy_app():
    tmp = DIR.with_name(APP + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(payload()) as z:
        z.extractall(tmp)
    old = DIR.with_name(APP + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if DIR.exists():
        _rename(DIR, old)
    tmp.rename(DIR)
    shutil.rmtree(old, ignore_errors=True)
    shutil.copy2(sys.executable, DIR / "Uninstall.exe")


def _rename(src: Path, dst: Path, tries: int = 20):
    """Un antivirus o el Explorador pueden tener un fichero abierto un momento: se reintenta unos segundos. Si sigue,
    se dice QUÉ programa lo tiene (si no, «acceso denegado» no le dice nada a nadie)."""
    for _ in range(tries):
        try:
            src.rename(dst)
            return
        except PermissionError:
            time.sleep(0.5)
    users = sorted(_lockers())
    raise PermissionError("Signa's folder is in use" + (f" by: {', '.join(users)}" if users else "") +
                          ". Close it and run the installer again.")


def _lockers() -> set[str]:
    import psutil
    here = str(DIR.resolve()).lower()
    names = set()
    for p in psutil.process_iter(["name"]):
        try:
            if any(here in f.path.lower() for f in p.open_files()) or \
                    (p.cwd() or "").lower().startswith(here):
                names.add(p.info["name"])
        except (psutil.Error, OSError):
            continue
    return names


def shortcut():
    lnk = START_MENU / f"{APP}.lnk"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{DIR / 'Signa.exe'}';"
          f"$s.WorkingDirectory='{DIR}';$s.IconLocation='{DIR / 'Signa.exe'},0';$s.Save()")  # el icono de Signa
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000, check=False)


def register():
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, value in (("DisplayName", APP), ("DisplayVersion", version()), ("Publisher", APP),
                            ("DisplayIcon", str(DIR / "Signa.exe")), ("InstallLocation", str(DIR)),
                            ("UninstallString", f'"{DIR / "Uninstall.exe"}" /uninstall')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)


def launch():
    subprocess.Popen([str(DIR / "Signa.exe")], cwd=str(DIR), creationflags=DETACHED)


def install():
    stop_signa(wait=0)
    copy_app()
    shortcut()
    register()


def uninstall():
    stop_signa(wait=0)
    (START_MENU / f"{APP}.lnk").unlink(missing_ok=True)
    for key, name in ((UNINSTALL_KEY, None), (r"Software\Microsoft\Windows\CurrentVersion\Run", APP)):
        try:
            if name:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, name)
            else:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass
    # la carpeta (con este exe dentro) se borra cuando ya se ha cerrado
    subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{DIR}"', creationflags=0x08000000)


def window():
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title(f"Install {APP}")
    root.geometry("420x170")
    root.resizable(False, False)
    tk.Label(root, text=f"{APP} {version()}", font=("Segoe UI", 14, "bold")).pack(pady=(18, 4))
    tk.Label(root, text=f"Installs to {DIR}\nNo administrator rights needed.", font=("Segoe UI", 9)).pack()
    status = tk.Label(root, text="", font=("Segoe UI", 9))
    status.pack(pady=4)

    def go():
        btn.config(state="disabled")
        status.config(text="Installing…")
        root.update()
        try:
            install()
        except Exception as e:
            messagebox.showerror(APP, f"Couldn't install: {e}")
            btn.config(state="normal")
            return
        launch()
        root.destroy()
    btn = tk.Button(root, text="Install", width=14, command=go)
    btn.pack(pady=6)
    root.mainloop()


def main():
    args = [a.lower() for a in sys.argv[1:]]
    if "/uninstall" in args:
        uninstall()
    elif "/update" in args:
        stop_signa()
        copy_app()
        register()
        launch()
    else:
        window()


if __name__ == "__main__":
    main()
