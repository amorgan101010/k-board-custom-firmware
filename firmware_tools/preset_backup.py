#!/usr/bin/env python3
"""Read-only preset backup and byte-exact restore files for the K-Board.

`backup` asks the board for slot 0 and writes the raw dump, its 470-byte
image, and a SysEx file that writes exactly that image back. `restore-file`
builds the same SysEx from a saved image without touching a device.
`send` writes a restore file to the board; it is the only command that changes
the device and is never run implicitly.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kboard_protocol import (
    MODEL, PRESET_DUMP_HEADER, Device, _Writer, build_settings_request,
    decode_preset_image, preset_image_from_dump, PRODUCT_ID,
)


def image_to_sysex(image: bytes) -> bytes:
    """Encode a 470-byte preset image the way the official editor writes one."""
    decode_preset_image(image)  # validates length and every enumerated value
    w = _Writer()
    w.data.extend((0xF0, *PRODUCT_ID, 0x01))
    for byte in (0, 2, 0x22, 0x20):
        w.encoded(byte, sum_byte=False)
    crc = w.crc
    w.encoded(crc >> 8, sum_byte=False)
    w.encoded(crc & 255, sum_byte=False)
    w.flush()
    for byte in (0xA1, 470 >> 8, 470 & 255):
        w.encoded(byte, crc=False, sum_byte=False)
    w.crc = 0xFFFF
    w.sum_byte = 0
    w.encoded(image[0])
    cursor = 1
    for key in MODEL:
        if key == "Preset_Name":
            continue
        if "Gain" in key and key != "Globals_Gain":
            number = int.from_bytes(image[cursor:cursor + 2], "big")
            cursor += 2
            w.encoded(number >> 8, sum_value=number)
            w.encoded(number & 255, sum_byte=False)
        else:
            w.encoded(image[cursor])
            cursor += 1
    w.encoded(w.sum_byte, sum_byte=False)
    w.flush()
    w.data.append(0xF7)
    if len(w.data) != 561 or any(b & 0x80 for b in w.data[1:-1]):
        raise ValueError("invalid encoded preset length or MIDI data byte")
    return bytes(w.data)


def read_dump(device: Device, timeout: float = 3.0) -> bytes:
    import mido
    for _ in device.input.iter_pending():
        pass
    request = build_settings_request(0)
    device.output.send(mido.Message("sysex", data=request[1:-1]))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for msg in device.input.iter_pending():
            if msg.type == "sysex":
                packet = bytes((0xF0, *msg.data, 0xF7))
                if packet.startswith(PRESET_DUMP_HEADER):
                    return packet
        time.sleep(0.01)
    raise TimeoutError("K-Board did not answer the preset dump request")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("prefix", type=Path)
    restore = sub.add_parser("restore-file")
    restore.add_argument("image", type=Path)
    restore.add_argument("output", type=Path)
    send = sub.add_parser("send")
    send.add_argument("restore_syx", type=Path)
    sub.add_parser("identity")
    args = parser.parse_args()

    if args.command == "restore-file":
        args.output.write_bytes(image_to_sysex(args.image.read_bytes()))
        return
    with Device() as device:
        if args.command == "identity":
            print(device.identity())
        elif args.command == "backup":
            print("firmware", device.identity())
            packet = read_dump(device)
            image = preset_image_from_dump(packet)
            restore = image_to_sysex(image)
            Path(f"{args.prefix}.dump.syx").write_bytes(packet)
            Path(f"{args.prefix}.image.bin").write_bytes(image)
            Path(f"{args.prefix}.restore.syx").write_bytes(restore)
            print(f"saved {args.prefix}.{{dump.syx,image.bin,restore.syx}}")
        else:
            import mido
            payload = args.restore_syx.read_bytes()
            device.output.send(mido.Message("sysex", data=payload[1:-1]))
            print("sent", len(payload), "bytes")


if __name__ == "__main__":
    main()
