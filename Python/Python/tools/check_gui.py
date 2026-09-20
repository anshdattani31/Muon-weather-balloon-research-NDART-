"""Smoke-test the actual desktop controls without leaving an open window."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tkinter as tk

from ndart.config import FIELDS
from ndart.gui import App


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        root.update()
        assert len(app.variables) == len(FIELDS)
        with tempfile.TemporaryDirectory() as folder:
            for storage, suffix in (("CSV", "csv"), ("JSONL", "jsonl"), ("SQLite", "sqlite3")):
                app.variables["output_dir"].set(folder)
                app.variables["storage"].set(storage)
                app.variables["duration_seconds"].set("0.3")
                app.variables["packet_interval"].set("0.0")
                app.start()
                deadline = time.monotonic() + 10
                while app.worker.is_alive() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                if app.worker.is_alive():
                    app.stop()
                    app.worker.join(5)
                    raise AssertionError("GUI run did not terminate")
                app.poll()
                assert app.engine.stats["received"] > 0, app.status.get()
                assert list(Path(folder).glob("*/onboard/*." + suffix)), storage
            print(f"GUI check passed: {len(FIELDS)} settings and all three storage selections")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
