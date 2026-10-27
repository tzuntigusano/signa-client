"""Genera el icono OFICIAL de Signa (signa/app/assets/signa.ico, signa.icns para Mac y signa.png) a partir del logo
de la app, con los colores de la marca (fijos: no cambian con el tema). Un .ico con todos los tamaños que usa
Windows (16…256) y un .icns para Mac (32…1024), cada tamaño dibujado a su tamaño (no escalado) y en PNG.

    venv\\Scripts\\python tools\\make_icon.py"""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from signa.app.brand import SVG  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
OUT = ROOT / "signa" / "app" / "assets"


def render(size: int) -> bytes:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(SVG.encode())).render(p)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def ico(images: list[tuple[int, bytes]]) -> bytes:
    """Cabecera ICONDIR + una entrada por tamaño (PNG dentro: Windows Vista en adelante)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 = 256 en el formato .ico
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + entries + data


# .icns (Mac): cada tamaño en PNG con su código (ic11 = 32, ic12 = 64, ic07 = 128, ic08 = 256, ic09 = 512, ic10 = 1024)
ICNS_TYPES = {32: b"ic11", 64: b"ic12", 128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}


def icns(images: dict[int, bytes]) -> bytes:
    body = b"".join(ICNS_TYPES[s] + struct.pack(">I", len(png) + 8) + png for s, png in images.items())
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main():
    QGuiApplication([])
    OUT.mkdir(parents=True, exist_ok=True)
    images = [(s, render(s)) for s in SIZES]
    (OUT / "signa.ico").write_bytes(ico(images))
    (OUT / "signa.png").write_bytes(images[-1][1])
    (OUT / "signa.icns").write_bytes(icns({s: render(s) for s in ICNS_TYPES}))
    print(f"{OUT / 'signa.ico'} ({len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
