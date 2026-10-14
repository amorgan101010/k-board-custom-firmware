#!/usr/bin/env python3
"""Momentary black-key octave controls for non-Chromatic scale playback."""

from __future__ import annotations

from firmware_tools import build_octave_patch as octave
from firmware_tools import build_pressure_glide_patch as glide
from firmware_tools import build_scale_quantizer_patch as scales
from firmware_tools import build_velocity_slider_patch as velocity


# Five free bytes immediately after the consumed-key bitmap hold ten 4-bit
# recency ranks. INIT_DONE (0x0F73) stores normal octave + 1.
RANKS = 0x0F88
CONTACT = 0xC255
KEYON = 0xBE22
KEYOFF = 0xC505
MUTED = 0xC040
GET_RANK = 0xC87E
SET_RANK = 0xC934
UPDATE_EFFECTIVE = 0xB953
RESET = 0xB6E5
BUTTON_DISPATCH = 0xC322
STOCK_BUTTON_DISPATCH = 0xB271


def _key_slot(r: velocity.Asm, bad: str) -> None:
    """Map black-key physical index R7 to modifier slot 0..9, else jump."""
    pairs = ((1, 0), (3, 1), (6, 2), (8, 3), (10, 4),
             (13, 5), (15, 6), (18, 7), (20, 8), (22, 9))
    for key, slot in pairs:
        r.mov_a_r(7)
        r.cjne_a(key, f"key_{key}")
        r.mov_a(slot)
        r.ljmp("slot_ready")
        r.label(f"key_{key}")
    r.ljmp(bad)
    r.label("slot_ready")


def build_get_rank() -> bytes:
    r = velocity.Asm(GET_RANK)
    # R7 is slot. Keep the slot parity in R4 while forming RANKS + slot/2.
    r.mov_a_r(7)
    r.anl(1)
    r.mov_r_a(4)
    r.mov_a_r(7)
    r.emit(0x03)  # RR A
    r.anl(7)
    r.add(RANKS & 255)
    r.emit(0xF5, 0x82, 0x75, 0x83, RANKS >> 8)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_a_r(4)
    r.rel(0x60, "low")
    r.mov_a_r(6)
    r.emit(0xC4)  # SWAP A
    r.ljmp("mask")
    r.label("low")
    r.mov_a_r(6)
    r.label("mask")
    r.anl(0x0F)
    r.ret()
    return r.finish()


def build_set_rank() -> bytes:
    r = velocity.Asm(SET_RANK)
    # R7 is slot, R5 is rank. Preserve the other nibble.
    r.mov_a_r(7)
    r.anl(1)
    r.mov_r_a(4)
    r.mov_a_r(7)
    r.emit(0x03)
    r.anl(7)
    r.add(RANKS & 255)
    r.emit(0xF5, 0x82, 0x75, 0x83, RANKS >> 8)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_a_r(4)
    r.rel(0x60, "set_low")
    r.mov_a_r(6)
    r.anl(0x0F)
    r.mov_r_a(6)
    r.mov_a_r(5)
    r.emit(0x23, 0x23, 0x23, 0x23)  # rank << 4
    r.emit(0x4E)  # ORL A,R6
    r.ljmp("store")
    r.label("set_low")
    r.mov_a_r(6)
    r.anl(0xF0)
    r.mov_r_a(6)
    r.mov_a_r(5)
    r.emit(0x4E)
    r.label("store")
    r.movx_store()
    r.ret()
    return r.finish()


