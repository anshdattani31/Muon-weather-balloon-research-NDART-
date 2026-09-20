"""Settings shared by the desktop form, CLI and acquisition engine."""

import copy
import json
import math
from pathlib import Path

# key: (tab, label, default, choices); None choices means an editable value.
FIELDS = {
    "mode": ("Devices", "Application role", "Onboard + simulated ground", ["Onboard + simulated ground", "Onboard", "Ground receiver"]),
    "source": ("Devices", "Detector input", "Simulator", ["Simulator", "JSONL replay", "Serial JSONL"]),
    "serial_ports": ("Devices", "Detector serial ports (comma separated)", "", None),
    "detector_ids": ("Devices", "Detector IDs (comma separated)", "CW1,CW2", None),
    "source_baud": ("Devices", "Detector serial baud", 115200, None),
    "replay_file": ("Devices", "JSONL replay file", "", None),
    "replay_loop": ("Devices", "Repeat replay file", False, [False, True]),
    "event_rate": ("Devices", "Simulated / replay muon records per second", 2.0, None),
    "sensor_interval": ("Devices", "Simulated sensor interval (seconds)", 5.0, None),
    "gnss_interval": ("Devices", "Simulated GNSS interval (seconds)", 5.0, None),
    "simulation_coincidence": ("Devices", "Simulated coincidence fraction (0 to 1)", 1.0, None),
    "event_selection": ("Data", "Muon records to transmit", "All muon records", ["All muon records", "Confirmed coincidences only"]),
    "transmit_sensors": ("Data", "Transmit sensor records", False, [False, True]),
    "transmit_gnss": ("Data", "Transmit GNSS records", False, [False, True]),
    "transmit_raw_fields": ("Data", "Raw fields to transmit (comma list; blank = all)", "", None),
    "timestamp_source": ("Data", "Preferred event timestamp", "Host receipt", ["Host receipt", "Device (host fallback)"]),
    "timestamp_format": ("Data", "Host timestamp format", "UTC ISO 8601", ["UTC ISO 8601", "Unix seconds"]),
    "storage": ("Storage", "Storage format", "CSV", ["CSV", "JSONL", "SQLite"]),
    "output_dir": ("Storage", "Output directory", "data", None),
    "session_label": ("Storage", "Flight / session label", "flight", None),
    "rotate_records": ("Storage", "Records per file (0 = no rotation)", 10000, None),
    "sync_to_disk": ("Storage", "Flush records to disk (slower)", True, [False, True]),
    "radio": ("Radio", "Radio transport", "Loopback simulator", ["Loopback simulator", "Disabled (local logging)", "Serial bridge"]),
    "radio_port": ("Radio", "Radio serial port", "", None),
    "radio_baud": ("Radio", "Radio serial baud", 115200, None),
    "packet_bytes": ("Radio", "Maximum radio payload bytes (25 to 254)", 200, None),
    "compression": ("Radio", "Record compression", "None", ["None", "zlib"]),
    "packet_interval": ("Radio", "Minimum seconds between fragments", 0.1, None),
    "delivery": ("Radio", "Delivery policy", "ACK and retry", ["ACK and retry", "Best effort"]),
    "ack_timeout": ("Radio", "ACK wait / retry interval (seconds)", 5.0, None),
    "max_retries": ("Radio", "Additional attempts before deferring", 3, None),
    "retry_cooldown": ("Radio", "Deferred message retry delay (seconds)", 30.0, None),
    "reassembly_timeout": ("Radio", "Incomplete record timeout (seconds)", 30.0, None),
    "queue_limit": ("Reliability", "Maximum pending records", 10000, None),
    "queue_policy": ("Reliability", "When transmission queue fills", "Drop oldest pending", ["Drop oldest pending", "Drop newest transmission"]),
    "resume_queue": ("Reliability", "Resume pending transmissions on restart", True, [False, True]),
    "reconnect_interval": ("Reliability", "Disconnected device retry interval (seconds)", 5.0, None),
    "duration_seconds": ("Reliability", "Run duration seconds (0 = until Stop)", 0.0, None),
    "simulation_loss": ("Reliability", "Loopback packet loss fraction (0 to 1)", 0.0, None),
    "simulation_duplicates": ("Reliability", "Loopback duplicate fraction (0 to 1)", 0.0, None),
    "simulation_corruption": ("Reliability", "Loopback corruption fraction (0 to 1)", 0.0, None),
    "simulation_reorder": ("Reliability", "Loopback random delivery delay seconds", 0.0, None),
}

