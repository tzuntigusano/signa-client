"""Bots de Telegram de cada usuario: se enlazan desde la app (pantalla Telegram) y los usa el ejecutor.

El TOKEN no sale nunca del PC: vive en %APPDATA%\\Signa\\executor.json, sección "telegram":
    {"hacedor": {"token": "...", "chat_id": "123", "username": "mi_bot"}}
Al servidor solo va el @ del bot (para enseñarlo). Un bot = un proveedor: Telegram solo deja a UN programa leer
los botones de un bot, así que dos proveedores (o el copiador y Signa) no pueden compartirlo.

Llamadas a Telegram BLOQUEANTES (urllib): la app las lanza en un hilo."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import tls
from .logs import HOME

CONFIG = HOME / "executor.json"
TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,}$")


class BotError(Exception):
    """Fallo con un texto que se le puede enseñar al usuario tal cual."""
    friendly = True


# --- configuración local ---------------------------------------------------------------------------
def load() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cfg: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(CONFIG)  # de golpe: el ejecutor nunca lee un fichero a medias


def linked() -> dict[str, dict]:
    return {p: t for p, t in (load().get("telegram") or {}).items() if t.get("token") and t.get("chat_id")}


def link(provider: str, token: str, chat_id: str, username: str):
    cfg = load()
    cfg.setdefault("telegram", {})[provider] = {"token": token, "chat_id": str(chat_id), "username": username}
    save(cfg)


def unlink(provider: str):
    """Quita el bot del proveedor, pero recuerda su chat: si se vuelve a enlazar (a este u otro proveedor) no hace
    falta otro Start, que Telegram ya no ofrece en un bot iniciado."""
    cfg = load()
    t = (cfg.get("telegram") or {}).pop(provider, None)
    if t is not None:
        cfg.setdefault("known_chats", {})[bot_id(t["token"])] = t["chat_id"]
        save(cfg)


def bot_id(token: str) -> str:
    return token.split(":", 1)[0]  # la parte pública del token: identifica al bot, no da acceso


def known_chat(token: str) -> str | None:
    """Chat en el que ya funcionó este bot (enlazado ahora a otro proveedor o quitado antes)."""
    t = next((t for t in linked().values() if t["token"] == token), None)
    return t["chat_id"] if t else (load().get("known_chats") or {}).get(bot_id(token))


def owner_of(token: str) -> str | None:
    """Proveedor que ya usa ese bot (si alguno)."""
    return next((p for p, t in linked().items() if t["token"] == token), None)


# --- Telegram --------------------------------------------------------------------------------------
def api(token: str, method: str, wait: float = 15, **params):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=wait, context=tls.context()) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except ValueError:
            body = {"error_code": e.code, "description": str(e)}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BotError("Can't reach Telegram. Check your internet connection.") from e
    if body.get("ok"):
        return body["result"]
    code = body.get("error_code")
    if code in (401, 404):
        raise BotError("Telegram doesn't recognise this token. Copy it again from @BotFather.")
    if code == 409:
        raise BotError("This bot is already being used by another program (your copier, or Signa for another "
                       "provider). Each provider needs its own bot.")
    if code == 403:
        raise BotError("The bot can't write to you. Open it in Telegram and press Start.")
    raise BotError(f"Telegram said: {body.get('description') or code}")


def check_token(token: str) -> str:
    """@ del bot si el token vale."""
    token = token.strip()
    if not TOKEN_RE.match(token):
        raise BotError("That doesn't look like a bot token. It's the long text @BotFather gives you, like "
                       "123456789:AAH…")
    return api(token, "getMe")["username"]


def find_start(token: str, offset: int | None = None) -> tuple[int | None, dict | None]:
    """Una consulta (espera hasta 10 s) de mensajes nuevos al bot. Devuelve (siguiente offset, chat privado de quien
    le ha escrito, o None)."""
    ups = api(token, "getUpdates", wait=20, offset=offset, timeout=10)
    chat = None
    for u in ups:
        offset = u["update_id"] + 1
        msg = u.get("message") or {}
        if (msg.get("chat") or {}).get("type") == "private":
            chat = msg["chat"]
    if ups:
        api(token, "getUpdates", offset=offset, timeout=0)  # marcarlos leídos: el ejecutor no los repite
    return offset, chat


def send(token: str, chat_id: str, text: str):
    api(token, "sendMessage", chat_id=chat_id, text=text)
