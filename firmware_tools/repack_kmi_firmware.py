#!/usr/bin/env python3
"""Pack Intel HEX records into KMI's chunked firmware SysEx format.

This is the inverse of extract_kmi_firmware.py.  Its self-test regenerates
the official ``K-Board Firmware v1.2.2_cs512.syx`` byte for byte, so a
patched image is packaged exactly as KMI's own Hex_to_SysEx tool would.
The tool only writes files; it never opens a MIDI port.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools.extract_kmi_firmware import Record, extract
from kboard_protocol import _crc_byte

CHUNK_HEADER = bytes((0xF0, 0x00, 0x01, 0x5F, 0x7A, 0x1A, 0x00,
                      0x00, 0x00, 0x00, 0x00, 0x00, 0x01))
PREAMBLE = bytes((0x00, 0x02, 0x11, 0x10))
MAX_MESSAGE = 512
PAGE = 512
STOCK_122_SHA256 = "33e300"  # prefix recorded when the board was updated


def _crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc = _crc_byte(crc, byte)
    return crc


def _flush(buffer: bytearray) -> None:
    while len(buffer) % 7:
        buffer.append(0)


def _record_bytes(record: Record) -> bytes:
    """One record as KMI frames it: 03, length, ':' line, checksum, CRC."""
    count = len(record.data)
    line = bytes((0x03, count + 7, 0x3A, count, record.address >> 8,
                  record.address & 0xFF, record.record_type)) + record.data
    checksum = (-sum(line[3:])) & 0xFF
    line += bytes((checksum,))
    return line + _crc(line).to_bytes(2, "big")


def _encode_7bit(decoded: bytes) -> bytes:
    if len(decoded) % 7:
        raise ValueError("decoded chunk is not aligned to 7-byte groups")
    out = bytearray()
    for offset in range(0, len(decoded), 7):
        group = decoded[offset:offset + 7]
        out.extend(byte & 0x7F for byte in group)
        out.append(sum(((byte >> 7) & 1) << bit for bit, byte in enumerate(group)))
    return bytes(out)


def _message(records: list[Record]) -> bytes:
    decoded = bytearray(PREAMBLE)
    decoded += _crc(PREAMBLE).to_bytes(2, "big")
    _flush(decoded)
    for record in records:
        decoded += _record_bytes(record)
        _flush(decoded)
    return CHUNK_HEADER + _encode_7bit(bytes(decoded)) + b"\xF7"


def pack(records: list[Record]) -> bytes:
    """Group records into chunks that never cross a 512-byte flash page."""
    messages: list[bytes] = []
    chunk: list[Record] = []
    for record in records:
        if chunk and record.record_type == 0:
            last = chunk[-1]
            contiguous = last.address + len(last.data) == record.address
            same_page = last.address // PAGE == (record.address + len(record.data) - 1) // PAGE
            fits = len(_message(chunk + [record])) <= MAX_MESSAGE
            if not (contiguous and same_page and fits):
                messages.append(_message(chunk))
                chunk = []
        chunk.append(record)
    if chunk:
        messages.append(_message(chunk))
    return b"".join(messages)


def records_from_image(stock: list[Record], image: bytes) -> list[Record]:
    """Apply a patched 64 KiB image to the stock record layout.

    Bytes covered by stock records are updated in place.  Any other byte that
    differs from erased flash (0xFF) is emitted in new 16-byte-aligned records,
    inserted in address order before the EOF record.
    """
    covered = set()
    updated = []
    for record in stock:
        if record.record_type == 0:
            span = range(record.address, record.address + len(record.data))
            covered.update(span)
            record = Record(record.chunk, record.address, 0,
                            bytes(image[a] for a in span))
        updated.append(record)

    extra = sorted(a for a in range(0x2400, 0x10000)
                   if a not in covered and image[a] != 0xFF)
    new: list[Record] = []
    for address in extra:
        if new and address < new[-1].address + 16 and address // 16 == new[-1].address // 16:
            last = new[-1]
            gap = address - (last.address + len(last.data))
            new[-1] = Record(0, last.address, 0, last.data + b"\xFF" * gap + bytes((image[address],)))
        else:
            new.append(Record(0, address, 0, bytes((image[address],))))

    data = [r for r in updated if r.record_type == 0] + new
    data.sort(key=lambda r: r.address)
    return data + [r for r in updated if r.record_type != 0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path, help="official chunked K-Board .syx")
    parser.add_argument("image", type=Path, nargs="?", help="patched 64 KiB .bin")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    stock_bytes = args.stock.read_bytes()
    records, _ = extract(args.stock)
    if pack(records) != stock_bytes:
        raise SystemExit("self-test failed: repacking the stock records does not "
                         "reproduce the official file")
    print(f"self-test ok: stock file reproduced byte for byte "
          f"(sha256 {hashlib.sha256(stock_bytes).hexdigest()[:12]})")
    if args.image is None:
        return
    if args.output is None:
        raise SystemExit("--output is required when packing an image")
    image = args.image.read_bytes()
    if len(image) != 0x10000:
        raise SystemExit("image must be a flat 64 KiB flash image")
    packed = pack(records_from_image(records, image))
    args.output.write_bytes(packed)
    print(f"wrote {args.output} ({len(packed)} bytes, "
          f"sha256 {hashlib.sha256(packed).hexdigest()[:12]})")


if __name__ == "__main__":
    main()
