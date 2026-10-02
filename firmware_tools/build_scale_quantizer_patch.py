#!/usr/bin/env python3
"""Layer the session-only scale selector and note quantizer onto custom firmware."""

from __future__ import annotations

import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools import build_cyclone_game_patch as cyclone
from firmware_tools import build_sensor_config_patch as menus
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.build_relative_tilt_patch import STOCK_SYX
from firmware_tools.extract_kmi_firmware import extract
from firmware_tools.repack_kmi_firmware import pack, records_from_image


# Code follows Cyclone's last routine at 0xC100. Scale patterns and quantizer
# tables occupy erased flash below the stock preset area.
DISPATCH = 0xC200
INPUT = 0xC280
CHORD = 0xC400
ACTIVE_INPUT = 0xC600
KEYON = 0xC800
MARK_KEY = 0xC8C0
SEND = 0xC900
QUANTIZE = 0xCA00
DRAW = 0xCC00
LED_GATE = 0xCD00
INIT = 0xCE00
ALL_OFF = 0xCE80
RESTORE_BUTTONS = 0xCEB0
CLEAR_KEYS = 0xCEC8

PACKED_RECORDS = 0xCF00
TRANSPOSE_MOD = 0xD200
KEYOFF = 0xD240

SCALE_STATE = velocity.XRAM_STATE  # 0 idle, 6 chord release, 7 editing, 8 exit release
BUTTONS = 0x0F65            # entry chord: Tilt bit 0, Pressure bit 1, Velocity bit 2
PENDING = 0x0F66            # entry chord timing / forced-redraw flag
START_HI = 0x0F67
START_LO = 0x0F68
SCALE_ID = 0x0F69           # white-key index 0..14
TRANSPOSE = 0x0F6A          # biased semitone transpose 0..24; 12 is neutral
KEY_HELD = 0x0F6B           # octave down/up edge state bits 0/1
RELEASE_MASK = 0x0F6C       # wait for initial Tilt + Pressure release
ROOT_PHASE = 0x0F6D
BLINK_TICK = 0x0F6E
DRAW_FORCE = PENDING
SAVED_BITS = 0x0F6F
SAVED_VALID = 0x0F70
INIT_DONE = 0x0F73
CONSUMED_KEY_MARKS = 0x0F74  # 25 key marks packed into a four-byte bitmap
CONSUMED_KEY_MARK_BYTES = 4

PRESS = 0x79
HELD_MIN = 0x40
HOLD_MS = 1000
KEY_COUNT = 25
SCALE_COUNT = 15
WHITE_PITCH_CLASSES = (0, 2, 4, 5, 7, 9, 11)

SCALE_MASKS = (
    0xFFF,  # Chromatic
    0xAB5,  # Ionian / major
    0x6AD,  # Dorian
    0x5AB,  # Phrygian
    0xAD5,  # Lydian
    0x6B5,  # Mixolydian
    0x5AD,  # Aeolian / natural minor
    0x56B,  # Locrian
    0x295,  # Major pentatonic
    0x4A9,  # Minor pentatonic
    0x4E9,  # Blues
    0x9AD,  # Harmonic minor
    0x555,  # Whole tone
    0x18D,  # Hirajoshi
    0x5B3,  # Phrygian dominant
)


def _jcc_far(r: velocity.Asm, opcode: int, name: str) -> None:
    # Emit a conditional branch over a long absolute jump.
    r.emit(opcode, 3)
    r.ljmp(name)


def scale_membership(scale: int, pitch_class: int) -> bool:
    return bool(SCALE_MASKS[scale] & (1 << pitch_class))


def packed_scale_records() -> tuple[bytes, bytes]:
    """Pack one LED state and one signed quantizer delta per (scale, pitch class)."""
    records = bytearray()
    for scale in range(SCALE_COUNT):
        degrees = tuple(pc for pc in range(12) if scale_membership(scale, pc))
        # Seven-note scales use the seven white keys as consecutive degrees.
        # Black keys and scales of other sizes retain nearest-pitch snapping.
        white_notes = dict(zip(WHITE_PITCH_CLASSES, degrees)) if len(degrees) == 7 else {}
        for pitch_class in range(12):
            led_state = 2 if pitch_class == 0 else int(
                scale_membership(scale, pitch_class))
            delta = min(
                range(-6, 7),
                key=lambda d: (0 if scale_membership(scale, (pitch_class + d) % 12)
                               else 1, abs(d), 0 if d <= 0 else 1),
            )
            if pitch_class in white_notes:
                delta = white_notes[pitch_class] - pitch_class
            records.append((led_state << 4) | (delta & 0x0F))
    transpose_mod = bytes(index % 12 for index in range(25))
    return bytes(records), transpose_mod


def build_init() -> bytes:
    r = velocity.Asm(INIT)
    r.mov_dptr(INIT_DONE)
    r.movx_load()
    r.rel(0x70, "done")
    r.mov_dptr(TRANSPOSE)
    r.mov_a(12)
    r.movx_store()
    r.mov_dptr(SCALE_ID)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(INIT_DONE)
    r.mov_a(1)
    r.movx_store()
    r.label("done")
    r.ret()
    return r.finish()


