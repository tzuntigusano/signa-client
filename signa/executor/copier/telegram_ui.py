"""Telegram: tarjetas con botones Lanzar / Limite / Ignorar + comandos.

Solo acepta pulsaciones y mensajes del chat configurado (TELEGRAM_CHAT_ID).
"""

import asyncio
import logging
import time

import aiohttp

try:  # macOS empaquetado: certificados raiz de certifi (ver tls.py)
    from tls import SSL_CTX
except ImportError:
    SSL_CTX = None

log = logging.getLogger("telegram")


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.chat_id = str(chat_id)
        self.session: aiohttp.ClientSession | None = None
        self.offset = 0
        # Callbacks que monta main.py
        self.on_button = None    # async (action, call_id, message_id)
        self.on_text = None      # async (text, reply_to_message_id)
        # Telegram castiga el exceso de mensajes (429 «retry after N»): mientras dure, los refrescos
        # de precio se saltan y lo importante (respuestas, compras, ventas) espera su turno
        self.blocked_until = 0.0
        self.last_text_id = None  # id del ultimo mensaje TUYO (para borrar lo que escribes en un custom)

    async def _call(self, method: str, **params):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=SSL_CTX) if SSL_CTX else None)
        try:
            async with self.session.post(self.base + method, json=params,
                                         timeout=aiohttp.ClientTimeout(total=40)) as r:
                data = await r.json()
                if not data.get("ok"):
                    log.warning("Telegram %s: %s", method, data.get("description"))
                    wait = (data.get("parameters") or {}).get("retry_after")
                    if data.get("error_code") == 429 and wait:
                        self.blocked_until = time.monotonic() + float(wait) + 1
                return data.get("result")
        except Exception as e:  # red caida: no tumbar el bot
            log.warning("Telegram %s fallo: %s", method, e)
            return None

    @property
    def blocked(self) -> bool:
        return time.monotonic() < self.blocked_until

    async def _wait_unblocked(self):
        if self.blocked:  # castigo de Telegram: esperar a que pase (lo importante no se pierde)
            await asyncio.sleep(self.blocked_until - time.monotonic())

    async def send(self, text: str, buttons=None, force_reply=False, silent=False, html=False,
                   reply_to: int | None = None):
        await self._wait_unblocked()
        params = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True,
                  "disable_notification": silent}
        if html:
            params["parse_mode"] = "HTML"
        if reply_to:  # en respuesta a la tarjeta (si ya no existe, se envia igual)
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        elif force_reply:
            params["reply_markup"] = {"force_reply": True}
        res = await self._call("sendMessage", **params)
        return res["message_id"] if res else None

    async def delete(self, message_id: int):
        await self._call("deleteMessage", chat_id=self.chat_id, message_id=message_id)

    async def edit(self, message_id: int, text: str, buttons=None, html=False, live=False):
        """Sin buttons, Telegram quita el teclado (estado final de la tarjeta).
        live=True: refresco de precio, prescindible -> si Telegram nos ha castigado, se salta."""
        if live and self.blocked:
            return
        await self._wait_unblocked()
        params = {"chat_id": self.chat_id, "message_id": message_id, "text": text,
                  "disable_web_page_preview": True}
        if html:
            params["parse_mode"] = "HTML"
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        await self._call("editMessageText", **params)

    @staticmethod
    def qty_rows(call_id: int, qty: int, max_qty: int | None):
        """Cuantos contratos. Hasta 6: un boton por cantidad (✔ en la elegida). Mas: ➖/➕,
        el numero del centro para escribirlo, y atajos ¼ ½ ¾ All."""
        def b(text, action):
            return {"text": text, "callback_data": f"{action}:{call_id}"}

        if max_qty and max_qty <= 6:
            return [[b(("✔ " if n == qty else "") + str(n), f"q{n}") for n in range(1, max_qty + 1)]]
        label = (f"{qty} / {max_qty}" if max_qty else f"{qty} contracts") + " ✏️"
        rows = [[b("➖", "qm"), b(label, "qx"), b("➕", "qp")]]
        if max_qty:
            presets = {}
            for name, f in (("¼", 0.25), ("½", 0.5), ("¾", 0.75), ("All", 1)):
                presets.setdefault(max(1, round(max_qty * f)), name)  # sin duplicados
            rows.append([b(("✔ " if n == qty else "") + f"{name} · {n}", f"q{n}")
                         for n, name in presets.items()])
        return rows

    @staticmethod
    def card_buttons(call_id: int, can_launch: bool, limit: float | None = None,
                     bid: float | None = None, ask: float | None = None, qty_rows=None,
                     ignore_label: str = "❌ Ignore", side: str = "BUY", tabs: bool = False):
        """side: lo que hacen los botones de precio (BUY/SELL), escrito en CADA boton.
        tabs: panel de posicion -> pestañas 🔴 SELL / 🟢 BUY MORE para cambiar de lado."""
        def b(text, action):
            return {"text": text, "callback_data": f"{action}:{call_id}"}

        if not can_launch:
            return [[b(ignore_label, "no")]]
        dot = "🟢" if side == "BUY" else "🔴"
        rows = []
        if tabs:
            rows.append([b(("✔ " if side == "SELL" else "") + "🔴 SELL", "ms"),
                         b(("✔ " if side == "BUY" else "") + "🟢 BUY MORE", "mb")])
        rows += qty_rows or []
        first = [b("⚡ Market", "mkt")]
        if limit is not None:  # la call trae precio: limite a ese precio como primera opcion
            first.insert(0, b(f"{dot} Call {limit:.2f}", "go"))
        rows.append(first)
        if bid is not None and ask is not None:  # precios rapidos con la ultima cotizacion
            rows.append([b(f"{dot} Bid {bid:.2f}", "bid"), b(f"{dot} Ask {ask:.2f}", "ask")])
        last = [b(f"{dot} ✏️ My price", "lim"), b("🔄 Refresh", "ref")]
        if ignore_label:  # panel de posicion: sin Dismiss (quitaria los botones y no podrias vender)
            last.append(b(ignore_label, "no"))
        rows.append(last)
        return rows

    @staticmethod
    def tpsl_rows(call_id: int, kind: str, options, qty: int):
        """Submenu (sustituye a los botones del panel): TP o SL por % sobre tu media.
        options: [(pct, precio)]."""
        def b(text, action):
            return {"text": text, "callback_data": f"{action}:{call_id}"}

        tp = kind == "tp"
        rows = [[b(f"⬇️ {'🎯 TAKE PROFIT' if tp else '🛑 STOP LOSS'} · {qty} contract"
                   f"{'s' if qty != 1 else ''} ⬇️", "nop")]]
        btns = [b(f"{'+' if tp else '−'}{pct}% · {px:.2f}", f"{'t' if tp else 's'}{pct}")
                for pct, px in options]
        rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
        rows.append([b("✏️ Custom %", "tcu" if tp else "scu"),
                     b(f"🗑 Remove {'TP' if tp else 'SL'}", "trm" if tp else "srm"), b("⬅️ Back", "bk")])
        return rows

    async def poll(self):
        """Bucle de getUpdates (long polling)."""
        await self._call("deleteWebhook")
        while True:
            ups = await self._call("getUpdates", offset=self.offset, timeout=25,
                                   allowed_updates=["callback_query", "message"])
            if ups is None:
                await asyncio.sleep(3)
                continue
            for u in ups:
                self.offset = u["update_id"] + 1
                try:
                    await self._handle(u)
                except Exception:
                    log.exception("error procesando update")

    async def _handle(self, u: dict):
        if cq := u.get("callback_query"):
            msg = cq.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                return  # no es tu chat: ignorar
            await self._call("answerCallbackQuery", callback_query_id=cq["id"])
            action, _, cid = cq.get("data", "").partition(":")
            if self.on_button and cid.isdigit():
                await self.on_button(action, int(cid), msg.get("message_id"))
        elif m := u.get("message"):
            if str(m.get("chat", {}).get("id")) != self.chat_id:
                return
            reply_to = (m.get("reply_to_message") or {}).get("message_id")
            self.last_text_id = m.get("message_id")
            if self.on_text and m.get("text"):
                await self.on_text(m["text"].strip(), reply_to)
