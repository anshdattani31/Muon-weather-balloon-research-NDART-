"""Replaceable detector and radio interfaces; no assumed Cosmic Watch firmware."""

import base64
import heapq
import json
import random
import time
from datetime import datetime, timezone


def host_timestamp(style):
    return time.time() if style == "Unix seconds" else datetime.now(timezone.utc).isoformat()


def normalize(raw, config, fallback_id=None):
    if not isinstance(raw, dict):
        raise ValueError("Input record must be a JSON object")
    mapping = config["field_mapping"]
    kind = raw.get(mapping["type"], "muon")
    if kind not in ("muon", "sensor", "gnss"):
        raise ValueError("Record type must be muon, sensor or gnss")
    coincidence = raw.get(mapping["coincidence"])
    if coincidence is not None and type(coincidence) is not bool:
        raise ValueError("Coincidence must be JSON true, false or null (not text / integer)")
    received = host_timestamp(config["timestamp_format"])
    device_time = raw.get(mapping["device_timestamp"])
    detector = raw.get(mapping["detector_id"], fallback_id)
    if kind == "muon" and (not isinstance(detector, str) or not detector):
        raise ValueError("Muon record requires a detector_id")
    return {
        "type": kind, "detector_id": detector, "received_at": received,
        "timestamp": device_time if config["timestamp_source"] == "Device (host fallback)" and device_time is not None else received,
        "device_timestamp": device_time, "coincidence": coincidence,
        "simulated": config["source"] == "Simulator", "raw": raw,
    }


class Simulator:
    def __init__(self, config):
        self.config = config
        self.ids = [s.strip() for s in config["detector_ids"].split(",")]
        self.next_muon = self.next_sensor = self.next_gnss = time.monotonic()
        self.sequence = 0

    def poll(self):
        now = time.monotonic()
        records = []
        # Bounded work per poll prevents acquisition from starving the transport.
        for _ in range(100):
            if now < self.next_muon:
                break
            self.sequence += 1
            records.append({"type": "muon", "detector_id": self.ids[(self.sequence - 1) % len(self.ids)],
                            "timestamp": time.time(), "count": 1, "simulation_sequence": self.sequence,
                            "coincidence": random.random() < self.config["simulation_coincidence"],
                            "pulse_placeholder": round(random.uniform(10, 100), 3)})
            self.next_muon += 1 / self.config["event_rate"]
        if now >= self.next_sensor:
            for detector in self.ids:
                records.append({"type": "sensor", "detector_id": detector, "timestamp": time.time(),
                                "temperature_c_placeholder": round(random.uniform(15, 25), 2),
                                "pressure_hpa_placeholder": round(random.uniform(990, 1020), 2),
                                "gyro_xyz_placeholder": [0.0, 0.0, 0.0]})
            self.next_sensor = now + self.config["sensor_interval"]
        if now >= self.next_gnss:
            records.append({"type": "gnss", "timestamp": time.time(), "fix_valid": False,
                            "latitude_placeholder": 0.0, "longitude_placeholder": 0.0, "altitude_m_placeholder": 0.0})
            self.next_gnss = now + self.config["gnss_interval"]
        return [(record, None) for record in records]

    def close(self):
        pass


class Replay:
    def __init__(self, config):
        self.handle = open(config["replay_file"], encoding="utf-8")
        self.config = config
        self.next_record = 0.0

    def poll(self):
        now = time.monotonic()
        if now < self.next_record:
            return []
        self.next_record = now + 1 / self.config["event_rate"]
        line = self.handle.readline(1024 * 1024 + 2)
        if not line and self.config["replay_loop"]:
            self.handle.seek(0)
            line = self.handle.readline(1024 * 1024 + 2)
        if not line:
            return []
        if len(line) > 1024 * 1024:
            raise ValueError("Replay input line exceeds 1 MiB")
        if not line.strip():
            return []
        return [(json.loads(line), None)]

    def close(self):
        self.handle.close()