def build_all_off() -> bytes:
    r = velocity.Asm(ALL_OFF)
    r.mov_r(4, 0)
    r.label("channel")
    r.mov_a_r(4)
    # Save the counter before using A to form the status byte.
    r.emit(0xC0, 0xE0)  # PUSH ACC
    r.orl(0xB0)
    r.mov_r_a(7)
    r.mov_r(5, 123)
    r.mov_r(3, 0)
    r.lcall(0x7C67)
    r.emit(0xD0, 0xE0)  # POP ACC
    r.mov_r_a(4)
    r.inc_r(4)
    r.cjne_r(4, 16, "channel")
    # CC 123 silences the synth, but does not pass through our per-note
    # note-off hook. Clear the relative-tilt baseline/state bytes too.
    r.mov_r(4, 0)
    r.mov_dptr(0x0F10)
    r.label("clear_tilt_state")
    r.clr_a()
    r.movx_store()
    r.inc_dptr()
    r.inc_r(4)
    r.cjne_r(4, 16, "clear_tilt_state")
    r.ret()
    return r.finish()


def build_restore_buttons() -> bytes:
    r = velocity.Asm(RESTORE_BUTTONS)
    r.mov_dptr(SAVED_VALID)
    r.movx_load()
    r.rel(0x60, "done")
    r.mov_dptr(SAVED_BITS)
    r.movx_load()
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_store()
    r.lcall(velocity.STOCK_LOG_STATE)
    r.mov_dptr(SAVED_VALID)
    r.clr_a()
    r.movx_store()
    r.label("done")
    r.ret()
    return r.finish()


def build_clear_keys() -> bytes:
    r = velocity.Asm(CLEAR_KEYS)
    r.mov_dptr(cyclone.DRAW_FLAG)
    r.mov_a(1)
    r.movx_store()
    r.mov_r(2, 0)
    r.label("clear")
    r.mov_a_r(2)
    r.mov_r_a(7)
    r.mov_r(5, 0)
    r.lcall(velocity.STOCK_SET_LED)
    r.inc_r(2)
    r.cjne_r(2, KEY_COUNT, "clear")
    r.mov_dptr(cyclone.DRAW_FLAG)
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def build_dispatch() -> bytes:
    """Run scale control before Cyclone/menu dispatch, preserving game priority."""
    r = velocity.Asm(DISPATCH)
    r.lcall(INIT)
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(6)
    r.rel(0x40, "idle")
    r.lcall(ACTIVE_INPUT)
    r.ret()
    r.label("idle")
    r.mov_dptr(cyclone.GAME_STATE)
    r.movx_load()
    r.rel(0x70, "game")
    r.emit(0xC0, 0x07)  # preserve the sensor reading in R7
    r.lcall(CHORD)
    r.emit(0xD0, 0x07)
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    # This byte is shared with the sensitivity menus: states 1-5 still need
    # their next sensor sample. Only states 6-8 belong to the scale selector.
    r.clr_c()
    r.subb(6)
    r.rel(0x50, "done")
    r.mov_dptr(PENDING)
    r.movx_load()
    r.rel(0x70, "candidate")
    r.ljmp_abs(cyclone.GAME_DISPATCH)
    r.label("candidate")
    # Let Cyclone's full three-button chord win whenever it is present.
    r.mov_dptr(cyclone.GAME_STATE)
    r.movx_load()
    r.rel(0x70, "game")
    r.lcall(cyclone.GAME_INPUT)
    r.mov_dptr(cyclone.GAME_STATE)
    r.movx_load()
    r.rel(0x70, "game")
    r.emit(0xC0, 0x07)
    r.lcall(CHORD)
    r.emit(0xD0, 0x07)
    r.ret()
    r.label("candidate_check")
    r.mov_dptr(PENDING)
    r.clr_a()
    r.movx_store()
    r.ret()
    r.label("game")
    r.mov_dptr(PENDING)
    r.clr_a()
    r.movx_store()
    r.ljmp_abs(cyclone.GAME_DISPATCH)
    r.label("done")
    r.ret()
    return r.finish()


