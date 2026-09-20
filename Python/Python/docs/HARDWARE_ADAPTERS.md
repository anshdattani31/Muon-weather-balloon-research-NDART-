# Hardware adapter contract

The serial modes are implemented generic interfaces. They do not claim that stock Cosmic Watch or Heltec firmware already speaks this protocol. Confirm the actual firmware and output first.

## Detector input

Install the optional serial package only when using serial hardware:

```powershell
python -m pip install -r requirements-serial.txt
```

The built-in serial adapter expects UTF-8 JSON objects separated by newline characters. For example:

```json
{"type":"muon","detector_id":"CW1","timestamp":123.45,"coincidence":true,"count":1}
```

`type` may be `muon`, `sensor`, or `gnss`; it defaults to `muon` if missing. `detector_id` may come from the configured port-to-ID list. Port IDs are matched in list order and should use stable device paths on the Pi. `coincidence` must be a JSON boolean or null; absence means unknown. Unrecognized record types are rejected and logged. Extra fields are preserved under `raw`. Configure alternate top-level key names on the Field mapping tab.

Serial JSONL supports several ports. GNSS or sensor messages can appear on those streams. If actual GNSS uses a separate Heltec-specific command channel, implement that source before selecting it; the radio adapter does not currently extract GNSS automatically.

For output that is CSV, binary, or another device-specific protocol, implement a source with:

- `poll()`: return a short bounded list of `(raw_record_dict, fallback_detector_id)` pairs; return an empty list if nothing is available.
- `close()`: release connections.

Register that source in `source_for()` and add its configuration choice and validation. Keep parsing and device commands here, not in `engine.py`. An `OSError` from polling triggers reconnection; malformed JSON / values are logged as rejected records. One disconnected serial detector currently reopens the detector source as a group. There is no independent per-detector recovery worker yet.

The replay adapter accepts the same raw JSONL schema. `examples/detector-records.jsonl` is explicitly simulated sample data, not measured Cosmic Watch output. Replay is paced at the configured record rate, not the original timestamp intervals.

## Radio serial bridge

The Python-to-board serial envelope is one JSON line:

```json
{"kind":"packet","payload":"BASE64_OF_BINARY_FRAGMENT"}
```

The transmitting board must decode the base64 value and send ONLY those binary fragment bytes over LoRa. The receiving board must base64-encode the received radio payload and provide the same JSON envelope to Python. The serial JSON/base64 wrapper is larger than the binary fragment and is not subject to the application's 254-byte radio-payload setting. Do not send the wrapper unchanged over LoRa.

After a complete message is committed on the receiver, Python emits:

```json
{"kind":"ack","session":"0123456789abcdef","message":1}
```

The two board firmwares must carry that acknowledgement over the return radio link and emit the same JSON line to the sender's Python process. An ACK confirms the whole message. Use a distinct board-level ACK frame type so it cannot be confused with a data fragment. Radio parameters and turnaround timing must be configured in the firmware. Board debug messages must not be mixed into this JSON stream, or they will be logged as invalid frames.

Use Onboard / Serial bridge on the Pi and Ground receiver / Serial bridge on the receiving computer. Both must have the optional serial dependency. The receiver accepts any valid application packet up to 254 bytes regardless of its local packet-size setting. Its incomplete-message timeout must exceed the time needed to receive the sender's largest fragmented message, allowing for delays and retries.

## Binary data packet version 1

Network byte order (big endian), Python struct `!2sBB8sIHHI`, 24-byte header:

| Field | Bytes | Meaning |
| --- | ---: | --- |
| Magic | 2 | ASCII ND |
| Version | 1 | 1 |
| Flags | 1 | 0 = JSON UTF-8, 1 = zlib-compressed JSON UTF-8 |
| Session | 8 | Random session identity |
| Message | 4 | Unsigned message number |
| Index | 2 | Zero-based fragment index |
| Total | 2 | Number of fragments |
| CRC | 4 | CRC32 of concatenated encoded message bytes |
| Data | Variable | Slice of encoded message |

The configured limit is for the header plus data. Firmware may impose a smaller usable radio payload; select the lower value. CRC covers the encoded message, not each header, and is not cryptographic authentication. Changes to the binary schema require a new protocol version and compatible endpoints.

## Adding other choices

Storage writers implement `write(record)` and `close()`; update the storage factory/selection when adding a new one. Radio adapters implement `send_packet(bytes)`, `send_ack((session_hex, message_int))`, `poll()` and `close()`. Radio `poll()` returns tuples with kind `packet`, `ack` or `error`. Keep calls bounded and nonblocking wherever possible. New record formats must preserve the distinction between event time and host receipt time.
