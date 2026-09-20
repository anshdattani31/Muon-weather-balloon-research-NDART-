"""Recover ground records from their durable ledger into a new export folder."""

import argparse
import json
import sqlite3
from pathlib import Path

from .storage import RecordStore


def export(ledger_path, output_dir, storage="CSV"):
    source = Path(ledger_path).resolve()
    destination = Path(output_dir).resolve()
    if not source.is_file():
        raise ValueError("Ledger file does not exist")
    if destination.exists():
        raise ValueError("Choose a new output folder to avoid overwriting an earlier export")
    if storage not in ("CSV", "JSONL", "SQLite"):
        raise ValueError("Unsupported output format")
    db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    store = None
    count = 0
    try:
        rows = db.execute("SELECT record_json FROM received ORDER BY session,message")
        destination.mkdir(parents=True, exist_ok=False)
        store = RecordStore(destination, storage)
        for row in rows:
            store.write(json.loads(row[0]))
            count += 1
    finally:
        if store:
            store.close()
        db.close()
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--format", choices=["CSV", "JSONL", "SQLite"], default="CSV")
    args = parser.parse_args()
    print(f"Exported {export(args.ledger, args.output, args.format)} records")


if __name__ == "__main__":
    main()