def build_chord() -> bytes:
    """Track Tilt+Pressure hold; bypass the sensitivity menu during the chord."""
    r = velocity.Asm(CHORD)
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.mov_r_a(4)
    r.cjne_a(0, "pressure")
    r.mov_a(1)
    r.rel(0x80, "button")
    r.label("pressure")
    r.cjne_a(1, "velocity")
    r.mov_a(2)
    r.rel(0x80, "button")
    r.label("velocity")
    r.cjne_a(4, "check")
    r.mov_a(4)
    r.label("button")
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_dptr(BUTTONS)
    r.movx_load()
    r.mov_r_a(7)
    r.mov_a_r(6)
    r.clr_c()
    r.subb(PRESS)
    r.rel(0x50, "set")
    r.mov_a_r(6)
    r.clr_c()
    r.subb(HELD_MIN)
    r.rel(0x50, "check")
    r.mov_a_r(5)
    r.xrl(0xFF)
    r.mov_r_a(3)
    r.mov_a_r(7)
    r.emit(0x5B)                  # ANL A,R3
    r.mov_dptr(BUTTONS)
    r.movx_store()
    r.ljmp("check")
    r.label("set")
    # Snapshot Tilt/Pressure enable bits when the chord forms.
    r.mov_a_r(5)
    r.cjne_a(4, "save_pair_state")
    r.ljmp("set_bit")
    r.label("save_pair_state")
    r.mov_a_r(7)
    r.anl(0x03)
    r.rel(0x70, "set_bit")
    r.mov_dptr(velocity.XRAM_BUTTON_BITS)
    r.movx_load()
    r.mov_dptr(SAVED_BITS)
    r.movx_store()
    r.mov_dptr(SAVED_VALID)
    r.mov_a(1)
    r.movx_store()
    r.label("set_bit")
    r.mov_a_r(7)
    r.emit(0x4D)                  # ORL A,R5
    r.mov_dptr(BUTTONS)
    r.movx_store()
    r.label("check")
    # Only start the two-button mode when no sensitivity/slider mode owns input.
    r.mov_dptr(velocity.XRAM_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(3)
    _jcc_far(r, 0x40, "clear_pending")
    r.mov_dptr(BUTTONS)
    r.movx_load()
    r.anl(0x03)
    r.cjne_a(0x03, "not_pair")
    r.mov_dptr(PENDING)
    r.movx_load()
    r.rel(0x70, "elapsed")
    r.lcall(velocity.TICK)
    r.mov_dptr(START_HI)
    r.mov_a_r(6)
    r.movx_store()
    r.inc_dptr()
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(PENDING)
    r.mov_a(1)
    r.movx_store()
    # Cancel an individual menu hold that began on the first chord button.
    r.mov_dptr(velocity.XRAM_STATE)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(SAVED_VALID)
    r.mov_a(1)
    r.movx_store()
    r.ret()
    r.label("elapsed")
    # Velocity held means the full game chord may still be completing.
    r.mov_dptr(BUTTONS)
    r.movx_load()
    r.anl(0x04)
    r.rel(0x70, "wait")
    r.lcall(velocity.TICK)
    r.mov_dptr(START_LO)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(7)
    r.clr_c()
    r.subb_r(5)
    r.mov_r_a(5)
    r.mov_dptr(START_HI)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_a_r(6)
    r.subb_r(4)
    r.rel(0x40, "wait")
    r.cjne_a(3, "compare_hi")
    r.mov_a_r(5)
    r.clr_c()
    r.subb(0xE8)
    r.rel(0x40, "wait")
    r.label("compare_hi")
    r.rel(0x40, "wait")
    r.label("enter")
    r.mov_dptr(SCALE_STATE)
    r.mov_a(6)
    r.movx_store()
    r.mov_dptr(RELEASE_MASK)
    r.mov_a(3)
    r.movx_store()
    r.mov_dptr(ROOT_PHASE)
    r.mov_a(1)
    r.movx_store()
    r.mov_a_direct(0x4C)
    r.mov_dptr(BLINK_TICK)
    r.movx_store()
    r.mov_dptr(PENDING)
    r.clr_a()
    r.movx_store()
    # DRAW_FORCE aliases PENDING while the chord detector is active.
    r.mov_dptr(DRAW_FORCE)
    r.mov_a(1)
    r.movx_store()
    r.lcall(RESTORE_BUTTONS)
    # Keep the original toggle snapshot until selector exit. Tilt and
    # Pressure also act as their own on/off buttons when pressed to exit.
    r.mov_dptr(SAVED_VALID)
    r.mov_a(1)
    r.movx_store()
    r.lcall(ALL_OFF)
    r.lcall(DRAW)
    r.ret()
    r.label("wait")
    r.ret()
    r.label("not_pair")
    r.mov_dptr(PENDING)
    r.movx_load()
    r.rel(0x60, "clear_pending")
    r.lcall(RESTORE_BUTTONS)
    r.mov_dptr(PENDING)
    r.clr_a()
    r.movx_store()
    r.ret()
    r.label("clear_pending")
    r.mov_dptr(PENDING)
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def build_active_input() -> bytes:
    """Release the entry chord, handle arrow transpose, and exit on Tilt/Pressure."""
    r = velocity.Asm(ACTIVE_INPUT)
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.cjne_a(6, "check_exit_wait")
    # State 1: wait for the entry Tilt+Pressure chord to be released.
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "initial_pressure")
    r.mov_a(1)
    r.rel(0x80, "initial_check")
    r.label("initial_pressure")
    r.cjne_a(1, "initial_not_pressure")
    r.mov_a(2)
    r.ljmp("initial_check")
    r.label("initial_not_pressure")
    r.ljmp("done")
    r.label("initial_check")
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.clr_c()
    r.subb(HELD_MIN)
    _jcc_far(r, 0x40, "done")
    r.mov_dptr(RELEASE_MASK)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_a_r(5)
    r.xrl(0xFF)
    r.mov_r_a(7)
    r.mov_a_r(6)
    r.emit(0x5F)                  # ANL A,R7
    r.mov_dptr(RELEASE_MASK)
    r.movx_store()
    _jcc_far(r, 0x60, "done")
    r.mov_dptr(SCALE_STATE)
    r.mov_a(7)
    r.movx_store()
    r.ljmp("done")
    r.label("check_exit_wait")
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.cjne_a(8, "editing")
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "exit_release_pressure")
    r.mov_a(1)
    r.rel(0x80, "exit_release_check")
    r.label("exit_release_pressure")
    r.cjne_a(1, "exit_release_other")
    r.mov_a(2)
    r.ljmp("exit_release_check")
    r.label("exit_release_other")
    r.ljmp("done")
    r.label("exit_release_check")
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.clr_c()
    r.subb(HELD_MIN)
    _jcc_far(r, 0x40, "done")
    r.mov_dptr(RELEASE_MASK)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_a_r(5)
    r.xrl(0xFF)
    r.mov_r_a(7)
    r.mov_a_r(6)
    r.emit(0x5F)
    r.mov_dptr(RELEASE_MASK)
    r.movx_store()
    _jcc_far(r, 0x60, "done")
    r.mov_dptr(SCALE_STATE)
    r.clr_a()
    r.movx_store()
    r.lcall(RESTORE_BUTTONS)
    r.ljmp("done")
    r.label("editing")
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "pressure_exit")
    r.ljmp("exit_check")
    r.label("pressure_exit")
    r.cjne_a(1, "arrow_down")
    r.label("exit_check")
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.clr_c()
    r.subb(PRESS)
    _jcc_far(r, 0x50, "done")
    r.mov_dptr(velocity.XRAM_CHANNEL)
    r.movx_load()
    r.cjne_a(0, "exit_pressure_bit")
    r.mov_a(1)
    r.rel(0x80, "exit_begin")
    r.label("exit_pressure_bit")
    r.mov_a(2)
    r.label("exit_begin")
    r.mov_dptr(RELEASE_MASK)
    r.movx_store()
    r.mov_dptr(SCALE_STATE)
    r.mov_a(8)
    r.movx_store()
    r.lcall(RESTORE_BUTTONS)
    r.lcall(CLEAR_KEYS)
    r.ljmp("done")
    r.label("arrow_down")
    r.cjne_a(6, "arrow_up")
    r.mov_a(1)
    r.rel(0x80, "arrow")
    r.label("arrow_up")
    r.cjne_a(7, "arrow_other")
    r.mov_a(2)
    r.ljmp("arrow")
    r.label("arrow_other")
    r.ljmp("done")
    r.label("arrow")
    r.mov_r_a(5)
    r.mov_dptr(velocity.XRAM_READING)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_a_r(6)
    r.clr_c()
    r.subb(PRESS)
    r.rel(0x40, "arrow_not_pressed")
    r.mov_dptr(KEY_HELD)
    r.movx_load()
    r.mov_r_a(7)
    r.mov_a_r(7)
    r.emit(0x5D)                  # ANL A,R5
    _jcc_far(r, 0x60, "done")
    r.mov_a_r(7)
    r.emit(0x4D)                  # ORL A,R5
    r.mov_dptr(KEY_HELD)
    r.movx_store()
    r.mov_dptr(TRANSPOSE)
    r.movx_load()
    r.mov_r_a(4)
    r.cjne_r(5, 1, "up_bound")
    r.mov_a_r(4)
    _jcc_far(r, 0x70, "done")
    r.emit(0x14)                    # DEC A
    r.ljmp("store_transpose")
    r.label("up_bound")
    r.mov_a_r(4)
    r.clr_c()
    r.subb(24)
    _jcc_far(r, 0x40, "done")
    r.mov_a_r(4)
    r.emit(0x04)                    # INC A
    r.label("store_transpose")
    r.mov_dptr(TRANSPOSE)
    r.movx_store()
    r.mov_dptr(DRAW_FORCE)
    r.mov_a(1)
    r.movx_store()
    r.lcall(DRAW)
    r.ljmp("done")
    r.label("arrow_release")
    r.mov_a_r(5)
    r.xrl(0xFF)
    r.mov_r_a(7)
    r.mov_dptr(KEY_HELD)
    r.movx_load()
    r.emit(0x5F)                  # ANL A,R7
    r.movx_store()
    r.ljmp("done")
    r.label("arrow_not_pressed")
    r.mov_a_r(6)
    r.clr_c()
    r.subb(HELD_MIN)
    _jcc_far(r, 0x40, "done")
    r.mov_a_r(5)
    r.xrl(0xFF)
    r.mov_r_a(7)
    r.mov_dptr(KEY_HELD)
    r.movx_load()
    r.emit(0x5F)
    r.movx_store()
    r.label("done")
    r.mov_dptr(velocity.XRAM_READING)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.cjne_a(7, "done_return")
    r.lcall(DRAW)
    r.label("done_return")
    r.ret()
    return r.finish()


