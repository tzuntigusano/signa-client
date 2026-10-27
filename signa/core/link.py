"""Conexión con signahub: dirección y TLS ANCLADO a su certificado (no se acepta ningún otro, ni de una CA)."""
import ssl
from pathlib import Path

SERVER = "198.46.187.25:8770"
CERT = Path(__file__).with_name("signa_cert.pem")


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cadata=CERT.read_text(encoding="ascii"))
    ctx.check_hostname = True  # el certificado lleva la IP del servidor
    return ctx


def url(path: str, ws: bool = True) -> str:
    return f"{'wss' if ws else 'https'}://{SERVER}{path}"