def build_update_effective() -> bytes:
    r = velocity.Asm(UPDATE_EFFECTIVE)
    # Find the held key with the greatest rank; ties cannot occur.
    r.mov_r(7, 0)
    r.mov_r(5, 0)  # best rank
    r.mov_r(3, 0xFF)  # best slot
    r.label("scan")
    r.lcall(GET_RANK)
    r.mov_r_a(6)
    r.clr_c()
    r.emit(0x95, 0x05)  # SUBB A,R5
    r.rel(0x40, "next")
    r.rel(0x60, "next")
    r.mov_a_r(6)
    r.mov_r_a(5)
    r.mov_a_r(7)
    r.mov_r_a(3)
    r.label("next")
    r.inc_r(7)
    r.cjne_r(7, 10, "scan")

    # Base octave is INIT_DONE - 1. Preserve it in R4.
    r.mov_dptr(scales.INIT_DONE)
    r.movx_load()
    r.rel(0x70, "base_ready")
    r.mov_dptr(0x005F)
    r.movx_load()
    r.emit(0x04)  # INC A
    r.mov_dptr(scales.INIT_DONE)
    r.movx_store()
    r.mov_dptr(scales.INIT_DONE)
    r.movx_load()
    r.label("base_ready")
    r.emit(0x14)  # DEC A
    r.mov_r_a(4)
    r.mov_a_r(3)
    r.cjne_a(0xFF, "has_modifier")
    r.mov_a_r(4)
    r.ljmp("clamp")
    r.label("has_modifier")
    # Slots 0/5=-2, 1/6=-1, 2/7=0, 3/8=+1, 4/9=+2.
    for slots, amount, label in [((0, 5), -2, "minus_two"),
                                 ((1, 6), -1, "minus_one"),
                                 ((2, 7), 0, "neutral"),
                                 ((3, 8), 1, "plus_one")]:
        for slot in slots:
            r.mov_a_r(3)
            r.cjne_a(slot, f"slot_{slot}")
            r.ljmp(label)
            r.label(f"slot_{slot}")
    # The only remaining valid slot is 4/9, +2.
    r.label("plus_two")
    r.mov_a_r(4)
    r.add(2)
    r.ljmp("clamp")
    r.label("minus_two")
    r.mov_a_r(4)
    r.clr_c()
    r.emit(0x94, 2)
    r.ljmp("clamp_low")
    r.label("minus_one")
    r.mov_a_r(4)
    r.clr_c()
    r.emit(0x94, 1)
    r.ljmp("clamp_low")
    r.label("neutral")
    r.mov_a_r(4)
    r.ljmp("clamp")
    r.label("plus_one")
    r.mov_a_r(4)
    r.emit(0x04)  # INC A
    r.ljmp("clamp")
    r.label("clamp_low")
    # Underflow is indicated by carry from SUBB.
    r.rel(0x50, "clamp")
    r.clr_a()
    r.ljmp("store")
    r.label("clamp")
    r.mov_r_a(6)
    r.clr_c()
    r.emit(0x94, 5)
    r.rel(0x50, "at_or_above_five")
    r.mov_a_r(6)
    r.ljmp("store")
    r.label("at_or_above_five")
    r.mov_a(5)
    r.label("store")
    r.mov_dptr(0x005F)
    r.movx_store()
    r.ret()
    return r.finish()


def build_contact() -> bytes:
    r = velocity.Asm(CONTACT)
    # Called only after the stock raw-force gate confirms an active contact.
    r.emit(0xC0, 0xE0, 0xC0, 0xD0, 0xC0, 0x82, 0xC0, 0x83,
           0xC0, 0x03, 0xC0, 0x04, 0xC0, 0x05, 0xC0, 0x06, 0xC0, 0x07)
    r.mov_dptr(scales.SCALE_STATE)
    r.movx_load()
    r.rel(0x60, "state_idle")
    r.ljmp("done")
    r.label("state_idle")
    r.mov_dptr(scales.cyclone.GAME_STATE)
    r.movx_load()
    r.rel(0x60, "game_idle")
    r.ljmp("done")
    r.label("game_idle")
    r.mov_dptr(scales.SCALE_ID)
    r.movx_load()
    r.rel(0x70, "non_chromatic")
    r.ljmp("done")
    r.label("non_chromatic")
    r.mov_a_direct(0x36)
    r.mov_r_a(7)
    _key_slot(r, "done")
    r.mov_r_a(3)  # retain candidate slot
    r.mov_r_a(7)
    # Existing rank means this is a continuing scan, not a new press.
    r.lcall(GET_RANK)
    r.rel(0x60, "register")
    r.ljmp("done")
    r.label("register")
    # Rank becomes one greater than the current maximum, preserving recency.
    r.mov_r(7, 0)
    r.mov_r(5, 0)
    r.label("max_scan")
    r.lcall(GET_RANK)
    r.mov_r_a(6)
    r.clr_c()
    r.emit(0x95, 0x05)
    r.rel(0x40, "max_next")
    r.mov_a_r(6)
    r.mov_r_a(5)
    r.label("max_next")
    r.inc_r(7)
    r.cjne_r(7, 10, "max_scan")
    # The original slot is saved in R3; R5 still holds the current maximum.
    r.inc_r(5)
    r.mov_a_r(3)
    r.mov_r_a(7)
    r.lcall(SET_RANK)
    r.mov_a_direct(0x36)
    r.mov_r_a(7)
    r.lcall(scales.MARK_KEY)
    r.lcall(UPDATE_EFFECTIVE)
    r.label("done")
    r.emit(0xD0, 0x07, 0xD0, 0x06, 0xD0, 0x05, 0xD0, 0x04, 0xD0, 0x03,
           0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xD0, 0xD0, 0xE0)
    r.ret()
    return r.finish()


