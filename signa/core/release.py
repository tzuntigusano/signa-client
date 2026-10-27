"""Actualizaciones FIRMADAS: el servidor publica latest.json {version, file, sha256, sig}; la app solo instala un
instalador cuyo sha256 coincide y cuya firma (Ed25519 sobre ese sha256) valida con la clave PÚBLICA de aquí. La
privada solo existe en el PC del admin (%APPDATA%\\Signa\\release_key.pem, tools/release.py): aunque alguien tomara
el servidor, no podría colar un instalador."""
import base64
import hashlib
from pathlib import Path

# base64 de la clave pública Ed25519 (la escribe tools/release.py la primera vez)
PUBLIC_KEY = "sO+G8jpPcYmFRZ+R4knH6JQqXEUR5lonST4LBuTUjPY="


def version_tuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in (v or "0").split("."))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(digest: str, sig_b64: str, public_key: str | None = None) -> bool:
    """¿La firma de ese sha256 es de la clave de Signa?"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = public_key if public_key is not None else PUBLIC_KEY
    if not key:
        return False  # sin clave pública no se instala nada
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(base64.b64decode(sig_b64), digest.encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def check_file(path: Path, manifest: dict, public_key: str | None = None) -> str | None:
    """None si el fichero es bueno; si no, por qué no."""
    digest = sha256(path)
    if digest != manifest.get("sha256"):
        return "the download is damaged (checksum doesn't match)"
    if not verify(digest, manifest.get("sig", ""), public_key):
        return "the update isn't signed by Signa"
    return None
