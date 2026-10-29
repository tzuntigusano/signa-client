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
import plistlib
import shutil
import struct
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
    if IS_MAC:  # en Mac no hay panel de admin (signa/app/window.py, HAS_ADMIN)
        args += ["--osx-bundle-identifier", "com.signa.app", "--exclude-module", "signa.app.screens.admin"]
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


def sign_mac(app: Path):
    """Con MAC_SIGN_IDENTITY (el workflow de Mac: el certificado de tools/mac_sign_cert.py), la app entera firmada
    con ESE certificado en vez de «ad hoc»: la misma identidad en todas las versiones = el llavero no vuelve a pedir
    la contraseña al actualizar. Sin la variable (un Mac cualquiera), se queda la firma ad hoc de PyInstaller."""
    ident = os.environ.get("MAC_SIGN_IDENTITY")
    if not ident:
        print("MAC_SIGN_IDENTITY not set: ad-hoc signature (the keychain will ask again after each update)")
        return
    subprocess.run(["codesign", "--force", "--deep", "--timestamp=none", "--sign", ident, str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    subprocess.run(["codesign", "--display", "--requirements", "-", str(app)], check=True)  # al log: su identidad


def macho_min_os(path: Path) -> tuple[int, int] | None:
    """El macOS mínimo con el que se compiló un binario Mach-O (LC_BUILD_VERSION / LC_VERSION_MIN_MACOSX), o None
    si no es Mach-O. En uno «fat» (universal2), el mayor de sus arquitecturas."""
    with open(path, "rb") as f:
        head = f.read(4)
    if len(head) < 4 or struct.unpack("<I", head)[0] not in (0xFEEDFACF, 0xCAFEBABE, 0xBEBAFECA):
        return None
    data = path.read_bytes()
    if struct.unpack_from("<I", data)[0] != 0xFEEDFACF:  # fat (big endian)
        found = []
        for i in range(struct.unpack_from(">I", data, 4)[0]):
            _cpu, _sub, off, size, _align = struct.unpack_from(">IIIII", data, 8 + 20 * i)
            found.append(_thin_min_os(data[off:off + size]))
        found = [v for v in found if v]
        return max(found) if found else None
    return _thin_min_os(data)


def _thin_min_os(data: bytes) -> tuple[int, int] | None:
    if len(data) < 32 or struct.unpack_from("<I", data)[0] != 0xFEEDFACF:
        return None
    p = 32
    for _ in range(struct.unpack_from("<I", data, 16)[0]):
        cmd, size = struct.unpack_from("<II", data, p)
        if cmd in (0x32, 0x24):  # LC_BUILD_VERSION (minos en +12) · LC_VERSION_MIN_MACOSX (version en +8)
            v = struct.unpack_from("<I", data, p + (12 if cmd == 0x32 else 8))[0]
            return v >> 16, (v >> 8) & 0xFF
        p += size
    return None


def mac_min_os(app: Path):
    """Con MAC_MIN_OS (el workflow de Mac): lo declara en el Info.plist (un macOS más viejo dice «necesita macOS X»
    en vez de abrir y cerrarse sin más, como pasó en un iMac con Monterey, 29/10) y, con MAC_MIN_OS_STRICT, falla
    si algún binario de dentro pide un macOS más nuevo (Qt 6.12 pedía 14.4; numpy y el OpenSSL del Python de
    GitHub, 14 y 15)."""
    want = os.environ.get("MAC_MIN_OS")
    if not want:
        return
    plist = app / "Contents" / "Info.plist"
    info = plistlib.loads(plist.read_bytes())
    info["LSMinimumSystemVersion"] = want
    plist.write_bytes(plistlib.dumps(info))
    limit = tuple(int(x) for x in want.split(".")[:2])
    bad = []
    for f in app.rglob("*"):
        if f.is_file() and not f.is_symlink() and (v := macho_min_os(f)) and v > limit:
            bad.append(f"{f.relative_to(app)}: macOS {v[0]}.{v[1]}")
    print("\n".join(sorted(bad)) if bad else f"every binary runs on macOS {want} or newer")
    if bad and os.environ.get("MAC_MIN_OS_STRICT"):
        raise SystemExit(f"{len(bad)} binaries need a macOS newer than {want}")


def build_mac(app: Path) -> list[Path]:
    mac_min_os(app)  # antes de firmar: cambia el Info.plist
    sign_mac(app)
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