def build_keyon() -> bytes:
    r = velocity.Asm(KEYON)
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.cjne_a(7, "stock")
    r.mov_a_r(7)
    r.clr_c()
    r.subb(KEY_COUNT)
    r.rel(0x50, "consume")
    # Selector keys have no stock note-on allocation; suppress their release.
    r.lcall(MARK_KEY)
    r.mov_a_r(7)
    r.mov_dptr(velocity.WHITE_TABLE)
    r.movc()
    r.cjne_a(0xFF, "select")
    r.rel(0x80, "consume")
    r.label("select")
    r.mov_dptr(SCALE_ID)
    r.movx_store()
    r.mov_dptr(DRAW_FORCE)
    r.mov_a(1)
    r.movx_store()
    # Preserve all caller registers while the LED routine redraws the map.
    for direct in (0xD0, 0xE0, 0x82, 0x83, 0x00, 0x01, 0x02, 0x03, 0x04,
                   0x05, 0x06, 0x07, 0xF0):
        r.emit(0xC0, direct)
    r.lcall(DRAW)
    for direct in (0xF0, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01, 0x00,
                   0x83, 0x82, 0xE0, 0xD0):
        r.emit(0xD0, direct)
    r.label("consume")
    # Selector key presses choose a scale and must not also play notes.
    r.ret()
    r.label("stock")
    # All valid key presses in menu states 2, 3, and 5 return from the menu
    # key-on handler without allocating a voice, including black keys.
    # Mark their releases while keeping white-key value editing intact.
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.cjne_a(2, "check_menu_active")
    r.ljmp("menu")
    r.label("check_menu_active")
    r.cjne_a(3, "check_menu_transition")
    r.ljmp("menu")
    r.label("check_menu_transition")
    r.cjne_a(5, "stock_note")
    r.ljmp("menu")
    r.label("menu")
    r.lcall(MARK_KEY)
    r.ljmp_abs(menus.KEYON_ROUTINE)
    r.label("stock_note")
    # The hook replaces the stock routine's first instruction, MOV DPTR,#0x0889.
    # Replay it, then continue the body at 0x4C52. Returning here skipped all
    # ordinary key/note processing and left the MIDI output broken.
    r.mov_dptr(0x0889)
    r.ljmp_abs(0x4C52)
    return r.finish()


