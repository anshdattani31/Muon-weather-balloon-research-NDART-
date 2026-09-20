"""Session logs and durable transmission/receipt ledgers."""

import csv
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class OutputLock:
    """OS advisory lock, automatically released on process termination."""

    def __init__(self, path):
        self.handle = open(path, "a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Another run uses this output directory and role. Stop it or choose a different directory.") from exc

    def close(self):
        if not self.handle.closed:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()


def new_session(config):
    label = re.sub(r"[^A-Za-z0-9_-]", "_", config["session_label"])[:60] or "flight"
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + label + "_" + uuid.uuid4().hex[:8]
    root = Path(config["output_dir"]).resolve()
    directory = root / name
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "settings.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return root, directory


class RecordStore:
    """CSV retains variable fields in a JSON column, avoiding silent field loss."""

    def __init__(self, folder, storage, rotate=0, sync=True):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.storage, self.rotate, self.sync = storage, rotate, sync
        self.count = 0
        self.part = -1
        self.handle = self.database = self.writer = None

    def _open(self):
        part = self.count // self.rotate if self.rotate else 0
        if self.part == part:
            return
        self.close()
        self.part = part
        path = self.folder / f"records_{part:05d}"
        if self.storage == "SQLite":
            self.database = sqlite3.connect(str(path) + ".sqlite3")
            self.database.execute("PRAGMA synchronous=" + ("FULL" if self.sync else "NORMAL"))
            self.database.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY, type TEXT, timestamp TEXT, data_json TEXT NOT NULL)")
        else:
            suffix = ".csv" if self.storage == "CSV" else ".jsonl"
            file_path = Path(str(path) + suffix)
            exists = file_path.exists() and file_path.stat().st_size > 0
            self.handle = file_path.open("a", encoding="utf-8", newline="")
            if self.storage == "CSV":
                self.writer = csv.writer(self.handle)
                if not exists:
                    self.writer.writerow(["type", "detector_id", "timestamp", "received_at", "coincidence", "record_json"])

    def write(self, record):
        self._open()
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if self.database:
            with self.database:
                self.database.execute("INSERT INTO records(type,timestamp,data_json) VALUES (?,?,?)", (record.get("type"), str(record.get("timestamp", "")), encoded))
        elif self.storage == "CSV":
            # Full unmodified contents live in record_json. Neutralize spreadsheet formulas in display columns.
            def safe(value):
                text = str(value)
                return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
            self.writer.writerow([safe(record.get(k, "")) for k in ("type", "detector_id", "timestamp", "received_at", "coincidence")] + [encoded])
        else:
            self.handle.write(encoded + "\n")
        if self.handle:
            self.handle.flush()
            if self.sync:
                os.fsync(self.handle.fileno())
        self.count += 1

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None
        if self.database:
            self.database.close()
            self.database = None


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS pending (
              id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
              message INTEGER NOT NULL, payload BLOB NOT NULL, eligible REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS received (
              session TEXT NOT NULL, message INTEGER NOT NULL, record_json TEXT NOT NULL,
              PRIMARY KEY(session,message));
        """)

    def enqueue(self, session, message, payload, limit, policy, protected=None):
        dropped = 0
        with self.db:
            if self.count() >= limit:
                if policy == "Drop newest transmission":
                    return False, 1
                row = self.db.execute("SELECT id FROM pending WHERE id != ? ORDER BY id LIMIT 1", (protected or -1,)).fetchone()
                if row is None:
                    return False, 1
                self.db.execute("DELETE FROM pending WHERE id=?", row)
                dropped = 1
            self.db.execute("INSERT INTO pending(session,message,payload) VALUES (?,?,?)", (session, message, payload))
        return True, dropped

    def next(self, now):
        return self.db.execute("SELECT id,session,message,payload FROM pending WHERE eligible <= ? ORDER BY id LIMIT 1", (now,)).fetchone()

    def defer(self, row_id, until):
        with self.db:
            self.db.execute("UPDATE pending SET eligible=? WHERE id=?", (until, row_id))

    def remove(self, row_id):
        with self.db:
            self.db.execute("DELETE FROM pending WHERE id=?", (row_id,))

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    def has_received(self, key):
        return self.db.execute("SELECT 1 FROM received WHERE session=? AND message=?", key).fetchone() is not None

    def receive(self, key, record):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO received VALUES (?,?,?)", (*key, json.dumps(record, ensure_ascii=False, allow_nan=False)))

    def close(self):
        self.db.close()