def build_keyon() -> bytes:
    r = velocity.Asm(KEYON)
    # If raw scanning already registered the black-key contact, mark its
    # release as consumed and return before allocation or any MIDI output.
    r.emit(0xC0, 0xE0, 0xC0, 0xD0, 0xC0, 0x82, 0xC0, 0x83,
           0xC0, 0x03, 0xC0, 0x04, 0xC0, 0x05, 0xC0, 0x06, 0xC0, 0x07)
    r.mov_dptr(scales.SCALE_STATE)
    r.movx_load()
    r.rel(0x60, "normal_state")
    r.lcall(RESET)
    r.ljmp("ordinary")
    r.label("normal_state")
    r.mov_dptr(scales.cyclone.GAME_STATE)
    r.movx_load()
    r.rel(0x60, "playing_mode")
    r.lcall(RESET)
    r.ljmp("ordinary")
    r.label("playing_mode")
    r.mov_dptr(scales.SCALE_ID)
    r.movx_load()
    r.rel(0x60, "ordinary")
    r.mov_a_r(7)
    _key_slot(r, "ordinary")
    r.mov_r_a(7)
    r.lcall(GET_RANK)
    r.rel(0x60, "ordinary")
    r.emit(0xD0, 0x07)
    r.lcall(scales.MARK_KEY)
    r.emit(0xD0, 0x06, 0xD0, 0x05, 0xD0, 0x04, 0xD0, 0x03,
           0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xD0, 0xD0, 0xE0)
    r.emit(0xD3)
    r.ret()
    r.label("ordinary")
    r.emit(0xD0, 0x07, 0xD0, 0x06, 0xD0, 0x05, 0xD0, 0x04, 0xD0, 0x03,
           0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xD0, 0xD0, 0xE0)
    r.ljmp_abs(glide.UI_KEYON)
    return r.finish()


def build_keyoff() -> bytes:
    r = velocity.Asm(KEYOFF)
    r.emit(0xC0, 0xE0, 0xC0, 0xD0, 0xC0, 0x82, 0xC0, 0x83,
           0xC0, 0x03, 0xC0, 0x04, 0xC0, 0x05, 0xC0, 0x06, 0xC0, 0x07)
    r.mov_a_r(7)
    _key_slot(r, "ordinary")
    r.mov_r_a(7)
    r.lcall(GET_RANK)
    r.rel(0x60, "ordinary")
    # Clear rank before recalculating; another held key can regain control.
    r.mov_r(5, 0)
    r.lcall(SET_RANK)
    r.lcall(UPDATE_EFFECTIVE)
    # This early-return release path bypasses the stock key-follow LED clear.
    # Point the shared helper at the released physical key and extinguish it.
    r.mov_a_direct(0x36)
    r.mov_dptr(glide.PENDING_KEY)
    r.movx_store()
    r.clr_a()
    r.lcall(glide.PHYSICAL_LED)
    r.emit(0xD0, 0x07)
    r.lcall(scales.KEYOFF)  # clear the shared consumed-key mark
    r.emit(0xD0, 0x06, 0xD0, 0x05, 0xD0, 0x04, 0xD0, 0x03,
           0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xD0, 0xD0, 0xE0)
    r.emit(0xD3)
    r.ret()
    r.label("ordinary")
    r.emit(0xD0, 0x07, 0xD0, 0x06, 0xD0, 0x05, 0xD0, 0x04, 0xD0, 0x03,
           0xD0, 0x83, 0xD0, 0x82, 0xD0, 0xD0, 0xD0, 0xE0)
    r.ljmp_abs(glide.UI_KEYOFF)
    return r.finish()


