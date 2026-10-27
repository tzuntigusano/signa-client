"""SUPERVISOR de un IB Gateway (una cuenta): el bucle de StartIBC.bat en Python. Lanza el Gateway con IBC y lo relanza
en su reinicio diario (sin login), en frío o si caducó el 2FA / el diálogo de login. Termina cuando el Gateway se
cierra de verdad (STOP desde la app, o lo cierras tú).

    venv\\Scripts\\pythonw -m signa.gateway paper|live      (lo arranca la app: pantalla IBKR)"""
import subprocess
import sys
import time

from signa.core import runtime
from signa.core.logs import Log, single_instance

from . import ACCOUNTS, GatewayError, command_line, credentials, install, new_session, next_step, port_open, \
    restart_option, settings_dir, write_config

MAX_RELOGINS = 10  # reintentos seguidos con login (2FA sin aprobar…): no avisar al móvil eternamente
LOGIN_WAIT = 180  # s: lo más que se dejan las credenciales en el config.ini esperando a que entre


def main(account: str) -> int:
    log = Log(f"gateway-{account}")

    def say(msg: str):
        log(f"gateway:{account}", msg)

    lock = single_instance(ACCOUNTS[account]["lock"])
    if lock is None:
        say("already running: exiting")
        return 3
    try:
        gw = install()
        if credentials(account) is None:
            raise GatewayError("no IBKR credentials saved for this account")
        config = write_config(account)
    except GatewayError as e:
        say(f"can't start: {e}")
        return 1
    settings = settings_dir(account)
    session = new_session()
    relogins, cold = 0, False
    out = (log.path.parent / f"gateway-{account}.java.log").open("a", encoding="utf-8", errors="ignore")
    while True:
        opt, _ = (None, False) if cold else restart_option(settings)
        cold = False
        say(f"starting IB Gateway {gw.name} with IBC" + (f" ({opt}: no login needed)" if opt else ""))
        creds = credentials(account)  # se releen: si las cambias en la app, el próximo login ya usa las nuevas
        if creds is None:
            say("credentials removed in Signa: stopping")
            return 1
        # usuario y contraseña en el config.ini SOLO mientras inicia sesión (no en la línea de comandos)
        write_config(account, creds)
        proc = subprocess.Popen(command_line(account, gw, config, session, opt), cwd=str(settings),
                                stdin=subprocess.DEVNULL, stdout=out, stderr=out, **runtime.no_window())
        try:
            for _ in range(LOGIN_WAIT):
                if proc.poll() is not None or port_open(ACCOUNTS[account]["api"]):
                    break
                time.sleep(1)
        finally:
            write_config(account)  # dentro (o ya fuera): sin credenciales en el disco
        code = proc.wait()
        step = next_step(code, settings, session)
        say(f"Gateway exited (code {code}) -> {step or 'stop'}")
        if step is None:
            return 0
        if step == "restart":
            relogins += 1
            if relogins > MAX_RELOGINS:
                say("too many failed logins in a row: giving up (start it again from Signa)")
                return 2
        else:
            relogins = 0
        cold = step == "cold"
        time.sleep(2)


if __name__ == "__main__":
    acct = sys.argv[1] if len(sys.argv) > 1 else ""
    if acct not in ACCOUNTS:
        print("usage: python -m signa.gateway paper|live")
        sys.exit(1)
    sys.exit(main(acct))