def build_mark_key() -> bytes:
    r = velocity.Asm(MARK_KEY)
    r.mov_a_r(7)
    r.clr_c()
    r.subb(KEY_COUNT)
    r.rel(0x50, "done")

    # Save the scratch registers without assuming which register bank the
    # stock key scanner selected.
    r.mov_a_r(4)
    r.emit(0xC0, 0xE0)
    r.mov_a_r(5)
    r.emit(0xC0, 0xE0)

    # R4 = bit number, DPTR = bitmap byte for key index R7.
    r.mov_a_r(7)
    r.anl(0x07)
    r.mov_r_a(4)
    r.mov_a_r(7)
    r.emit(0x03, 0x03, 0x03)  # RR A x3: key index / 8
    r.anl(0x03)
    r.add(CONSUMED_KEY_MARKS & 255)
    r.emit(0xF5, 0x82, 0x75, 0x83, CONSUMED_KEY_MARKS >> 8)

    # Form 1 << (key index & 7).
    r.mov_r(5, 1)
    r.label("shift_mask")
    r.mov_a_r(4)
    r.rel(0x60, "mask_ready")
    r.emit(0x1C)  # DEC R4
    r.mov_a_r(5)
    r.emit(0x23)  # RL A
    r.mov_r_a(5)
    r.rel(0x80, "shift_mask")
    r.label("mask_ready")
    r.movx_load()
    r.emit(0x4D)  # ORL A,R5
    r.movx_store()
    r.emit(0xD0, 0xE0)  # restore R5
    r.mov_r_a(5)
    r.emit(0xD0, 0xE0)  # restore R4
    r.mov_r_a(4)
    r.label("done")
    r.ret()
    return r.finish()