def build_reset() -> bytes:
    r = velocity.Asm(RESET)
    r.mov_dptr(RANKS)
    r.mov_r(7, 5)
    r.clr_a()
    r.label("clear")
    r.movx_store()
    r.inc_dptr()
    r.cjne_r(7, 1, "decrement")
    r.ljmp("finish")
    r.label("decrement")
    r.emit(0x1F)  # DEC R7
    r.ljmp("clear")
    r.label("finish")
    r.lcall(UPDATE_EFFECTIVE)
    r.ret()
    return r.finish()


def build_button_dispatch() -> bytes:
    r = velocity.Asm(BUTTON_DISPATCH)
    saved = (0xE0, 0xD0, 0x82, 0x83, 0x00, 0x03, 0x04, 0x05, 0x06, 0x07)
    # Read the caller return address below the saved register frame so octave
    # direction remains known even when the stock clamp leaves ACC unchanged.
    for direct in saved:
        r.emit(0xC0, direct)
    r.mov_a_direct(0x81)  # SP
    r.add(0xF5)           # SP - 11: low byte of original return PC
    r.mov_r_a(0)
    r.emit(0xE6)          # MOV A,@R0
    r.mov_r_a(6)
    r.emit(0x08, 0xE6)    # INC R0; MOV A,@R0 -> return high byte
    r.mov_r_a(7)
    # If no momentary control is held, run the previous dispatcher verbatim.
    r.mov_dptr(RANKS)
    r.movx_load()
    r.mov_r_a(5)
    for _ in range(4):
        r.inc_dptr()
        r.movx_load()
        r.emit(0x4D)      # ORL A,R5
        r.mov_r_a(5)
    r.mov_a_r(5)
    r.rel(0x70, "has_modifiers")
    r.ljmp("stock")
    r.label("has_modifiers")
    # INIT_DONE holds normal octave + 1. Apply the physical button direction
    # to that value, independent of the currently effective octave.
    r.mov_dptr(scales.INIT_DONE)
    r.movx_load()
    r.emit(0x14)          # DEC A -> normal octave
    r.mov_r_a(4)
    r.mov_a_r(6)
    r.cjne_a(0xEF, "check_up")
    r.mov_a_r(4)
    r.rel(0x60, "save_base")
    r.emit(0x14)          # DEC A; base cannot fall below zero
    r.mov_r_a(4)
    r.ljmp("save_base")
    r.label("check_up")
    r.cjne_a(0x04, "update")
    r.mov_a_r(4)
    r.cjne_a(5, "increment")
    r.ljmp("update")
    r.label("increment")
    r.emit(0x04)          # INC A
    r.mov_r_a(4)
    r.label("save_base")
    r.mov_a_r(4)
    r.emit(0x04)          # bias by one
    r.mov_dptr(scales.INIT_DONE)
    r.movx_store()
    r.label("update")
    r.lcall(UPDATE_EFFECTIVE)
    # The editor option still controls stock retriggering for front buttons.
    r.mov_dptr(octave.MODE_BYTE)
    r.movx_load()
    r.anl(0xF0)
    r.cjne_a(0x70, "finish_custom")
    r.mov_dptr(octave.MODE_BYTE)
    r.movx_load()
    r.anl(octave.RETRIGGER_BIT)
    r.rel(0x60, "finish_custom")
    r.mov_dptr(octave.MODE_MARKER)
    r.movx_load()
    r.clr_c()
    r.subb(101)
    r.rel(0x50, "finish_custom")
    for direct in reversed(saved):
        r.emit(0xD0, direct)
    r.mov_dptr(0x005F)
    r.movx_load()
    r.lcall(0x600E)
    r.ret()
    r.label("finish_custom")
    # Keep the stock in-progress flag lifecycle even though sounding notes are
    # left untouched by a momentary-key change.
    r.mov_dptr(0x0259)
    r.mov_a(1)
    r.movx_store()
    r.clr_a()
    r.movx_store()
    for direct in reversed(saved):
        r.emit(0xD0, direct)
    r.ret()
    r.label("stock")
    for direct in reversed(saved):
        r.emit(0xD0, direct)
    r.lcall(STOCK_BUTTON_DISPATCH)
    r.mov_dptr(0x005F)
    r.movx_load()
    r.emit(0x04)  # Keep the saved normal octave synchronized after the stock path.
    r.mov_dptr(scales.INIT_DONE)
    r.movx_store()
    r.ret()
    return r.finish()


