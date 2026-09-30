#!/usr/bin/env python3
"""Extract Intel HEX records from a chunked KMI firmware SysEx image.

KMI's Hex_to_SysEx format packs each group of seven 8-bit bytes into eight
MIDI-safe bytes: seven low-bit payload bytes followed by one high-bit mask.
This tool validates both the MIDI framing and every Intel HEX checksum before
writing any output.  It is intentionally an analysis tool; it never opens a
MIDI port or writes to a device.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path


KMI_HEADER = bytes((0xF0, 0x00, 0x01, 0x5F, 0x7A, 0x1A, 0x00))
CHUNK_PAYLOAD_OFFSET = 13
CHUNK_PREAMBLE = bytes((0x00, 0x02, 0x11, 0x10, 0xC8, 0xD3, 0x00))


@dataclass(frozen=True)
class Record:
    chunk: int
    address: int
    record_type: int
    data: bytes

    def intel_hex(self) -> str:
        body = bytes((len(self.data), self.address >> 8, self.address & 0xFF,
                      self.record_type)) + self.data
        checksum = (-sum(body)) & 0xFF
        return ":" + (body + bytes((checksum,))).hex().upper()

    def metadata(self) -> dict:
        result = asdict(self)
        result["data"] = self.data.hex()
        return result


def split_sysex(raw: bytes) -> list[bytes]:
    """Split a file into complete F0...F7 messages and reject stray bytes."""
    messages: list[bytes] = []
    cursor = 0
    while cursor < len(raw):
        if raw[cursor] != 0xF0:
            raise ValueError(f"stray byte 0x{raw[cursor]:02x} at file offset {cursor}")
        end = raw.find(b"\xF7", cursor + 1)
        if end < 0:
            raise ValueError(f"unterminated SysEx message at file offset {cursor}")
        messages.append(raw[cursor:end + 1])
        cursor = end + 1
    return messages


def decode_7bit(encoded: bytes) -> bytes:
    if len(encoded) % 8:
        raise ValueError(f"encoded payload is {len(encoded)} bytes; expected groups of 8")
    decoded = bytearray()
    for offset in range(0, len(encoded), 8):
        lows = encoded[offset:offset + 7]
        mask = encoded[offset + 7]
        if any(byte & 0x80 for byte in encoded[offset:offset + 8]):
            raise ValueError("encoded payload contains a non-MIDI data byte")
        decoded.extend(byte | (((mask >> bit) & 1) << 7)
                       for bit, byte in enumerate(lows))
    return bytes(decoded)


def parse_records(decoded: bytes, chunk_number: int) -> list[Record]:
    if not decoded.startswith(CHUNK_PREAMBLE):
        got = decoded[:len(CHUNK_PREAMBLE)].hex(" ")
        raise ValueError(f"chunk {chunk_number}: unexpected preamble {got}")

    records: list[Record] = []
    cursor = len(CHUNK_PREAMBLE)
    while cursor < len(decoded):
        while cursor < len(decoded) and decoded[cursor] == 0:
            cursor += 1
        if cursor == len(decoded):
            break
        if decoded[cursor] != 0x03:
            raise ValueError(
                f"chunk {chunk_number}: expected record marker at decoded offset "
                f"{cursor}, got 0x{decoded[cursor]:02x}"
            )
        if cursor + 7 > len(decoded):
            raise ValueError(f"chunk {chunk_number}: truncated record header")

        total_length = decoded[cursor + 1]
        if decoded[cursor + 2] != 0x3A:
            raise ValueError(f"chunk {chunk_number}: missing Intel HEX colon")
        count = decoded[cursor + 3]
        address = int.from_bytes(decoded[cursor + 4:cursor + 6], "big")
        record_type = decoded[cursor + 6]
        end = cursor + 7 + count
        if end + 3 > len(decoded):
            raise ValueError(f"chunk {chunk_number}: truncated record at 0x{address:04x}")
        data = decoded[cursor + 7:end]
        checksum = decoded[end]
        # Two bytes after the Intel checksum are KMI's per-line transport CRC.
        expected_total = count + 7
        if total_length != expected_total:
            raise ValueError(
                f"chunk {chunk_number}: record 0x{address:04x} length field "
                f"{total_length}, expected {expected_total}"
            )
        check_body = bytes((count, address >> 8, address & 0xFF, record_type)) + data
        if (sum(check_body) + checksum) & 0xFF:
            raise ValueError(f"chunk {chunk_number}: bad Intel checksum at 0x{address:04x}")
        records.append(Record(chunk_number, address, record_type, data))
        cursor = end + 3
    return records


def extract(path: Path) -> tuple[list[Record], list[bytes]]:
    messages = split_sysex(path.read_bytes())
    records: list[Record] = []
    decoded_chunks: list[bytes] = []
    for number, message in enumerate(messages, 1):
        if not message.startswith(KMI_HEADER) or message[-1] != 0xF7:
            raise ValueError(f"message {number}: not a K-Board firmware SysEx message")
        if len(message) <= CHUNK_PAYLOAD_OFFSET or message[12] != 0x01:
            raise ValueError(
                "this tool expects the chunked firmware format used by K-Board 1.2.2"
            )
        decoded = decode_7bit(message[CHUNK_PAYLOAD_OFFSET:-1])
        decoded_chunks.append(decoded)
        records.extend(parse_records(decoded, number))
    return records, decoded_chunks


def build_image(records: list[Record]) -> tuple[bytearray, int, int]:
    data_records = [record for record in records if record.record_type == 0]
    if not data_records:
        raise ValueError("firmware contains no Intel HEX data records")
    low = min(record.address for record in data_records)
    high = max(record.address + len(record.data) for record in data_records)
    image = bytearray(b"\xFF" * 0x10000)
    written: dict[int, int] = {}
    for record in data_records:
        for offset, byte in enumerate(record.data):
            address = record.address + offset
            if address in written and written[address] != byte:
                raise ValueError(f"conflicting data records at 0x{address:04x}")
            image[address] = byte
            written[address] = byte
    return image, low, high


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("firmware_analysis"))
    parser.add_argument("--name", help="output basename (defaults to input stem)")
    args = parser.parse_args()

    records, chunks = extract(args.input)
    image, low, high = build_image(records)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    name = args.name or args.input.stem.replace(" ", "_")

    (output / f"{name}.hex").write_text(
        "\n".join(record.intel_hex() for record in records) + "\n"
    )
    (output / f"{name}.bin").write_bytes(image)
    (output / f"{name}.app.bin").write_bytes(image[low:high])
    metadata = {
        "source": str(args.input),
        "messages": len(chunks),
        "decoded_bytes": sum(map(len, chunks)),
        "data_start": low,
        "data_end_exclusive": high,
        "data_bytes_in_span": high - low,
        "records": [record.metadata() for record in records],
    }
    (output / f"{name}.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"{len(chunks)} messages, {len(records)} records")
    print(f"data span 0x{low:04X}..0x{high - 1:04X} ({high - low} bytes)")
    print(output / f"{name}.bin")


if __name__ == "__main__":
    main()
