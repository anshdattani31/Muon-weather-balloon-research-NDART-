# N-Dart configurable Python application

A desktop settings window plus a headless Raspberry Pi / ground receiver application. The default profile runs with simulated detectors and simulated radio, without extra Python packages.

## Start here

From the Python folder in VS Code's terminal:

```powershell
python main.py
```

1. Leave the defaults selected for the first run.
2. Open **Storage**, then choose **CSV**, **JSONL**, or **SQLite**.
3. Click **Start**. Watch the received-record and acknowledgement counters.
4. Click **Stop** before changing settings.
5. Use **Save profile** to keep a setup and **Load profile** to restore it later.

Each run creates a new session under `data/` by default. The window's log shows the full path. Relative paths are relative to the directory where you launch Python. Choose absolute paths if you will launch from different directories. This application does not modify the Cosmic Watches' own SD cards.

Python 3.10 or newer is required. Tkinter is used for the desktop. The headless application does not require Tkinter. Your current Python installation has Tkinter and SQLite available.

## What can be selected

| Area | Available choices |
| --- | --- |
| Application role | Onboard with simulated ground, onboard, separate ground receiver |
| Detector input | Simulator, JSONL file replay, serial JSONL adapter |
| Device identity and connection | Detector IDs, serial ports, serial baud |
| Simulation and replay | Record rate, coincidence fraction, replay looping |
| Simulated environmental data | Sensor and GNSS intervals |
| Event selection | All muon records or explicitly marked coincidences |
| Transmission contents | Sensor/GNSS inclusion and selected raw fields |
| Time | Host receipt or device timestamp with host fallback; host ISO UTC or Unix seconds |
| Storage | CSV, JSON Lines, SQLite; output folder, session name, file rotation, disk synchronization |
| Transport | Simulated radio, local logging only, serial bridge |
| Packet splitting | Maximum packet size, between-fragment interval, optional zlib compression |
| Delivery | Best effort or acknowledgements with retries |
| Recovery | ACK timeout, retries per round, later retry delay, incomplete-message timeout |
| Pending transmissions | Queue size, drop oldest pending or drop newest transmission, resume after restart |
| Device disconnection | Automatic reconnection interval |
| Run length | Timed run or run until Stop / Ctrl+C |
| Radio testing | Packet loss, duplicates, corruption, random delay / reordering |
| Input schema | Editable mapping for type, detector ID, device timestamp, coincidence |

Numeric settings are text fields; discrete choices are dropdowns. The settings are defined centrally in `ndart/config.py`. Adding a fundamentally different interface or algorithm requires a corresponding adapter implementation as well as its setting; a dropdown cannot infer an unknown hardware protocol.

Sensors and GNSS default to local-only storage, consistent with the earlier conversation. Radio retries and CRC checking are implementation choices, not previously confirmed experimental requirements. The default simulated rate of two muon records per second and sample values are placeholders.

## Headless operation

```powershell
python -m ndart --headless --seconds 10
python -m ndart --write-defaults my-profile.json
python -m ndart --headless --config my-profile.json
```

Use `--output PATH` to override the output folder. Ctrl+C stops cleanly. A zero duration runs until interrupted. A timed run stops acquisition and transmission at the deadline; it does not wait for every pending transmission to finish. Pending records remain in the ledger when queue resumption is enabled.

## Files and responsibilities

| File | Purpose |
| --- | --- |
| `main.py` | VS Code / desktop entry point |
| `ndart/config.py` | Setting definitions, defaults, validation, profile loading |
| `ndart/gui.py` | Tabs, dropdowns, profile buttons, status and log |
| `ndart/adapters.py` | Simulator, replay, serial detector and radio interfaces |
| `ndart/protocol.py` | Numbered fragments, CRC32, compression, reassembly |
| `ndart/storage.py` | Selectable record stores, durable queue and receipt ledger |
| `ndart/engine.py` | Connects acquisition, local saving, queuing and transmission |
| `docs/HARDWARE_ADAPTERS.md` | Exact software contracts to implement for real hardware |
| `docs/WALKTHROUGH.md` | Suggested order for understanding the code |
| `tests/test_ndart.py` | Data integrity and failure-recovery tests |

## Storage and restart behavior