def build_muted() -> bytes:
    r = velocity.Asm(MUTED)
    r.emit(0xC0, 0xE0, 0xC0, 0xD0, 0xC0, 0x82, 0xC0, 0x83,
           0xC0, 0x04, 0xC0, 0x06, 0xC0, 0x07)
    r.mov_dptr(scales.SCALE_STATE)
    r.movx_load()
    r.rel(0x60, "scale_idle")
    r.ljmp("clear")
    r.label("scale_idle")
    r.mov_dptr(scales.cyclone.GAME_STATE)
    r.movx_load()
    r.rel(0x60, "game_idle")
    r.ljmp("clear")
    r.label("game_idle")
    r.mov_dptr(scales.SCALE_ID)
    r.movx_load()
    r.rel(0x70, "has_scale")
    r.ljmp("clear")
    r.label("has_scale")
    r.mov_a_direct(0x36)
    r.mov_r_a(7)
    _key_slot(r, "clear")
    r.mov_r_a(7)
    r.lcall(GET_RANK)
    r.rel(0x70, "muted")
    r.ljmp("clear")
    r.label("muted")
    r.emit(0xD0, 0x07, 0xD0, 0x06, 0xD0, 0x04, 0xD0, 0x83, 0xD0, 0x82,
           0xD0, 0xD0, 0xD0, 0xE0, 0xD3)
    r.ret()
    r.label("clear")
    r.emit(0xD0, 0x07, 0xD0, 0x06, 0xD0, 0x04, 0xD0, 0x83, 0xD0, 0x82,
           0xD0, 0xD0, 0xD0, 0xE0, 0xC3)
    r.ret()
    return r.finish()


