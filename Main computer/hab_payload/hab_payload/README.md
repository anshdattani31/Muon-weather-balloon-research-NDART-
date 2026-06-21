# HAB Payload Software
### Raspberry Pi → LoRa Telemetry System
#### Sensors: BMP280 · GPS (GT-U7) · CosmicWatch v3X Muon Detector

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  Raspberry Pi                        │
│                                                     │
│  BMP280 ──(I2C)──┐                                  │
│  GT-U7  ──(UART)─┤─► main.py ──(UART)──► ESP32     │
│  CosmicWatch─(USB)┘     │                Heltec     │
│                         │                  │        │
│                         └──► SD Card CSV   │ LoRa   │
│                                            ▼  915MHz│
└─────────────────────────────────────────────────────┘
                                        Ground Station
```

---

## Hardware Wiring

### BMP280 → Raspberry Pi (I2C)
| BMP280 Pin | Pi Pin | GPIO |
|------------|--------|------|
| VCC        | 3.3V   | Pin 1 |
| GND        | GND    | Pin 6 |
| SDA        | SDA    | GPIO 2 (Pin 3) |
| SCL        | SCL    | GPIO 3 (Pin 5) |
| SDO        | GND    | → I2C address 0x76 |

> If SDO is tied HIGH, address is 0x77 — update `config.py`.

### GT-U7 GPS → Raspberry Pi (UART)
| GPS Pin | Pi Pin | GPIO |
|---------|--------|------|
| VCC     | 3.3V   | Pin 1 |
| GND     | GND    | Pin 6 |
| TX      | RX     | GPIO 15 (Pin 10) |
| RX      | TX     | GPIO 14 (Pin 8) |

> Disable the Pi's serial console first:
> `sudo raspi-config` → Interface Options → Serial Port → disable login shell, enable hardware port.
> Device will appear as `/dev/ttyAMA0` (Pi 3/4/5) or `/dev/serial0`.

### CosmicWatch v3X → Raspberry Pi (USB)
- Plug the Pico's USB cable directly into a Pi USB port.
- It appears as `/dev/ttyUSB0` (or `/dev/ttyACM0` depending on the Pico firmware).
- Check with: `ls /dev/tty*` before and after plugging in.
- Update `config.py → cosmicwatch → port` accordingly.

### ESP32 Heltec → Raspberry Pi (USB-Serial)
- Plug the ESP32's USB-C cable into a Pi USB port.
- It appears as `/dev/ttyUSB1` (or `/dev/ttyUSB0` if CosmicWatch uses ACM).
- Update `config.py → lora → port` accordingly.
- Flash `lora_esp32/lora_transmitter.ino` to the ESP32 **before** connecting to Pi.

### SD Card
Mount a FAT32 USB flash drive or microSD (via reader) at `/mnt/sdcard`:
```bash
sudo mkdir -p /mnt/sdcard
sudo mount /dev/sda1 /mnt/sdcard   # adjust /dev/sda1 to your device
```
For auto-mount at boot, add to `/etc/fstab`:
```
/dev/sda1  /mnt/sdcard  vfat  defaults,noatime  0  2
```

---

## Software Setup

### 1. Enable I2C on Raspberry Pi
```bash
sudo raspi-config  # Interface Options → I2C → Enable
sudo reboot
```

### 2. Install Python dependencies
```bash
pip3 install -r requirements.txt --break-system-packages
```

### 3. Test individual sensors
```bash
# BMP280
python3 -c "from sensors.bmp280_sensor import BMP280Sensor; s=BMP280Sensor(); print(s.read())"

# GPS (wait 30s for fix)
python3 -c "import time; from sensors.gps_sensor import GPSSensor; s=GPSSensor(); time.sleep(5); print(s.read())"

# CosmicWatch
python3 -c "import time; from sensors.cosmicwatch import CosmicWatchSensor; s=CosmicWatchSensor(); time.sleep(10); print(s.read_counts())"
```

### 4. Flash the ESP32
- Open `lora_esp32/lora_transmitter.ino` in Arduino IDE
- Board: **Heltec WiFi LoRa 32(V2)**  
  (Install via Boards Manager: add `https://resource.heltec.cn/download/package_heltec_esp32_index.json`)
