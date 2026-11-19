"""(Ivan 18/11/2026) Lee los mensajes de un canal de Discord abierto en una VENTANA DE EDGE de este PC. Para los
proveedores que publican en TARJETAS de bot (Namrood): el aviso de Windows de una tarjeta solo dice «@Namrood», sin
el contrato, así que por notificaciones no se pueden leer.

Cómo: el agente abre (y mantiene abierta) una ventana de Edge APARTE (su propio perfil, con la sesión de Discord de
Ivan iniciada una vez) en ese canal, con el puerto de depuración local (solo 127.0.0.1). Se conecta a la página por
ese puerto (Chrome DevTools Protocol) y deja dentro un vigilante que, cada vez que aparece un mensaje nuevo, le pasa
su texto (el de la tarjeta incluido). Solo LEE lo que tu Discord ya recibió y enseña: no llama a Discord ni usa la
cuenta para nada. La ventana puede estar minimizada o en una pantalla apagada (Edge se abre sin frenar la página en
segundo plano); no hace falta hacer scroll: el vigilante la mantiene abajo del todo, dentro de la página, sin tocar
el ratón. Si la ventana se cierra o deja de responder, se vuelve a abrir sola.

Configuración en agent.json:
    "dom": [{"provider": "namrood", "url": "https://discord.com/channels/<servidor>/<canal>",
             "port": 9233, "profile": "%LOCALAPPDATA%\\Signa\\edge-discord"}]
"""
import asyncio
import base64
import json
import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp

