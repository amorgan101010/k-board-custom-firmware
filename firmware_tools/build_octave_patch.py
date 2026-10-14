#!/usr/bin/env python3
"""Make normal octave-button changes affect only future notes.

The stock buttons calculate the next value for XRAM 0x005F, then call 0x600E
to store it and refresh sounding notes. The custom path stores the value
directly and skips that refresh unless CV1 Max bit 3 requests stock behavior.
The stock per-key note table at 0x02BB + key then continues to supply the
original pitch to the ordinary note-off path.
"""

from __future__ import annotations

from firmware_tools import build_velocity_slider_patch as velocity


MODE_BYTE = 0x04EF
MODE_MARKER = 0x04F8
RETRIGGER_BIT = 0x08
HOOKS = (0x5FEC, 0x6001)
STOCK_CALL = bytes.fromhex("12 60 0e")
# The extra pressure-glide octave comparison occupies the former 0xEDB0 slot.
# This 67-byte erased run follows the pressure rearm helper and is reserved by
# the combined builder's destination checks.
OCTAVE_DISPATCH = 0xB00D


def build_dispatch() -> bytes:
    r = velocity.Asm(OCTAVE_DISPATCH)
    r.emit(0xC0, 0xE0, 0xC0, 0xD0, 0xC0, 0x82, 0xC0, 0x83)
    r.mov_dptr(MODE_BYTE)
    r.movx_load()
    r.anl(0xF0)
    r.cjne_a(0x70, "skip")
    r.mov_dptr(MODE_BYTE)
    r.movx_load()
    r.anl(RETRIGGER_BIT)
    r.rel(0x60, "skip")
    # CV2 Max <= 100 distinguishes the current custom sidecar from an
    # untouched stock preset, whose factory value is 127 (0x7F).
    r.mov_dptr(MODE_MARKER)
    r.movx_load()
    r.clr_c()
    r.subb(101)
    r.rel(0x50, "skip")
    r.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xD0, 0xD0, 0xE0)
    r.lcall(0x600E)
    r.ret()
    r.label("skip")
    r.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xD0, 0xD0, 0xE0)
    # At both stock call sites ACC is already the clamped next octave. Stock
    # 0x600E refreshes currently sounding notes and also commits this value.
    # Commit it directly when skipping the refresh so future note-ons use it.
    r.mov_dptr(0x005F)
    r.movx_store()
    # Preserve the stock refresh-in-progress flag lifecycle without entering
    # the routine that sends note/expression messages and reallocates voices.
    r.mov_dptr(0x0259)
    r.mov_a(1)
    r.movx_store()
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def apply(image: bytes) -> bytes:
    result = bytearray(image)
    for address in HOOKS:
        if result[address:address + 3] != STOCK_CALL:
            raise ValueError(f"unexpected stock octave refresh call at 0x{address:04X}")
        result[address:address + 3] = bytes((0x12, OCTAVE_DISPATCH >> 8,
                                             OCTAVE_DISPATCH & 255))
    code = build_dispatch()
    if any(value != 0xFF for value in result[OCTAVE_DISPATCH:OCTAVE_DISPATCH + len(code)]):
        raise ValueError("octave dispatcher destination is not erased")
    result[OCTAVE_DISPATCH:OCTAVE_DISPATCH + len(code)] = code
    return bytes(result)