def apply(image: bytes) -> bytes:
    result = bytearray(image)
    # Route raw contact, key allocation/release, and expression through the
    # modifier-aware wrappers. Hook bytes are checked to catch layer drift.
    if result[glide.RAW_SCAN:glide.RAW_SCAN + 3] != bytes.fromhex("c0 d0 c0"):
        raise ValueError("unexpected raw scan helper entry")
    # Inject contact registration after the raw force has crossed threshold.
    # KEYON and KEYOFF entry wrappers are redirected with validated LCALLs.
    keyon = bytearray(result[glide.KEYON:glide.KEYON + 3])
    if keyon != bytes((0x12, glide.UI_KEYON >> 8, glide.UI_KEYON & 255)):
        raise ValueError("pressure key-on wrapper differs")
    result[glide.KEYON:glide.KEYON + 3] = bytes((0x12, KEYON >> 8, KEYON & 255))
    keyoff = bytearray(result[glide.KEYOFF_ROUTINE:glide.KEYOFF_ROUTINE + 3])
    if keyoff != bytes((0x12, glide.UI_KEYOFF >> 8, glide.UI_KEYOFF & 255)):
        raise ValueError("pressure key-off wrapper differs")
    result[glide.KEYOFF_ROUTINE:glide.KEYOFF_ROUTINE + 3] = bytes((0x12, KEYOFF >> 8, KEYOFF & 255))
    # Modifier expression must be consumed before pressure-glide routing.
    old_raw = glide.build_raw_scan()
    new_raw = glide.build_raw_scan(contact_hook=CONTACT)
    if result[glide.RAW_SCAN:glide.RAW_SCAN + len(old_raw)] != old_raw:
        raise ValueError("pressure raw-scan routine differs")
    result[glide.RAW_SCAN:glide.RAW_SCAN + len(old_raw)] = b"\xff" * len(old_raw)
    result[glide.RAW_SCAN:glide.RAW_SCAN + len(new_raw)] = new_raw
    old_pressure = glide.build_pressure_handler()
    new_pressure = glide.build_pressure_handler(modifier_gate=MUTED)
    if result[glide.PRESSURE:glide.PRESSURE + len(old_pressure)] != old_pressure:
        raise ValueError("pressure handler differs")
    result[glide.PRESSURE:glide.PRESSURE + len(old_pressure)] = b"\xff" * len(old_pressure)
    result[glide.PRESSURE:glide.PRESSURE + len(new_pressure)] = new_pressure
    old_all_off = scales.build_all_off(retire_voices=glide.RETIRE_VOICES)
    new_all_off = scales.build_all_off(retire_voices=glide.RETIRE_VOICES,
                                       reset_octave=RESET)
    if result[scales.ALL_OFF:scales.ALL_OFF + len(old_all_off)] != old_all_off:
        raise ValueError("scale selector all-off routine differs")
    result[scales.ALL_OFF:scales.ALL_OFF + len(old_all_off)] = b"\xff" * len(old_all_off)
    result[scales.ALL_OFF:scales.ALL_OFF + len(new_all_off)] = new_all_off
    game_patch = scales.cyclone
    old_game_entry = game_patch.build_enter_wait(retire_voices=glide.RETIRE_VOICES)
    new_game_entry = game_patch.build_enter_wait(retire_voices=glide.RETIRE_VOICES,
                                                  reset_octave=RESET)
    if result[game_patch.ENTER_WAIT:game_patch.ENTER_WAIT + len(old_game_entry)] != old_game_entry:
        raise ValueError("Cyclone entry routine differs")
    result[game_patch.ENTER_WAIT:game_patch.ENTER_WAIT + len(old_game_entry)] = b"\xff" * len(old_game_entry)
    result[game_patch.ENTER_WAIT:game_patch.ENTER_WAIT + len(new_game_entry)] = new_game_entry
    # Preserve the old octave dispatcher for ordinary/front-button handling;
    # active modifiers use a wrapper that updates the hidden normal octave.
    old_dispatch = octave.build_dispatch()
    if result[octave.OCTAVE_DISPATCH:octave.OCTAVE_DISPATCH + len(old_dispatch)] != old_dispatch:
        raise ValueError("stock octave dispatcher differs")
    if result[STOCK_BUTTON_DISPATCH:STOCK_BUTTON_DISPATCH + len(old_dispatch)] != b"\xff" * len(old_dispatch):
        raise ValueError("stock octave dispatcher relocation is occupied")
    result[STOCK_BUTTON_DISPATCH:STOCK_BUTTON_DISPATCH + len(old_dispatch)] = old_dispatch
    result[octave.OCTAVE_DISPATCH:octave.OCTAVE_DISPATCH + 3] = bytes(
        (0x02, BUTTON_DISPATCH >> 8, BUTTON_DISPATCH & 255))
    # Routine placement is completed after the existing layer has emitted.
    routines = {
        CONTACT: build_contact(), KEYON: build_keyon(), KEYOFF: build_keyoff(),
        MUTED: build_muted(), GET_RANK: build_get_rank(), SET_RANK: build_set_rank(),
        UPDATE_EFFECTIVE: build_update_effective(), RESET: build_reset(),
        BUTTON_DISPATCH: build_button_dispatch(),
    }
    for address, code in routines.items():
        if address == BUTTON_DISPATCH:
            if result[address:address + len(code)] != b"\xff" * len(code):
                raise ValueError(f"momentary-octave destination occupied at 0x{address:04X}")
        elif result[address:address + len(code)] != b"\xff" * len(code):
            raise ValueError(f"momentary-octave destination occupied at 0x{address:04X}")
        result[address:address + len(code)] = code
    return bytes(result)
