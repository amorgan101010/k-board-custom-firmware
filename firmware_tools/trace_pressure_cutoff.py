#!/usr/bin/env python3
"""Trace the stock pressure mapper and held-key cutoff entirely offline.

Uses the supplied preset with a linear pressure curve. Sensor acquisition,
global aggregation, and tilt are outside this trace; real key-state and
pressure-mapping bytes execute in the interpreter. No MIDI port is opened.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools.build_relative_tilt_patch import STOCK_SYX
from firmware_tools.extract_kmi_firmware import extract
from firmware_tools.mcs51 import Machine
from kboard_protocol import decode_preset_image


def stock_image() -> bytes:
    records, _ = extract(STOCK_SYX)
    image = bytearray(b"\xff" * 65536)
    for record in records:
        if record.record_type == 0:
            image[record.address:record.address + len(record.data)] = record.data
    return bytes(image)


def machine_for_preset(code: bytes, preset: bytes) -> Machine:
    decode_preset_image(preset)
    if preset[0x0363 - 0x0347] != 0:
        raise ValueError("This trace fixture supports the linear pressure curve only")
    table = code.find(bytes(range(128)))
    if table != 0x2E3D:
        raise ValueError("Stock linear curve table differs")
    machine = Machine(code)
    machine.sp = 0x51  # stock startup stack pointer
    machine.xram[0x0347:0x051D] = preset
    # Startup normally initializes the curve pointer table. This fixture
    # enters after startup and supplies the exact stock linear ROM pointer.
    machine.xram[0x033B:0x033D] = table.to_bytes(2, "big")
    return machine


def trace_held_key(code: bytes, preset: bytes, raw: int) -> tuple[int, int, list]:
    machine = machine_for_preset(code, preset)
    configured_threshold = preset[0x034E - 0x0347]
    on_threshold = configured_threshold + 5 if configured_threshold < 126 else 126
    machine.iram[0x36] = 0  # physical key index
    machine.iram[0x4D] = on_threshold - 5
    machine.iram[0x4E] = on_threshold
    machine.iram[0x4C] = 100
    machine.xram[0x09C1:0x09C3] = bytes((0, 0xDA))
    machine.xram[0x00DA] = 2  # key already held
    machine.xram[0x007A] = raw  # maximum of the two scaled sensors
    machine.xram[0x0044] = 64  # neutral tilt
    machine.xram[0x0287] = raw  # previous scan has the same steady sensor value
    machine.xram[0x00F6] = 99  # different previous channel sample, force output
    machine.xram[0x0060] = 1  # allocated member channel
    machine.xram[0x00AD] = 1
    machine.xram[0x0079] = 1
    machine.xram[0x025A] = 1
    machine.xram[0x093E] = 3  # physical tilt and pressure enabled
    events = []
    machine.stubs[0x313D] = lambda m: True  # drained USB writer
    machine.stubs[0x7C67] = lambda m: events.append(
        ("midi", m.r(7), m.r(5), m.r(3))) or True
    machine.stubs[0x8247] = lambda m: events.append(
        ("pressure", m.r(5), m.r(7))) or True
    machine.stubs[0x7FF3] = lambda m: True
    machine.stubs[0x6C72] = lambda m: m.set_r(7, 64) or True
    machine.stubs[0x5BCB] = lambda m: m.set_r(7, 0) or True
    # Real scan decision, real 0x62D7 release, real 0x38FB pressure dispatch,
    # and real 0x6183 mapper. Acquisition and global aggregation are excluded.
    machine.call(0x2625, dptr=0x0044)
    return machine.xram[0x007A], machine.xram[0x0060], events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset_image", type=Path)
    args = parser.parse_args()
    preset = args.preset_image.read_bytes()
    code = stock_image()
    print("raw | mapper output | scan pressure source | member channel | emitted pressure")
    for raw in (127, 50, 36, 35, 34, 33, 32, 31, 30, 29, 28, 20, 0):
        mapper = machine_for_preset(code, preset)
        mapper.call(0x6183, r6=3, r7=0x63, r5=raw)
        source, channel, events = trace_held_key(code, preset, raw)
        pressure = [event[2] for event in events if event[0] == "pressure"]
        print(f"{raw:3} | {mapper.r(7):13} | {source:20} | {channel:14} | {pressure}")


if __name__ == "__main__":
    main()
