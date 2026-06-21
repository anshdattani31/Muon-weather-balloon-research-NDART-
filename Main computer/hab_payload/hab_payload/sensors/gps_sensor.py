"""
sensors/gps_sensor.py
=====================
Reads NMEA sentences from the GT-U7 (or any NMEA GPS) over UART.
Parses GGA (position + fix quality) and RMC (speed + validity) sentences.

Install:
    pip3 install pyserial pynmea2
"""

import logging
import time
import threading
import serial

log = logging.getLogger(__name__)

try:
    import pynmea2
    _NMEA_AVAILABLE = True
except ImportError:
    log.warning("pynmea2 not installed — GPS will return simulation data")
    _NMEA_AVAILABLE = False


_SENTINEL = {
    "lat":       None,
    "lon":       None,
    "alt_m":     None,
    "speed_kmh": None,
    "satellites":0,
    "fix":       0,
    "valid":     False,
}


class GPSSensor:
    """
    Continuously reads NMEA sentences in a background thread so that
    `read()` always returns the most recent fix without blocking the main loop.
    """

    def __init__(self, port: str = "/dev/ttyAMA0", baudrate: int = 9600,
                 timeout: float = 2.0):
        self.port     = port
        self.baudrate = baudrate
        self.timeout  = timeout

        self._latest  = _SENTINEL.copy()
        self._lock    = threading.Lock()
        self._running = False
        self._serial  = None
        self._thread  = None

        self._start_reader()

    # ── Background thread ──────────────────────────────────────────────────────

    def _start_reader(self):
        try:
            self._serial = serial.Serial(
                self.port, self.baudrate, timeout=self.timeout
            )
            self._running = True
            self._thread  = threading.Thread(
                target=self._reader_loop, daemon=True, name="gps-reader"
            )
            self._thread.start()
            log.info(f"GPS reader started on {self.port} @ {self.baudrate}baud")
        except Exception as e:
            log.error(f"GPS UART open failed ({self.port}): {e} — simulation mode")
            self._running = False

    def _reader_loop(self):
        while self._running:
            try:
                raw = self._serial.readline().decode("ascii", errors="replace").strip()
                if not raw.startswith("$"):
                    continue
                if not _NMEA_AVAILABLE:
                    continue

                msg = pynmea2.parse(raw)

                with self._lock:
                    # GGA — position, altitude, fix quality
                    if isinstance(msg, pynmea2.types.talker.GGA):
                        fix = int(msg.gps_qual) if msg.gps_qual else 0
                        if fix > 0:
                            self._latest["lat"]        = float(msg.latitude)
                            self._latest["lon"]        = float(msg.longitude)
                            self._latest["alt_m"]      = float(msg.altitude) if msg.altitude else None
                            self._latest["satellites"] = int(msg.num_sats) if msg.num_sats else 0
                            self._latest["fix"]        = fix
                            self._latest["valid"]      = True

                    # RMC — speed over ground
                    elif isinstance(msg, pynmea2.types.talker.RMC):
                        if msg.status == "A":  # Active (valid)
                            knots = float(msg.spd_over_grnd) if msg.spd_over_grnd else 0.0
                            self._latest["speed_kmh"] = round(knots * 1.852, 1)

            except pynmea2.ParseError:
                pass  # Malformed sentence — ignore
            except serial.SerialException as e:
                log.error(f"GPS serial error: {e}")
                time.sleep(1.0)
            except Exception as e:
                log.debug(f"GPS parse exception: {e}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def read(self) -> dict:
        """Return the latest GPS fix as a dict (thread-safe snapshot)."""
        if self._running:
            with self._lock:
                return self._latest.copy()

        # Simulation fallback
        import math
        t = time.time()
        return {
            "lat":        33.0 + 0.001 * math.sin(t / 60),
            "lon":       -96.0 + 0.001 * math.cos(t / 60),
            "alt_m":      20000 + 500 * math.sin(t / 120),
            "speed_kmh":  round(abs(20 * math.sin(t / 90)), 1),
            "satellites": 8,
            "fix":        1,
            "valid":      False,
        }

    def close(self):
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        log.info("GPS reader closed")
