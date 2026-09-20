"""Bounded binary fragmentation. CRC checks accidental corruption, not identity."""

import json
import struct
import time
import zlib

HEADER = struct.Struct("!2sBB8sIHHI")
MAX_RECORD = 1024 * 1024
MAX_FRAGMENTS = 8192


def encode_record(record):
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def fragment(payload, session, message_id, packet_bytes=200, compression="None"):
    if not 25 <= packet_bytes <= 254 or len(session) != 8:
        raise ValueError("Invalid packet size or session ID")
    if len(payload) > MAX_RECORD:
        raise ValueError("Record exceeds 1 MiB safety limit")
    if compression not in ("None", "zlib"):
        raise ValueError("Unknown compression")
    flags = int(compression == "zlib")
    encoded = zlib.compress(payload) if flags else payload
    size = packet_bytes - HEADER.size
    count = max(1, (len(encoded) + size - 1) // size)
    if count > MAX_FRAGMENTS:
        raise ValueError("Record requires too many fragments; increase packet size")
    crc = zlib.crc32(encoded)
    return [HEADER.pack(b"ND", 1, flags, session, message_id, index, count, crc)
            + encoded[index * size:(index + 1) * size] for index in range(count)]


class Reassembler:
    def __init__(self, timeout=30.0, max_pending=128):
        self.timeout = timeout
        self.max_pending = max_pending
        self.pending = {}
        self.expired = 0

    def expire(self, now=None):
        now = time.monotonic() if now is None else now
        stale = [key for key, value in self.pending.items() if now - value[0] >= self.timeout]
        for key in stale:
            del self.pending[key]
        self.expired += len(stale)
        return len(stale)

    def accept(self, packet, now=None):
        now = time.monotonic() if now is None else now
        self.expire(now)
        if not HEADER.size < len(packet) <= 254:
            raise ValueError("Invalid fragment length")
        magic, version, flags, session, msg, index, count, crc = HEADER.unpack(packet[:HEADER.size])
        if magic != b"ND" or version != 1 or flags not in (0, 1):
            raise ValueError("Unsupported packet header")
        if not 1 <= count <= MAX_FRAGMENTS or index >= count:
            raise ValueError("Invalid fragment numbering")
        key = (session.hex(), msg)
        if key not in self.pending:
            if len(self.pending) >= self.max_pending:
                raise ValueError("Too many incomplete messages")
            self.pending[key] = [now, (flags, count, crc), {}, 0]
        entry = self.pending[key]
        if entry[1] != (flags, count, crc):
            del self.pending[key]
            raise ValueError("Conflicting fragment headers")
        chunk = packet[HEADER.size:]
        previous = entry[2].get(index)
        if previous is not None and previous != chunk:
            del self.pending[key]
            raise ValueError("Conflicting duplicate fragment")
        if previous is None:
            entry[2][index] = chunk
            entry[3] += len(chunk)
        if entry[3] > MAX_RECORD + 1024:
            del self.pending[key]
            raise ValueError("Encoded record exceeds size limit")
        if len(entry[2]) != count:
            return None
        encoded = b"".join(entry[2][i] for i in range(count))
        del self.pending[key]
        if zlib.crc32(encoded) != crc:
            raise ValueError("Record checksum failed")
        if flags:
            decoder = zlib.decompressobj()
            try:
                payload = decoder.decompress(encoded, MAX_RECORD + 1)
            except zlib.error as exc:
                raise ValueError("Invalid compressed record") from exc
            if len(payload) > MAX_RECORD or not decoder.eof or decoder.unused_data:
                raise ValueError("Invalid or oversized compressed record")
        else:
            payload = encoded
        if len(payload) > MAX_RECORD:
            raise ValueError("Decoded record exceeds size limit")
        record = json.loads(payload)
        if not isinstance(record, dict):
            raise ValueError("Received record must be a JSON object")
        return key, record
