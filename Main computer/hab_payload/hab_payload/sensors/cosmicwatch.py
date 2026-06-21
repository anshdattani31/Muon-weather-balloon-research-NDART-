"""
sensors/cosmicwatch.py
======================
Reads event lines from the CosmicWatch v3X (Raspberry Pi Pico-based) detector
over its USB-serial port.

CosmicWatch v3X default serial output format (one line per detected event):
    <event_id>, <ADC_value>, <SiPM_temp_C>, <deadtime_us>, <unix_time_ms>

Example line:
    42, 2891, 24.3, 1200, 1718000123456

This driver:
  - Reads all lines accumulated since the last call in a background thread
  - On `read_counts()` it atomically returns and resets the counter
  - Also tracks average ADC value (proxy for muon energy deposition)

Install:
    pip3 install pyserial
"""

import logging
import threading
import time
import serial

log = logging.getLogger(__name__)


class CosmicWatchSensor:
    """
    Accumulates CosmicWatch event counts between calls to read_counts().
    Designed for the v3X USB-serial output format.
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 9600,
                 read_timeout: float = 1.0):
        self.port    = port
        self.baudrate = baudrate

        self._lock       = threading.Lock()
        self._count      = 0
        self._adc_sum    = 0
        self._serial     = None
        self._running    = False
        self._thread     = None
        self._last_event_id = None

        self._start_reader(read_timeout)

    # ── Background thread ──────────────────────────────────────────────────────

    def _start_reader(self, timeout: float):
        try:
            self._serial = serial.Serial(
                self.port, self.baudrate, timeout=timeout
            )
            self._running = True
            self._thread  = threading.Thread(
                target=self._reader_loop, daemon=True, name="cw-reader"
            )
            self._thread.start()
            log.info(f"CosmicWatch reader started on {self.port}")
        except Exception as e:
            log.error(f"CosmicWatch UART open failed ({self.port}): {e} — simulation mode")
            self._running = False

    def _reader_loop(self):
        """
        Parse each line from the Pico.  Each valid event line increments the
        shared counter.  Non-event lines (headers, blank lines) are silently
        skipped.
        """
        while self._running:
            try:
                raw = self._serial.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").strip()
                if not line or line.startswith("#"):
                    continue  # header / comment lines

                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 2:
                    continue

                try:
                    event_id = int(parts[0])
                    adc_val  = int(parts[1])
                except ValueError:
                    log.debug(f"CW non-data line: {line}")
                    continue

                # Guard against duplicate event IDs if Pico resets mid-flight
                if event_id == self._last_event_id:
                    continue
                self._last_event_id = event_id

                with self._lock:
                    self._count   += 1
                    self._adc_sum += adc_val

            except serial.SerialException as e:
                log.error(f"CosmicWatch serial error: {e}")
                time.sleep(1.0)
            except Exception as e:
                log.debug(f"CosmicWatch parse exception: {e}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def read_counts(self) -> dict:
        """
        Atomically return and reset the event counter.

        Returns:
            dict:
              count     – number of coincidence events since last call
              adc_avg   – mean ADC value of those events (proxy for energy)
              valid     – True if reading from real hardware
        """
        if self._running:
            with self._lock:
                count   = self._count
                adc_avg = round(self._adc_sum / count, 1) if count > 0 else 0.0
                # Reset accumulators
                self._count   = 0
                self._adc_sum = 0
            return {"count": count, "adc_avg": adc_avg, "valid": True}

        # Simulation: Poisson-ish muon rate.
        # Surface rate ≈ 1 count/cm²/min.  Scintillator ~5×5 cm = 25 cm²
        # → ~4 counts/10s at surface; rises with altitude (≈×4–8 at 30 km).
        import random
        count = int(random.gauss(6, 2))
        count = max(0, count)
        return {
            "count":   count,
            "adc_avg": round(random.gauss(2900, 200), 1),
            "valid":   False,
        }

    def close(self):
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        log.info("CosmicWatch reader closed")