Session folders contain `settings.json`, `system.jsonl`, and separate `onboard/` and `ground/` record folders. CSV has convenient common columns plus a complete `record_json` column for fields that differ between sensors. JSONL stores a complete JSON object per line. SQLite stores complete JSON plus common query columns. Rotation counts records, not bytes; zero disables it.

An internal SQLite ledger is used for reliability regardless of the chosen export format. It records pending transmissions and completed receipts. It is not a replacement for your selected CSV/JSONL/SQLite logs. With queue resumption enabled, onboard runs reuse `onboard_ledger.sqlite3` in the output root. Ground receiver runs use `ground_ledger.sqlite3`. Select the same output root to resume its queue. An operating-system lock prevents two runs with the same role from using that root simultaneously.

With resumption disabled, each onboard run uses an isolated session ledger; previous ledgers are left intact. Pending records retain their original contents and identity when resumed, although new packet size and compression settings apply. Choosing a smaller queue limit does not erase an existing larger backlog immediately: it drains over time and new insertions apply the overflow policy.

The active message is protected from the drop-oldest policy. If no other entry can be evicted, the new transmission is dropped. Local acquisition records remain available. Best effort removes a pending message after all its fragments are handed to the transport; that is not proof the ground received it.

Completed ground records are committed to the receipt ledger before being exported and acknowledged. If power fails between the ledger commit and CSV/JSONL/SQLite export, the complete record remains in the ledger but may be absent from the export. Use `python -m ndart.export_ledger --ledger PATH --output NEW_FOLDER --format CSV` to rebuild an export from the receipt ledger. Transport session/message IDs allow records from resumed runs to be identified.

No automatic retention deletion is implemented: session folders and ledgers are kept until you archive or remove them. Disk-write failures stop the run and are reported; radio I/O failures reconnect while local acquisition continues. There is a short unavoidable interval between saving a local record and adding it to the outbound ledger; a sudden power loss there leaves the local record without a queued transmission. Hardware buffering and power-loss protection must be checked on the actual system.

## Delivery protocol and current limits

Every transmitted JSON record is split into fragments if needed. Each fragment includes a version, random session ID, message number, index, total count and full-message CRC32. Packet size includes this application header and must be below 255 bytes. Fragment payloads are reassembled out of order; conflicting duplicates and checksum failures are rejected. Incomplete messages expire. Completed-message duplicates are suppressed using the receipt ledger. Missing entire messages in best-effort mode are not inferred from sequence gaps on the display.

ACK mode retries the entire message, not just the missing fragment. After the configured retry count it defers that message and tries it again later, allowing other messages to proceed. Both endpoints must use compatible ACK policy and protocol. A single message is transmitted at a time. Large records, small packets, high event rates and long ACK waits can exceed radio capacity. The simulator does not calculate LoRa airtime or prove that every live event can reach the ground.

Safety limits: 1 MiB per record, 8,192 fragments per record, and 128 simultaneously incomplete received records. Very small packet sizes can therefore reject a large record even below 1 MiB. CRC32 detects accidental damage; it provides no encryption or authentication. Those features are not implemented or offered as working selections.

Physical coincidence calculation, device clock synchronization, exact detector output parsing, real GNSS extraction, Heltec firmware and radio frequency/power configuration remain dependent on verified hardware. Sensor interval controls currently apply to the simulator; setting them does not reprogram a Cosmic Watch. Device timestamps are preserved as supplied and are not converted between units or clock domains. A muon record may represent one or several counts; counters in the UI count records, not inferred physical muons. No automatic pairing/deduplication of the two watches is assumed.

Automatic startup is an operating-system deployment step rather than a live application toggle. `docs/ndart.service.example` shows an optional Pi service template; it is not installed. Do not run a real flight until hardware adapters, throughput, coincidence semantics, timings and failure behavior have been validated with the equipment.

## Verify

```powershell
python -m unittest discover -s tests -v
python tools/check_gui.py
```

Tests cover every storage format, packet limits, reconstruction, corruption, duplicates, incomplete fragments, timestamp mapping, queue persistence, retry after loss, a disconnected radio, and storage failure. The GUI check creates a hidden application window, changes settings, starts a short simulation, and checks the output.
