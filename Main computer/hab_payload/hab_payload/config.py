"""
config.py — HAB Payload Configuration
======================================
Edit this file to match your exact wiring and hardware versions.
All other modules import from here — no magic numbers scattered elsewhere.
"""

CONFIG = {
    # ── Timing ────────────────────────────────────────────────────────────────
    "tx_interval_s": 10,          # Seconds between LoRa transmissions

    # ── BMP280 ────────────────────────────────────────────────────────────────
    "bmp280": {
        # I2C address: 0x76 (SDO→GND) or 0x77 (SDO→VCC)
        "i2c_address": 0x76,
        # I2C bus number (1 for standard Raspberry Pi GPIO header)
        "i2c_bus": 1,
        # Sea-level pressure in hPa — used for altitude calculation.
        # Update to current local QNH before launch for accurate AGL altitude.
        "sea_level_pressure_hpa": 1013.25,
    },

    # ── GPS (GT-U7 or similar NMEA device) ───────────────────────────────────
    "gps": {
        # Primary UART on Pi 4/5: /dev/ttyAMA0
        # If using USB-serial adapter: /dev/ttyUSB0
        "port": "/dev/ttyAMA0",
        "baudrate": 9600,
        "timeout_s": 2.0,
    },

    # ── CosmicWatch v3X (connects via USB-serial from the Pico's USB port) ───
    "cosmicwatch": {
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        # How long to wait for a line from the Pico before giving up
        "read_timeout_s": 1.0,
    },

    # ── LoRa ESP32 Heltec (connected to Pi via UART or USB-serial) ────────────
    "lora": {
        # If using a direct UART connection: /dev/ttyAMA1 (requires enabling UART2)
        # If using a USB-serial cable from ESP32: /dev/ttyUSB1
        "port": "/dev/ttyUSB1",
        "baudrate": 115200,
        # Seconds to wait for ACK from ESP32 after sending a packet
        "ack_timeout_s": 3.0,
    },

    # ── SD Card / file logging ─────────────────────────────────────────────────
    "sd": {
        # Mount point of the SD card.
        # On Pi OS with a USB SD reader this is often /media/pi/<label>
        # On the Pi's own microSD you can log to /home/pi/logs
        "mount_path": "/mnt/sdcard",
    },
}