def build_keyoff() -> bytes:
    r = velocity.Asm(KEYOFF)
    r.emit(0xC0, 0xD0)  # PUSH PSW; stock caller's flags survive the lookup
    r.mov_a_r(7)
    r.clr_c()
    r.subb(KEY_COUNT)
    r.rel(0x50, "stock_no_scratch")

    # Preserve the caller's scratch registers across this release hook.
    r.mov_a_r(4)
    r.emit(0xC0, 0xE0)
    r.mov_a_r(5)
    r.emit(0xC0, 0xE0)

    # Compute the bitmap byte and the one-bit mask for this key.
    r.mov_a_r(7)
    r.anl(0x07)
    r.mov_r_a(4)
    r.mov_a_r(7)
    r.emit(0x03, 0x03, 0x03)
    r.anl(0x03)
    r.add(CONSUMED_KEY_MARKS & 255)
    r.emit(0xF5, 0x82, 0x75, 0x83, CONSUMED_KEY_MARKS >> 8)
    r.mov_r(5, 1)
    r.label("shift_mask")
    r.mov_a_r(4)
    r.rel(0x60, "mask_ready")
    r.emit(0x1C)
    r.mov_a_r(5)
    r.emit(0x23)
    r.mov_r_a(5)
    r.rel(0x80, "shift_mask")
    r.label("mask_ready")

    r.movx_load()
    r.emit(0x5D)  # ANL A,R5
    r.rel(0x60, "restore_stock")

    # Clear only this key's bit; other keys may still be held.
    r.mov_a_r(5)
    r.emit(0xF4)  # CPL A
    r.mov_r_a(5)
    r.movx_load()
    r.emit(0x5D)  # ANL A,R5
    r.movx_store()
    r.label("restore_consumed")
    r.emit(0xD0, 0xE0)
    r.mov_r_a(5)
    r.emit(0xD0, 0xE0)
    r.mov_r_a(4)
    r.emit(0xD0, 0xD0)  # POP PSW
    r.ret()
    r.label("restore_stock")
    r.emit(0xD0, 0xE0)
    r.mov_r_a(5)
    r.emit(0xD0, 0xE0)
    r.mov_r_a(4)
    r.label("stock_no_scratch")
    r.emit(0xD0, 0xD0)  # POP PSW
    # Replay the displaced instruction and continue the stock release body.
    r.mov_dptr(0x088A)
    r.ljmp_abs(0x62DA)
    return r.finish()


def build_send(glide_send: int | None = None) -> bytes:
    r = velocity.Asm(SEND)
    r.lcall(INIT)
    # While selecting, suppress note-on/off output; the entry routine has
    # cleared held notes. Other expression messages still pass through.
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(6)
    r.rel(0x40, "quantize")
    r.mov_a_r(7)
    r.anl(0xF0)
    r.cjne_a(0x80, "check_on")
    r.ret()
    r.label("check_on")
    r.cjne_a(0x90, "menu")
    r.ret()
    r.label("quantize")
    r.mov_a_r(7)
    r.anl(0xF0)
    r.cjne_a(0x80, "check_note_on")
    r.lcall(QUANTIZE)
    if glide_send is not None:
        r.ljmp("menu")
    else:
        r.ljmp_abs(cyclone.GAME_SEND_HOOK)
    r.label("check_note_on")
    r.cjne_a(0x90, "menu")
    r.lcall(QUANTIZE)
    r.label("menu")
    if glide_send is not None:
        # Pressure glide sees quantized note pitches and may consume a joined
        # note-on, note-off, pressure, or bend before it reaches the receiver.
        r.lcall(glide_send)
        r.rel(0x50, "send")  # JNC: glide helper did not consume this message
        r.ret()
        r.label("send")
    # Preserve the existing Cyclone suppression behavior.
    r.ljmp_abs(cyclone.GAME_SEND_HOOK)
    return r.finish()


