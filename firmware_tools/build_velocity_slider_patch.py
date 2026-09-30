#!/usr/bin/env python3
"""Add an on-board velocity-sensitivity slider to the v6 relative-tilt image.

Hold the Velocity button for two and a half seconds. The keyboard stops playing and the
white keys become a 15-step slider from gain 60 to 254 (100 = unchanged); the
LEDs of the white keys up to the chosen step light. Press the Velocity button
again to leave. The stock short-press toggle is undone on entry and never sees
the exit press.

Three hooks, all absolute-address replacements of one three-byte stock
instruction:
  0x575C  sensor channel dispatch (runs once per channel per round)
  0x4C4F  per-key note-on entry
  0x7C67  common MIDI message sender

The gain is written to the live preset copy (XRAM 0x0396:0x0397, big endian),
which the stock note-on code reads for every note. It is not saved to flash.

This only writes a flat image and a SysEx file. It never touches a device.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools.build_relative_tilt_patch import ROOT, Routine, build_image as build_v6
from firmware_tools.extract_kmi_firmware import build_image as decode_image, extract
from firmware_tools.repack_kmi_firmware import pack, records_from_image
from firmware_tools.build_relative_tilt_patch import STOCK_SYX

DEFAULT_OUTPUT = ROOT / "firmware_analysis/kboard_1.2.2-relative-tilt-v8"

# Stock routines and data the slider uses.
STOCK_SET_LED = 0x7AFB      # R7 = LED index, R5 = level (0xFF on)
STOCK_LOG_STATE = 0x6661    # appends XRAM 0x93E to the flash state log
STOCK_SEND_BODY = 0x7C6A    # rest of the MIDI sender after the hooked entry
XRAM_BUTTON_BITS = 0x093E   # bit 2 = velocity on
XRAM_CHANNEL = 0x0978       # sensor channel being processed
XRAM_READING = 0x0977       # its 0..127 reading
XRAM_VELOCITY_GAIN = 0x0396  # 16-bit big endian, 100 = x1.0
IRAM_TICK_HI, IRAM_TICK_LO = 0x4B, 0x4C  # millisecond counter
VELOCITY_CHANNEL = 4
PRESS = 0x79        # same press threshold as the stock buttons
HELD_MIN = 0x40     # a finger this firm is still on the button; tolerates jitter during the hold
HOLD_MS = 2500   # ticks are 1 ms on the board (measured: 5000 took 5.0 s)

# State lives above the tilt patch's 0x0F00-0x0F3F.
XRAM_STATE = 0x0F40    # 0 idle, 1 timing, 2 entered (button down), 3 active, 4 exiting
XRAM_T0 = 0x0F41       # two bytes: tick when the press began
XRAM_STEP = 0x0F43     # selected step, 0..14
XRAM_SAVED = 0x0F44    # velocity bit as it was before the press

# Free flash in the v6 image (0x8715-0x8DB5 is erased).
SEND_HOOK = 0x8720
KEYON_HOOK = 0x8750
TICK = 0x8790
DRAW = 0x87A0
READ_HOOK = 0x87E0
READ_BODY = 0x8820
WHITE_TABLE = 0x8B00
GAIN_TABLE = 0x8B20

KEY_COUNT = 25
GAIN_STEPS = 15
GAIN_MIN, GAIN_MAX = 60, 254
WHITE_PITCH_CLASSES = (0, 2, 4, 5, 7, 9, 11)

HOOKS = {  # address: (expected stock bytes, replacement opcode, target)
    0x4C4F: ("900889", 0x12, KEYON_HOOK),
    0x575C: ("900977", 0x12, READ_HOOK),
    0x7C67: ("9008a5", 0x02, SEND_HOOK),
}


def white_table() -> bytes:
    """Key number -> white-key step 0..14, or 0xFF for a black key."""
    table, step = bytearray(), 0
    for key in range(KEY_COUNT):
        if key % 12 in WHITE_PITCH_CLASSES:
            table.append(step)
            step += 1
        else:
            table.append(0xFF)
    assert step == GAIN_STEPS
    return bytes(table)


def gain_table() -> bytes:
    return bytes(round(GAIN_MIN + i * (GAIN_MAX - GAIN_MIN) / (GAIN_STEPS - 1))
                 for i in range(GAIN_STEPS))


class Asm(Routine):
    """The handful of 8051 instructions these routines need."""

    def mov_dptr(self, address): self.emit(0x90, address >> 8, address & 255)
    def movx_load(self): self.emit(0xE0)
    def movx_store(self): self.emit(0xF0)
    def mov_a_r(self, n): self.emit(0xE8 + n)
    def mov_r_a(self, n): self.emit(0xF8 + n)
    def mov_r(self, n, value): self.emit(0x78 + n, value)
    def mov_a(self, value): self.emit(0x74, value)
    def mov_a_direct(self, address): self.emit(0xE5, address)
    def clr_a(self): self.emit(0xE4)
    def clr_c(self): self.emit(0xC3)
    def subb(self, value): self.emit(0x94, value)
    def subb_r(self, n): self.emit(0x98 + n)
    def add(self, value): self.emit(0x24, value)
    def xrl(self, value): self.emit(0x64, value)
    def xrl_r(self, n): self.emit(0x68 + n)
    def orl(self, value): self.emit(0x44, value)
    def anl(self, value): self.emit(0x54, value)
    def inc_dptr(self): self.emit(0xA3)
    def inc_r(self, n): self.emit(0x08 + n)
    def movc(self): self.emit(0x93)
    def lcall(self, address): self.emit(0x12, address >> 8, address & 255)
    def ljmp_abs(self, address): self.emit(0x02, address >> 8, address & 255)
    def ret(self): self.emit(0x22)

    def cjne_r(self, n, value, name):
        self.emit(0xB8 + n, value, 0)
        self.fixups.append((len(self.code) - 1, name))

    def jc_far(self, name):
        self.emit(0x50, 3)   # JNC over the LJMP
        self.ljmp(name)
    def jnc_far(self, name):
        self.emit(0x40, 3)   # JC over the LJMP
        self.ljmp(name)
    def store_state(self, value, address=XRAM_STATE):
        self.mov_dptr(address)
        self.mov_a(value)
        self.movx_store()


def build_send_hook() -> bytes:
    """Drop everything but note-off and system messages while the slider is active."""
    r = Asm(SEND_HOOK)
    r.mov_dptr(XRAM_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(2)
    r.rel(0x40, "pass")      # JC: state 0 or 1
    r.clr_c()
    r.subb(2)
    r.rel(0x50, "pass")      # JNC: state 4 (exiting) plays normally
    r.mov_a_r(7)             # R7 is the status byte
    r.clr_c()
    r.subb(0x90)
    r.rel(0x40, "pass")      # note-off and data bytes
    r.mov_a_r(7)
    r.clr_c()
    r.subb(0xF0)
    r.rel(0x50, "pass")      # system messages
    r.ret()                  # drop note-on, pressure, control, bend
    r.label("pass")
    r.mov_dptr(0x08A5)       # the instruction the hook displaced
    r.ljmp_abs(STOCK_SEND_BODY)
    return r.finish()


def build_keyon_hook() -> bytes:
    """A white key pressed in slider mode picks that step; the stock note-on still runs."""
    r = Asm(KEYON_HOOK)
    r.mov_dptr(XRAM_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(2)
    r.rel(0x40, "stock")
    r.clr_c()
    r.subb(2)
    r.rel(0x50, "stock")
    r.mov_a_r(7)             # R7 = key number
    r.clr_c()
    r.subb(KEY_COUNT)
    r.rel(0x50, "stock")     # not a key
    r.mov_a_r(7)
    r.mov_dptr(WHITE_TABLE)
    r.movc()                 # A = step, or 0xFF for a black key
    r.cjne_a(0xFF, "white")
    r.rel(0x80, "stock")
    r.label("white")
    r.mov_dptr(XRAM_STEP)
    r.movx_store()
    r.mov_dptr(GAIN_TABLE)
    r.movc()                 # A still holds the step
    r.mov_dptr(XRAM_VELOCITY_GAIN + 1)
    r.movx_store()
    r.mov_dptr(XRAM_VELOCITY_GAIN)
    r.clr_a()
    r.movx_store()
    r.label("stock")
    r.mov_dptr(0x0889)       # the instruction the hook displaced
    r.ret()
    return r.finish()


def build_tick() -> bytes:
    """R6:R7 = millisecond counter, re-read if the interrupt ticked mid-read."""
    r = Asm(TICK)
    r.label("again")
    r.mov_a_direct(IRAM_TICK_HI)
    r.mov_r_a(6)
    r.mov_a_direct(IRAM_TICK_LO)
    r.mov_r_a(7)
    r.mov_a_direct(IRAM_TICK_HI)
    r.xrl_r(6)
    r.rel(0x70, "again")
    r.ret()
    return r.finish()


def build_draw() -> bytes:
    """Light the white keys up to the chosen step (R0 = 1 turns every key LED off)."""
    r = Asm(DRAW)
    r.mov_dptr(XRAM_STEP)
    r.movx_load()
    r.mov_r_a(4)             # R4 = chosen step
    r.mov_r(2, 0)            # R2 = key
    r.label("key")
    r.mov_dptr(WHITE_TABLE)
    r.mov_a_r(2)
    r.movc()
    r.mov_r_a(3)             # R3 = step of this key, or 0xFF
    r.mov_a_r(0)
    r.rel(0x70, "off")       # clear-all request
    r.cjne_r(3, 0xFF, "white")
    r.rel(0x80, "off")
    r.label("white")
    r.mov_a_r(4)
    r.clr_c()
    r.subb_r(3)
    r.rel(0x40, "off")       # key is above the chosen step
    r.mov_r(5, 0xFF)
    r.rel(0x80, "put")
    r.label("off")
    r.mov_r(5, 0x00)
    r.label("put")
    r.mov_a_r(2)
    r.mov_r_a(7)
    r.lcall(STOCK_SET_LED)
    r.inc_r(2)
    r.cjne_r(2, KEY_COUNT, "key")
    r.ret()
    return r.finish()


def build_read_hook() -> bytes:
    """Hook entry: preserve the caller's registers, run the body, replay the displaced MOV."""
    r = Asm(READ_HOOK)
    for register in (0, 1, 2, 3, 4, 5, 6, 7, 0xF0):  # R0-R7 (bank 0) and B
        r.emit(0xC0, register)
    r.lcall(READ_BODY)
    for register in (0xF0, 7, 6, 5, 4, 3, 2, 1, 0):
        r.emit(0xD0, register)
    r.mov_dptr(XRAM_READING)                   # the instruction the hook displaced
    r.ret()
    return r.finish()
