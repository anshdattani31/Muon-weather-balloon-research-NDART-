"""
sensors/bmp280_sensor.py
========================
Reads temperature, pressure, and derived altitude from a BMP280 via I2C.

Uses the `adafruit-circuitpython-bmp280` library which wraps the raw I2C
compensation math so we don't have to implement the datasheet's 20-step
integer arithmetic ourselves.

Install:
    pip3 install adafruit-circuitpython-bmp280 adafruit-blinka smbus2
"""

import logging
import time

log = logging.getLogger(__name__)

try:
    import board
    import busio
    import adafruit_bmp280
    _HW_AVAILABLE = True
except ImportError:
    log.warning("adafruit_bmp280 / blinka not installed — running in SIMULATION mode")
    _HW_AVAILABLE = False


class BMP280Sensor:
    """Interface to BMP280 over I2C."""

    def __init__(self, i2c_address: int = 0x76, sea_level_pressure: float = 1013.25):
        self.address = i2c_address
        self.sea_level_pressure = sea_level_pressure
        self._device = None

        if _HW_AVAILABLE:
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                self._device = adafruit_bmp280.Adafruit_BMP280_I2C(
                    i2c, address=i2c_address
                )
                self._device.sea_level_pressure = sea_level_pressure
                # Recommended settings for balloon: weather monitoring mode
                self._device.mode = adafruit_bmp280.MODE_NORMAL
                self._device.standby_period = adafruit_bmp280.STANDBY_TC_500
                self._device.iir_filter = adafruit_bmp280.IIR_FILTER_DISABLE
                self._device.overscan_pressure = adafruit_bmp280.OVERSCAN_X16
                self._device.overscan_temperature = adafruit_bmp280.OVERSCAN_X2
                log.info(f"BMP280 initialised at I2C address 0x{i2c_address:02X}")
            except Exception as e:
                log.error(f"BMP280 init failed: {e}")
                self._device = None
        else:
            log.info("BMP280: simulation mode active")

    def read(self) -> dict:
        """
        Returns:
            dict with keys: temp_c, pressure_hpa, altitude_m
            On failure returns safe sentinel values so the packet still forms.
        """
        if self._device is not None:
            try:
                temp     = round(self._device.temperature, 2)
                pressure = round(self._device.pressure, 2)
                altitude = round(self._device.altitude, 1)
                return {
                    "temp_c":       temp,
                    "pressure_hpa": pressure,
                    "altitude_m":   altitude,
                    "valid":        True,
                }
            except Exception as e:
                log.error(f"BMP280 read error: {e}")

        # Simulation / fallback
        import random, math
        t = time.time()
        return {
            "temp_c":       round(-30 + 10 * math.sin(t / 60), 2),
            "pressure_hpa": round(500 + 50 * math.sin(t / 120), 2),
            "altitude_m":   round(20000 + 1000 * math.sin(t / 300), 1),
            "valid":        False,  # marks as simulated
        }

    def close(self):
        pass  # I2C bus managed by blinka; no explicit close needed
