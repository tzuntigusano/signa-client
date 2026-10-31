"""LISTA DE CAMBIOS de cada versión (CHANGELOG.md, en la raíz del repo): tools/release.py la mete en latest.json, el
servidor la manda a la app con la versión publicada y el aviso de actualización enseña lo nuevo de TODAS las
versiones que te saltas (de la tuya a la publicada), no solo la última.

Formato del fichero: «## 0.1.17» por versión (la más nueva arriba) y debajo una línea «- …» por cambio (si no cabe,
sigue en la línea de abajo con sangría)."""
import re

KEEP = 15  # versiones que viajan en latest.json (quien venga de más atrás ve las 15 últimas)


def version_tuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in str(v).split("."))


def parse(text: str) -> list[dict]:
    """[{version, notes: [str]}], en el orden del fichero (la más nueva primero)."""
    out: list[dict] = []
    for line in text.splitlines():
        if m := re.match(r"^##\s+v?(\d+(?:\.\d+)+)\s*$", line.strip()):
            out.append({"version": m.group(1), "notes": []})
        elif out and (m := re.match(r"^[-*]\s+(.+)$", line.strip())):
            out[-1]["notes"].append(m.group(1).strip())
        elif out and out[-1]["notes"] and line.startswith((" ", "\t")) and line.strip():  # viñeta en varias líneas
            out[-1]["notes"][-1] += " " + line.strip()
    return out


def between(changes: list[dict] | None, have: str, latest: str) -> list[dict]:
    """Lo nuevo para quien tiene `have` y va a instalar `latest`: las versiones > have y <= latest, más nueva arriba."""
    lo, hi = version_tuple(have), version_tuple(latest)
    got = [c for c in changes or [] if isinstance(c, dict) and c.get("notes")
           and lo < version_tuple(c.get("version", "0")) <= hi]
    return sorted(got, key=lambda c: version_tuple(c["version"]), reverse=True)


def as_html(changes: list[dict]) -> str:
    """Para el aviso de la app: cada versión con sus cambios en viñetas."""
    from html import escape
    parts = []
    for c in changes:
        items = "".join(f"<li>{escape(n)}</li>" for n in c["notes"])
        parts.append(f"<b>v{escape(c['version'])}</b><ul style='margin-top:2px; margin-left:-20px'>{items}</ul>")
    return "".join(parts)
