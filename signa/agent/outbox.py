"""Bandeja de salida en disco: cada mensaje del proveedor se guarda ANTES de mandarlo y solo se borra cuando el
servidor confirma. Si se cae la conexión (o el servidor), no se pierde ninguna call: se manda al volver."""
import json
import sqlite3
from pathlib import Path


class Outbox:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox (ref TEXT PRIMARY KEY, body TEXT, sent_at TEXT)")
        self.db.commit()

    def put(self, ref: str, body: dict):
        self.db.execute("INSERT OR IGNORE INTO outbox (ref, body) VALUES (?, ?)", (ref, json.dumps(body)))
        self.db.commit()

    def pending(self) -> list[dict]:
        return [json.loads(b) for (b,) in self.db.execute("SELECT body FROM outbox ORDER BY rowid")]

    def done(self, ref: str):
        self.db.execute("DELETE FROM outbox WHERE ref = ?", (ref,))
        self.db.commit()

    def __len__(self):
        return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
