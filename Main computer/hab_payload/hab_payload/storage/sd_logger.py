"""
storage/sd_logger.py
====================
Appends data rows to a CSV file on the SD card.

Features:
  - Creates a new timestamped file at startup (one file per flight)
  - Writes a CSV header on first write
  - Flushes after every row to prevent data loss if power is cut
  - Falls back to /home/pi/logs if the SD card is not mounted

File naming example:
    /mnt/sdcard/hab_log_20260620_143012.csv
"""

import csv
import logging
import os
import time
from datetime import datetime

log = logging.getLogger(__name__)

CSV_HEADER = [
    "packet_id", "utc_timestamp",
    "temp_c", "pressure_hpa", "bmp_altitude_m",
    "gps_lat", "gps_lon", "gps_alt_m", "gps_speed_kmh", "gps_satellites", "gps_fix",
    "muon_count", "muon_adc_avg", "window_s",
    "bmp_valid", "gps_valid", "cw_valid",
]


class SDLogger:
    """Append-only CSV logger that writes to the SD card."""

    def __init__(self, mount_path: str = "/mnt/sdcard", filename_prefix: str = "hab_log"):
        self._file   = None
        self._writer = None

        path = self._resolve_path(mount_path)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filepath  = os.path.join(path, f"{filename_prefix}_{timestamp}.csv")

        try:
            os.makedirs(path, exist_ok=True)
            self._file   = open(filepath, "w", newline="", buffering=1)  # line-buffered
            self._writer = csv.writer(self._file)
            self._writer.writerow(CSV_HEADER)
            self._file.flush()
            log.info(f"SD logger writing to: {filepath}")
        except Exception as e:
            log.error(f"SD logger could not open {filepath}: {e}")

    def _resolve_path(self, mount_path: str) -> str:
        """Return mount_path if mounted, otherwise fall back to home dir."""
        if os.path.ismount(mount_path) or os.path.isdir(mount_path):
            return mount_path
        fallback = os.path.expanduser("~/hab_logs")
        log.warning(f"SD card not found at {mount_path} — falling back to {fallback}")
        return fallback

    def write_row(self, row: list):
        """Append one CSV row and flush immediately."""
        if self._writer is None:
            log.error("SD logger not initialised — row dropped")
            return
        try:
            self._writer.writerow(row)
            self._file.flush()
        except Exception as e:
            log.error(f"SD write error: {e}")

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()
            log.info("SD logger closed")
