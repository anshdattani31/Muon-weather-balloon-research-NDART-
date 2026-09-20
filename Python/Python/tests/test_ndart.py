import csv
import json
import random
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ndart.adapters import Loopback, normalize
from ndart.config import defaults, load, save, validate
from ndart.engine import Engine
from ndart.export_ledger import export
from ndart.protocol import HEADER, Reassembler, encode_record, fragment
from ndart.storage import Ledger, OutputLock, RecordStore


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_reordered_duplicates_and_packet_limit(self):
        record = {"type": "muon", "raw": {"note": "μ" * 1000, "counts": list(range(200))}}
        for compression in ("None", "zlib"):
            for limit in (25, 100, 254):
                with self.subTest(compression=compression, limit=limit):
                    packets = fragment(encode_record(record), b"12345678", 42, limit, compression)
                    self.assertTrue(all(HEADER.size < len(p) <= limit < 255 for p in packets))
                    shuffled = packets + packets[:3]
                    random.Random(12).shuffle(shuffled)
                    receiver = Reassembler()
                    complete = [value for packet in shuffled if (value := receiver.accept(packet))]
                    self.assertEqual(complete[0], ((b"12345678".hex(), 42), record))

    def test_checksum_rejects_corruption(self):
        packets = fragment(b'{"hello":"world"}', b"12345678", 1, 254)
        damaged = packets[0][:-1] + bytes([packets[0][-1] ^ 1])
        with self.assertRaisesRegex(ValueError, "checksum"):
            Reassembler().accept(damaged)

    def test_missing_fragment_expires(self):
        receiver = Reassembler(timeout=2)
        packets = fragment(encode_record({"data": "x" * 100}), b"12345678", 1, 40)
        self.assertIsNone(receiver.accept(packets[0], now=0))
        self.assertEqual(receiver.expire(now=3), 1)
        self.assertFalse(receiver.pending)

    def test_different_messages_do_not_mix(self):
        receiver = Reassembler()
        a = fragment(encode_record({"data": "a" * 100}), b"12345678", 1, 50)
        b = fragment(encode_record({"data": "b" * 100}), b"12345678", 2, 50)
        results = []
        for one, two in zip(a, b):
            for packet in (one, two):
                result = receiver.accept(packet)
                if result:
                    results.append(result[1]["data"])
        self.assertEqual(results, ["a" * 100, "b" * 100])

    def test_invalid_headers_and_limits(self):
        for packet in (b"", b"x" * 255, b"x" * 25):
            with self.assertRaises(ValueError):
                Reassembler().accept(packet)
        for limit in (24, 255):
            with self.assertRaises(ValueError):
                fragment(b"{}", b"12345678", 1, limit)

    def test_conflicting_duplicate_rejected(self):
        packets = fragment(encode_record({"data": "x" * 100}), b"12345678", 1, 40)
        receiver = Reassembler()
        receiver.accept(packets[0])
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            receiver.accept(packets[0][:-1] + b"Z")