DEFAULT_MAPPING = {
    "type": "type", "detector_id": "detector_id", "device_timestamp": "timestamp",
    "coincidence": "coincidence",
}


def defaults():
    result = {key: spec[2] for key, spec in FIELDS.items()}
    result["field_mapping"] = copy.deepcopy(DEFAULT_MAPPING)
    return result


def validate(values):
    """Reject unsupported values instead of silently pretending to implement them."""
    unknown = set(values) - set(FIELDS) - {"field_mapping"}
    if unknown:
        raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
    config = defaults()
    config.update(values)
    for key, (_, label, default, choices) in FIELDS.items():
        value = config[key]
        if type(value) is not type(default):
            if isinstance(default, float) and type(value) is int:
                config[key] = float(value)
            else:
                raise ValueError(f"{label}: expected {type(default).__name__}")
        if choices is not None and config[key] not in choices:
            raise ValueError(f"{label}: unsupported selection")
        if isinstance(default, (float, int)) and not isinstance(default, bool):
            if not math.isfinite(config[key]) or config[key] < 0:
                raise ValueError(f"{label}: must be finite and nonnegative")
    for key in ("source_baud", "radio_baud", "event_rate", "sensor_interval", "gnss_interval", "ack_timeout", "retry_cooldown", "reassembly_timeout", "queue_limit", "reconnect_interval"):
        if config[key] <= 0:
            raise ValueError(f"{FIELDS[key][1]} must be greater than zero")
    for key in ("simulation_loss", "simulation_duplicates", "simulation_corruption", "simulation_coincidence"):
        if config[key] > 1:
            raise ValueError(f"{FIELDS[key][1]} must be between zero and one")
    if not 25 <= config["packet_bytes"] <= 254:
        raise ValueError("Maximum packet size must be between 25 and 254 bytes, including our header")
    if not config["output_dir"].strip():
        raise ValueError("Select an output directory")
    if not config["detector_ids"].strip():
        raise ValueError("Provide at least one detector ID")
    ids = [s.strip() for s in config["detector_ids"].split(",")]
    if any(not s for s in ids) or len(set(ids)) != len(ids):
        raise ValueError("Detector IDs must be nonempty and unique")
    if config["source"] == "JSONL replay" and config["mode"] != "Ground receiver":
        if not Path(config["replay_file"]).is_file():
            raise ValueError("Choose an existing JSONL replay file")
    if config["source"] == "Serial JSONL" and config["mode"] != "Ground receiver":
        ports = [p.strip() for p in config["serial_ports"].split(",") if p.strip()]
        if not ports or len(ports) != len(ids):
            raise ValueError("Provide one serial port for each detector ID")
        if len(set(ports)) != len(ports):
            raise ValueError("Each detector serial port must be unique")
        if config["radio"] == "Serial bridge" and config["radio_port"] in ports:
            raise ValueError("Detector and radio cannot use the same serial port")
    if config["radio"] == "Serial bridge" and not config["radio_port"].strip():
        raise ValueError("A radio serial port is required")
    if config["mode"] == "Ground receiver" and config["radio"] != "Serial bridge":
        raise ValueError("Ground receiver mode requires a serial bridge")
    if config["mode"] == "Onboard + simulated ground" and config["radio"] != "Loopback simulator":
        raise ValueError("Combined simulation mode requires the loopback simulator")
    mapping = config["field_mapping"]
    if not isinstance(mapping, dict) or set(mapping) != set(DEFAULT_MAPPING):
        raise ValueError("Field mapping must contain type, detector_id, device_timestamp and coincidence")
    if any(not isinstance(v, str) or not v for v in mapping.values()):
        raise ValueError("Field mapping values must be nonempty input field names")
    return config


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


def save(path, values):
    Path(path).write_text(json.dumps(validate(values), indent=2) + "\n", encoding="utf-8")