class SerialLines:
    def __init__(self, port, baud):
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("Serial input needs pyserial. Run: python -m pip install -r requirements-serial.txt") from exc
        self.port = serial.Serial(port, baudrate=baud, timeout=0, write_timeout=0.25)
        self.buffer = bytearray()

    def read(self):
        self.buffer.extend(self.port.read(min(self.port.in_waiting, 65536)))
        if len(self.buffer) > 1024 * 1024:
            self.buffer.clear()
            raise ValueError("Serial line buffer exceeds 1 MiB; check baud and protocol")
        lines = []
        for _ in range(100):
            end = self.buffer.find(b"\n")
            if end < 0:
                break
            line = bytes(self.buffer[:end]).strip()
            del self.buffer[:end + 1]
            if line:
                lines.append(line.decode("utf-8"))
        return lines

    def send(self, envelope):
        data = (json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8")
        written = self.port.write(data)
        if written != len(data):
            raise OSError("Incomplete serial write")

    def close(self):
        self.port.close()


class SerialSource:
    def __init__(self, config):
        self.devices = []
        try:
            for port, detector in zip(config["serial_ports"].split(","), config["detector_ids"].split(",")):
                self.devices.append((SerialLines(port.strip(), config["source_baud"]), detector.strip()))
        except Exception:
            self.close()
            raise

    def poll(self):
        records = []
        for device, detector in self.devices:
            for line in device.read():
                try:
                    records.append((json.loads(line), detector))
                except ValueError:
                    records.append(({"type": "invalid", "raw_line": line}, detector))
        return records

    def close(self):
        for device, _ in self.devices:
            device.close()


def source_for(config):
    return {"Simulator": Simulator, "JSONL replay": Replay, "Serial JSONL": SerialSource}[config["source"]](config)


class Loopback:
    def __init__(self, config):
        self.config = config
        self.frames = []
        self.serial = 0

    def _put(self, frame):
        if random.random() < self.config["simulation_loss"]:
            return
        for _ in range(1 + int(random.random() < self.config["simulation_duplicates"])):
            self.serial += 1
            heapq.heappush(self.frames, (time.monotonic() + random.random() * self.config["simulation_reorder"], self.serial, frame))

    def send_packet(self, packet):
        if random.random() < self.config["simulation_corruption"]:
            damaged = bytearray(packet)
            damaged[-1] ^= 1
            packet = bytes(damaged)
        self._put(("packet", packet))

    def send_ack(self, key):
        self._put(("ack", key))

    def poll(self):
        frames = []
        while self.frames and self.frames[0][0] <= time.monotonic() and len(frames) < 100:
            frames.append(heapq.heappop(self.frames)[2])
        return frames

    def close(self):
        pass


class SerialBridge:
    def __init__(self, config):
        self.device = SerialLines(config["radio_port"], config["radio_baud"])

    def send_packet(self, packet):
        self.device.send({"kind": "packet", "payload": base64.b64encode(packet).decode("ascii")})

    def send_ack(self, key):
        self.device.send({"kind": "ack", "session": key[0], "message": key[1]})

    def poll(self):
        frames = []
        for line in self.device.read():
            try:
                frame = json.loads(line)
                if frame["kind"] == "packet":
                    packet = base64.b64decode(frame["payload"], validate=True)
                    frames.append(("packet", packet))
                elif frame["kind"] == "ack":
                    session, message = frame["session"], frame["message"]
                    if not isinstance(session, str) or len(bytes.fromhex(session)) != 8 or type(message) is not int or not 0 <= message <= 0xffffffff:
                        raise ValueError("Invalid ACK identity")
                    frames.append(("ack", (session, message)))
                else:
                    raise ValueError("Unknown bridge frame kind")
            except (ValueError, KeyError, TypeError) as exc:
                frames.append(("error", f"Invalid bridge frame: {exc}"))
        return frames

    def close(self):
        self.device.close()