- Library: search and install **"Heltec ESP32 Dev-Boards"** in Library Manager
- Select the correct COM port and upload

### 5. Run the payload
```bash
cd /home/pi/hab_payload
sudo python3 main.py
```

To run on boot:
```bash
# Create a systemd service
sudo nano /etc/systemd/system/hab_payload.service
```
```ini
[Unit]
Description=HAB Payload Main Controller
After=multi-user.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/hab_payload/main.py
WorkingDirectory=/home/pi/hab_payload
Restart=always
RestartSec=5
User=pi

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable hab_payload
sudo systemctl start hab_payload
```

---

## LoRa Packet Format

```
HAB,<id>,<utc>,<temp_c>,<pres_hpa>,<bmp_alt_m>,<lat>,<lon>,<gps_alt_m>,<spd_kmh>,<sats>,<fix>,<muons>,<adc_avg>,<win_s>*<XOR_checksum>
```

Example:
```
HAB,47,143022,-42.3,312.8,28450,33.123456,-96.654321,28312,14.2,9,1,12,2891,10*3F
```

| Field      | Description                              |
|------------|------------------------------------------|
| `id`       | Sequential packet counter                |
| `utc`      | HHMMSS UTC time                          |
| `temp_c`   | BMP280 temperature (°C)                  |
| `pres_hpa` | Pressure (hPa)                           |
| `bmp_alt_m`| Altitude derived from pressure (m)       |
| `lat/lon`  | GPS coordinates (decimal degrees)        |
| `gps_alt_m`| GPS altitude MSL (m)                     |
| `spd_kmh`  | GPS ground speed (km/h)                  |
| `sats`     | Number of GPS satellites                 |
| `fix`      | GPS fix quality (0=none, 1=GPS, 2=DGPS)  |
| `muons`    | CosmicWatch event count in last window   |
| `adc_avg`  | Mean SiPM ADC value (energy proxy)       |
| `win_s`    | Counting window duration (seconds)       |
| `*XX`      | XOR checksum (2 hex digits)              |

---

## CSV Backup Format

Saved to `/mnt/sdcard/hab_log_YYYYMMDD_HHMMSS.csv` with columns:

```
packet_id, utc_timestamp, temp_c, pressure_hpa, bmp_altitude_m,
gps_lat, gps_lon, gps_alt_m, gps_speed_kmh, gps_satellites, gps_fix,
muon_count, muon_adc_avg, window_s, bmp_valid, gps_valid, cw_valid
```

The `_valid` columns flag `True` when the sensor is responding; `False` when
simulation/fallback data is substituted.

---

## CosmicWatch Serial Protocol

The v3X Pico outputs one line per coincidence event:
```
<event_id>, <ADC_value>, <SiPM_temp_C>, <deadtime_us>, <unix_time_ms>
```
`cosmicwatch.py` counts all events received between transmissions and reports
the count + mean ADC per 10-second window. The ADC value is proportional to
the energy deposited in the scintillator — useful as a basic energy proxy for
your altitude-dependent comparison analysis.

---

## Simulation Mode

If a sensor or port is unavailable (sensor not connected, wrong `/dev/tty*`),
each driver automatically falls back to mathematically plausible simulated
data and marks `valid = False` in the CSV. This lets you develop and test the
full stack on a desk without physical hardware.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `BMP280 init failed` | I2C enabled? Correct address (0x76/0x77)? `i2cdetect -y 1` |
| `GPS serial not open` | Serial console disabled in raspi-config? Correct port? |
| `CosmicWatch UART open failed` | USB plugged in? `dmesg | grep tty` after plugging |
| `LoRa UART open failed` | ESP32 flashed? Correct port in config? |
| ESP32 shows `TX ERR` | LoRa antenna connected? Band setting correct? |
| SD not found | SD mounted? `mount | grep sdcard` |
