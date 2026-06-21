"""
comms/lora_uart.py
==================
Sends formatted ASCII packets to the Heltec WiFi LoRa 32 V2 (ESP32) over UART.

The ESP32 firmware (see lora_esp32/lora_transmitter.ino) listens on its UART0
at 115200 baud, reads a newline-terminated string, and immediately re-transmits
it over LoRa at 915 MHz.

Packet format sent to ESP32 (comma-separated, newline-terminated):
    HAB,<id>,<utc>,<temp_c>,<pres_hpa>,<bmp_alt_m>,<lat>,<lon>,<gps_alt_m>,
        <speed_kmh>,<sats>,<muon_count>,<adc_avg>,<window_s>*<checksum>\n

The ESP32 ACKs each packet with "OK\n" within ack_timeout_s seconds.
If no ACK is received the packet is still logged to SD.

Install:
    pip3 install pyserial
"""

import logging
import time
import serial

log = logging.getLogger(__name__)


def _nmea_checksum(sentence: str) -> str:
    """XOR checksum of all characters between (not including) HAB and *."""
    cs = 0
    for ch in sentence:
        cs ^= ord(ch)
    return f"{cs:02X}"


class LoRaTransmitter:

    def __init__(self, port: str = "/dev/ttyUSB1", baudrate: int = 115200,
                 ack_timeout: float = 3.0):
        self.port        = port
        self.baudrate    = baudrate
        self.ack_timeout = ack_timeout
        self._serial     = None

        try:
            self._serial = serial.Serial(port, baudrate, timeout=ack_timeout)
            time.sleep(0.1)  # Let ESP32 reset if it auto-resets on serial connect
            self._serial.reset_input_buffer()
            log.info(f"LoRa UART open on {port} @ {baudrate} baud")
        except Exception as e:
            log.error(f"LoRa UART open failed ({port}): {e} — TX will be skipped")

    def send(self, packet_string: str) -> bool:
        """
        Send a pre-formatted packet string to the ESP32.

        Args:
            packet_string: The full LoRa packet string (no trailing newline needed).
        Returns:
            True if ESP32 ACKed, False otherwise.
        """
        if self._serial is None or not self._serial.is_open:
            log.warning("LoRa serial not open — skipping TX")
            return False

        try:
            payload = (packet_string.strip() + "\n").encode("ascii")
            self._serial.write(payload)
            self._serial.flush()

            # Wait for ACK from ESP32
            deadline = time.monotonic() + self.ack_timeout
            while time.monotonic() < deadline:
                ack_line = self._serial.readline().decode("ascii", errors="replace").strip()
                if ack_line == "OK":
                    return True
                elif ack_line.startswith("ERR"):
                    log.warning(f"ESP32 TX error: {ack_line}")
                    return False

            log.warning("LoRa ACK timeout — packet may not have been transmitted")
            return False

        except serial.SerialException as e:
            log.error(f"LoRa serial write error: {e}")
            return False

    def close(self):
        if self._serial and self._serial.is_open:
            self._serial.close()
        log.info("LoRa UART closed")
