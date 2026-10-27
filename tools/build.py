"""Construye Signa para el sistema en el que se ejecuta:  python tools/build.py

WINDOWS (venv\\Scripts\\python tools\\build.py)
  1. dist\\Signa\\        la app (PyInstaller, una carpeta): Signa.exe es la app y, con «--module», el agente, el
                         ejecutor y los supervisores de los Gateway
  2. dist\\SignaSetup-<versión>.exe   el instalador (lleva la app dentro): instala sin permisos de administrador,
                         también desinstala y actualiza (signa/installer.py)

MAC (en un Mac: el workflow .github/workflows/mac.yml de GitHub Actions)
  1. dist/Signa.app      la app (PyInstaller): su ejecutable también arranca, con «--module», el ejecutor y los
                         supervisores de los Gateway (en Mac no hay agente de notificaciones)
  2. dist/Signa-<versión>-mac-<arm64|x86_64>.dmg   para instalar (arrastrar a Aplicaciones)
  3. dist/Signa-mac-<arm64|x86_64>-<versión>.zip   la .app para las actualizaciones automáticas (se firma al publicar)

Firmar y publicar una versión: tools/release.py."""
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signa import __version__  # noqa: E402

DIST, BUILD = ROOT / "dist", ROOT / "build"
ASSETS = ROOT / "signa/app/assets"  # tools/make_icon.py
IS_MAC = sys.platform == "darwin"
ICON = ASSETS / ("signa.icns" if IS_MAC else "signa.ico")


def pyinstaller(*args: str):
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(DIST), "--workpath", str(BUILD), "--specpath", str(BUILD), *args],
                   cwd=ROOT, check=True)


def build_app() -> Path:
    data = [(ROOT / "signa/ibc", "signa/ibc"), (ROOT / "signa/core/signa_cert.pem", "signa/core"),
            (ROOT / "signa/executor/copier", "signa/executor/copier"),  # el código del copiador se importa del disco
            (ASSETS, "signa/app/assets")]  # el icono
    hidden = ["signa.app.__main__", "signa.executor.__main__", "signa.gateway.__main__", "signa.selfcheck"]
    collect = ["ib_async", "aeventkit", "keyring", "tzdata", "PySide6.QtSvg"]
    if IS_MAC:
        hidden += ["keyring.backends.macOS"]
    else:
        hidden += ["signa.agent.__main__", "keyring.backends.Windows"]
        collect += ["winrt"]  # el agente: notificaciones de Windows
    args = ["--name", "Signa", "--windowed", "--onedir", "--icon", str(ICON)]
    if IS_MAC:
        args += ["--osx-bundle-identifier", "com.signa.app"]
    for src, dst in data:
        args += ["--add-data", f"{src}{os.pathsep}{dst}"]
    for h in hidden:
        args += ["--hidden-import", h]
    for c in collect:
        args += ["--collect-all", c]
    pyinstaller(*args, str(ROOT / "signa/launcher.py"))
    return DIST / ("Signa.app" if IS_MAC else "Signa")


# --- Windows: el instalador ---------------------------------------------------------------------------------
def build_setup(app_dir: Path) -> Path:
    payload = BUILD / "Signa.zip"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as z:
        for f in app_dir.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(app_dir))
    (BUILD / "VERSION").write_text(__version__)
    name = f"SignaSetup-{__version__}"
    pyinstaller("--name", name, "--onefile", "--windowed", "--icon", str(ICON), "--hidden-import", "psutil",
                "--add-data", f"{payload}{os.pathsep}.", "--add-data", f"{BUILD / 'VERSION'}{os.pathsep}.",
                str(ROOT / "signa/installer.py"))
    return DIST / f"{name}.exe"


# --- Mac: el .dmg y el .zip de las actualizaciones -----------------------------------------------------------
def mac_arch() -> str:
    return "arm64" if platform.machine() == "arm64" else "x86_64"


def build_mac(app: Path) -> list[Path]:
    arch = mac_arch()
    zip_path = DIST / f"Signa-mac-{arch}-{__version__}.zip"
    zip_path.unlink(missing_ok=True)
    # ditto conserva lo que necesita una .app (permisos, enlaces); un zip normal la rompería
    subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(zip_path)], check=True)
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    subprocess.run(["ditto", str(app), str(stage / "Signa.app")], check=True)
    (stage / "Applications").symlink_to("/Applications")  # arrastrar Signa aquí = instalar
    dmg = DIST / f"Signa-{__version__}-mac-{arch}.dmg"
    dmg.unlink(missing_ok=True)
    subprocess.run(["hdiutil", "create", "-volname", "Signa", "-srcfolder", str(stage), "-ov", "-format", "UDZO",
                    str(dmg)], check=True)
    return [dmg, zip_path]


def main() -> Path:
    shutil.rmtree(DIST / ("Signa.app" if IS_MAC else "Signa"), ignore_errors=True)
    app = build_app()
    if IS_MAC:
        out = build_mac(app)
        print("\n".join(f"{p} ({p.stat().st_size // 1024 // 1024} MB)" for p in out))
        return out[-1]
    setup = build_setup(app)
    print(f"app:   {app}\nsetup: {setup} ({setup.stat().st_size // 1024 // 1024} MB)")
    return setup


if __name__ == "__main__":
    main()
