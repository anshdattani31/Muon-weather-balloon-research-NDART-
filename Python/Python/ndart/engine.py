"""Acquisition loop used by both the desktop application and the CLI."""

import json
import threading
import time
import uuid
from datetime import datetime, timezone

from .adapters import Loopback, SerialBridge, normalize, source_for
from .config import validate
from .protocol import Reassembler, encode_record, fragment
from .storage import Ledger, OutputLock, RecordStore, new_session


class Engine:
    def __init__(self, config, notify=None):
        self.config = validate(config)
        self.notify = notify or (lambda event: None)
        self.stop_event = threading.Event()
        self.stats = {"collected": 0, "received": 0, "sent_fragments": 0, "acked": 0,
                      "retries": 0, "duplicates": 0, "dropped_transmissions": 0, "errors": 0,
                      "expired_messages": 0, "pending": 0, "last_received": "—"}
        self.source = self.radio = self.ledger = self.local = self.ground = self.error_file = None
        self.output_lock = None
        self.source_retry_at = self.radio_retry_at = 0.0
        self.active = None
        self.next_packet = 0.0
        self.session = uuid.uuid4().bytes[:8]
        self.sequence = 0
        self.reassembler = Reassembler(self.config["reassembly_timeout"])

    def stop(self):
        self.stop_event.set()

    def log(self, message, error=False):
        if error:
            self.stats["errors"] += 1
        event = {"time": datetime.now(timezone.utc).isoformat(), "kind": "error" if error else "log", "message": str(message)}
        if self.error_file:
            self.error_file.write(json.dumps(event) + "\n")
            self.error_file.flush()
        self.notify(event)

    def _setup(self):
        c = self.config
        root, folder = new_session(c)
        self.folder = folder
        self.error_file = (folder / "system.jsonl").open("a", encoding="utf-8")
        self.log(f"Session folder: {folder}")
        ground_only = c["mode"] == "Ground receiver"
        self.output_lock = OutputLock(root / ("ground.lock" if ground_only else "onboard.lock"))
        # A separate root ledger per role permits onboard and receiver to run together.
        ledger_path = root / ("ground_ledger.sqlite3" if ground_only else "onboard_ledger.sqlite3")
        if not c["resume_queue"] and not ground_only:
            ledger_path = folder / "isolated_ledger.sqlite3"
        self.ledger = Ledger(ledger_path)
        self.local = RecordStore(folder / "onboard", c["storage"], c["rotate_records"], c["sync_to_disk"])
        self.ground = RecordStore(folder / "ground", c["storage"], c["rotate_records"], c["sync_to_disk"])
        self._reconnect()
        self.log("Started. Simulator measurements are placeholders." if c["source"] == "Simulator" and not ground_only else "Started.")

    def _disconnect(self, name, exc):
        device = getattr(self, name)
        if device:
            try:
                device.close()
            except OSError:
                pass
        setattr(self, name, None)
        setattr(self, name + "_retry_at", time.monotonic() + self.config["reconnect_interval"])
        self.log(f"{name.capitalize()} unavailable; will reconnect: {exc}", True)
        if name == "radio" and self.active:
            self.active["index"] = 0
            self.active["deadline"] = None

    def _reconnect(self):
        c = self.config
        if not self.source and c["mode"] != "Ground receiver" and time.monotonic() >= self.source_retry_at:
            try:
                self.source = source_for(c)
            except OSError as exc:
                self._disconnect("source", exc)
        if not self.radio and c["radio"] != "Disabled (local logging)" and time.monotonic() >= self.radio_retry_at:
            try:
                self.radio = Loopback(c) if c["radio"] == "Loopback simulator" else SerialBridge(c)
            except OSError as exc:
                self._disconnect("radio", exc)

    def _collect(self):
        try:
            records = self.source.poll()
        except OSError as exc:
            self._disconnect("source", exc)
            return
        except (ValueError, UnicodeError) as exc:
            self.log(f"Input rejected: {exc}", True)
            return
        for raw, detector in records:
            try:
                # Simulator schema is fixed, independent of the hardware mapping.
                c = self.config
                if c["source"] == "Simulator":
                    from .config import DEFAULT_MAPPING
                    c = dict(c, field_mapping=DEFAULT_MAPPING)
                record = normalize(raw, c, detector)
                payload = encode_record(record)
            except (ValueError, TypeError) as exc:
                self.log(f"Input rejected: {exc}", True)
                continue
            self.local.write(record)
            self.stats["collected"] += 1
            if self.config["radio"] == "Disabled (local logging)":
                continue
            selected = record["type"] == "muon" and (c["event_selection"] == "All muon records" or record["coincidence"] is True)
            selected |= record["type"] == "sensor" and c["transmit_sensors"]
            selected |= record["type"] == "gnss" and c["transmit_gnss"]
            if not selected:
                continue
            if c["transmit_raw_fields"].strip():
                keys = [key.strip() for key in c["transmit_raw_fields"].split(",")]
                transmitted = dict(record, raw={k: v for k, v in record["raw"].items() if k in keys})
                payload = encode_record(transmitted)
            self.sequence += 1
            if self.sequence > 0xffffffff:
                self.session, self.sequence = uuid.uuid4().bytes[:8], 1
            try:
                fragment(payload, self.session, self.sequence, c["packet_bytes"], c["compression"])
            except ValueError as exc:
                self.stats["dropped_transmissions"] += 1
                self.log(f"Record saved locally but not queued: {exc}", True)
                continue
            _, dropped = self.ledger.enqueue(self.session.hex(), self.sequence, payload, c["queue_limit"], c["queue_policy"], self.active["row"][0] if self.active else None)
            self.stats["dropped_transmissions"] += dropped
            if dropped:
                self.log("Transmission queue full; configured drop policy applied. Local record retained.", True)

    def _receive(self):
        try:
            frames = self.radio.poll()
        except OSError as exc:
            self._disconnect("radio", exc)
            return
        except (ValueError, UnicodeError) as exc:
            self.log(f"Radio frame rejected: {exc}", True)
            return
        for kind, data in frames:
            if kind == "error":
                self.log(data, True)
            elif kind == "ack":
                if self.active and data == (self.active["row"][1], self.active["row"][2]):
                    self.ledger.remove(self.active["row"][0])
                    self.active = None
                    self.stats["acked"] += 1
            elif kind == "packet":
                try:
                    result = self.reassembler.accept(data)
                except (ValueError, UnicodeError) as exc:
                    self.log(f"Packet rejected: {exc}", True)
                    continue
                if result is None:
                    continue
                key, record = result
                if self.ledger.has_received(key):
                    self.stats["duplicates"] += 1
                else:
                    # The SQLite ledger is the durable receipt source of truth. Export
                    # formats remain useful even if a process dies between these writes.
                    stored = dict(record, transport_session=key[0], transport_message=key[1])
                    self.ledger.receive(key, stored)
                    self.ground.write(stored)
                    self.stats["received"] += 1
                    self.stats["last_received"] = datetime.now(timezone.utc).isoformat()
                if self.config["delivery"] == "ACK and retry":
                    try:
                        self.radio.send_ack(key)
                    except OSError as exc:
                        self._disconnect("radio", exc)
                        return

    def _transmit(self):
        now = time.monotonic()
        c = self.config
        if self.active is None:
            row = self.ledger.next(time.time())
            if row is None:
                return
            try:
                packets = fragment(row[3], bytes.fromhex(row[1]), row[2], c["packet_bytes"], c["compression"])
            except ValueError as exc:
                self.ledger.defer(row[0], time.time() + c["retry_cooldown"])
                self.log(f"Pending record incompatible with current packet settings: {exc}", True)
                return
            self.active = {"row": row, "packets": packets, "index": 0, "attempt": 0, "deadline": None}
        a = self.active
        if a["index"] < len(a["packets"]):
            if now < self.next_packet:
                return
            try:
                self.radio.send_packet(a["packets"][a["index"]])
            except OSError as exc:
                self._disconnect("radio", exc)
                return
            self.stats["sent_fragments"] += 1
            a["index"] += 1
            self.next_packet = now + c["packet_interval"]
            if a["index"] == len(a["packets"]):
                if c["delivery"] == "Best effort":
                    self.ledger.remove(a["row"][0])
                    self.active = None
                else:
                    a["deadline"] = now + c["ack_timeout"]
        elif now >= a["deadline"]:
            if a["attempt"] < c["max_retries"]:
                a["attempt"] += 1
                a["index"] = 0
                a["deadline"] = None
                self.stats["retries"] += 1
            else:
                self.ledger.defer(a["row"][0], time.time() + c["retry_cooldown"])
                self.log(f"Message {a['row'][2]} unacknowledged; retained for a later retry.")
                self.active = None

    def run(self):
        failure = None
        try:
            self._setup()
            start = time.monotonic()
            next_status = 0.0
            while not self.stop_event.is_set():
                if self.config["duration_seconds"] and time.monotonic() - start >= self.config["duration_seconds"]:
                    break
                self._reconnect()
                if self.source:
                    self._collect()
                if self.radio:
                    self._receive()
                    if self.radio and self.config["mode"] != "Ground receiver":
                        self._transmit()
                self.reassembler.expire()
                self.stats["expired_messages"] = self.reassembler.expired
                if time.monotonic() >= next_status:
                    self.stats["pending"] = self.ledger.count()
                    self.notify({"kind": "status", **self.stats})
                    next_status = time.monotonic() + 0.25
                self.stop_event.wait(0.005)
            self.stats["pending"] = self.ledger.count()
            self.log(f"Stopped. {self.stats['pending']} pending transmissions retained in the ledger.")
        except Exception as exc:
            failure = str(exc)
            try:
                self.log(f"Run stopped: {exc}", True)
            except Exception:
                self.notify({"kind": "error", "message": failure})
        finally:
            if self.ledger:
                try:
                    self.stats["pending"] = self.ledger.count()
                except Exception:
                    pass
            for resource in (self.source, self.radio, self.local, self.ground, self.ledger, self.error_file, self.output_lock):
                if resource:
                    try:
                        resource.close()
                    except Exception as exc:
                        failure = failure or str(exc)
            self.notify({"kind": "finished", "failure": failure, **self.stats})
        return failure
