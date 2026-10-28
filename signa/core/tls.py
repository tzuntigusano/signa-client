"""TLS para webs públicas (Telegram). En Mac la app empaquetada no encuentra los certificados raíz del sistema (el
Python de dentro los busca en una carpeta que no existe) y TODA conexión https falla con «certificate verify
failed»: se usan los de certifi, que van dentro. En Windows, los del sistema (valen también con proxys de empresa).
La conexión con signahub no usa esto: va anclada a su certificado (core.link)."""
import ssl
from functools import cache

from .runtime import IS_WIN


@cache
def context() -> ssl.SSLContext:
    if IS_WIN:
        return ssl.create_default_context()
    import certifi
    return ssl.create_default_context(cafile=certifi.where())
