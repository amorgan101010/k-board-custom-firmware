#!/usr/bin/env python3
"""Build the v12 on-board velocity, pressure, and tilt sensitivity menus.

Hold a settings button for 1 second to enter. While in a menu, a short press
of any settings button exits; holding another settings button for 1 second
switches pages. On exit, slot 0 is saved through the stock flash routines.
This builder never opens a MIDI port.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.build_relative_tilt_patch import ROOT
from firmware_tools.extract_kmi_firmware import extract
from firmware_tools.repack_kmi_firmware import pack, records_from_image

MODE = 0x0F45                 # 0 velocity, 1 pressure, 2 tilt
PENDING_MODE = 0x0F46
SESSION_BITS = 0x0F47
SAVE_INDEX_HI, SAVE_INDEX_LO = 0x0F50, 0x0F51
SAVE_ROUTINE = 0xA900
DISPATCH = 0x8B60
KEYON_ROUTINE = 0x8BA0
PAGE_BUTTON_BITS = 0x9800
VELOCITY_BODY, PRESSURE_BODY, TILT_BODY = 0xA000, 0xA300, 0xA600
TILT_TABLE = 0x8B40
PRESSURE_GAIN = 0x0364
TILT_SENSITIVITY = 0x034F
PRESET_IMAGE = 0x0347
PRESET_LENGTH = 470
PRESET_ERASE = 0x7A5A
PRESET_WRITE = 0x77DB
MENU_HOLD_MS = 1000
V12_SLIDER_BASE_SHA256 = "fbad2a98d5325ff5903d0ff96bcf54a9bde7a453a5782c3875f50be389f2d140"


def build_tilt_table() -> bytes:
    return bytes(round(i * 70 / 14) for i in range(15))


def _store(image: bytearray, address: int, code: bytes) -> None:
    if address < 0 or address + len(code) > len(image):
        raise ValueError(f"routine 0x{address:04X} exceeds the image")
    image[address:address + len(code)] = code


def build_dispatch() -> bytes:
    r = velocity.Asm(DISPATCH)
    r.mov_dptr(velocity.XRAM_STATE)
    r.movx_load()
    r.rel(0x60, "by_channel")
    r.cjne_a(3, "not_active")
    r.ljmp("by_channel")
    r.label("not_active")
    r.cjne_a(5, "current_page")
    r.mov_dptr(PENDING_MODE)
    r.movx_load()
    r.ljmp("by_mode")
    r.label("current_page")
    r.mov_dptr(MODE)
    r.movx_load()
    r.label("by_mode")
    r.cjne_a(0, "pressure_active")
    r.ljmp_abs(VELOCITY_BODY)
    r.label("pressure_active")
    r.cjne_a(1, "tilt_active")
    r.ljmp_abs(PRESSURE_BODY)
    r.label("tilt_active")
    r.ljmp_abs(TILT_BODY)
    r.label("by_channel")
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(velocity.VELOCITY_CHANNEL, "pressure")
    r.ljmp_abs(VELOCITY_BODY)
    r.label("pressure")
    r.cjne_a(1, "tilt")
    r.ljmp_abs(PRESSURE_BODY)
    r.label("tilt")
    r.cjne_a(0, "done")
    r.ljmp_abs(TILT_BODY)
    r.label("done")
    r.ret()
    return r.finish()


def build_page_button_bits() -> bytes:
    """Make the stock button LEDs show only the page currently being edited."""
    r = velocity.Asm(PAGE_BUTTON_BITS)
    r.mov_dptr(MODE)
    r.movx_load()
    r.cjne_a(0, "pressure")
    r.mov_a(0x04)
    r.rel(0x80, "apply")
    r.label("pressure")
    r.cjne_a(1, "tilt")
    r.mov_a(0x02)
    r.rel(0x80, "apply")
    r.label("tilt")
    r.mov_a(0x01)
    r.label("apply")
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_load()
    r.anl(0xF8)
    r.emit(0x4D)                    # ORL A,R5
    r.movx_store()
    r.ret()
    return r.finish()


def _emit_restore_session_bits(r: velocity.Asm) -> None:
    r.mov_dptr(SESSION_BITS)
    r.movx_load()
    r.anl(0x07)
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_load()
    r.anl(0xF8)
    r.emit(0x4D)                    # ORL A,R5
    r.movx_store()
    r.lcall(velocity.STOCK_LOG_STATE)


def _emit_nearest_step(r: velocity.Asm, *, prefix: str, gain: int, table: int,
                       byte_setting: bool, invert_max: int | None) -> None:
    start, scan = f"{prefix}_start", f"{prefix}_scan"
    r.mov_r(1, velocity.GAIN_STEPS - 1)
    r.mov_dptr(gain)
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
        r.mov_r_a(4)
        r.inc_dptr()
        r.movx_load()
        r.mov_r_a(5)
        r.mov_a_r(4)
        r.rel(0x70, start)
        r.mov_a_r(5)
    r.add(6)
    r.rel(0x40, start)
    r.mov_r_a(5)
    r.mov_r(2, 0)
    r.mov_r(1, 0)
    r.label(scan)
    r.mov_dptr(table)
    r.mov_a_r(2)
    r.movc()
    r.mov_r_a(3)
    r.mov_a_r(5)
    r.clr_c()
    r.subb_r(3)
    r.rel(0x40, start)
    r.mov_a_r(2)
    r.mov_r_a(1)
    r.inc_r(2)
    r.cjne_r(2, velocity.GAIN_STEPS, scan)
    r.label(start)
    r.mov_dptr(velocity.XRAM_STEP)
    r.mov_a_r(1)
    r.movx_store()


def build_read_body(address: int, channel: int, button_mask: int, mode: int,
                    gain: int, table: int, byte_setting: bool,
                    invert_max: int | None) -> bytes:
    """One button's entry, short-exit, and long-press page-switch logic."""
    r = velocity.Asm(address)
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(channel, "quit")
    r.rel(0x80, "body")
    r.label("quit")
    r.ret()
    r.label("body")
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.mov_r_a(7)
    r.mov_dptr(velocity.XRAM_STATE)
    r.movx_load()
    r.rel(0x60, "idle")
    r.cjne_a(1, "not_timing")
    r.ljmp("timing")
    r.label("not_timing")
    r.cjne_a(2, "not_entered")
    r.ljmp("entered")
    r.label("not_entered")
    r.cjne_a(3, "not_active")
    r.ljmp("active")
    r.label("not_active")
    r.cjne_a(5, "not_transition")
    r.ljmp("transition")
    r.label("not_transition")
    r.ljmp("out")

    # Idle: remember the original button states and start the one-second hold.
    r.label("idle")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(velocity.PRESS)
    r.jc_far("out")
    r.lcall(velocity.TICK)
    r.mov_dptr(velocity.XRAM_T0)
    r.mov_a_r(6)
    r.movx_store()
    r.inc_dptr()
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_load()
    r.anl(0x07)
    r.mov_dptr(SESSION_BITS)
    r.movx_store()
    r.mov_dptr(MODE)
    r.mov_a(mode)
    r.movx_store()
    r.store_state(1)
    r.ljmp("out")

    # First entry: restore any stock toggle, initialize the slider, and show page.
    r.label("timing")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(velocity.HELD_MIN)
    r.rel(0x50, "held")
    r.store_state(0)
    r.ljmp("out")
    r.label("held")
    r.lcall(velocity.TICK)
    r.mov_dptr(velocity.XRAM_T0)
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
    r.mov_r_a(6)
    r.mov_a_r(7)
    r.clr_c()
    r.subb(MENU_HOLD_MS & 0xFF)
    r.mov_a_r(6)
    r.subb(MENU_HOLD_MS >> 8)
    r.jc_far("out")
    _emit_restore_session_bits(r)
    _emit_nearest_step(r, prefix="entry", gain=gain, table=table,
                       byte_setting=byte_setting, invert_max=invert_max)
    r.store_state(2)
    r.lcall(PAGE_BUTTON_BITS)
    r.ljmp("draw")

    # Entered: wait until the held entry button is released.
    r.label("entered")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(velocity.HELD_MIN)
    r.jnc_far("draw")
    r.store_state(3)
    r.lcall(PAGE_BUTTON_BITS)
    r.ljmp("draw")

    # Active: any settings button starts a candidate exit/switch hold.
    r.label("active")
    r.lcall(PAGE_BUTTON_BITS)
    r.mov_a_r(7)
    r.clr_c()
    r.subb(velocity.PRESS)
    r.jc_far("draw")
    r.lcall(velocity.TICK)
    r.mov_dptr(velocity.XRAM_T0)
    r.mov_a_r(6)
    r.movx_store()
    r.inc_dptr()
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(PENDING_MODE)
    r.mov_a(mode)
    r.movx_store()
    r.store_state(5)
    r.ljmp("mask")

    # Transition: a short press exits; a one-second press on another button switches.
    r.label("transition")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(velocity.HELD_MIN)
    r.jnc_far("transition_held")
    _emit_restore_session_bits(r)
    r.mov_r(0, 1)
    r.lcall(velocity.DRAW)
    r.store_state(0)
    r.lcall(SAVE_ROUTINE)
    r.ljmp("out")

    r.label("transition_held")
    r.lcall(velocity.TICK)
    r.mov_dptr(velocity.XRAM_T0)
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
    r.mov_r_a(6)
    r.mov_a_r(7)
    r.clr_c()
    r.subb(MENU_HOLD_MS & 0xFF)
    r.mov_a_r(6)
    r.subb(MENU_HOLD_MS >> 8)
    r.jc_far("transition_wait")
    r.mov_dptr(MODE)
    r.movx_load()
    r.cjne_a(mode, "switch_page")
    r.ljmp("transition_wait")  # holding the current page's button still exits on release
    r.label("switch_page")
    _emit_restore_session_bits(r)
    r.mov_dptr(MODE)
    r.mov_a(mode)
    r.movx_store()
    _emit_nearest_step(r, prefix="switch", gain=gain, table=table,
                       byte_setting=byte_setting, invert_max=invert_max)
    r.store_state(2)
    r.lcall(PAGE_BUTTON_BITS)
    r.ljmp("draw")

    r.label("transition_wait")
    r.lcall(PAGE_BUTTON_BITS)
    r.ljmp("mask")
    r.label("draw")
    r.mov_r(0, 0)
    r.lcall(velocity.DRAW)
    r.label("mask")
    r.mov_dptr(velocity.XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.ret()
    r.label("out")
    r.ret()
    return r.finish()


def build_keyon() -> bytes:
    r = velocity.Asm(velocity.KEYON_HOOK)
    r.mov_dptr(velocity.XRAM_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(2)
    r.rel(0x40, "stock")
    r.clr_c()
    r.subb(2)
    r.rel(0x50, "stock")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(velocity.KEY_COUNT)
    r.rel(0x50, "stock")
    r.mov_a_r(7)
    r.mov_dptr(velocity.WHITE_TABLE)
    r.movc()
    r.cjne_a(0xFF, "white")
    r.rel(0x80, "stock")
    r.label("white")
    r.mov_r_a(6)                    # R6 = white-key step
    r.mov_dptr(velocity.XRAM_STEP)
    r.movx_store()
    r.mov_dptr(MODE)
    r.movx_load()
    r.cjne_a(1, "not_pressure")
    r.mov_dptr(velocity.GAIN_TABLE)
    r.ljmp_abs(0x9300)
    r.label("not_pressure")
    r.cjne_a(2, "velocity")
    r.mov_dptr(TILT_TABLE)
    r.ljmp_abs(0x9320)
    r.label("velocity")
    r.mov_dptr(velocity.GAIN_TABLE)
    r.ljmp_abs(0x9340)
    r.label("stock")
    r.mov_dptr(0x0889)
    r.ret()
    return r.finish()


def build_send_hook() -> bytes:
    """Mute MIDI while entering, editing, or changing pages (states 2, 3, 5)."""
    r = velocity.Asm(velocity.SEND_HOOK)
    r.mov_dptr(velocity.XRAM_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(2)
    r.rel(0x40, "pass")
    r.cjne_a(4, "mute")
    r.ljmp("pass")
    r.label("mute")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(0x90)
    r.rel(0x40, "pass")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(0xF0)
    r.rel(0x50, "pass")
    r.ret()
    r.label("pass")
    r.mov_dptr(0x08A5)
    r.ljmp_abs(velocity.STOCK_SEND_BODY)
    return r.finish()


def build_keyon_helpers() -> dict[int, bytes]:
    # The three tails share the white-key step in R6 and the selected table
    # in DPTR. Each tail replays the stock instruction at the hook site.
    helpers = {}
    for address, target, inverse in (
        (0x9300, PRESSURE_GAIN, None),
        (0x9320, TILT_SENSITIVITY, 70),
        (0x9340, velocity.XRAM_VELOCITY_GAIN, None),
    ):
        r = velocity.Asm(address)
        r.mov_a_r(6)
        r.movc()
        if inverse is not None:
            r.mov_r_a(5)
            r.mov_a(inverse)
            r.clr_c()
            r.subb_r(5)
        r.mov_dptr(target + (1 if inverse is None else 0))
        r.movx_store()
        if inverse is None:
            r.mov_dptr(target)
            r.clr_a()
            r.movx_store()
        r.mov_dptr(0x0889)
        r.ret()
        helpers[address] = r.finish()
    return helpers


def build_save_preset() -> bytes:
    """Rewrite slot 0 from the live XRAM preset image after menu exit."""
    r = velocity.Asm(SAVE_ROUTINE)
    r.mov_r(7, 0)                    # this editor/device workflow uses slot 0
    r.lcall(PRESET_ERASE)            # stock erase routine, F000 slot
    r.mov_dptr(SAVE_INDEX_HI)
    r.clr_a()
    r.movx_store()
    r.inc_dptr()
    r.movx_store()
    r.label("byte")
    r.mov_dptr(SAVE_INDEX_HI)
    r.movx_load()
    r.mov_r_a(6)
    r.inc_dptr()
    r.movx_load()
    r.mov_r_a(7)
    # A = XRAM[0x0347 + index]
    r.mov_dptr(PRESET_IMAGE)
    r.mov_a_r(7)
    r.emit(0x25, 0x82, 0xF5, 0x82)  # ADD A,DPL; MOV DPL,A
    r.emit(0xE5, 0x83, 0x3E, 0xF5, 0x83)  # DPH + index high + carry
    r.movx_load()
    r.mov_r_a(5)
    # Stock flash address is bank FF, page F0 plus index high, low index.
    r.mov_a_r(6)
    r.add(0xF0)
    r.mov_r_a(2)
    r.mov_a_r(7)
    r.mov_r_a(1)
    r.mov_r(3, 0xFF)
    r.lcall(PRESET_WRITE)
    r.mov_dptr(SAVE_INDEX_LO)
    r.movx_load()
    r.emit(0x04)                       # INC A
    r.movx_store()
    r.rel(0x70, "check")              # JNZ
    r.mov_dptr(SAVE_INDEX_HI)
    r.movx_load()
    r.emit(0x04, 0xF0)                 # INC A; MOVX @DPTR,A
    r.movx_store()
    r.label("check")
    r.mov_dptr(SAVE_INDEX_HI)
    r.movx_load()
    r.cjne_a(1, "byte")
    r.inc_dptr()
    r.movx_load()
    r.cjne_a(0xD6, "byte")
    r.ret()
    return r.finish()


def build_image(base_image: bytes | None = None) -> tuple[bytes, bytes]:
    # The standalone command accepts the known slider artifact. The unified
    # builder supplies it in memory, so users only need one build command.
    base = base_image
    if base is None:
        base = (ROOT / "firmware_analysis/kboard_1.2.2-relative-tilt-v12-slider.bin").read_bytes()
    if base_image is None and hashlib.sha256(base).hexdigest() != V12_SLIDER_BASE_SHA256:
        raise ValueError("v12 slider base image hash mismatch; refusing to layer menus on an unknown image")
    patched = bytearray(base)
    bodies = (
        (VELOCITY_BODY, velocity.VELOCITY_CHANNEL, 0x04, 0,
         velocity.XRAM_VELOCITY_GAIN, velocity.GAIN_TABLE, False, None),
        (PRESSURE_BODY, 1, 0x02, 1, PRESSURE_GAIN, velocity.GAIN_TABLE, False, None),
        (TILT_BODY, 0, 0x01, 2, TILT_SENSITIVITY, TILT_TABLE, True, 70),
    )
    emitted = []
    for address, channel, mask, mode, gain, table, byte_value, inverse in bodies:
        body = build_read_body(address, channel, mask, mode, gain, table,
                               byte_value, inverse)
        emitted.append((address, body))
    emitted.extend((
        (DISPATCH, build_dispatch()),
        (KEYON_ROUTINE, build_keyon()),
        (SAVE_ROUTINE, build_save_preset()),
        (TILT_TABLE, build_tilt_table()),
        (PAGE_BUTTON_BITS, build_page_button_bits()),
        *build_keyon_helpers().items(),
    ))
    # These are all new code/data areas in erased flash. Fail closed if a base
    # image or a future routine-size change would overwrite firmware bytes.
    ordered = sorted(emitted)
    for (start, code), (next_start, _) in zip(ordered, ordered[1:]):
        if start + len(code) > next_start:
            raise ValueError(f"v12 regions overlap near 0x{next_start:04X}")
    for address, code in emitted:
        if any(byte != 0xFF for byte in patched[address:address + len(code)]):
            raise ValueError(f"v12 code/data region at 0x{address:04X} is occupied")
        _store(patched, address, code)
    _store(patched, velocity.READ_BODY, bytes((0x02, DISPATCH >> 8, DISPATCH & 255)))
    send_hook = build_send_hook()
    if len(send_hook) > velocity.KEYON_HOOK - velocity.SEND_HOOK:
        raise ValueError("v12 MIDI hook overlaps the existing key-on helper")
    _store(patched, velocity.SEND_HOOK, send_hook)
    # The extended three-menu dispatcher no longer fits in the original slider
    # key-on helper's 0x8750–0x878F slot (the millisecond tick starts at 0x8790).
    # Leave the slider's unused helper there and redirect the hook to free flash at 8BA0.
    patched[0x4C4F:0x4C52] = bytes((0x02, KEYON_ROUTINE >> 8, KEYON_ROUTINE & 255))
    records, _ = extract(velocity.STOCK_SYX)
    return bytes(patched), pack(records_from_image(records, patched))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-prefix", type=Path,
                        default=ROOT / "firmware_analysis/kboard_1.2.2-relative-tilt-v12")
    args = parser.parse_args()
    image, sysex = build_image()
    image_path, sysex_path = Path(f"{args.output_prefix}.bin"), Path(f"{args.output_prefix}.syx")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image)
    sysex_path.write_bytes(sysex)
    print(f"image: {image_path} ({len(image)} bytes, sha256 {hashlib.sha256(image).hexdigest()})")
    print(f"SysEx: {sysex_path} ({len(sysex)} bytes, sha256 {hashlib.sha256(sysex).hexdigest()})")


if __name__ == "__main__":
    main()
