"""Actualizar Signa instalada (Settings › Updates): descarga lo publicado para ESTA plataforma, comprueba su sha256 y
su FIRMA (signa.core.release) y lo instala:
  · Windows: el instalador en modo /update (espera a que la app se cierre, cambia los ficheros y la vuelve a abrir)
  · Mac: un .zip con la Signa.app nueva; un pequeño script espera a que la app se cierre, cambia la .app y la abre
Desde el código (sin instalar) no aplica: se actualiza con git."""
import json
import os
import platform
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from ..core import link, release, runtime
from ..core.logs import HOME


class UpdateError(Exception):
    friendly = True


def platform_key() -> str:
    """windows · mac-arm64 · mac-x86_64 (las entradas de latest.json)."""
    if runtime.IS_MAC:
        return "mac-arm64" if platform.machine() == "arm64" else "mac-x86_64"
    return "windows"


def entry(manifest: dict, key: str | None = None) -> dict | None:
    """Lo publicado para una plataforma ({file, sha256, sig}). El latest.json de antes (solo Windows) va arriba."""
    key = key or platform_key()
    found = (manifest.get("platforms") or {}).get(key)
    if found is None and key == "windows" and manifest.get("file"):
        found = {k: manifest[k] for k in ("file", "sha256", "sig")}
    return found


def manifest() -> dict:
    try:
        with urllib.request.urlopen(link.url("/download/latest.json", ws=False), context=link.ssl_context(),
                                    timeout=15) as r:
            return json.loads(r.read())
    except (OSError, ValueError) as e:
        raise UpdateError("Can't reach the Signa server to check for updates.") from e


def download_and_check() -> Path:
    """Lo de la última versión para esta plataforma, YA comprobado (si no, UpdateError con el porqué)."""
    m = entry(manifest())
    if m is None:
        raise UpdateError("There's no update for this computer yet.")
    path = Path(tempfile.gettempdir()) / m["file"]
    try:
        with urllib.request.urlopen(link.url(f"/download/{m['file']}", ws=False), context=link.ssl_context(),
                                    timeout=300) as r, open(path, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    except OSError as e:
        raise UpdateError("The download didn't finish. Try again.") from e
    why = release.check_file(path, m)
    if why:
        path.unlink(missing_ok=True)
        raise UpdateError(f"Not installed: {why}.")
    return path


def mac_script(zip_path: Path, app: Path, pid: int) -> str:
    """El cambio de la .app en Mac, cuando la app ya se ha cerrado (no se puede sustituir mientras corre)."""
    new = zip_path.with_suffix("")
    # cerrada la app, fuera también el ejecutor y los supervisores (la misma .app con «--module»): si siguieran, la
    # versión nueva no podría arrancar los suyos. Los IB Gateway (Java) siguen funcionando.
    # todo va a update.log (antes no quedaba rastro de por qué fallaba) y, si algo falla, se vuelve a dejar la .app
    # de antes en su sitio y se abre: nunca se queda el Mac sin Signa
    log = HOME / "update.log"
    return f"""#!/bin/sh
mkdir -p "{log.parent}"
exec >>"{log}" 2>&1
echo "== $(date) updating {app} with {zip_path.name}"
n=0
while kill -0 {pid} 2>/dev/null; do sleep 0.5; n=$((n+1)); [ $n -gt 240 ] && kill -9 {pid}; done
pkill -f "{app}/Contents/MacOS/Signa"; sleep 2; pkill -9 -f "{app}/Contents/MacOS/Signa"
set -x
rm -rf "{new}" && mkdir -p "{new}" && /usr/bin/ditto -x -k "{zip_path}" "{new}" && [ -d "{new}/Signa.app" ] || {{
  echo "FAILED: could not unpack the update"; open "{app}"; exit 1; }}
rm -rf "{app}.old"
mv "{app}" "{app}.old" || {{ echo "FAILED: can't move the old app (permissions?)"; open "{app}"; exit 1; }}
if mv "{new}/Signa.app" "{app}"; then
  /usr/bin/xattr -dr com.apple.quarantine "{app}" 2>/dev/null
  rm -rf "{app}.old" "{new}"
  echo "OK: updated"
else
  echo "FAILED: can't put the new app in place; the old one goes back"
  mv "{app}.old" "{app}"
fi
open "{app}"
"""


def install(path: Path):
    """Deja la actualización en marcha: la app tiene que cerrarse justo después."""
    if not runtime.FROZEN:
        raise UpdateError("This copy runs from the source code: update it with git.")
    if runtime.IS_MAC:
        app = runtime.bundle()
        if app is None:
            raise UpdateError("Can't find Signa.app to update.")
        if "/AppTranslocation/" in str(app):  # macOS la abre desde una copia temporal de solo lectura
            raise UpdateError("macOS is running Signa from a temporary copy, so it can't replace itself. Quit Signa, "
                              "drag Signa.app into Applications in Finder, open it from there and try again.")
        if not os.access(app.parent, os.W_OK):
            raise UpdateError(f"Signa can't replace itself in {app.parent} (no permission). Install the new version "
                              "from its .dmg instead.")
        script = path.with_suffix(".sh")
        script.write_text(mac_script(path, app, os.getpid()))
        runtime.spawn(["/bin/sh", str(script)], cwd=str(path.parent))
    else:
        subprocess.Popen([str(path), "/update"], cwd=os.path.dirname(path), **runtime.detached())