class ConfigurationTests(unittest.TestCase):
    def test_profile_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            save(path, defaults())
            self.assertEqual(load(path), defaults())

    def test_bad_settings(self):
        for key, value in (("packet_bytes", 255), ("event_rate", 0.0), ("event_rate", float("nan")), ("simulation_loss", 1.1), ("queue_limit", True), ("storage", "unknown"), ("detector_ids", "A,A")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate(dict(defaults(), **{key: value}))

    def test_mapping_and_preserved_fields(self):
        c = defaults()
        c["field_mapping"] = {"type": "kind", "detector_id": "unit", "device_timestamp": "clock", "coincidence": "matched"}
        c["timestamp_source"] = "Device (host fallback)"
        raw = {"kind": "muon", "unit": "A", "clock": 12.5, "matched": True, "other": [1, 2]}
        result = normalize(raw, c)
        self.assertEqual(result["timestamp"], 12.5)
        self.assertEqual(result["raw"], raw)
        self.assertTrue(result["coincidence"])
        with self.assertRaises(ValueError):
            normalize(dict(raw, matched="false"), c)


class StorageTests(unittest.TestCase):
    def test_ledger_export_preserves_receipt_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger_path = Path(folder) / "ledger.sqlite3"
            ledger = Ledger(ledger_path)
            expected = {"type": "muon", "raw": {"count": 1}, "transport_message": 9}
            ledger.receive(("1234567812345678", 9), expected)
            ledger.close()
            destination = Path(folder) / "recovered"
            self.assertEqual(export(ledger_path, destination, "JSONL"), 1)
            self.assertEqual(json.loads((destination / "records_00000.jsonl").read_text(encoding="utf-8")), expected)
            with self.assertRaises(ValueError):
                export(ledger_path, destination, "JSONL")

    def test_all_formats_preserve_records_and_rotate(self):
        records = [{"type": "muon", "detector_id": "A", "timestamp": 12 + i, "raw": {"x": [i, "μ"]}} for i in range(3)]
        for fmt, suffix in (("CSV", "csv"), ("JSONL", "jsonl"), ("SQLite", "sqlite3")):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as folder:
                store = RecordStore(folder, fmt, rotate=2)
                for record in records:
                    store.write(record)
                store.close()
                files = sorted(Path(folder).glob("*." + suffix))
                self.assertEqual(len(files), 2)
                recovered = []
                for path in files:
                    if fmt == "CSV":
                        with path.open(encoding="utf-8", newline="") as handle:
                            recovered.extend(json.loads(row["record_json"]) for row in csv.DictReader(handle))
                    elif fmt == "JSONL":
                        recovered.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
                    else:
                        db = sqlite3.connect(path)
                        recovered.extend(json.loads(row[0]) for row in db.execute("SELECT data_json FROM records ORDER BY id"))
                        db.close()
                self.assertEqual(recovered, records)

    def test_queue_persistence_policy_and_receipt_deduplication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.sqlite3"
            ledger = Ledger(path)
            self.assertEqual(ledger.enqueue("session", 1, b"one", 1, "Drop oldest pending"), (True, 0))
            row = ledger.next(time.time())
            self.assertEqual(ledger.enqueue("session", 2, b"two", 1, "Drop oldest pending", row[0]), (False, 1))
            ledger.close()
            ledger = Ledger(path)
            self.assertEqual(ledger.count(), 1)
            self.assertEqual(ledger.next(time.time())[3], b"one")
            self.assertEqual(ledger.enqueue("session", 2, b"two", 1, "Drop oldest pending"), (True, 1))
            ledger.receive(("session", 2), {"a": 1})
            ledger.receive(("session", 2), {"a": 1})
            self.assertTrue(ledger.has_received(("session", 2)))
            self.assertEqual(ledger.db.execute("SELECT COUNT(*) FROM received").fetchone()[0], 1)
            ledger.close()

    def test_output_lock_excludes_second_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "lock"
            first = OutputLock(path)
            try:
                with self.assertRaises(RuntimeError):
                    OutputLock(path)
            finally:
                first.close()
            second = OutputLock(path)
            second.close()


class EngineTests(unittest.TestCase):
    def run_until(self, config, condition):
        config = dict(config, duration_seconds=3.0)

        def notify(event):
            if event["kind"] == "status" and condition(event):
                engine.stop()

        engine = Engine(config, notify)
        self.assertIsNone(engine.run())
        self.assertTrue(condition(engine.stats), engine.stats)
        return engine

    def config(self, folder, **overrides):
        c = defaults()
        c.update(output_dir=folder, storage="JSONL", duration_seconds=0.35, event_rate=5.0,
                 packet_interval=0.0, ack_timeout=0.04, retry_cooldown=0.05, sync_to_disk=False)
        c.update(overrides)
        return c

    def test_end_to_end_each_storage(self):
        for storage in ("CSV", "JSONL", "SQLite"):
            with self.subTest(storage=storage), tempfile.TemporaryDirectory() as folder:
                engine = self.run_until(self.config(folder, storage=storage), lambda s: s["acked"] > 0 and s["pending"] == 0)
                self.assertGreater(engine.stats["received"], 0)
                self.assertEqual(engine.stats["received"], engine.stats["acked"])
                self.assertEqual(engine.stats["errors"], 0)

    def test_coincidence_selection_and_local_sensors(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(self.config(folder, event_selection="Confirmed coincidences only", simulation_coincidence=0.0))
            self.assertIsNone(engine.run())
            self.assertGreater(engine.stats["collected"], 0)
            self.assertEqual(engine.stats["received"], 0)

    def test_loss_retries_and_restart_resumes_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Engine(self.config(folder, simulation_loss=1.0))
            self.assertIsNone(first.run())
            self.assertGreater(first.stats["retries"], 0)
            self.assertGreater(first.stats["pending"], 0)
            second = self.run_until(self.config(folder, event_selection="Confirmed coincidences only", simulation_coincidence=0.0),
                                    lambda s: s["pending"] == 0 and s["received"] == first.stats["pending"])
            self.assertEqual(second.stats["pending"], 0)
            self.assertEqual(second.stats["received"], first.stats["pending"])

    def test_duplicate_delivery_does_not_duplicate_ground_records(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = self.run_until(self.config(folder, simulation_duplicates=1.0), lambda s: s["acked"] > 0 and s["pending"] == 0)
            db = sqlite3.connect(Path(folder) / "onboard_ledger.sqlite3")
            total = db.execute("SELECT COUNT(*) FROM received").fetchone()[0]
            db.close()
            self.assertEqual(total, engine.stats["received"])
            self.assertEqual(engine.stats["pending"], 0)

    def test_radio_disconnect_keeps_acquiring(self):
        class UnavailableRadio(Loopback):
            def send_packet(self, packet):
                raise OSError("disconnected")
        with tempfile.TemporaryDirectory() as folder, patch("ndart.engine.Loopback", UnavailableRadio):
            engine = Engine(self.config(folder))
            self.assertIsNone(engine.run())
            self.assertGreater(engine.stats["collected"], 4)
            self.assertGreater(engine.stats["pending"], 0)
            self.assertGreater(engine.stats["errors"], 0)

    def test_storage_failure_stops_run(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(RecordStore, "write", side_effect=OSError("disk full")):
            engine = Engine(self.config(folder))
            self.assertIn("disk full", engine.run())

    def test_ground_export_failure_preserves_durable_receipt(self):
        original_write = RecordStore.write

        def write(store, record):
            if store.folder.name == "ground":
                raise OSError("ground disk full")
            return original_write(store, record)

        with tempfile.TemporaryDirectory() as folder, patch.object(RecordStore, "write", write):
            engine = Engine(self.config(folder))
            self.assertIn("ground disk full", engine.run())
            self.assertEqual(export(Path(folder) / "onboard_ledger.sqlite3", Path(folder) / "recovered", "CSV"), 1)

    def test_replay_supports_all_three_record_types(self):
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(__file__).resolve().parents[1] / "examples" / "detector-records.jsonl"
            engine = Engine(self.config(folder, source="JSONL replay", replay_file=str(sample),
                                       event_rate=40.0, transmit_sensors=True, transmit_gnss=True))
            self.assertIsNone(engine.run())
            self.assertEqual(engine.stats["collected"], 4)
            self.assertEqual(engine.stats["received"], 4)

    def test_transmitted_fields_do_not_change_local_record(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(self.config(folder, transmit_raw_fields="count"))
            self.assertIsNone(engine.run())
            db = sqlite3.connect(Path(folder) / "onboard_ledger.sqlite3")
            record = json.loads(db.execute("SELECT record_json FROM received LIMIT 1").fetchone()[0])
            db.close()
            self.assertEqual(record["raw"], {"count": 1})
            local = next(Path(folder).glob("*/onboard/*.jsonl"))
            first = json.loads(local.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("pulse_placeholder", first["raw"])

    def test_disabled_radio_only_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(self.config(folder, mode="Onboard", radio="Disabled (local logging)"))
            self.assertIsNone(engine.run())
            self.assertGreater(engine.stats["collected"], 0)
            self.assertEqual(engine.stats["pending"], 0)
            self.assertEqual(engine.stats["sent_fragments"], 0)


if __name__ == "__main__":
    unittest.main()
