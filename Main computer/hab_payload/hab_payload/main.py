#!/usr/bin/env python3
"""
HAB Payload Main Controller
============================
Collects data from BMP280 (temp/pressure/altitude), GPS, and CosmicWatch muon
detector, then transmits a packet via LoRa (UART → ESP32 Heltec) every 10 seconds.
All transmitted packets are also written to the onboard SD card as a CSV backup.

Hardware wiring assumptions:
  BMP280  → I2C (SDA=GPIO2, SCL=GPIO3) or SPI — configure in config.py
  GPS     → UART1 (/dev/ttyAMA0 or /dev/serial0) at 9600 baud
  CosmicWatch → UART0 (/dev/ttyUSB0) at 9600 baud (USB-serial from Pico)
  LoRa ESP32  → UART (/dev/ttyUSB1) at 115200 baud
  SD card     → Mounted at /mnt/sdcard  (or /media/pi/sdcard — set in config.py)

Run:
  sudo python3 main.py
"""

import time
import logging
import signal
import sys
from datetime import datetime

from config import CONFIG
from sensors.bmp280_sensor import BMP280Sensor
from sensors.gps_sensor import GPSSensor
from sensors.cosmicwatch import CosmicWatchSensor
from comms.lora_uart import LoRaTransmitter
from storage.sd_logger import SDLogger
from utils.packet import build_packet, packet_to_csv_row

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/home/pi/hab_payload.log"),
    ],
)
log = logging.getLogger("main")


def graceful_shutdown(sensors, lora, sd_logger):
    """Clean up all resources on SIGINT/SIGTERM."""
    log.info("Shutting down payload...")
    for s in sensors:
        try:
            s.close()
        except Exception:
            pass
    try:
        lora.close()
    except Exception:
        pass
    try:
        sd_logger.close()
    except Exception:
        pass
    log.info("Shutdown complete.")
    sys.exit(0)


def main():
    log.info("=== HAB Payload Starting ===")
    log.info(f"Transmission interval: {CONFIG['tx_interval_s']}s")

    # ── Initialise sensors ─────────────────────────────────────────────────────
    bmp = BMP280Sensor(
        i2c_address=CONFIG["bmp280"]["i2c_address"],
        sea_level_pressure=CONFIG["bmp280"]["sea_level_pressure_hpa"],
    )
    gps = GPSSensor(
        port=CONFIG["gps"]["port"],
        baudrate=CONFIG["gps"]["baudrate"],
        timeout=CONFIG["gps"]["timeout_s"],
    )
    cw = CosmicWatchSensor(
        port=CONFIG["cosmicwatch"]["port"],
        baudrate=CONFIG["cosmicwatch"]["baudrate"],
    )

    # ── Initialise comms / storage ─────────────────────────────────────────────
    lora = LoRaTransmitter(
        port=CONFIG["lora"]["port"],
        baudrate=CONFIG["lora"]["baudrate"],
    )
    sd = SDLogger(
        mount_path=CONFIG["sd"]["mount_path"],
        filename_prefix="hab_log",
    )

    sensors = [bmp, gps, cw]

    # Register clean shutdown on Ctrl-C / kill
    signal.signal(signal.SIGINT,  lambda s, f: graceful_shutdown(sensors, lora, sd))
    signal.signal(signal.SIGTERM, lambda s, f: graceful_shutdown(sensors, lora, sd))

    packet_id = 0
    log.info("Entering transmission loop...")

    while True:
        loop_start = time.monotonic()

        # ── Read sensors ───────────────────────────────────────────────────────
        bmp_data = bmp.read()          # dict: temp_c, pressure_hpa, altitude_m
        gps_data = gps.read()          # dict: lat, lon, alt_m, speed_kmh, sats, fix
        muon_data = cw.read_counts()   # dict: count (events in last window), adc_avg

        # ── Assemble packet ────────────────────────────────────────────────────
        packet_id += 1
        packet = build_packet(
            packet_id=packet_id,
            timestamp=datetime.utcnow(),
            bmp=bmp_data,
            gps=gps_data,
            muon=muon_data,
            window_s=CONFIG["tx_interval_s"],
        )

        # ── Transmit via LoRa ──────────────────────────────────────────────────
        tx_str = packet["lora_string"]
        ok = lora.send(tx_str)
        if ok:
            log.info(f"[TX #{packet_id}] {tx_str}")
        else:
            log.warning(f"[TX #{packet_id}] LoRa send FAILED — data saved to SD only")

        # ── Write backup to SD ─────────────────────────────────────────────────
        sd.write_row(packet_to_csv_row(packet))

        # ── Sleep for remainder of interval ───────────────────────────────────
        elapsed = time.monotonic() - loop_start
        sleep_for = max(0.0, CONFIG["tx_interval_s"] - elapsed)
        if sleep_for > 0:
            time.sleep(sleep_for)


if __name__ == "__main__":
    main()
