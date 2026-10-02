#!/usr/bin/env python3
"""Build a native, per-MPE-channel relative-tilt K-Board 1.2.2 image.

This only writes a flat flash image and a firmware SysEx file. It never opens
a MIDI port or writes to the device. The four hooks intercept note-on,
note-off, per-key tilt, and the common 14-bit bend sender.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools.extract_kmi_firmware import build_image as build_stock_image, extract
from firmware_tools.repack_kmi_firmware import pack, records_from_image


ROOT = Path(__file__).resolve().parent.parent
STOCK_SYX = ROOT / "firmware_stock/K-Board Firmware v1.2.2_cs512.syx"
STOCK_SYX_SHA256 = "33e300a9da3626a80e021543f5159c4ce2d24bc4d16dbd369e0e1606f5018033"
STOCK_IMAGE_SHA256 = "66058de35eb397f6ec03fc31884380ecd5d8debbb6cece4e89285c90feb2d7f4"
DEFAULT_OUTPUT = ROOT / "firmware_analysis/kboard_1.2.2-relative-tilt-v13-hires-candidate"

# These three-byte calls/entry instructions are replaced by absolute hooks.
HOOKS = {
    0x7FF3: bytes.fromhex("ae 07 ee"),  # MOV R6,R7; MOV A,R6
    0x7F10: bytes.fromhex("12 7c 67"),  # LCALL MIDI send (note-on)
    0x7FEF: bytes.fromhex("12 7c 67"),  # LCALL MIDI send (note-off)
    0x7EA6: bytes.fromhex("ef 54 7f"),  # 14-bit bend sender entry
}

NOTE_ON = 0x8300
NOTE_OFF = 0x8330
BEND = 0x8340
PAD = 0x8500
PAD_SCALE = 0x8680
COMBINE = 0x8700
SCALE14 = 0x8E15
HIRES_BEND = 0x8E90
XDATA_TILT_LOW = 0x0F00  # 16 bytes: cached low 7-bit bend data per channel
XDATA_TILT_BASELINE = 0x0F10  # 16 bytes: state sentinels or biased 7-bit baselines
XDATA_PAD_ACTIVE = 0x0F20
XDATA_PAD_BASELINE = 0x0F21
XDATA_PAD_OFFSET = 0x0F22  # 7-bit bend, 64 = center
XDATA_TILT_CURRENT = 0x0F30  # current uncombined bend per member
XDATA_BEND_REDUCTION = 0x04F1  # former CV input 1 offset: 64 - bend width
XDATA_DEADZONE = 0x04FA  # former CV input 2 offset: raw tilt steps
XDATA_PAD_REDUCTION = 0x04F0  # former CV input 1 minimum
XDATA_PAD_DEADZONE = 0x04F9  # former CV input 2 minimum
XDATA_BEND_FLAGS = 0x04EF  # 0x70 | relative tilt/pad/combine flags
XDATA_BEND_FLAGS_ESCAPE = 0x04F8  # disambiguates the legacy 0x7E mode byte
RELATIVE_TILT_BIT = 0x04
RELATIVE_PAD_BIT = 0x02
COMBINE_BENDS_BIT = 0x01


class Routine:
    """Tiny 8051 byte emitter with checked relative branches."""

    def __init__(self, address: int):
        self.address = address
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []
        self.absolute_fixups: list[tuple[int, str]] = []

    def emit(self, *values: int) -> None:
        self.code.extend(values)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate label {name}")
        self.labels[name] = self.address + len(self.code)

    def rel(self, opcode: int, name: str) -> None:
        self.emit(opcode, 0)
        self.fixups.append((len(self.code) - 1, name))

    def cjne_a(self, immediate: int, name: str) -> None:
        self.emit(0xB4, immediate, 0)
        self.fixups.append((len(self.code) - 1, name))

    def cjne_r2(self, immediate: int, name: str) -> None:
        self.emit(0xBA, immediate, 0)
        self.fixups.append((len(self.code) - 1, name))

    def jb(self, bit: int, name: str) -> None:
        self.emit(0x20, bit, 0)
        self.fixups.append((len(self.code) - 1, name))

    def ljmp(self, name: str) -> None:
        self.emit(0x02, 0, 0)
        self.absolute_fixups.append((len(self.code) - 2, name))

    def finish(self) -> bytes:
        for position, name in self.fixups:
            if name not in self.labels:
                raise ValueError(f"missing label {name}")
            target = self.labels[name]
            displacement = target - (self.address + position + 1)
            if not -128 <= displacement <= 127:
                raise ValueError(f"branch to {name} is out of range: {displacement}")
            self.code[position] = displacement & 0xFF
        for position, name in self.absolute_fixups:
            if name not in self.labels:
                raise ValueError(f"missing label {name}")
            target = self.labels[name]
            self.code[position:position + 2] = target.to_bytes(2, "big")
        return bytes(self.code)


def _channel_dpl(routine: Routine, offset: int = 0) -> None:
    # A = (R3 & 0x0F) | offset; DPL = A. DPH already equals 0x0F.
    routine.emit(0xEB, 0x54, 0x0F)  # MOV A,R3; ANL A,#0F
    if offset:
        routine.emit(0x44, offset)  # ORL A,#offset
    routine.emit(0xF5, 0x82)  # MOV DPL,A


def build_note_on() -> bytes:
    r = Routine(NOTE_ON)
    r.emit(0x90, 0x03, 0x51, 0xE0)  # MOV DPTR,#MPE_ENABLED; MOVX A,@DPTR
    r.rel(0x60, "send")  # JZ: ordinary MIDI mode
    r.emit(0xEF, 0x54, 0x0F)  # channel from status R7
    r.rel(0x60, "send")  # MPE master channel has no per-note baseline
    r.emit(0x44, 0x10)  # baseline/state slot = 0x0F10 + channel
    r.emit(0xF5, 0x82, 0x75, 0x83, 0x0F)  # DPTR = 0x0F10 + channel
    r.emit(0x74, 0x81, 0xF0)  # baseline[channel] = armed sentinel
    r.emit(0xEF, 0x54, 0x0F, 0x44, 0x30, 0xF5, 0x82)
    r.emit(0x74, 0x40, 0xF0)  # cached tilt[channel] = center
    r.emit(0xEF, 0x54, 0x0F, 0xF5, 0x82, 0x75, 0x83, 0x0F)
    r.emit(0xE4, 0xF0)  # cached low bend byte[channel] = 0
    r.label("send")
    r.emit(0x02, 0x7C, 0x67)  # LJMP stock MIDI output helper
    return r.finish()


def build_note_off() -> bytes:
    r = Routine(NOTE_OFF)
    r.emit(0xEF, 0x54, 0x0F, 0x44, 0x10, 0xF5, 0x82, 0x75, 0x83, 0x0F)
    r.emit(0xE4, 0xF0)  # baseline/state[channel] = inactive
    r.emit(0x02, 0x7C, 0x67)  # LJMP stock MIDI output helper
    return r.finish()


def build_pad() -> bytes:
    """Turn the master pad bend into an additive member-channel bend."""
    r = Routine(PAD)
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0)
    r.cjne_a(0x7E, "check_zero_default")
    r.emit(0x90, XDATA_BEND_FLAGS_ESCAPE >> 8, XDATA_BEND_FLAGS_ESCAPE & 255, 0xE0)
    r.cjne_a(0x7E, "legacy_stock")
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0)
    r.ljmp("check_relative")
    r.label("legacy_stock")
    r.emit(0x90, XDATA_PAD_ACTIVE >> 8, XDATA_PAD_ACTIVE & 255, 0xE4, 0xF0)
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255, 0x74, 0x40, 0xF0)
    r.ljmp("replay")  # v12's former all-stock marker
    r.label("check_zero_default")
    r.rel(0x60, "relative_pad")  # an unset mode byte uses all custom behaviors
    r.label("check_relative")
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0, 0x54, RELATIVE_PAD_BIT)
    r.rel(0x70, "relative_pad")
    # Absolute pad: retain its raw position as the member-channel addend.
    r.ljmp("absolute_pad")
    r.label("relative_pad")
    r.emit(0x90, 0x03, 0x51, 0xE0)
    r.rel(0x70, "check_master")
    r.ljmp("replay")
    r.label("check_master")
    r.emit(0xED)  # master channel is zero
    r.rel(0x60, "mpe")
    r.ljmp("replay")
    r.label("mpe")
    r.emit(0xC0, 0x02, 0xC0, 0x03, 0xC0, 0x04, 0xC0, 0x05,
           0xC0, 0x06, 0xC0, 0x07)
    # The stock scan state stays nonzero while the pad is touched. The raw
    # bend fallback covers a scan/update race at the start of contact.
    r.emit(0x90, 0x09, 0x99, 0xE0)
    r.rel(0x70, "active")
    r.emit(0xEE)
    r.cjne_a(0x20, "active")
    r.emit(0xEF)
    r.rel(0x70, "active")
    r.ljmp("released")
    r.label("active")
    # Convert the incoming 14-bit value to a seven-bit position: R6:R7 >> 7.
    r.emit(0xEF, 0x23, 0x54, 0x01, 0xFA)  # R2 = high bit of R7
    r.emit(0xEE, 0x23, 0x4A, 0xFF)  # R7 = R6*2 | R2
    r.emit(0x90, XDATA_PAD_ACTIVE >> 8, XDATA_PAD_ACTIVE & 255, 0xE0)
    r.rel(0x70, "movement")
    r.emit(0x74, 0x01, 0xF0)  # active = 1
    r.emit(0x90, XDATA_PAD_BASELINE >> 8, XDATA_PAD_BASELINE & 255,
           0xEF, 0xF0)  # baseline = landing position
    r.emit(0x7F, 0x40)  # no bend at first touch
    r.ljmp("save_offset")
    r.label("movement")
    r.emit(0x90, XDATA_PAD_BASELINE >> 8, XDATA_PAD_BASELINE & 255,
           0xE0, 0xFE)  # R6 = baseline
    r.emit(0x12, PAD_SCALE >> 8, PAD_SCALE & 255)
    r.label("save_offset")
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255,
           0xEF, 0xF0)
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0, 0x54, COMBINE_BENDS_BIT)
    r.rel(0x60, "send_members")
    r.rel(0x70, "send_members")
    r.ljmp("send_global")

    r.label("absolute_pad")
    r.emit(0x90, 0x03, 0x51, 0xE0)
    r.rel(0x70, "absolute_mode")
    r.ljmp("replay")
    r.label("absolute_mode")
    r.emit(0xED)
    r.rel(0x60, "absolute_mpe")
    r.ljmp("replay")
    r.label("absolute_mpe")
    r.emit(0xC0, 0x02, 0xC0, 0x03, 0xC0, 0x04, 0xC0, 0x05,
           0xC0, 0x06, 0xC0, 0x07)
    r.emit(0x90, 0x09, 0x99, 0xE0)
    r.rel(0x70, "absolute_active")
    r.emit(0xEE)
    r.cjne_a(0x20, "absolute_active")
    r.emit(0xEF)
    r.rel(0x70, "absolute_active")
    r.ljmp("released")
    r.label("absolute_active")
    r.emit(0xEF, 0x23, 0x54, 0x01, 0xFA, 0xEE, 0x23, 0x4A, 0xFF)
    r.emit(0x90, XDATA_PAD_ACTIVE >> 8, XDATA_PAD_ACTIVE & 255, 0xE0)
    r.rel(0x70, "absolute_save")
    r.emit(0x74, 0x01, 0xF0)
    r.label("absolute_save")
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255, 0xEF, 0xF0)
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0, 0x54, COMBINE_BENDS_BIT)
    r.rel(0x60, "send_members")
    r.rel(0x70, "send_members")
    r.ljmp("send_global")
    r.ljmp("send_members")

    r.label("released")
    r.emit(0x90, XDATA_PAD_ACTIVE >> 8, XDATA_PAD_ACTIVE & 255, 0xE0)
    r.rel(0x60, "restore")  # no pad transition
    r.emit(0xE4, 0xF0)
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255,
           0x74, 0x40, 0xF0)
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0, 0x54, COMBINE_BENDS_BIT)
    r.rel(0x60, "send_members")
    r.rel(0x70, "send_members")
    r.ljmp("send_global")

    r.label("send_members")
    r.emit(0x7A, 0x01)  # member channel 1
    r.label("member_loop")
    r.emit(0xEA, 0x24, 0x10, 0xF5, 0x82, 0x75, 0x83, 0x0F, 0xE0)
    r.rel(0x60, "next_member")  # inactive
    r.emit(0xEA, 0x44, 0x30, 0xF5, 0x82, 0xE0, 0xFF)
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255,
           0xE0, 0xFE)
    r.emit(0x12, COMBINE >> 8, COMBINE & 255)
    r.emit(0xC0, 0x02, 0xFB)  # preserve loop channel; R3 = combined MSB
    r.emit(0x90, 0x0F, 0x00, 0xEA, 0xF5, 0x82, 0xE0, 0xFD)  # R5 = cached tilt LSB
    r.emit(0xEA, 0x44, 0xE0, 0xFF, 0x12, 0x7C, 0x67)
    r.emit(0xD0, 0x02)
    r.label("next_member")
    r.emit(0x0A)
    r.cjne_r2(16, "member_loop")

    r.label("restore")
    r.emit(0xD0, 0x07, 0xD0, 0x06, 0xD0, 0x05, 0xD0, 0x04,
           0xD0, 0x03, 0xD0, 0x02)
    # Master must stay at center: the member bends already include the pad.
    r.emit(0x7E, 0x20, 0x7F, 0x00)
    r.ljmp("replay")
    r.label("send_global")
    r.emit(0xD0, 0x07, 0xD0, 0x06, 0xD0, 0x05, 0xD0, 0x04,
           0xD0, 0x03, 0xD0, 0x02)
    # Convert the seven-bit bend back to the stock sender's 14-bit input.
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255, 0xE0, 0xFF)
    r.emit(0xEF, 0xC3, 0x13, 0xFE, 0x74, 0x00, 0x13, 0xFF)
    r.emit(0xEF, 0x54, 0x7F, 0x02, 0x7E, 0xA9)
    r.label("replay")
    r.emit(0xEF, 0x54, 0x7F, 0x02, 0x7E, 0xA9)
    return r.finish()


def build_pad_scale() -> bytes:
    """Map pad displacement from the landing point into a seven-bit bend."""
    r = Routine(PAD_SCALE)
    r.emit(0xEF, 0xC3, 0x9E)  # current - baseline
    r.rel(0x50, "positive")
    r.emit(0xF4, 0x04, 0x7E, 0x01)
    r.rel(0x80, "magnitude")
    r.label("positive")
    r.emit(0x7E, 0x00)
    r.label("magnitude")
    r.emit(0xFF, 0xC0, 0x06)
    r.emit(0x90, XDATA_PAD_DEADZONE >> 8, XDATA_PAD_DEADZONE & 255,
           0xE0, 0xFE)
    r.emit(0xEF, 0xC3, 0x9E)
    r.rel(0x40, "center")
    r.rel(0x60, "center")
    r.emit(0xFF, 0xEF, 0xC3, 0x94, 0x41)
    r.rel(0x40, "within_span")
    r.emit(0x7F, 0x40)
    r.label("within_span")
    r.emit(0x90, XDATA_PAD_REDUCTION >> 8, XDATA_PAD_REDUCTION & 255,
           0xE0, 0xFE, 0x74, 0x40, 0xC3, 0x9E)
    r.rel(0x40, "center")
    r.emit(0xC0, 0xF0, 0xF5, 0xF0, 0xEF, 0xA4)
    r.emit(0xFF, 0xE5, 0xF0, 0x23, 0x23, 0xFE)
    r.emit(0xEF, 0x54, 0xC0, 0x23, 0x23, 0x4E, 0xFF, 0xD0, 0xF0)
    r.emit(0xD0, 0x06, 0xEE)
    r.rel(0x70, "negative")
    r.emit(0xEF, 0x24, 0x40)
    r.jb(0xE7, "high")
    r.ljmp("save")
    r.label("high")
    r.emit(0x74, 0x7F)
    r.ljmp("save")
    r.label("negative")
    r.emit(0x74, 0x40, 0xC3, 0x9F)
    r.rel(0x50, "save")
    r.emit(0xE4)
    r.ljmp("save")
    r.label("center")
    r.emit(0xD0, 0x06, 0x74, 0x40)
    r.label("save")
    r.emit(0xFF, 0x22)
    return r.finish()


def build_combine() -> bytes:
    """R7 = clamp(R7 + R6 - 64, 0, 127)."""
    r = Routine(COMBINE)
    r.emit(0xEF, 0x2E, 0xC3, 0x94, 0x40)
    r.rel(0x40, "low")
    r.jb(0xE7, "high")
    r.ljmp("save")
    r.label("low")
    r.emit(0xE4)
    r.ljmp("save")
    r.label("high")
    r.emit(0x74, 0x7F)
    r.label("save")
    r.emit(0xFF, 0x22)
    return r.finish()


def build_bend() -> bytes:
    r = Routine(BEND)
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0)
    r.cjne_a(0x7E, "check_zero_default")
    r.emit(0x90, XDATA_BEND_FLAGS_ESCAPE >> 8, XDATA_BEND_FLAGS_ESCAPE & 255, 0xE0)
    r.cjne_a(0x7E, "legacy_stock")
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0)
    r.ljmp("check_relative")
    r.label("legacy_stock")
    r.ljmp("replay")  # v12's former all-stock marker
    r.label("check_zero_default")
    r.rel(0x60, "relative")  # an unset mode byte uses all custom behaviors
    r.label("check_relative")
    # New presets use mode prefix 0x70 and carry an exact 0..100 percentage.
    # Older 0x78 presets retain their original seven-bit scaling path.
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0, 0x54, 0xF8)
    r.cjne_a(0x70, "legacy_flags")
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0,
           0x54, RELATIVE_TILT_BIT)
    r.rel(0x70, "highres_jump")
    r.ljmp("legacy_flags")
    r.label("highres_jump")
    r.emit(0x02, HIRES_BEND >> 8, HIRES_BEND & 255)
    r.label("legacy_flags")
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0)
    r.emit(0x54, RELATIVE_TILT_BIT)
    r.rel(0x70, "relative")
    # Absolute tilt can still be mixed with the pad when requested.
    r.emit(0x90, 0x0F, 0x00)
    _channel_dpl(r, 0x10)
    r.emit(0x74, 0x82, 0xF0)  # active absolute bend; relative mode can re-arm
    _channel_dpl(r, 0x30)
    r.emit(0x75, 0x83, 0x0F, 0xEF, 0xF0)
    r.emit(0x90, 0x0F, 0x00)
    _channel_dpl(r)
    r.emit(0xE4, 0xF0)  # absolute tilt cache has no sub-semitone low byte
    r.emit(0x90, 0x03, 0x51, 0xE0)
    r.rel(0x70, "absolute_check_flags")
    r.ljmp("replay")
    r.label("absolute_check_flags")
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0, 0x54, COMBINE_BENDS_BIT)
    r.rel(0x60, "absolute_combine")
    r.rel(0x70, "absolute_combine")
    r.ljmp("replay")
    r.label("absolute_combine")
    r.emit(0x90, XDATA_PAD_ACTIVE >> 8, XDATA_PAD_ACTIVE & 255, 0xE0)
    r.rel(0x60, "absolute_no_combine")
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255, 0xE0, 0xFE)
    r.emit(0x12, COMBINE >> 8, COMBINE & 255)
    r.ljmp("replay")
    r.label("absolute_no_combine")
    r.ljmp("replay")
    r.label("relative")
    r.emit(0x90, 0x03, 0x51, 0xE0)  # only change MPE mode
    r.rel(0x70, "mpe")  # JNZ
    r.ljmp("replay")
    r.label("mpe")
    _channel_dpl(r, 0x10)
    r.emit(0x75, 0x83, 0x0F, 0xE0)  # baseline/state[channel]
    r.rel(0x60, "inactive")
    r.cjne_a(0x81, "check_absolute_marker")
    r.ljmp("first_sample")
    r.label("check_absolute_marker")
    r.cjne_a(0x82, "active")

    # Store a biased baseline (1..128) so zero remains the reset/inactive
    # state and 0x81/0x82 remain unambiguous sentinels.
    r.label("first_sample")
    r.emit(0xEF, 0x04, 0xF0)  # baseline[channel] = R7 + 1
    r.emit(0x7F, 0x40)  # tilt value = 64 (center)
    r.ljmp("combined")
    r.label("inactive")
    r.ljmp("replay")
    r.label("active")
    r.emit(0x14, 0xFE, 0xEF, 0xC3, 0x9E)  # unbias baseline; A = current - baseline
    r.rel(0x50, "positive")  # JNC
    r.emit(0xF4, 0x04, 0x7E, 0x01)  # abs(delta); R6 = negative sign
    r.rel(0x80, "magnitude")
    r.label("positive")
    r.emit(0x7E, 0x00)  # R6 = positive sign
    r.label("magnitude")
    r.emit(0xFF, 0xC0, 0x06)  # R7 = abs(delta); PUSH R6 sign

    # Subtract a neutral band around the landing point. Values within it
    # produce center; movement beyond the band ramps up from zero.
    r.emit(0x90, XDATA_DEADZONE >> 8, XDATA_DEADZONE & 255, 0xE0, 0xFE)
    r.emit(0xEF, 0xC3, 0x9E)  # A = magnitude - deadzone
    r.rel(0x40, "center")  # JC
    r.rel(0x60, "center")  # JZ
    r.emit(0xFF)  # R7 = magnitude beyond deadzone
    # Sensor travel can exceed 64 steps if the initial contact is near an
    # edge. Cap it so "25%" truly means no more than 25% of the bend span.
    r.emit(0xEF, 0xC3, 0x94, 0x41)  # magnitude - 65
    r.rel(0x40, "within_span")  # JC: magnitude <=64
    r.emit(0x7F, 0x40)
    r.label("within_span")

    # The default preset stores zero here, yielding width 64 and exact v1
    # behavior. A stored reduction of 48 yields width 16 (25% of v1).
    r.emit(0x90, XDATA_BEND_REDUCTION >> 8, XDATA_BEND_REDUCTION & 255,
           0xE0, 0xFE, 0x74, 0x40, 0xC3, 0x9E)
    r.rel(0x40, "center")  # invalid reduction >64 safely gives zero width
    r.emit(0xC0, 0xF0, 0xF5, 0xF0, 0xEF, 0xA4)  # preserve B; multiply

    # The product is at most 127*64. Divide by 64 using (B<<2)|(A>>6).
    r.emit(0xFF, 0xE5, 0xF0, 0x23, 0x23, 0xFE)
    r.emit(0xEF, 0x54, 0xC0, 0x23, 0x23, 0x4E, 0xFF, 0xD0, 0xF0)
    r.emit(0xD0, 0x06, 0xEE)  # POP sign into R6; MOV A,R6
    r.rel(0x70, "negative")  # JNZ
    r.emit(0xEF, 0x24, 0x40)  # positive: A = 64 + scaled magnitude
    r.jb(0xE7, "high")
    r.ljmp("save")
    r.label("high")
    r.emit(0x74, 0x7F)
    r.ljmp("save")
    r.label("negative")
    r.emit(0x74, 0x40, 0xC3, 0x9F)  # negative: A = 64 - magnitude
    r.rel(0x50, "save")  # JNC
    r.emit(0xE4)  # CLR A
    r.ljmp("save")

    r.label("center")
    r.emit(0xD0, 0x06, 0x7F, 0x40)  # POP sign; R7 = center
    r.ljmp("combined")
    r.label("save")
    r.emit(0xFF)  # MOV R7,A
    r.label("combined")
    _channel_dpl(r, 0x30)
    r.emit(0x75, 0x83, 0x0F, 0xEF, 0xF0)  # cache tilt before mixing
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0, 0x54, COMBINE_BENDS_BIT)
    r.rel(0x60, "check_pad_active")
    r.rel(0x60, "replay")
    r.label("check_pad_active")
    r.emit(0x90, XDATA_PAD_ACTIVE >> 8, XDATA_PAD_ACTIVE & 255, 0xE0)
    r.rel(0x60, "replay")
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255,
           0xE0, 0xFE)
    r.emit(0x12, COMBINE >> 8, COMBINE & 255)
    r.label("replay")
    r.emit(0xAE, 0x07, 0xEE, 0x02, 0x7F, 0xF6)
    # Replays the original three bytes, then continues at stock 0x7FF6.
    return r.finish()


def build_scale14() -> bytes:
    """R7=magnitude 0..64, R6=percent 0..100; return exact 14-bit delta in R6:R7."""
    r = Routine(SCALE14)
    # Product P=magnitude*percent, shifted three places so its 13 bits can be
    # consumed from the carry flag in a 13-round divide-by-25 long division.
    r.emit(0xEF, 0x8E, 0xF0, 0xA4, 0xF8, 0xE5, 0xF0, 0xF9)
    # MOV A,R7; MOV B,R6; MUL AB; R0=product low, R1=high
    r.emit(0xC3)
    for _ in range(3):
        r.emit(0xE8, 0x33, 0xF8, 0xE9, 0x33, 0xF9)
    r.emit(0x7A, 0x00, 0x7C, 0x00, 0x7D, 0x00, 0x7F, 0x0D)
    r.label("divide")
    r.emit(0xC3, 0xE8, 0x33, 0xF8, 0xE9, 0x33, 0xF9)
    r.emit(0xEA, 0x33, 0xFA, 0xC3, 0xEA, 0x94, 0x19)
    r.rel(0x40, "small_remainder")
    r.emit(0xFA, 0xD3)
    r.ljmp("quotient_shift")
    r.label("small_remainder")
    r.emit(0xC3)
    r.label("quotient_shift")
    r.emit(0xEC, 0x33, 0xFC, 0xED, 0x33, 0xFD)
    r.rel(0xDF, "divide")  # DJNZ R7,divide
    # floor(32*r/25) = r + floor(7*r/25), using the 8051 DIV AB.
    r.emit(0xEA, 0x75, 0xF0, 0x07, 0xA4, 0x75, 0xF0, 0x19, 0x84)
    r.emit(0x2A, 0xFA)  # add remainder to floor(7*r/25)
    # delta = quotient*32 + fractional term
    r.emit(0xEC)
    for _ in range(5):
        r.emit(0xC3, 0x33)
    r.emit(0xFF)  # quotient low << 5
    r.emit(0xEC, 0x03, 0x54, 0x7F, 0x03, 0x54, 0x7F, 0x03, 0x54, 0x7F, 0xFE)
    # quotient low >> 3 using rotate plus a clear top bit on each pass
    r.emit(0xED, 0x23, 0x23, 0x23, 0x23, 0x23, 0x4E, 0xFE)  # merge quotient high << 5
    r.emit(0xEA, 0x2F, 0xFF)  # add fractional term to low delta
    r.emit(0xEE, 0x34, 0x00, 0xFE)  # propagate carry to high delta
    r.emit(0x22)
    return r.finish()


def build_hires_bend() -> bytes:
    """Relative tilt path using 0..100 percent and the full 14-bit bend word."""
    r = Routine(HIRES_BEND)
    r.emit(0x90, 0x03, 0x51, 0xE0)
    r.rel(0x70, "mpe")
    # Non-MPE still uses the stock absolute sensor value and formatter.
    r.emit(0xAE, 0x07, 0xEE, 0x02, 0x7F, 0xF6)
    r.label("mpe")
    _channel_dpl(r, 0x10)
    r.emit(0x75, 0x83, 0x0F, 0xE0)
    r.rel(0x60, "passthrough")
    r.cjne_a(0x81, "check_active")
    r.ljmp("first_sample")
    r.label("check_active")
    r.cjne_a(0x82, "active")
    r.ljmp("first_sample")
    r.label("first_sample")
    r.emit(0xEF, 0x04, 0xF0)  # biased baseline = current + 1
    r.emit(0x7E, 0x40, 0x7F, 0x00)  # centered 14-bit bend
    r.ljmp("cache_and_mix")
    r.label("passthrough")
    r.emit(0xAE, 0x07, 0xEE, 0x02, 0x7F, 0xF6)

    r.label("active")
    # Signed movement from the per-note baseline.
    r.emit(0xE0, 0x14, 0xFE, 0xEF, 0xC3, 0x9E)
    r.rel(0x50, "positive")
    r.emit(0xF4, 0x04, 0x7E, 0x01)
    r.rel(0x80, "magnitude")
    r.label("positive")
    r.emit(0x7E, 0x00)
    r.label("magnitude")
    r.emit(0xFF, 0xC0, 0x06)  # save sign while R6 is reused
    r.emit(0x90, XDATA_DEADZONE >> 8, XDATA_DEADZONE & 255, 0xE0, 0xFE)
    r.emit(0xEF, 0xC3, 0x9E)
    r.rel(0x40, "center")
    r.rel(0x60, "center")
    r.emit(0xFF, 0xEF, 0xC3, 0x94, 0x41)
    r.rel(0x40, "within_span")
    r.emit(0x7F, 0x40)
    r.label("within_span")
    r.emit(0x90, XDATA_BEND_FLAGS_ESCAPE >> 8, XDATA_BEND_FLAGS_ESCAPE & 255,
           0xE0, 0xFE, 0x12, SCALE14 >> 8, SCALE14 & 255)
    r.emit(0xAC, 0x06, 0xAD, 0x07, 0xD0, 0x06)  # preserve delta; restore sign
    r.emit(0xEE)
    r.rel(0x70, "negative")
    # Positive: center + delta, with +8192 saturated to MIDI maximum.
    r.emit(0xEC, 0x24, 0x20)
    r.cjne_a(0x40, "positive_normal")
    r.emit(0x7C, 0x3F, 0x7D, 0xFF)
    r.ljmp("encode")
    r.label("positive_normal")
    r.emit(0xFC)
    r.ljmp("encode")
    r.label("negative")
    r.emit(0xE4, 0xC3, 0x9D, 0xFD, 0x74, 0x20, 0x9C, 0xFC)
    r.ljmp("encode")
    r.label("center")
    r.emit(0xD0, 0x06, 0x7E, 0x40, 0x7F, 0x00)
    r.ljmp("cache_and_mix")
    r.label("encode")
    # Raw word R4:R5 -> MIDI data bytes (MSB in R6, LSB in R7).
    r.emit(0xED, 0x23, 0x54, 0x01, 0xFA)  # R2 = bit 7 of raw low byte
    r.emit(0xED, 0x54, 0x7F, 0xFF)  # low seven bits
    r.emit(0xEC, 0x23, 0x4A, 0xFE)  # (raw high << 1) | bit 7
    r.label("cache_and_mix")
    # Cache both MIDI data bytes by member channel.
    r.emit(0x90, 0x0F, 0x00)
    _channel_dpl(r)
    r.emit(0xEF, 0xF0)
    r.emit(0x90, 0x0F, 0x30)
    _channel_dpl(r, 0x30)
    r.emit(0xEE, 0xF0)
    r.emit(0x90, XDATA_BEND_FLAGS >> 8, XDATA_BEND_FLAGS & 255, 0xE0,
           0x54, COMBINE_BENDS_BIT)
    r.rel(0x60, "send")
    r.emit(0x90, XDATA_PAD_ACTIVE >> 8, XDATA_PAD_ACTIVE & 255, 0xE0)
    r.rel(0x60, "send")
    # Add the seven-bit pad displacement to the MSB, retaining tilt's LSB.
    r.emit(0xC0, 0x07)
    r.emit(0x90, 0x0F, 0x30)
    _channel_dpl(r, 0x30)
    r.emit(0xE0, 0xFF)
    r.emit(0x90, XDATA_PAD_OFFSET >> 8, XDATA_PAD_OFFSET & 255, 0xE0, 0xFE)
    r.emit(0x12, COMBINE >> 8, COMBINE & 255, 0xEF, 0xFE, 0xD0, 0x07)
    r.label("send")
    r.emit(0x02, 0x7F, 0xFF)  # stock status/data formatter and MIDI sender
    return r.finish()


def build_image() -> tuple[bytes, bytes]:
    stock_bytes = STOCK_SYX.read_bytes()
    if hashlib.sha256(stock_bytes).hexdigest() != STOCK_SYX_SHA256:
        raise ValueError("stock SysEx hash differs from the verified 1.2.2 file")
    records, _ = extract(STOCK_SYX)
    if pack(records) != stock_bytes:
        raise ValueError("stock SysEx does not repack byte for byte")
    stock_image, _, _ = build_stock_image(records)
    if hashlib.sha256(stock_image).hexdigest() != STOCK_IMAGE_SHA256:
        raise ValueError("stock image hash differs from the verified 1.2.2 image")

    patched = bytearray(stock_image)
    routines = {NOTE_ON: build_note_on(), NOTE_OFF: build_note_off(),
                BEND: build_bend(), PAD: build_pad(),
                PAD_SCALE: build_pad_scale(), COMBINE: build_combine(),
                SCALE14: build_scale14(), HIRES_BEND: build_hires_bend()}
    if XDATA_TILT_BASELINE != 0x0F10:
        raise ValueError("unexpected tilt baseline/state address")
    for address, code in routines.items():
        if any(byte != 0xFF for byte in patched[address:address + len(code)]):
            raise ValueError(f"code region at 0x{address:04X} is occupied")
        patched[address:address + len(code)] = code
    for address, expected in HOOKS.items():
        if bytes(patched[address:address + 3]) != expected:
            raise ValueError(f"stock hook at 0x{address:04X} differs")
    for address, target in ((0x7FF3, BEND), (0x7F10, NOTE_ON),
                            (0x7FEF, NOTE_OFF), (0x7EA6, PAD)):
        patched[address:address + 3] = bytes((0x02 if address in (0x7FF3, 0x7EA6) else 0x12,
                                              target >> 8, target & 0xFF))
    packed = pack(records_from_image(records, patched))
    return bytes(patched), packed


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