def build_read_body(address=READ_BODY, channel=VELOCITY_CHANNEL, button_mask=0x04,
                    mode=0, mode_address=None, gain_address=XRAM_VELOCITY_GAIN,
                    gain_table_address=GAIN_TABLE, byte_setting=False,
                    invert_max=None, save_address=None) -> bytes:
    """Build one sensitivity-menu state machine for a front-panel sensor button."""
    r = Asm(address)
    r.mov_dptr(XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(channel, "quit")
    r.rel(0x80, "body")
    r.label("quit")
    r.ret()
    r.label("body")
    r.mov_dptr(XRAM_READING)
    r.movx_load()
    r.mov_r_a(7)                               # R7 = raw reading
    r.mov_dptr(XRAM_STATE)
    r.movx_load()
    r.rel(0x60, "idle")                        # JZ
    r.cjne_a(1, "not1")
    r.ljmp("timing")
    r.label("not1")
    r.cjne_a(2, "not2")
    r.ljmp("entered")
    r.label("not2")
    r.cjne_a(3, "not3")
    r.ljmp("active")
    r.label("not3")
    r.ljmp("exiting")

    # State 0: a press starts the clock and remembers the velocity bit.
    r.label("idle")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(PRESS)
    r.jc_far("out")
    r.lcall(TICK)
    r.mov_dptr(XRAM_T0)
    r.mov_a_r(6)
    r.movx_store()
    r.inc_dptr()
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(XRAM_BUTTON_BITS)
    r.movx_load()
    r.anl(button_mask)
    r.mov_dptr(XRAM_SAVED)
    r.movx_store()
    if mode_address is not None:
        r.mov_dptr(mode_address)
        r.mov_a(mode)
        r.movx_store()
    r.store_state(1)
    r.ljmp("out")

    # State 1: stock still sees the button. After HOLD_MS take it over.
    r.label("timing")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(HELD_MIN)
    r.rel(0x50, "held")                        # JNC: still down
    r.store_state(0)
    r.ljmp("out")
    r.label("held")
    r.lcall(TICK)
    r.mov_dptr(XRAM_T0)
    r.movx_load()
    r.mov_r_a(4)
    r.inc_dptr()
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(7)
    r.clr_c()
    r.subb_r(5)
    r.mov_r_a(7)
    r.mov_a_r(6)
    r.subb_r(4)
    r.mov_r_a(6)                               # R6:R7 = elapsed milliseconds
    r.mov_a_r(7)
    r.clr_c()
    r.subb(HOLD_MS & 0xFF)
    r.mov_a_r(6)
    r.subb(HOLD_MS >> 8)
    r.jc_far("out")                         # JC: not yet
    # Entering: put the velocity bit back the way it was before this press.
    r.mov_dptr(XRAM_SAVED)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_dptr(XRAM_BUTTON_BITS)
    r.movx_load()
    r.mov_r_a(4)
    r.anl(button_mask)
    r.xrl_r(5)
    r.rel(0x60, "restored")                    # JZ: stock never toggled
    r.mov_a_r(4)
    r.xrl(button_mask)
    r.orl(0xF0)
    r.movx_store()
    r.lcall(STOCK_LOG_STATE)
    r.label("restored")
    # Start on the step nearest the current gain.
    r.mov_r(1, GAIN_STEPS - 1)
    r.mov_dptr(gain_address)
    r.movx_load()
    if invert_max is not None:
        r.mov_r_a(5)
        r.mov_a(invert_max)
        r.clr_c()
        r.subb_r(5)
        r.mov_r_a(5)
    elif byte_setting:
        r.mov_r_a(5)
    else:
        r.mov_r_a(4)  # gain high byte
        r.inc_dptr()
        r.movx_load()
        r.mov_r_a(5)  # gain low byte
        r.mov_a_r(4)
        r.rel(0x70, "start")
        r.mov_a_r(5)
    if not byte_setting and invert_max is None:
        r.add(6)
        r.rel(0x40, "start")
    else:
        r.add(6)
        r.rel(0x40, "start")
    r.mov_r_a(5)                               # R5 = gain + 6
    r.mov_r(2, 0)
    r.mov_r(1, 0)
    r.label("scan")
    r.mov_dptr(gain_table_address)
    r.mov_a_r(2)
    r.movc()
    r.mov_r_a(3)
    r.mov_a_r(5)
    r.clr_c()
    r.subb_r(3)
    r.rel(0x40, "start")                       # step is above the gain
    r.mov_a_r(2)
    r.mov_r_a(1)
    r.inc_r(2)
    r.cjne_r(2, GAIN_STEPS, "scan")
    r.label("start")
    r.mov_dptr(XRAM_STEP)
    r.mov_a_r(1)
    r.movx_store()
    r.store_state(2)
    r.ljmp("draw")

    # State 2: entered while the button is still down; wait for release.
    r.label("entered")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(HELD_MIN)
    r.jnc_far("draw")                        # still down
    r.store_state(3)
    r.ljmp("draw")

    # State 3: slider active; the next press exits.
    r.label("active")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(PRESS)
    r.jc_far("draw")
    r.store_state(4)
    r.mov_r(0, 1)
    r.lcall(DRAW)
    r.ljmp("mask")

    # State 4: exit press is being swallowed until the button is released.
    r.label("exiting")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(HELD_MIN)
    r.jnc_far("mask")
    r.store_state(0)
    if save_address is not None:
        r.lcall(save_address)
    r.ljmp("out")

    r.label("draw")
    r.mov_r(0, 0)
    r.lcall(DRAW)
    r.label("mask")
    r.mov_dptr(XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.ret()
    r.label("out")
    r.ret()
    return r.finish()


def build_image() -> tuple[bytes, bytes]:
    v6_image, _ = build_v6()
    patched = bytearray(v6_image)
    routines = {SEND_HOOK: build_send_hook(), KEYON_HOOK: build_keyon_hook(),
                TICK: build_tick(), DRAW: build_draw(), READ_HOOK: build_read_hook(),
                READ_BODY: build_read_body(),
                WHITE_TABLE: white_table(), GAIN_TABLE: gain_table()}
    spans = sorted((address, address + len(code)) for address, code in routines.items())
    for (_, end), (start, _) in zip(spans, spans[1:]):
        if end > start:
            raise ValueError(f"slider routines overlap near 0x{start:04X}")
    for address, code in routines.items():
        if any(byte != 0xFF for byte in patched[address:address + len(code)]):
            raise ValueError(f"code region at 0x{address:04X} is occupied")
        patched[address:address + len(code)] = code
    for address, (expected, opcode, target) in HOOKS.items():
        if bytes(patched[address:address + 3]).hex() != expected:
            raise ValueError(f"hook site 0x{address:04X} does not hold the expected stock bytes")
        patched[address:address + 3] = bytes((opcode, target >> 8, target & 0xFF))
    records, _ = extract(STOCK_SYX)
    return bytes(patched), pack(records_from_image(records, patched))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    image, sysex = build_image()
    image_path = Path(f"{args.output_prefix}.bin")
    sysex_path = Path(f"{args.output_prefix}.syx")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image)
    sysex_path.write_bytes(sysex)
    print(f"image: {image_path} ({len(image)} bytes, sha256 {hashlib.sha256(image).hexdigest()})")
    print(f"SysEx: {sysex_path} ({len(sysex)} bytes, sha256 {hashlib.sha256(sysex).hexdigest()})")


if __name__ == "__main__":
    main()
