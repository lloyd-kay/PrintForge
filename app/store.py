import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    """Atomic single-farm snapshots; protected by the service's process lock."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'printforge.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS farm (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def load(self):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM farm WHERE id=1').fetchone()
            return json.loads(row[0]) if row else None

    def save(self, state):
        payload = json.dumps(state, allow_nan=False)
        with self.connect() as db:
            db.execute('INSERT INTO farm(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (payload,))
