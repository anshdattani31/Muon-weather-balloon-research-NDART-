"""
utils/packet.py
===============
Assembles all sensor readings into a single structured packet dict.
Provides two output formats:
  - lora_string : comma-separated ASCII string for LoRa transmission
  - csv_row     : flat list matching the SD card CSV header

LoRa packet format (human-readable, compact):
    HAB,<id>,<utc>,<temp>,<pres>,<bmp_alt>,<lat>,<lon>,<gps_alt>,<spd>,<sats>,<muons>,<adc>,<win>*<checksum>

Checksum: XOR of all bytes in the data portion (between HAB, and *)
"""

from datetime import datetime


def _xor_checksum(s: str) -> str:
    cs = 0
    for ch in s:
        cs ^= ord(ch)
    return f"{cs:02X}"


def _fmt(value, fmt=".2f", fallback="?"):
    """Format a numeric value, returning fallback string if None."""
    if value is None:
        return fallback
    try:
        return format(float(value), fmt)
    except (TypeError, ValueError):
        return fallback


def build_packet(
    packet_id: int,
    timestamp: datetime,
    bmp: dict,
    gps: dict,
    muon: dict,
    window_s: int = 10,
) -> dict:
    """
    Merge sensor dicts into a unified packet dict.

    Returns:
        dict with keys: packet_id, timestamp, all sensor fields,
        lora_string, and validity flags.
    """
    utc_str = timestamp.strftime("%H%M%S")  # HHMMSS — short for LoRa bandwidth

    # Build the data portion of the LoRa string (no prefix/checksum yet)
    data = ",".join([
        str(packet_id),
        utc_str,
        _fmt(bmp.get("temp_c"),       ".1f"),
        _fmt(bmp.get("pressure_hpa"), ".1f"),
        _fmt(bmp.get("altitude_m"),   ".0f"),
        _fmt(gps.get("lat"),          ".6f"),
        _fmt(gps.get("lon"),          ".6f"),
        _fmt(gps.get("alt_m"),        ".0f"),
        _fmt(gps.get("speed_kmh"),    ".1f"),
        str(gps.get("satellites", 0)),
        str(gps.get("fix", 0)),
        str(muon.get("count", 0)),
        _fmt(muon.get("adc_avg"),     ".0f"),
        str(window_s),
    ])

    checksum   = _xor_checksum(data)
    lora_str   = f"HAB,{data}*{checksum}"

    return {
        # Meta
        "packet_id":        packet_id,
        "timestamp":        timestamp.isoformat(),
        "window_s":         window_s,

        # BMP280
        "temp_c":           bmp.get("temp_c"),
        "pressure_hpa":     bmp.get("pressure_hpa"),
        "bmp_altitude_m":   bmp.get("altitude_m"),
        "bmp_valid":        bmp.get("valid", False),

        # GPS
        "gps_lat":          gps.get("lat"),
        "gps_lon":          gps.get("lon"),
        "gps_alt_m":        gps.get("alt_m"),
        "gps_speed_kmh":    gps.get("speed_kmh"),
        "gps_satellites":   gps.get("satellites", 0),
        "gps_fix":          gps.get("fix", 0),
        "gps_valid":        gps.get("valid", False),

        # Muon / CosmicWatch
        "muon_count":       muon.get("count", 0),
        "muon_adc_avg":     muon.get("adc_avg", 0.0),
        "cw_valid":         muon.get("valid", False),

        # Formatted outputs
        "lora_string":      lora_str,
    }


def packet_to_csv_row(packet: dict) -> list:
    """Return a flat list matching the CSV_HEADER in sd_logger.py."""
    return [
        packet["packet_id"],
        packet["timestamp"],
        packet.get("temp_c",        ""),
        packet.get("pressure_hpa",  ""),
        packet.get("bmp_altitude_m",""),
        packet.get("gps_lat",       ""),
        packet.get("gps_lon",       ""),
        packet.get("gps_alt_m",     ""),
        packet.get("gps_speed_kmh", ""),
        packet.get("gps_satellites",""),
        packet.get("gps_fix",       ""),
        packet.get("muon_count",    ""),
        packet.get("muon_adc_avg",  ""),
        packet["window_s"],
        packet.get("bmp_valid",  False),
        packet.get("gps_valid",  False),
        packet.get("cw_valid",   False),
    ]