EDGE_PATHS = (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
              r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
              r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe")
DEFAULT_PORT = 9233
DEFAULT_PROFILE = r"%LOCALAPPDATA%\Signa\edge-discord"
HEARTBEAT = 30      # s: el vigilante da señales de vida (y baja la lista al final) cada tanto
DEAD_AFTER = 150    # s sin señales: se reconecta (y si no hay ventana, se abre)
BINDING = "signaMsg"

# El vigilante (va DENTRO de la página de Discord). Manda cada mensaje del canal UNA vez: id, autor, texto (el del
# mensaje + el de sus tarjetas) y hora. Al instalarse manda también los que ya están en pantalla (el servidor
# deduplica y lo viejo solo lo guarda). Un mensaje cuya tarjeta aún no se ha pintado espera al siguiente repaso.
WATCHER = r"""
(() => {
  if (window.__signaDom) { window.__signaDom.scan(); return 'already'; }
  const seen = new Set();
  const channel = () => location.pathname.split('/')[3] || '';
  const txt = el => (el ? el.innerText : '').trim();
  function grab(li) {
    const id = li.id.split('-').pop();
    const content = txt(li.querySelector('[id^="message-content-"]'));
    const cards = [...li.querySelectorAll('article')].map(a => a.innerText.trim()).filter(Boolean);
    const own = content.replace(/(^|\s)@\S+/g, ' ').trim();   // «@Namrood» solo no cuenta como texto
    if (!own && !cards.length) return null;
    const text = [content, ...cards].filter(Boolean).join('\n\n');
    let author = '', p = li;                    // los mensajes seguidos del mismo autor no repiten el nombre
    for (let i = 0; p && !author && i < 30; i++, p = p.previousElementSibling) {
      const u = p.querySelector && p.querySelector('[id^="message-username-"]');
      if (u) author = u.innerText.split('\n')[0].trim();
    }
    const t = li.querySelector('time');
    const m = {kind: 'msg', id, author, text, at: t ? t.dateTime : new Date().toISOString(), channel: channel()};
    // (19/11) tarjeta SIN el contrato en el texto: va solo en su IMAGEN («SPY / 728 PUT / EXP … / ENTRY 0.51»)
    const a = !/\b\d+(\.\d+)?\s?[CP]\b/.test(text) && li.querySelector('article a[href*="/attachments/"]');
    if (a && /\.(png|jpe?g|webp)(\?|$)/i.test(a.href)) m.img = a.href;
    return m;
  }
  // la imagen, preparada para el OCR: mitad izquierda (ahí va el contrato; a la derecha hay marcas de agua), x2 y
  // en blanco y negro (texto claro sobre fondo oscuro -> negro sobre blanco). La misma que ya enseña la página.
  async function prep(url) {
    const bmp = await createImageBitmap(await (await fetch(url)).blob());
    const W = Math.round(bmp.width * 0.5), H = bmp.height, K = 2;
    const c = document.createElement('canvas'); c.width = W * K; c.height = H * K;
    const g = c.getContext('2d'); g.drawImage(bmp, 0, 0, W, H, 0, 0, W * K, H * K);
    const d = g.getImageData(0, 0, c.width, c.height), p = d.data;
    for (let i = 0; i < p.length; i += 4) {
      const v = 0.299 * p[i] + 0.587 * p[i + 1] + 0.114 * p[i + 2];
      p[i] = p[i + 1] = p[i + 2] = v > 170 ? 0 : 255;
    }
    g.putImageData(d, 0, 0);
    return c.toDataURL('image/png');
  }
  async function send(m) {
    if (m.img) {
      try { m.img = await prep(m.img); } catch (e) { m.img = ''; m.img_error = String(e); }
    }
    window.signaMsg(JSON.stringify(m));
  }
  function scan() {
    for (const li of document.querySelectorAll('li[id^="chat-messages-' + channel() + '-"]')) {
      const id = li.id.split('-').pop();
      if (seen.has(id)) continue;
      const m = grab(li);
      if (!m) continue;
      seen.add(id);
      send(m);
    }
  }
  function beat() {
    const sc = document.querySelector('main [class*="messagesWrapper"] [class*="scroller"]')
            || document.querySelector('[class*="messagesWrapper"] [class*="scroller"]');
    let bottom = true;
    if (sc) {
      bottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 120;
      if (!bottom) sc.scrollTop = sc.scrollHeight;          // abajo del todo: ahí salen los nuevos
    }
    scan();
    window.signaMsg(JSON.stringify({kind: 'hb', url: location.href, n: seen.size, bottom, hidden: document.hidden}));
  }
  let timer = null;
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scan, 250); })
    .observe(document.body, {childList: true, subtree: true, characterData: true});
  setInterval(beat, %HB% * 1000);
  window.__signaDom = {scan};
  scan();
  beat();
  return 'ok';
})()
""".replace("%HB%", str(HEARTBEAT))


def expand(p: str) -> str:
    return os.path.expandvars(p)


def edge_exe() -> str | None:
    for p in EDGE_PATHS:
        if Path(expand(p)).exists():
            return expand(p)
    return None


async def ocr_image(data: bytes) -> str:
    """Texto de una imagen con el OCR de Windows (Windows.Media.Ocr: local, sin red). Una línea por renglón."""
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    stream = InMemoryRandomAccessStream()
    w = DataWriter(stream)
    w.write_bytes(data)
    await w.store_async()
    await w.flush_async()
    w.detach_stream()
    stream.seek(0)
    bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    eng = OcrEngine.try_create_from_language(Language("en-US")) or OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        raise RuntimeError("no Windows OCR language installed")
    return "\n".join(line.text for line in (await eng.recognize_async(bmp)).lines)


def channel_path(url: str) -> str:
    """«https://discord.com/channels/<servidor>/<canal>» -> «/channels/<servidor>/<canal>»."""
    i = url.find("/channels/")
    return url[i:].rstrip("/") if i >= 0 else url


class DomReader:
    """Una pestaña / un canal. on_message(provider, msg_id, author, text, iso_ts) por cada mensaje nuevo. (19/11,
    Swift) Varios canales con el MISMO puerto y perfil = una sola ventana de Edge con una PESTAÑA por canal: cada
    lector busca la suya y, si no está, abre otra (nunca se lleva la de otro canal)."""
    claimed: set[str] = set()  # los canales de todos los lectores

    def __init__(self, cfg: dict, on_message: Callable[..., Awaitable[None]], log: Callable[[str], None]):
        self.provider = cfg["provider"]
        self.url = cfg["url"]
        self.path = channel_path(self.url)
        DomReader.claimed.add(self.path)
        self.port = int(cfg.get("port") or DEFAULT_PORT)
        self.profile = expand(cfg.get("profile") or DEFAULT_PROFILE)
        self.on_message = on_message
        self.log = log
        self.sent: OrderedDict[str, None] = OrderedDict()  # ids ya mandados en esta ejecución (no se repiten)
        self.next_id = 0

    # --- la ventana
    async def _pages(self, s: aiohttp.ClientSession) -> list[dict] | None:
        try:
            async with s.get(f"http://127.0.0.1:{self.port}/json", timeout=aiohttp.ClientTimeout(total=5)) as r:
                return [t for t in await r.json(content_type=None) if t.get("type") == "page"]
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            return None

    def _block_app_links(self):
        """(19/11) Que Edge NO abra la app de escritorio de Discord: la web, al cargar, lanza un enlace «discord://»
        y Windows abría la app en ese canal. Se bloquea ese esquema en el perfil (solo se toca con Edge cerrado)."""
        prefs = Path(self.profile) / "Default" / "Preferences"
        try:
            d = json.loads(prefs.read_text(encoding="utf-8")) if prefs.exists() else {}
            ph = d.setdefault("protocol_handler", {})
            ex = ph.setdefault("excluded_schemes", {})
            if ex.get("discord") is True:
                return
            ex["discord"] = True  # True = bloqueado (no se lanza ni se pregunta)
            prefs.parent.mkdir(parents=True, exist_ok=True)
            prefs.write_text(json.dumps(d), encoding="utf-8")
        except (OSError, ValueError) as e:
            self.log(f"{self.provider}: could not block discord:// links in the reader profile ({e})")

    def _launch(self):
        exe = edge_exe()
        if not exe:
            raise RuntimeError("Microsoft Edge not found")
        Path(self.profile).mkdir(parents=True, exist_ok=True)
        self._block_app_links()
        # ventana NORMAL de Edge (19/11, Ivan: nada de modo «app»)
        args = [exe, f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
                "--remote-allow-origins=http://127.0.0.1", "--no-first-run", "--no-default-browser-check",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows", "--new-window", self.url]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7  # SW_SHOWMINNOACTIVE: minimizada y sin quitarte el foco
        subprocess.Popen(args, startupinfo=si, close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.log(f"{self.provider}: opened the Discord reader window (Edge, minimised)")

    async def _page(self, s: aiohttp.ClientSession) -> dict:
        """La pestaña del canal; si la ventana no está, la abre; si está en otra página de Discord, vuelve al canal."""
        pages = await self._pages(s)
        if pages is None:
            self._launch()
            for _ in range(30):
                await asyncio.sleep(1)
                if (pages := await self._pages(s)) is not None and any(p.get("url") for p in pages):
                    break
            else:
                raise RuntimeError("the Discord reader window did not start")
            await asyncio.sleep(5)  # que Discord cargue
            pages = await self._pages(s) or []
        for p in pages:
            if self.path in p.get("url", ""):
                return p
        others = DomReader.claimed - {self.path}
        for p in pages:
            url = p.get("url", "")
            if "discord.com" in url and not any(o in url for o in others):
                return p  # otra página de Discord (no la de otro lector): se lleva al canal al conectar
        try:  # la ventana es de otros canales: una pestaña nueva para este
            async with s.put(f"http://127.0.0.1:{self.port}/json/new?{self.url}",
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                p = await r.json(content_type=None)
            self.log(f"{self.provider}: opened a new tab for {self.path} in the Discord reader window")
            await asyncio.sleep(5)  # que Discord cargue
            return p
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as e:
            raise RuntimeError(f"no Discord page in the reader window and no new tab ({e})") from None

    # --- la conexión con la página
    async def _cmd(self, ws, method: str, **params) -> dict:
        self.next_id += 1
        await ws.send_json({"id": self.next_id, "method": method, "params": params})
        return {"id": self.next_id}

    async def session(self, s: aiohttp.ClientSession):
        page = await self._page(s)
        async with s.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0, heartbeat=20) as ws:
            await self._cmd(ws, "Runtime.enable")
            await self._cmd(ws, "Page.enable")
            await self._cmd(ws, "Runtime.addBinding", name=BINDING)
            await self._cmd(ws, "Page.addScriptToEvaluateOnNewDocument", source=WATCHER)  # si Discord recarga
            if self.path not in page.get("url", ""):
                await self._cmd(ws, "Page.navigate", url=self.url)
            else:
                await self._cmd(ws, "Runtime.evaluate", expression=WATCHER)
            self.log(f"{self.provider}: reading {self.path} in the Discord window")
            loop = asyncio.get_running_loop()
            last = loop.time()
            while True:
                try:
                    m = await ws.receive(timeout=DEAD_AFTER - (loop.time() - last))
                except asyncio.TimeoutError:
                    raise RuntimeError(f"no sign of life from the Discord window in {DEAD_AFTER}s")
                if m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                    raise RuntimeError("the Discord window was closed")
                if m.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(m.data)
                if d.get("method") == "Page.loadEventFired":  # recargó: el vigilante vuelve solo (addScript…)
                    last = loop.time()
                    continue
                if d.get("method") != "Runtime.bindingCalled" or d["params"].get("name") != BINDING:
                    continue
                last = loop.time()
                try:
                    msg = json.loads(d["params"]["payload"])
                except ValueError:
                    continue
                if msg.get("kind") == "hb":
                    if self.path not in str(msg.get("url")):  # alguien la movió de canal: de vuelta
                        self.log(f"{self.provider}: window was on {msg.get('url')}; back to the channel")
                        await self._cmd(ws, "Page.navigate", url=self.url)
                    continue
                await self._got(msg)

    async def _got(self, msg: dict):
        mid, text = str(msg.get("id") or ""), str(msg.get("text") or "").strip()
        if not mid or not text or mid in self.sent or msg.get("channel") not in self.path:
            return
        self.sent[mid] = None
        while len(self.sent) > 2000:
            self.sent.popitem(last=False)
        img = str(msg.get("img") or "")
        if img.startswith("data:image/"):  # el contrato solo venía en la imagen: se lee aquí, con el OCR de Windows
            try:
                seen = await ocr_image(base64.b64decode(img.split(",", 1)[1]))
            except Exception as e:  # sin OCR: llega el texto tal cual (el servidor lo dejará en «other»)
                self.log(f"{self.provider}: could not read the card's image ({type(e).__name__}: {e})")
            else:
                if seen.strip():
                    text += "\n\n[image]\n" + seen.strip()
        elif msg.get("img_error"):
            self.log(f"{self.provider}: could not load the card's image ({msg['img_error'][:120]})")
        await self.on_message(self.provider, mid, str(msg.get("author") or ""), text, str(msg.get("at") or ""))

    async def run(self):
        wait = 2
        loop = asyncio.get_running_loop()
        async with aiohttp.ClientSession() as s:
            while True:
                start = loop.time()
                try:
                    await self.session(s)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if loop.time() - start > 300:
                        wait = 2  # llevaba rato bien: es un corte nuevo, no el mismo fallo repitiéndose
                    self.log(f"{self.provider}: Discord window: {type(e).__name__}: {e}; retrying in {wait}s")
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, 60)
