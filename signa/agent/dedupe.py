"""(Ivan 12/11/2026) El mismo mensaje llega por la app de Discord («pc») y por Discord web en el navegador («web»),
con décimas de segundo de diferencia. El agente manda UNO: si no, un servidor sin la fusión de fuentes (notices.py)
contaría dos mensajes y el motor compraría dos veces. Cada aviso casa una sola vez con uno de la OTRA fuente: lo que
repite la misma fuente sí son dos mensajes (él repite «1.4»). Puro: tests/test_agent.py."""

WINDOW = 30.0  # s: la app y el navegador avisan casi a la vez; más tarde ya es otro mensaje


def norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()


class Dedupe:
    def __init__(self, window: float = WINDOW):
        self.window = window
        self.recent: list[list] = []  # [proveedor, texto, fuente, instante, ¿ya casado?]

    def seen(self, provider: str, text: str, source: str, now: float) -> bool:
        """True = el mismo mensaje ya salió por la otra fuente (este no se manda). Si no, se apunta."""
        self.recent = [e for e in self.recent if now - e[3] <= self.window]
        t = norm(text)
        for e in self.recent:
            if e[0] == provider and e[1] == t and e[2] != source and not e[4]:
                e[4] = True
                return True
        self.recent.append([provider, t, source, now, False])
        return False
