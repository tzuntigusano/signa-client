"""Lee las notificaciones de Windows en cuanto llegan (no hace falta verlas). Solo lee: no toca la app que las
manda ni ninguna cuenta. Puede convivir con otro lector (Windows reparte la misma lista a todos)."""
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone

INVIS = dict.fromkeys(map(ord, "⁨⁩‎‏"), None)  # marcas invisibles de dirección de texto
TITLE_RE = re.compile(r"^(?P<author>.+?)\s+\((?P<channel>#[^,]+),\s*(?P<server>.+)\)$")


@dataclass
class Notice:
    id: int
    app: str
    author: str
    channel: str
    server: str
    text: str
    at: datetime  # cuándo la creó Windows (UTC)


def split_title(title: str) -> tuple[str, str, str]:
    """«⁨autor⁩ (⁨#canal⁩, ⁨Servidor⁩)» -> (autor, #canal, servidor). Si no tiene esa forma: (título, "", "")."""
    t = title.translate(INVIS).strip()
    m = TITLE_RE.match(t)
    return (m["author"].strip(), m["channel"].strip(), m["server"].strip()) if m else (t, "", "")


class NotPermitted(RuntimeError):
    pass


async def listen(poll: float = 0.25):
    """Generador asíncrono de Notice NUEVAS (las que ya estaban al arrancar se ignoran)."""
    from winrt.windows.ui.notifications import KnownNotificationBindings, NotificationKinds
    from winrt.windows.ui.notifications.management import (UserNotificationListener,
                                                             UserNotificationListenerAccessStatus)
    lst = UserNotificationListener.current
    if await lst.request_access_async() != UserNotificationListenerAccessStatus.ALLOWED:
        raise NotPermitted("No permission to read Windows notifications "
                           "(Settings → Privacy & security → Notifications).")
    seen = {n.id for n in await lst.get_notifications_async(NotificationKinds.TOAST)}
    while True:
        try:
            ns = await lst.get_notifications_async(NotificationKinds.TOAST)
        except Exception:  # muy raro: se reintenta
            await asyncio.sleep(2)
            continue
        for n in ns:
            if n.id in seen:
                continue
            seen.add(n.id)
            try:
                app = n.app_info.display_info.display_name if n.app_info else ""
                b = n.notification.visual.get_binding(KnownNotificationBindings.toast_generic)
                tx = [x.text.translate(INVIS) for x in b.get_text_elements()] if b else []
            except Exception:
                continue
            if not tx:
                continue
            author, channel, server = split_title(tx[0])
            at = n.creation_time
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            yield Notice(n.id, app, author, channel, server, "\n".join(tx[1:]).strip(), at)
        if len(seen) > 5000:
            seen = {n.id for n in ns}
        await asyncio.sleep(poll)