def build_quantize() -> bytes:
    r = velocity.Asm(QUANTIZE)
    # The stock USB sender and its caller still use the other working
    # registers after this hook. Only R5 (the MIDI note number) may change.
    for direct in (0xD0, 0xF0, 0x03, 0x04, 0x06, 0x07):
        r.emit(0xC0, direct)
    # R6 = transposed pitch, clamped to MIDI 0..127.
    r.mov_a_r(5)
    r.mov_dptr(TRANSPOSE)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_a_r(5)
    r.emit(0x2C)
    r.clr_c()
    r.subb(12)
    r.rel(0x40, "low_clamp")
    r.mov_r_a(6)
    r.cjne_a(0x80, "range_check")
    r.ljmp("high_clamp")
    r.label("range_check")
    r.rel(0x40, "pitch_ready")      # carry means below 128
    r.ljmp("high_clamp")
    r.label("low_clamp")
    r.mov_r(6, 0)
    r.ljmp("pitch_ready")
    r.label("high_clamp")
    r.mov_r(6, 127)
    r.label("pitch_ready")
    # Keep the clamped pitch on the stack while looking up its scale mapping.
    r.emit(0xC0, 0x06)
    r.mov_a_r(6)
    r.emit(0x75, 0xF0, 12, 0x84)  # MOV B,#12; DIV AB
    r.emit(0xAB, 0xF0)             # MOV R3,B
    r.mov_dptr(TRANSPOSE)
    r.movx_load()
    r.mov_dptr(TRANSPOSE_MOD)
    r.movc()
    r.mov_r_a(4)                   # R4 = transpose pitch class
    # Convert to the pitch class relative to the selected scale root.
    r.mov_a_r(3)
    r.clr_c()
    r.emit(0x9C)                   # SUBB A,R4
    r.rel(0x50, "relative_pc_ready")
    r.add(12)
    r.label("relative_pc_ready")
    r.mov_r_a(4)
    r.mov_dptr(SCALE_ID)
    r.movx_load()
    r.emit(0x75, 0xF0, 12, 0xA4)  # MUL AB: A = scale * 12
    r.emit(0x2C)                   # + relative pitch class; index 0..179
    r.mov_r_a(4)
    r.mov_dptr(PACKED_RECORDS)
    r.mov_a_r(4)
    r.movc()                        # packed LED state and signed delta
    r.anl(0x0F)
    r.jb(0xE3, "negative_nibble")  # sign extend the four-bit correction
    r.ljmp("delta_ready")
    r.label("negative_nibble")
    r.orl(0xF0)
    r.label("delta_ready")
    r.mov_r_a(3)
    r.jb(0xE7, "negative")
    r.emit(0xD0, 0x06)
    r.mov_a_r(6)
    r.emit(0x2B)                    # ADD A,R3
    r.cjne_a(0x80, "positive_bound")
    r.ljmp("set_high")
    r.label("positive_bound")
    r.rel(0x40, "set_note")        # carry means <128
    r.label("set_high")
    r.mov_a(127)
    r.label("set_note")
    r.mov_r_a(5)
    r.ljmp("restore")
    r.label("negative")
    r.mov_a_r(3)
    r.emit(0xF4, 0x04)              # CPL A; INC A => magnitude
    r.mov_r_a(3)
    r.emit(0xD0, 0x06)
    r.mov_a_r(6)
    r.clr_c()
    r.emit(0x9B)                    # SUBB A,R3
    r.rel(0x40, "set_low")
    r.mov_r_a(5)
    r.ljmp("restore")
    r.label("set_low")
    r.mov_r(5, 0)
    r.label("restore")
    for direct in (0x07, 0x06, 0x04, 0x03, 0xF0, 0xD0):
        r.emit(0xD0, direct)
    r.ret()
    return r.finish()


def build_draw() -> bytes:
    r = velocity.Asm(DRAW)
    # Redraw after a control change or when the root blink interval expires.
    r.mov_dptr(DRAW_FORCE)
    r.movx_load()
    r.rel(0x60, "blink")
    r.mov_dptr(DRAW_FORCE)
    r.clr_a()
    r.movx_store()
    r.ljmp("pattern")
    r.label("blink")
    r.mov_a_direct(0x4C)
    r.mov_r_a(7)
    r.mov_dptr(BLINK_TICK)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_a_r(7)
    r.clr_c()
    r.emit(0x9E)                    # SUBB A,R6
    r.clr_c()
    r.subb(128)
    r.rel(0x40, "no_draw")
    r.mov_dptr(BLINK_TICK)
    r.mov_a_r(7)
    r.movx_store()
    r.mov_dptr(ROOT_PHASE)
    r.movx_load()
    r.xrl(1)
    r.movx_store()
    r.ljmp("pattern")
    r.label("no_draw")
    r.ret()
    r.label("pattern")
    # Select a scale's 12 compact records; pitch class advances per key.
    r.mov_dptr(TRANSPOSE)
    r.movx_load()
    r.mov_dptr(TRANSPOSE_MOD)
    r.movc()
    r.mov_r_a(4)
    r.mov_a(12)
    r.clr_c()
    r.emit(0x9C)                    # A = 12 - transpose class
    r.cjne_a(12, "first_pc_ready")
    r.clr_a()
    r.label("first_pc_ready")
    r.mov_r_a(4)
    r.mov_dptr(SCALE_ID)
    r.movx_load()
    r.emit(0x75, 0xF0, 12, 0xA4)   # A = scale * 12
    r.mov_dptr(PACKED_RECORDS)
    r.emit(0x25, 0x82)             # add row offset to DPL
    r.emit(0xF5, 0x82, 0xE4, 0x35, 0x83, 0xF5, 0x83)
    # Preserve the pattern pointer around each stock LED update.
    r.mov_r(6, 0)
    r.label("copy")
    r.emit(0xC0, 0x06, 0xC0, 0x04, 0xC0, 0x82, 0xC0, 0x83)
    r.mov_a_r(4)
    r.movc()
    r.anl(0x30)
    r.emit(0xC4)                   # SWAP A: LED state moves to low nibble
    r.mov_r_a(3)
    r.mov_dptr(cyclone.DRAW_FLAG)
    r.mov_a(1)
    r.movx_store()
    r.mov_a_r(3)
    r.cjne_a(0, "member")
    r.mov_r(5, 0)
    r.rel(0x80, "put")
    r.label("member")
    r.cjne_a(2, "bright")
    r.mov_dptr(ROOT_PHASE)
    r.movx_load()
    r.rel(0x60, "dark")
    r.label("bright")
    r.mov_r(5, 0xFF)
    r.rel(0x80, "put")
    r.label("dark")
    r.mov_r(5, 0)
    r.label("put")
    r.mov_a_r(6)
    r.mov_r_a(7)
    r.lcall(velocity.STOCK_SET_LED)
    r.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0x04, 0xD0, 0x06)
    r.mov_a_r(4)
    r.emit(0x04)                   # next keyboard key is one semitone higher
    r.cjne_a(12, "next_pc_ready")
    r.clr_a()
    r.label("next_pc_ready")
    r.mov_r_a(4)
    r.inc_r(6)
    r.cjne_r(6, KEY_COUNT, "copy")
    r.mov_dptr(cyclone.DRAW_FLAG)
    r.clr_a()
    r.movx_store()
    r.ret()
    return r.finish()


