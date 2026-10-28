"""Lo que el telegram_ui del copiador importa como «tls» (en trade-copier es su propio tls.py): aquí, los certificados
de Signa (core.tls). No es una copia del copiador: tools/sync_copier.py no lo toca."""
from signa.core.tls import context

SSL_CTX = context()
