"""AVISOS DE IBKR al log del ejecutor (el copiador no los guarda): sin ellos, «se me desconecta el Gateway» o «no
hay precio de la 0DTE» no dejaban rastro en el diagnóstico del panel de admin.

  - conectividad: el Gateway pierde/recupera IBKR (1100/1101/1102), otra sesión con el mismo usuario (10197…),
    granjas de datos caídas/vueltas (2103-2110, 2157-2158) y la desconexión del socket con el Gateway;
  - errores de una petición concreta (reqId > 0): sin datos de mercado, contrato que no existe, orden rechazada…
Un mismo aviso solo se anota una vez por minuto (IBKR repite algunos en bucle)."""
import time

CONNECTIVITY = {1100, 1101, 1102, 1300, 2103, 2105, 2110, 2157, 10197}
QUIET = {2104, 2106, 2107, 2108, 2119, 2158}  # «todo OK» que IBKR manda al conectar: ruido
REPEAT = 60


class IbEvents:
    def __init__(self, account: str, log, clock=time.monotonic):
        self.account, self.log, self.clock = account, log, clock
        self.seen: dict[tuple, float] = {}

    def text(self, req_id: int, code: int, msg: str, contract=None) -> str | None:
        """Lo que se anota de un errorEvent, o None si no merece la pena (o ya se anotó hace menos de un minuto)."""
        if code in QUIET or (code not in CONNECTIVITY and req_id <= 0):
            return None
        key = (code, req_id if code not in CONNECTIVITY else 0, msg)
        now = self.clock()
        if now - self.seen.get(key, -REPEAT) < REPEAT:
            return None
        self.seen[key] = now
        what = (getattr(contract, "localSymbol", "") or getattr(contract, "symbol", "")) if contract else ""
        return f"IBKR {self.account} {code}{f' ({what})' if what else ''}: {msg}"

    def attach(self, ib):
        def on_error(req_id, code, msg, contract=None):
            t = self.text(req_id, code, msg, contract)
            if t:
                self.log(t)

        ib.errorEvent += on_error
        ib.disconnectedEvent += lambda: self.log(f"IBKR {self.account}: disconnected from the Gateway")