def build_led_gate() -> bytes:
    r = velocity.Asm(LED_GATE)
    r.emit(0xC0, 0xE0, 0xC0, 0x82, 0xC0, 0x83)  # preserve LED index lookup state
    r.mov_dptr(SCALE_STATE)
    r.movx_load()
    r.clr_c()
    r.subb(6)
    r.rel(0x40, "game")
    r.label("scale")
    r.mov_dptr(cyclone.DRAW_FLAG)
    r.movx_load()
    r.rel(0x70, "game")
    r.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    r.ljmp_abs(0x7B2E)
    r.label("game")
    r.emit(0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xE0)
    r.ljmp_abs(cyclone.LED_GATE)
    return r.finish()


def build_image(base_image: bytes, glide_send: int | None = None) -> tuple[bytes, bytes]:
    patched = bytearray(base_image)
    # Exact incoming hooks make the layer fail closed if the base changes.
    expected_dispatch = bytes((0x02, cyclone.GAME_DISPATCH >> 8, cyclone.GAME_DISPATCH & 255))
    if bytes(patched[menus.DISPATCH:menus.DISPATCH + 3]) != expected_dispatch:
        raise ValueError("Cyclone dispatcher hook differs")
    expected_send = bytes((0x02, cyclone.GAME_SEND_HOOK >> 8, cyclone.GAME_SEND_HOOK & 255))
    if bytes(patched[velocity.SEND_HOOK:velocity.SEND_HOOK + 3]) != expected_send:
        raise ValueError("Cyclone MIDI hook differs")
    expected_keyon = bytes((0x02, menus.KEYON_ROUTINE >> 8, menus.KEYON_ROUTINE & 255))
    if bytes(patched[0x4C4F:0x4C52]) != expected_keyon:
        raise ValueError("menu key-on hook differs")
    expected_led = bytes((0x02, cyclone.LED_GATE >> 8, cyclone.LED_GATE & 255))
    if bytes(patched[0x7B26:0x7B29]) != expected_led:
        raise ValueError("Cyclone LED gate hook differs")
    patched[menus.DISPATCH:menus.DISPATCH + 3] = bytes((0x02, DISPATCH >> 8, DISPATCH & 255))
    patched[velocity.SEND_HOOK:velocity.SEND_HOOK + 3] = bytes((0x02, SEND >> 8, SEND & 255))
    patched[0x4C4F:0x4C52] = bytes((0x02, KEYON >> 8, KEYON & 255))
    if bytes(patched[0x62D7:0x62DA]) != bytes.fromhex("90 08 8A"):
        raise ValueError("stock key-off entry differs")
    patched[0x62D7:0x62DA] = bytes((0x02, KEYOFF >> 8, KEYOFF & 255))
    patched[0x7B26:0x7B29] = bytes((0x02, LED_GATE >> 8, LED_GATE & 255))

    packed_records, transpose_mod = packed_scale_records()
    routines = {
        DISPATCH: build_dispatch(), CHORD: build_chord(), ACTIVE_INPUT: build_active_input(),
        KEYON: build_keyon(), MARK_KEY: build_mark_key(), SEND: build_send(glide_send),
        QUANTIZE: build_quantize(),
        DRAW: build_draw(), LED_GATE: build_led_gate(), INIT: build_init(),
        ALL_OFF: build_all_off(), KEYOFF: build_keyoff(),
        RESTORE_BUTTONS: build_restore_buttons(),
        CLEAR_KEYS: build_clear_keys(),
        PACKED_RECORDS: packed_records, TRANSPOSE_MOD: transpose_mod,
    }
    spans = sorted((address, address + len(data)) for address, data in routines.items())
    for (_, end), (start, _) in zip(spans, spans[1:]):
        if end > start:
            raise ValueError(f"scale quantizer regions overlap near 0x{start:04X}")
    for address, data in routines.items():
        if patched[address:address + len(data)] != b"\xff" * len(data):
            raise ValueError(f"scale quantizer region at 0x{address:04X} is occupied")
        patched[address:address + len(data)] = data
    records, _ = extract(STOCK_SYX)
    return bytes(patched), pack(records_from_image(records, patched))
