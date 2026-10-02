#!/usr/bin/env python3
"""Add pressure-weighted two-key glide to the complete custom K-Board image.

This builder only patches an in-memory image and emits a new firmware image;
it never opens a MIDI port or flashes a device.
"""

from __future__ import annotations

from firmware_tools import build_scale_quantizer_patch as scales
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools import build_relative_tilt_patch as tilt
from firmware_tools.extract_kmi_firmware import extract
from firmware_tools.repack_kmi_firmware import pack, records_from_image


# XRAM 0x0F8D–0x0FEC is the audited 96-byte state table. All entries are indexed
# by MIDI member channel, so overlapping pairs remain independent.
KEY_BY_CHANNEL = 0x0F8D
PITCH_BY_CHANNEL = 0x0F9D
WEIGHT_BY_CHANNEL = 0x0FAD
WEIGHT_LOW_BY_CHANNEL = 0x0FBD
CONTACT_PRESSURE_BY_CHANNEL = WEIGHT_LOW_BY_CHANNEL  # historical allocation alias
FLAGS_BY_CHANNEL = 0x0FCD
PARTNER_BY_CHANNEL = 0x0FDD
PRESSURE_BY_CHANNEL = 0x0F78  # 16 mapped aftertouch bytes, after the release bitmap

# Existing gaps in the patch state allocation.
PENDING_KEY = 0x0F48
PENDING_CHANNEL = 0x0F49
PENDING_NOTE = 0x0F4A
BEST_CHANNEL = 0x0F4B
BEST_DISTANCE = 0x0F4C
GLIDE_CONSUME = 0x0F4D
OUT_BEND_HI = 0x0F4E
OUT_BEND_LO = 0x0F4F
RAW_BEND_HI = 0x0F23
RAW_BEND_LO = 0x0F24
GLIDE_SIGN = 0x0F25
INTERNAL_SEND = 0x0F26
BEND_MODIFIED = 0x0F27
ROLE_SCRATCH = 0x0F28
PRIMARY_SCRATCH = 0x0F29
SCANNED_KEY = 0x0F2A
SCANNED_HIGH = 0x0F2B
SCANNED_LOW = 0x0F2C
RAW_SCAN = 0xAA00
RAW_RATIO = 0xAC00
RAW_INIT = 0xAD00
RESET_MEMBER = 0xAE00
REARM_TILT = 0xAF00
BLEND_TILT = 0x9D00
OUTPUT_PRESSURE = 0x9E00
RESEND_PRESSURE = 0x9E80

# The unused CV2 Channel field stores glide interval + 1 (0 means an old
# preset), and CV2 CC Number stores the tilt reference range in semitones.
GLIDE_RANGE_CODE = 0x04F4
TILT_REFERENCE_RANGE = 0x04FC
TILT_REFERENCE_AMOUNT = 0x04F6  # big-endian word: original percent * reference span

KEYON = 0x8F80
KEYOFF = 0x9000
UPDATE_BEND = 0x934F
KEYOFF_ROUTINE = KEYOFF
PRESSURE_SAMPLE = 0x9500
BEND_SAMPLE = 0x9600
CALCULATE = 0x981E
RATIO = 0xA94A
OUTPUT_SCALE = 0x9A00
PRESSURE_SEND = 0x9B00
PHYSICAL_LED = 0x9B20
TILT_SCALE = 0x9C00
BEND_TABLE = 0xD28B  # first erased byte after the current scale KEYOFF routine
PRESSURE = BEND_TABLE + 25 * 129 * 2

FLAG_FREE = 0
FLAG_NORMAL = 1
FLAG_PRIMARY = 2
FLAG_SECONDARY = 3
FLAG_PRIMARY_RELEASED = 4
FLAG_CONTACT_PENDING = 5


def glide_bend_table() -> bytes:
    """14-bit bend magnitudes for interval 0..24 and pressure ratio 0..128."""
    table = bytearray()
    for interval in range(25):
        for ratio in range(129):
            value = round(interval * ratio * 8192 / (24 * 128))
            table.extend((value >> 7 & 0x7F, value & 0x7F))
    return bytes(table)


class Asm(velocity.Asm):
    def dec_a(self) -> None:
        self.emit(0x14)

    def jz_far(self, name: str) -> None:
        self.emit(0x70, 3)  # JNZ skips the absolute jump
        self.emit(0x02, 0, 0)
        self.absolute_fixups.append((len(self.code) - 2, name))

    def jnz_far(self, name: str) -> None:
        self.emit(0x60, 3)  # JZ skips the absolute jump
        self.emit(0x02, 0, 0)
        self.absolute_fixups.append((len(self.code) - 2, name))

    def cjne_a_far(self, value: int, name: str) -> None:
        # Equality falls into the LJMP; inequality skips over it.
        self.emit(0xB4, value, 3, 0x02, 0, 0)
        self.absolute_fixups.append((len(self.code) - 2, name))

    def jc_far(self, name: str) -> None:
        self.emit(0x50, 3)  # JNC skips the absolute jump
        self.emit(0x02, 0, 0)
        self.absolute_fixups.append((len(self.code) - 2, name))

    def jnc_far(self, name: str) -> None:
        self.emit(0x40, 3)  # JC skips the absolute jump
        self.emit(0x02, 0, 0)
        self.absolute_fixups.append((len(self.code) - 2, name))


def _indexed(r: Asm, base: int, channel_register: int = 6) -> None:
    """Set DPTR to base + (channel_register & 15), within one XRAM page."""
    r.mov_a_r(channel_register)
    r.anl(0x0F)
    r.add(base & 0xFF)
    r.emit(0xF5, 0x82, 0x75, 0x83, base >> 8)


def _push(r: Asm, registers: tuple[int, ...]) -> None:
    for register in registers:
        r.emit(0xC0, register)


def _pop(r: Asm, registers: tuple[int, ...]) -> None:
    for register in reversed(registers):
        r.emit(0xD0, register)


def _read(r: Asm, base: int, channel_register: int = 6) -> None:
    _indexed(r, base, channel_register)
    r.movx_load()


def _write(r: Asm, base: int, value_register: int = 4,
           channel_register: int = 6) -> None:
    r.mov_a_r(value_register)
    _indexed(r, base, channel_register)
    r.mov_a_r(value_register)
    r.movx_store()


def build_keyon() -> bytes:
    """Remember the key, or rejoin a primary whose channel remains reserved."""
    r = Asm(KEYON)
    r.mov_dptr(PENDING_KEY)
    r.mov_a_r(7)
    r.movx_store()
    _push(r, (0xD0, 4, 6))
    r.mov_a_r(7)
    r.add(0x60)
    r.emit(0xF5, 0x82, 0x75, 0x83, 0)
    r.movx_load()
    r.cjne_a(0xFF, "mapped")
    r.ljmp("ordinary")
    r.label("mapped")
    r.mov_r_a(6)
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_PRIMARY_RELEASED, "ordinary")
    # Repressing this key must not allocate a new channel and overwrite the
    # mapping needed to release the original audible voice when the pair ends.
    r.mov_r(4, FLAG_PRIMARY)
    _write(r, FLAGS_BY_CHANNEL, 4)
    r.lcall(RAW_INIT)
    r.lcall(REARM_TILT)
    r.mov_a(0xFF)
    r.lcall(PHYSICAL_LED)
    r.lcall(UPDATE_BEND)
    r.lcall(RESEND_PRESSURE)
    _pop(r, (0xD0, 4, 6))
    r.ret()
    r.label("ordinary")
    _pop(r, (0xD0, 4, 6))
    r.ljmp_abs(scales.KEYON)
    return r.finish()


def build_keyoff() -> bytes:
    """Defer the primary release until the paired physical key is released."""
    r = Asm(KEYOFF_ROUTINE)
    r.mov_a_r(7)
    r.mov_dptr(PENDING_KEY)
    r.movx_store()
    r.mov_a_r(7)
    r.add(0x60)
    r.emit(0xF5, 0x82, 0x75, 0x83, 0x00)
    r.movx_load()
    r.cjne_a(0xFF, "mapped")
    r.ljmp_abs(scales.KEYOFF)
    r.label("mapped")
    r.mov_r_a(6)
    r.mov_dptr(PENDING_CHANNEL)
    r.mov_a_r(6)
    r.movx_store()
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_PRIMARY, "check_secondary")
    r.mov_r(4, FLAG_PRIMARY_RELEASED)
    _write(r, FLAGS_BY_CHANNEL, 4)
    # The voice stays allocated, but the physical key has been released.
    # Stock 0x62E3..0x62EA normally clears its LED in key-follow mode.
    r.clr_a()
    r.lcall(PHYSICAL_LED)
    r.lcall(UPDATE_BEND)  # releasing the primary reaches the companion pitch now
    r.lcall(RESEND_PRESSURE)
    r.ret()
    r.label("check_secondary")
    r.cjne_a_far(FLAG_SECONDARY, "secondary")
    r.ljmp("ordinary")
    r.label("secondary")
    _read(r, PARTNER_BY_CHANNEL)
    r.mov_r_a(6)
    r.mov_dptr(BEST_CHANNEL)
    r.mov_a_r(6)
    r.movx_store()
    r.emit(0xC0, 0xE0)  # stock release can send pressure and overwrite BEST_CHANNEL
    # Release the companion through scale selector/keyoff behavior. Its MIDI
    # note-off is consumed while its table role remains SECONDARY.
    r.mov_dptr(PENDING_KEY)
    r.movx_load()
    r.mov_r_a(7)
    r.lcall(scales.KEYOFF)
    r.emit(0xD0, 0xE0)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_store()
    r.mov_r_a(6)
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a_far(FLAG_PRIMARY_RELEASED, "released_primary")
    r.ljmp("companion_first")
    r.label("released_primary")
    # Primary was physically released first. End the audible voice now.
    _read(r, KEY_BY_CHANNEL)
    r.mov_r_a(7)
    r.emit(0xC0, 0x06)
    r.lcall(scales.KEYOFF)
    r.emit(0xD0, 0x06)
    _clear_channel(r)
    r.mov_dptr(PENDING_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    _clear_channel(r)
    r.ret()
    r.label("companion_first")
    # Primary remains down; return its slot to ordinary one-key behavior.
    r.mov_dptr(PENDING_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    r.clr_a()
    r.mov_r_a(4)
    _write(r, WEIGHT_BY_CHANNEL, 4)
    _write(r, WEIGHT_LOW_BY_CHANNEL, 4)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    r.lcall(UPDATE_BEND)  # remove the departed key's offset, retaining tilt
    r.mov_r(4, FLAG_NORMAL)
    _write(r, FLAGS_BY_CHANNEL, 4)
    r.mov_r(4, 0xFF)
    _write(r, PARTNER_BY_CHANNEL, 4)
    r.mov_dptr(PENDING_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    _clear_channel(r)
    r.ret()
    r.label("ordinary")
    r.mov_dptr(PENDING_KEY)
    r.movx_load()
    r.mov_r_a(7)
    r.lcall(scales.KEYOFF)
    r.mov_dptr(PENDING_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    _clear_channel(r)
    r.ret()
    return r.finish()


def _clear_channel(r: Asm) -> None:
    r.mov_r(4, FLAG_FREE)
    _write(r, FLAGS_BY_CHANNEL, 4)
    _write(r, PRESSURE_BY_CHANNEL, 4)
    r.mov_r(4, 0xFF)
    _write(r, PARTNER_BY_CHANNEL, 4)
    _write(r, CONTACT_PRESSURE_BY_CHANNEL, 4)
    _write(r, WEIGHT_BY_CHANNEL, 4)
    _write(r, KEY_BY_CHANNEL, 4)
    _write(r, PITCH_BY_CHANNEL, 4)


def build_pressure_handler() -> bytes:
    """Handle note-on assignment, per-key pressure, and paired bends."""
    r = Asm(PRESSURE)
    _push(r, (0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7))
    r.mov_dptr(BEND_MODIFIED)
    r.clr_a()
    r.movx_store()
    r.mov_dptr(INTERNAL_SEND)
    r.movx_load()
    r.jnz_far("pass")
    r.mov_a_r(7)
    r.anl(0xF0)
    r.cjne_a(0x90, "not_on")
    r.mov_a_r(3)
    r.jz_far("note_off")  # velocity zero is MIDI note-off
    r.ljmp("note_on")
    r.label("not_on")
    r.cjne_a(0xD0, "not_pressure")
    r.ljmp("pressure")
    r.label("not_pressure")
    r.cjne_a(0xE0, "not_bend")
    r.ljmp("bend")
    r.label("not_bend")
    r.cjne_a_far(0x80, "note_off")
    r.ljmp("pass")

    r.label("note_on")
    # The stock channel map at 0x0060+key and the emitted MIDI status agree.
    r.mov_a_r(7)
    r.anl(0x0F)
    r.mov_r_a(6)
    r.mov_dptr(PENDING_CHANNEL)
    r.mov_r(4, 0)
    r.mov_a_r(6)
    r.movx_store()
    r.mov_dptr(PENDING_NOTE)
    r.mov_a_r(5)
    r.movx_store()
    r.mov_dptr(0x0351)
    r.movx_load()
    r.jz_far("pass")
    r.mov_a_r(6)
    r.jz_far("pass")  # MPE master channel is not a key voice

    # Register every allocated member voice before considering a glide pair.
    r.mov_dptr(PENDING_KEY)
    r.movx_load()
    r.mov_r_a(4)
    _write(r, KEY_BY_CHANNEL, 4)
    r.mov_dptr(PENDING_NOTE)
    r.movx_load()
    r.mov_r_a(4)
    _write(r, PITCH_BY_CHANNEL, 4)
    r.lcall(RAW_INIT)
    r.mov_r(4, FLAG_NORMAL)
    _write(r, FLAGS_BY_CHANNEL, 4)
    r.mov_r(4, 0xFF)
    _write(r, PARTNER_BY_CHANNEL, 4)
    r.lcall(RESET_MEMBER)

    # A stored code of 1 means glide off; 2..25 encode 1..24 semitones.
    r.mov_dptr(GLIDE_RANGE_CODE)
    r.movx_load()
    r.cjne_a(1, "range_nonzero")
    r.ljmp("pass")
    r.label("range_nonzero")
    r.jc_far("pass")  # zero or legacy field
    r.cjne_a(26, "range_valid")
    r.ljmp("pass")
    r.label("range_valid")
    r.jnc_far("pass")  # >=26
    r.dec_a()              # R4 = allowed semitone distance
    r.mov_r_a(4)

    # Select the closest already-held, unpaired member note in range.
    r.mov_r(1, 0xFF)       # best channel sentinel
    r.mov_r(0, 0xFF)       # best distance
    r.mov_r(2, 1)          # member-channel scan (master 0 is excluded)
    r.label("scan")
    r.mov_dptr(PENDING_CHANNEL)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_a_r(2)
    r.clr_c()
    r.subb_r(5)
    r.rel(0x60, "next")  # current channel
    r.mov_a_r(2)
    r.mov_r_a(6)
    _read(r, FLAGS_BY_CHANNEL, 2)
    r.cjne_a(FLAG_NORMAL, "next")
    _read(r, PITCH_BY_CHANNEL, 2)
    r.mov_r_a(5)           # candidate pitch
    r.mov_dptr(PENDING_NOTE)
    r.movx_load()
    r.clr_c()
    r.subb_r(5)
    r.rel(0x50, "positive_distance")
    r.emit(0xF4, 0x04)     # abs(A), preserving the lower 7-bit pitch
    r.label("positive_distance")
    r.mov_r_a(5)
    r.mov_a_r(5)
    r.clr_c()
    r.subb_r(4)
    r.rel(0x40, "within_range")
    r.rel(0x60, "within_range")
    r.ljmp("next")
    r.label("within_range")
    r.mov_a_r(5)
    r.clr_c()
    r.subb_r(0)
    r.rel(0x40, "best")  # first candidate or closer than current best
    r.ljmp("next")
    r.label("best")
    r.mov_a_r(5)
    r.mov_r_a(0)
    r.mov_a_r(2)
    r.mov_r_a(1)
    r.label("next")
    r.inc_r(2)
    r.cjne_r(2, 16, "scan")
    r.cjne_r(1, 0xFF, "pair")
    r.ljmp("pass")
    r.label("pair")
    # The older note remains the host's voice; this new key supplies pressure.
    r.mov_a_r(1)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_store()
    r.mov_dptr(PENDING_CHANNEL)
    r.movx_load()
    r.mov_r_a(2)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    r.mov_a_r(2)
    r.mov_r_a(4)
    _write(r, PARTNER_BY_CHANNEL, 4)
    r.mov_r(4, FLAG_PRIMARY)
    _write(r, FLAGS_BY_CHANNEL, 4)
    r.mov_dptr(PENDING_CHANNEL)
    r.movx_load()
    r.mov_r_a(2)
    r.mov_a_r(2)
    r.mov_r_a(6)
    r.mov_r(4, FLAG_SECONDARY)
    _write(r, FLAGS_BY_CHANNEL, 4)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_a_r(2)
    r.mov_r_a(6)
    _write(r, PARTNER_BY_CHANNEL, 4)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    r.lcall(UPDATE_BEND)
    r.mov_dptr(GLIDE_CONSUME)
    r.mov_a(1)
    r.movx_store()
    r.emit(0xD3)  # SETB CY: note-on is internal to the paired glissando
    r.ljmp("finish")

    r.label("pressure")
    r.mov_a_r(7)
    r.anl(0x0F)
    r.mov_r_a(6)
    r.lcall(PRESSURE_SAMPLE)
    r.ljmp("finish")

    r.label("bend")
    r.mov_a_r(7)
    r.anl(0x0F)
    r.mov_r_a(6)
    r.lcall(BEND_SAMPLE)
    r.ljmp("finish")

    r.label("note_off")
    r.mov_a_r(7)
    r.anl(0x0F)
    r.mov_r_a(6)
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_SECONDARY, "pass")
    r.mov_dptr(GLIDE_CONSUME)
    r.mov_a(1)
    r.movx_store()
    r.emit(0xD3)
    r.ljmp("finish")

    r.label("pass")
    r.clr_c()
    r.label("finish")
    _pop(r, (0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7))
    r.mov_dptr(BEND_MODIFIED)
    r.movx_load()
    r.rel(0x60, "return")
    r.mov_dptr(OUT_BEND_HI)
    r.movx_load()
    r.mov_r_a(3)
    r.inc_dptr()
    r.movx_load()
    r.mov_r_a(5)
    r.label("return")
    r.ret()
    return r.finish()


def build_pressure_sample() -> bytes:
    """Use primary aftertouch while held, then route companion to its voice."""
    r = Asm(PRESSURE_SAMPLE)
    # Preserve each physical member's last mapped pressure, including stock
    # pre-note-on contact readings. These values never affect glide force.
    _write(r, PRESSURE_BY_CHANNEL, 5)
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_PRIMARY_RELEASED, "check_companion")
    r.ljmp("consume")  # late release zero / recontact cannot overwrite owner
    r.label("check_companion")
    r.cjne_a(FLAG_SECONDARY, "pass")
    _read(r, PARTNER_BY_CHANNEL)
    r.mov_r_a(6)
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_PRIMARY_RELEASED, "consume")
    # R5 still contains the original companion aftertouch value. Emit on the
    # retained primary channel through the real two-byte stock sender.
    r.lcall(OUTPUT_PRESSURE)
    r.label("consume")
    r.mov_dptr(GLIDE_CONSUME)
    r.mov_a(1)
    r.movx_store()
    r.emit(0xD3)
    r.ret()
    r.label("pass")
    r.clr_c()
    r.ret()
    return r.finish()


def build_output_pressure() -> bytes:
    """Send mapped pressure R5 on voice R6 without changing physical caches."""
    r = Asm(OUTPUT_PRESSURE)
    saved = (0xD0, 0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7)
    _push(r, saved)
    r.mov_a_r(6)
    r.orl(0xD0)
    r.mov_r_a(7)
    r.mov_dptr(INTERNAL_SEND)
    r.mov_a(1)
    r.movx_store()
    r.lcall(PRESSURE_SEND)
    r.mov_dptr(INTERNAL_SEND)
    r.clr_a()
    r.movx_store()
    _pop(r, saved)
    r.ret()
    return r.finish()


def build_resend_pressure() -> bytes:
    """Immediately transfer aftertouch ownership at release or recontact."""
    r = Asm(RESEND_PRESSURE)
    saved = (0xD0, 0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7)
    _push(r, saved)
    r.mov_dptr(0x093E)
    r.movx_load()
    r.anl(2)
    r.rel(0x60, "done")  # no synthetic aftertouch when physical Pressure is off
    r.emit(0xC0, 0x06)  # retained audible voice
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_PRIMARY_RELEASED, "read_pressure")
    _read(r, PARTNER_BY_CHANNEL)
    r.mov_r_a(6)
    r.label("read_pressure")
    _read(r, PRESSURE_BY_CHANNEL)
    r.mov_r_a(5)
    r.emit(0xD0, 0x06)
    r.lcall(OUTPUT_PRESSURE)
    r.label("done")
    _pop(r, saved)
    r.ret()
    return r.finish()


def build_raw_init() -> bytes:
    """Seed an allocated/rejoined channel from this physical key's scan."""
    r = Asm(RAW_INIT)
    _push(r, (0xE0, 0x82, 0x83, 4, 5))
    r.mov_r(4, 0)
    r.mov_r(5, 0)
    r.mov_dptr(PENDING_KEY)
    r.movx_load()
    r.mov_r_a(4)
    r.mov_dptr(SCANNED_KEY)
    r.movx_load()
    r.emit(0x6C)  # XRL A,R4
    r.rel(0x70, "zero")
    r.inc_dptr()
    r.movx_load()
    r.mov_r_a(4)
    r.inc_dptr()
    r.movx_load()
    r.mov_r_a(5)
    r.ljmp("store")
    r.label("zero")
    r.mov_r(4, 0)
    r.label("store")
    _write(r, WEIGHT_BY_CHANNEL, 4)
    _write(r, WEIGHT_LOW_BY_CHANNEL, 5)
    _pop(r, (0xE0, 0x82, 0x83, 4, 5))
    r.ret()
    return r.finish()


def build_raw_scan() -> bytes:
    """Read force before stock release clears it; restore the scan exactly."""
    r = Asm(RAW_SCAN)
    saved = (0xD0, 0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7)
    _push(r, saved)
    r.mov_r(4, 0)
    r.mov_r(5, 0)
    r.emit(0xE5, 0x36)  # physical key
    r.mov_dptr(SCANNED_KEY)
    r.movx_store()
    r.add(0x7A)
    r.emit(0xF5, 0x82, 0x75, 0x83, 0)
    r.movx_load()
    r.clr_c()
    r.emit(0x95, 0x4D)  # fixed stock release threshold
    r.jc_far("store")
    r.mov_r_a(4)
    # Select the larger unscaled sensor word for its fractional seven bits.
    # Globals Gain changes the coarse reading, so fractions are used only at
    # unity gain, keeping the force origin aligned with the actual gate.
    r.mov_dptr(0x034C)
    r.movx_load()
    r.cjne_a(100, "pack")
    r.emit(0xE5, 0x35, 0xC3, 0x95, 0x30)
    r.emit(0xE5, 0x34, 0x95, 0x2F)
    r.rel(0x40, "right")
    r.emit(0xE5, 0x35)
    r.ljmp("fraction")
    r.label("right")
    r.emit(0xE5, 0x30)
    r.label("fraction")
    r.anl(127)
    r.mov_r_a(5)
    r.label("pack")
    # 16-bit weight = (coarse force - off threshold) * 128 + fraction.
    r.mov_a_r(4)
    r.anl(1)
    r.emit(0x03)  # RR A: bit 0 -> bit 7
    r.emit(0x4D)  # ORL A,R5
    r.mov_r_a(5)
    r.mov_a_r(4)
    r.emit(0x03)
    r.anl(127)
    r.mov_r_a(4)
    r.label("store")
    r.mov_dptr(SCANNED_HIGH)
    r.mov_a_r(4)
    r.movx_store()
    r.inc_dptr()
    r.mov_a_r(5)
    r.movx_store()
    r.mov_dptr(0x0351)
    r.movx_load()
    r.jz_far("done")
    r.mov_dptr(GLIDE_RANGE_CODE)
    r.movx_load()
    r.clr_c()
    r.subb(2)
    r.jc_far("done")
    r.clr_c()
    r.subb(24)
    r.jnc_far("done")
    r.emit(0xE5, 0x36)
    r.add(0x60)
    r.emit(0xF5, 0x82, 0x75, 0x83, 0)
    r.movx_load()
    r.cjne_a(0xFF, "mapped")
    r.ljmp("done")
    r.label("mapped")
    r.mov_r_a(6)
    _read(r, FLAGS_BY_CHANNEL)
    r.jz_far("done")
    r.mov_r_a(7)
    _read(r, WEIGHT_BY_CHANNEL)
    r.emit(0x6C)  # XRL A,R4
    r.mov_r_a(0)
    _read(r, WEIGHT_LOW_BY_CHANNEL)
    r.emit(0x6D, 0x48)  # XRL A,R5; ORL A,R0
    r.jz_far("done")  # unchanged scan must not flood the MIDI writer
    _write(r, WEIGHT_BY_CHANNEL, 4)
    _write(r, WEIGHT_LOW_BY_CHANNEL, 5)
    r.mov_a_r(7)
    r.cjne_a(FLAG_SECONDARY, "primary")
    _read(r, PARTNER_BY_CHANNEL)
    r.mov_r_a(6)
    r.lcall(UPDATE_BEND)
    r.ljmp("done")
    r.label("primary")
    r.cjne_a(FLAG_PRIMARY, "done")
    r.lcall(UPDATE_BEND)
    r.label("done")
    _pop(r, saved)
    r.mov_dptr(0x0897)  # displaced stock instruction
    r.ljmp_abs(0x25F9)
    return r.finish()


def build_raw_ratio() -> bytes:
    """floor(128 * companion force / total force), using 16-bit weights."""
    r = Asm(RAW_RATIO)
    _read(r, WEIGHT_BY_CHANNEL)
    r.mov_r_a(4)
    _read(r, WEIGHT_LOW_BY_CHANNEL)
    r.mov_r_a(5)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_load()
    r.mov_r_a(6)
    _read(r, WEIGHT_BY_CHANNEL)
    r.mov_r_a(0)
    _read(r, WEIGHT_LOW_BY_CHANNEL)
    r.mov_r_a(3)
    r.emit(0x48)  # ORL A,R0
    r.rel(0x60, "zero")
    r.mov_a_r(4)
    r.emit(0x4D)
    r.rel(0x60, "full")
    r.mov_a_r(5)
    r.emit(0x2B)
    r.mov_r_a(1)  # denominator low
    r.mov_a_r(4)
    r.emit(0x38)  # ADDC A,R0
    r.mov_r_a(4)  # denominator high
    r.mov_a_r(0)
    r.mov_r_a(5)
    r.mov_a_r(3)
    r.mov_r_a(0)  # numerator low
    r.mov_a_r(5)
    r.mov_r_a(3)  # numerator high
    r.mov_r(2, 0)
    r.mov_r(7, 7)
    r.label("divide")
    r.clr_c()
    for reg in (0, 3):
        r.mov_a_r(reg)
        r.emit(0x33)
        r.mov_r_a(reg)
    r.mov_a_r(2)
    r.emit(0x23)
    r.mov_r_a(2)
    r.mov_a_r(0)
    r.clr_c()
    r.subb_r(1)
    r.mov_r_a(5)
    r.mov_a_r(3)
    r.subb_r(4)
    r.rel(0x40, "next")
    r.mov_r_a(3)
    r.mov_a_r(5)
    r.mov_r_a(0)
    r.mov_a_r(2)
    r.emit(0x44, 1)
    r.mov_r_a(2)
    r.label("next")
    r.emit(0xDF, 0)
    r.fixups.append((len(r.code) - 1, "divide"))
    r.ret()
    r.label("zero")
    r.mov_r(2, 0)
    r.ret()
    r.label("full")
    r.mov_r(2, 128)
    r.ret()
    return r.finish()


def build_reset_member() -> bytes:
    """Reset a newly allocated member's bend when physical Tilt is off."""
    r = Asm(RESET_MEMBER)
    saved = (0xD0, 0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7)
    _push(r, saved)
    r.mov_dptr(GLIDE_RANGE_CODE)
    r.movx_load()
    r.clr_c()
    r.subb(2)
    r.jc_far("done")
    r.clr_c()
    r.subb(24)
    r.jnc_far("done")
    r.mov_dptr(0x093E)
    r.movx_load()
    r.anl(1)
    r.jnz_far("done")
    r.mov_r(4, 64)
    _write(r, 0x0F30, 4)
    r.mov_r(4, 0)
    _write(r, 0x0F00, 4)
    r.mov_a_r(6)
    r.orl(0xE0)
    r.mov_r_a(7)
    r.mov_r(3, 64)
    r.mov_r(5, 0)
    r.mov_dptr(0x04EF)
    r.movx_load()
    r.anl(1)
    r.rel(0x60, "send")
    r.mov_dptr(0x0F20)
    r.movx_load()
    r.rel(0x60, "send")
    r.mov_dptr(0x0F22)
    r.movx_load()
    r.mov_r_a(3)  # center + pad - center = pad
    r.label("send")
    r.mov_dptr(INTERNAL_SEND)
    r.mov_a(1)
    r.movx_store()
    r.lcall(scales.SEND)
    r.mov_dptr(INTERNAL_SEND)
    r.clr_a()
    r.movx_store()
    _pop(r, saved)
    r.ret()
    r.label("done")
    _pop(r, saved)
    r.ret()
    return r.finish()


def build_pressure_send() -> bytes:
    """Intercept the stock two-byte pressure sender, separate from 0x7C67."""
    r = Asm(PRESSURE_SEND)
    r.lcall(PRESSURE)
    r.rel(0x50, "stock")
    r.ret()
    r.label("stock")
    r.mov_dptr(0x088B)  # displaced instruction at 0x7DF4
    r.ljmp_abs(0x7DF7)
    return r.finish()


def build_physical_led() -> bytes:
    """Set the pending physical key's LED to A in stock key-follow mode."""
    r = Asm(PHYSICAL_LED)
    saved = (0xD0, 0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7)
    _push(r, saved)
    r.mov_r_a(5)
    r.mov_dptr(0x035D)
    r.movx_load()
    r.cjne_a(1, "done")
    r.mov_dptr(PENDING_KEY)
    r.movx_load()
    r.mov_r_a(7)
    r.lcall(velocity.STOCK_SET_LED)
    r.label("done")
    _pop(r, saved)
    r.ret()
    return r.finish()


def build_bend_sample() -> bytes:
    """Route either paired key's tilt/pad update to the shared primary voice."""
    r = Asm(BEND_SAMPLE)
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_SECONDARY, "check_primary")
    _read(r, PARTNER_BY_CHANNEL)
    r.mov_r_a(6)
    r.ljmp("paired")
    r.label("check_primary")
    r.cjne_a(FLAG_PRIMARY, "check_released")
    r.ljmp("paired")
    r.label("check_released")
    r.cjne_a(FLAG_PRIMARY_RELEASED, "done")
    r.label("paired")
    # The native tilt paths already cached the uncombined 14-bit reading.
    # Recompute from both caches so a companion sample cannot be discarded.
    r.lcall(UPDATE_BEND)
    r.mov_dptr(GLIDE_CONSUME)
    r.mov_a(1)
    r.movx_store()
    r.emit(0xD3)
    r.ret()
    r.label("done")
    r.clr_c()
    r.ret()
    return r.finish()


def build_apply_glide() -> bytes:
    """Add/subtract CALCULATE's signed bend offset from RAW_BEND bytes."""
    r = Asm(APPLY_GLIDE)
    r.lcall(CALCULATE)
    r.mov_dptr(GLIDE_SIGN)
    r.movx_load()
    r.rel(0x70, "subtract")
    # Positive interval: low-byte addition carries into the seven-bit high byte.
    r.mov_dptr(RAW_BEND_LO)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_dptr(OUT_BEND_LO)
    r.movx_load()
    r.emit(0x2D)        # ADD A,R5
    r.mov_r_a(2)
    r.emit(0x23)        # RL A: the seven-bit low sum's bit 7 becomes bit 0
    r.anl(1)
    r.mov_r_a(4)
    r.mov_a_r(2)
    r.anl(0x7F)
    r.mov_dptr(OUT_BEND_LO)
    r.movx_store()
    r.mov_dptr(RAW_BEND_HI)
    r.movx_load()
    r.mov_r_a(2)
    r.mov_dptr(OUT_BEND_HI)
    r.movx_load()
    r.emit(0x2A)        # ADD A,R2
    r.emit(0x2C)        # ADD A,R4, the saved low carry
    r.cjne_a(0x80, "positive_limit")
    r.mov_a(0x7F)
    r.ljmp("saturate_positive")
    r.label("positive_limit")
    r.rel(0x40, "store_high")  # JC: below 128
    r.label("saturate_positive")
    r.mov_dptr(OUT_BEND_LO)
    r.mov_a(0x7F)
    r.movx_store()
    r.label("store_high")
    r.mov_dptr(OUT_BEND_HI)
    r.movx_store()
    r.ljmp("done")

    r.label("subtract")
    # Negative interval: subtract low data, then borrow from high data.
    r.mov_dptr(RAW_BEND_LO)
    r.movx_load()
    r.mov_r_a(5)
    r.mov_dptr(OUT_BEND_LO)
    r.movx_load()
    r.mov_r_a(2)
    r.mov_a_r(5)
    r.clr_c()
    r.emit(0x9A)        # SUBB A,R2: raw low - glide low
    r.mov_r_a(5)
    r.clr_a()
    r.emit(0x33)        # A = low-byte borrow
    r.mov_r_a(4)
    r.mov_a_r(5)
    r.anl(0x7F)
    r.mov_dptr(OUT_BEND_LO)
    r.movx_store()
    r.mov_dptr(RAW_BEND_HI)
    r.movx_load()
    r.mov_r_a(0)
    r.mov_dptr(OUT_BEND_HI)
    r.movx_load()
    r.mov_r_a(2)
    r.mov_a_r(0)
    r.clr_c()
    r.emit(0x95, 0x02)  # raw high - delta high
    r.rel(0x40, "saturate_negative")
    r.clr_c()
    r.emit(0x9C)        # SUBB A,R4, R4 holds the low-byte borrow
    r.rel(0x50, "negative_limit")
    r.label("saturate_negative")
    r.clr_a()
    r.mov_dptr(OUT_BEND_LO)
    r.movx_store()
    r.ljmp("store_negative_high")
    r.label("negative_limit")
    r.label("store_negative_high")
    r.mov_dptr(OUT_BEND_HI)
    r.movx_store()
    r.label("done")
    r.ret()
    return r.finish()


def build_ratio() -> bytes:
    """R0=second pressure, R1=sum; return floor(128*R0/R1) in R2."""
    r = Asm(RATIO)
    r.mov_r(2, 0)
    r.mov_r(3, 0)   # ninth remainder bit
    r.mov_r(7, 7)
    r.label("divide")
    # Build one quotient bit from the next binary fractional remainder bit.
    r.clr_c()
    r.mov_a_r(0)
    r.emit(0x33)    # RLC A
    r.mov_r_a(0)
    r.mov_a_r(3)
    r.emit(0x33)
    r.mov_r_a(3)
    r.clr_c()
    r.mov_a_r(2)
    r.emit(0x33)
    r.mov_r_a(2)
    r.mov_a_r(3)
    r.rel(0x70, "subtract_high")
    r.mov_a_r(0)
    r.clr_c()
    r.subb_r(1)
    r.rel(0x40, "next")  # borrow: remainder is below denominator
    r.mov_r_a(0)
    r.ljmp("set_bit")
    r.label("subtract_high")
    # With a ninth bit set, the low-byte subtraction necessarily borrows;
    # the wrapped result is the exact value of the 9-bit subtraction.
    r.mov_a_r(0)
    r.clr_c()
    r.subb_r(1)
    r.mov_r_a(0)
    r.mov_r(3, 0)
    r.label("set_bit")
    r.mov_a_r(2)
    r.orl(1)
    r.mov_r_a(2)
    r.label("next")
    r.emit(0xDF, 0)  # DJNZ R7,divide
    r.fixups.append((len(r.code) - 1, "divide"))
    r.ret()
    return r.finish()


def build_calculate() -> bytes:
    """Calculate a signed 14-bit glide offset from the pair's latest pressures."""
    r = Asm(CALCULATE)
    r.mov_dptr(PRIMARY_SCRATCH)
    r.mov_a_r(6)
    r.movx_store()
    # Remember partner channel and both heard pitches.
    _read(r, PARTNER_BY_CHANNEL)
    r.mov_r_a(2)
    r.mov_dptr(BEST_CHANNEL)
    r.mov_a_r(2)
    r.movx_store()
    _read(r, PITCH_BY_CHANNEL)
    r.mov_r_a(4)
    r.mov_dptr(PENDING_NOTE)
    r.mov_a_r(4)
    r.movx_store()
    r.mov_a_r(2)
    r.mov_r_a(6)
    _read(r, PITCH_BY_CHANNEL)
    r.mov_r_a(5)
    r.mov_dptr(PENDING_NOTE)
    r.movx_load()
    r.mov_r_a(4)
    # Pitch 2 - pitch 1 determines sign and interval.
    r.mov_a_r(5)
    r.clr_c()
    r.subb_r(4)
    r.rel(0x40, "negative")
    r.mov_r_a(4)
    r.mov_dptr(GLIDE_SIGN)
    r.clr_a()
    r.movx_store()
    r.ljmp("store_interval")
    r.label("negative")
    r.emit(0xF4, 0x04)  # CPL A; INC A
    r.mov_r_a(4)
    r.mov_dptr(GLIDE_SIGN)
    r.mov_a(1)
    r.movx_store()
    r.label("store_interval")
    r.mov_dptr(BEST_DISTANCE)
    r.mov_a_r(4)
    r.movx_store()

    # A physically released primary means the companion is now the exact target.
    r.mov_dptr(PRIMARY_SCRATCH)
    r.movx_load()
    r.mov_r_a(6)
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_PRIMARY_RELEASED, "pressure_weights")
    r.mov_r(2, 128)
    r.ljmp("lookup")

    r.label("pressure_weights")
    r.lcall(RAW_RATIO)

    r.label("lookup")
    r.mov_dptr(BEST_DISTANCE)
    r.movx_load()
    r.mov_r_a(4)   # semitone interval 0..24
    r.rel(0x60, "zero")
    # The table is the fast path for a 24-semitone receiver. Other configured
    # receiver spans need their own bend magnitude, not a 24-semitone value.
    r.lcall(OUTPUT_SCALE)
    r.rel(0x50, "table_lookup")
    r.ljmp("restore_primary")
    r.label("table_lookup")
    # Byte offset = 2 * (interval * 129 + ratio).
    r.mov_a_r(2)
    r.emit(0x2A)   # ADD A,R2: ratio * 2, preserving ratio 128 carry
    r.mov_r_a(5)
    r.clr_a()
    r.emit(0x33)
    r.mov_r_a(3)   # first high-offset carry
    r.mov_a_r(4)
    r.emit(0x23)   # RL A = interval * 2
    r.emit(0x2D)   # ADD A,R5 (ratio * 2)
    r.mov_r_a(5)
    r.clr_a()
    r.emit(0x33)
    r.mov_r_a(0)   # second high-offset carry
    r.mov_a_r(4)
    r.emit(0x2B, 0x28)  # interval + carry1 + carry2
    r.mov_r_a(3)
    r.mov_a_r(5)
    r.add(BEND_TABLE & 255)
    r.emit(0xF5, 0x82)
    r.mov_a_r(3)
    r.emit(0x34, BEND_TABLE >> 8)
    r.emit(0xF5, 0x83)
    r.clr_a()
    r.movc()
    r.emit(0xC0, 0x82, 0xC0, 0x83)
    r.mov_r_a(5)
    r.mov_dptr(OUT_BEND_HI)
    r.mov_a_r(5)
    r.movx_store()
    r.emit(0xD0, 0x83, 0xD0, 0x82)
    r.inc_dptr()
    r.clr_a()
    r.movc()
    r.mov_dptr(OUT_BEND_LO)
    r.movx_store()
    r.label("restore_primary")
    r.mov_dptr(PRIMARY_SCRATCH)
    r.movx_load()
    r.mov_r_a(6)
    r.ret()
    r.label("zero")
    r.mov_dptr(OUT_BEND_HI)
    r.clr_a()
    r.movx_store()
    r.inc_dptr()
    r.movx_store()
    r.mov_dptr(PRIMARY_SCRATCH)
    r.movx_load()
    r.mov_r_a(6)
    r.ret()
    return r.finish()


def build_output_scale() -> bytes:
    """For spans below 24, compute floor(interval * ratio * 64 / span)."""
    r = Asm(OUTPUT_SCALE)
    # Match the editor: receiver span is max(tilt reference, glide interval).
    r.mov_dptr(TILT_REFERENCE_RANGE)
    r.movx_load()
    r.rel(0x60, "default_reference")
    r.cjne_a(25, "reference_limit")
    r.ljmp("default_reference")
    r.label("reference_limit")
    r.rel(0x40, "reference_ready")
    r.label("default_reference")
    r.mov_a(12)
    r.label("reference_ready")
    r.mov_r_a(3)
    r.mov_dptr(GLIDE_RANGE_CODE)
    r.movx_load()
    r.rel(0x60, "range_ready")
    r.cjne_a(26, "glide_limit")
    r.ljmp("range_ready")
    r.label("glide_limit")
    r.rel(0x50, "range_ready")
    r.dec_a()
    r.mov_r_a(0)
    r.clr_c()
    r.subb_r(3)
    r.rel(0x40, "range_ready")
    r.mov_a_r(0)
    r.mov_r_a(3)
    r.label("range_ready")
    r.mov_a_r(3)
    r.cjne_a(24, "compute")
    r.clr_c()
    r.ret()
    r.label("compute")
    # A 24-bit numerator accommodates 24 * 128 * 64 = 196608.
    r.mov_a_r(2)
    r.emit(0xF5, 0xF0)  # B = pressure ratio
    r.mov_a_r(4)
    r.emit(0xA4)        # MUL AB: interval * ratio
    r.mov_r_a(0)
    r.emit(0xE5, 0xF0)
    r.mov_r_a(1)
    r.mov_r(2, 0)
    r.mov_r(7, 6)
    r.label("shift_numerator")
    r.clr_c()
    for register in (0, 1, 2):
        r.mov_a_r(register)
        r.emit(0x33)
        r.mov_r_a(register)
    r.emit(0xDF, 0)
    r.fixups.append((len(r.code) - 1, "shift_numerator"))
    r.mov_r(4, 0)  # quotient low
    r.mov_r(5, 0)  # quotient high
    r.mov_r(7, 0)  # remainder; always below 2 * span <= 48
    r.mov_r(6, 24)
    r.label("divide")
    r.clr_c()
    for register in (0, 1, 2, 7):
        r.mov_a_r(register)
        r.emit(0x33)
        r.mov_r_a(register)
    r.clr_c()
    r.subb_r(3)
    r.rel(0x40, "zero_bit")
    r.mov_r_a(7)
    r.emit(0xD3)
    r.ljmp("quotient_bit")
    r.label("zero_bit")
    r.clr_c()
    r.label("quotient_bit")
    for register in (4, 5):
        r.mov_a_r(register)
        r.emit(0x33)
        r.mov_r_a(register)
    r.emit(0xDE, 0)
    r.fixups.append((len(r.code) - 1, "divide"))
    # Conventional 16-bit quotient -> MIDI's two seven-bit bytes.
    r.mov_a_r(4)
    r.emit(0x23)
    r.anl(1)
    r.mov_r_a(0)
    r.mov_a_r(5)
    r.emit(0x23, 0x48)
    r.mov_dptr(OUT_BEND_HI)
    r.movx_store()
    r.mov_a_r(4)
    r.anl(127)
    r.mov_dptr(OUT_BEND_LO)
    r.movx_store()
    r.emit(0xD3)
    r.ret()
    return r.finish()


def build_rearm_tilt() -> bytes:
    """Give a recontacting reserved primary a fresh relative-tilt landing."""
    r = Asm(REARM_TILT)
    r.mov_r(4, 0x81)
    _write(r, tilt.XDATA_TILT_BASELINE, 4)
    r.mov_r(4, 64)
    _write(r, tilt.XDATA_TILT_CURRENT, 4)
    r.mov_r(4, 0)
    _write(r, tilt.XDATA_TILT_LOW, 4)
    r.ret()
    return r.finish()


def build_blend_tilt() -> bytes:
    """RAW_BEND = floor(((128-q)*tilt1 + q*tilt2)/128), then mix pad."""
    r = Asm(BLEND_TILT)
    r.mov_dptr(0x093E)
    r.movx_load()
    r.anl(1)
    r.jz_far("center")
    # RAW_RATIO uses BEST_CHANNEL and returns q in R2. Preserve the primary
    # while its scratch registers are reused, with no new persistent XRAM.
    r.emit(0xC0, 0x06)
    _read(r, PARTNER_BY_CHANNEL)
    r.mov_dptr(BEST_CHANNEL)
    r.movx_store()
    _read(r, FLAGS_BY_CHANNEL)
    r.cjne_a(FLAG_PRIMARY_RELEASED, "weights")
    r.mov_r(2, 128)
    r.ljmp("ratio_ready")
    r.label("weights")
    r.lcall(RAW_RATIO)
    r.label("ratio_ready")
    r.emit(0xD0, 0x06, 0xC0, 0x06)
    r.mov_a_r(2)
    r.emit(0xC0, 0xE0)  # companion factor q
    r.mov_a(128)
    r.clr_c()
    r.subb_r(2)
    r.mov_r_a(2)  # primary factor 128-q
    for register in (0, 1, 4, 5):
        r.mov_r(register, 0)
    # Accumulate high-data*factor in R0:R1, low-data*factor in R4:R5.
    # Each total is <=127*128, so no overflow or signed rounding is needed.
    for member in ("primary", "companion"):
        if member == "companion":
            r.emit(0xD0, 0xE0)
            r.mov_r_a(2)
            _read(r, PARTNER_BY_CHANNEL)
            r.mov_r_a(6)
        for base, high, low in ((tilt.XDATA_TILT_CURRENT, 0, 1),
                                (tilt.XDATA_TILT_LOW, 4, 5)):
            _read(r, base)
            r.emit(0x8A, 0xF0, 0xA4)  # B=factor; MUL AB
            r.emit(0x28 + low)        # ADD A,low
            r.mov_r_a(low)
            r.emit(0xE5, 0xF0, 0x38 + high)  # A=B; ADDC A,high
            r.mov_r_a(high)
    # Divide the low-data numerator by 128 and add it to the high-data sum.
    r.mov_a_r(5)
    r.emit(0x23)
    r.anl(1)
    r.mov_r_a(3)
    r.mov_a_r(4)
    r.emit(0x23, 0x4B, 0x29)  # low high*2 | bit7; ADD A,R1
    r.mov_r_a(1)
    r.mov_a_r(0)
    r.emit(0x34, 0)
    r.mov_r_a(0)
    # Encode the resulting raw 14-bit value into MIDI data bytes.
    r.mov_a_r(1)
    r.emit(0x23)
    r.anl(1)
    r.mov_r_a(3)
    r.mov_a_r(0)
    r.emit(0x23, 0x4B)
    r.mov_dptr(RAW_BEND_HI)
    r.movx_store()
    r.mov_a_r(1)
    r.anl(127)
    r.inc_dptr()
    r.movx_store()
    r.emit(0xD0, 0x06)
    r.ljmp("pad")
    r.label("center")
    r.mov_dptr(RAW_BEND_HI)
    r.mov_a(64)
    r.movx_store()
    r.inc_dptr()
    r.clr_a()
    r.movx_store()
    r.label("pad")
    # Add the shared pad once after blending, preserving the cached tilt LSB.
    r.mov_dptr(tilt.XDATA_BEND_FLAGS)
    r.movx_load()
    r.anl(1)
    r.rel(0x60, "done")
    r.mov_dptr(tilt.XDATA_PAD_ACTIVE)
    r.movx_load()
    r.rel(0x60, "done")
    r.emit(0xC0, 0x06)
    r.mov_dptr(RAW_BEND_HI)
    r.movx_load()
    r.mov_r_a(7)
    r.mov_dptr(tilt.XDATA_PAD_OFFSET)
    r.movx_load()
    r.mov_r_a(6)
    r.lcall(tilt.COMBINE)
    r.mov_a_r(7)
    r.mov_dptr(RAW_BEND_HI)
    r.movx_store()
    r.emit(0xD0, 0x06)
    r.label("done")
    r.ret()
    return r.finish()


def build_update_bend() -> bytes:
    """Re-send force-blended tilt plus the current glide and Bend Pad offsets."""
    r = Asm(UPDATE_BEND)
    _push(r, (0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7))
    r.lcall(BLEND_TILT)
    r.lcall(APPLY_GLIDE)
    # Tag the recursive send so SEND returns it directly to the stock/game gate.
    r.mov_dptr(INTERNAL_SEND)
    r.mov_a(1)
    r.movx_store()
    r.mov_a_r(6)
    r.orl(0xE0)
    r.mov_r_a(7)
    r.mov_dptr(OUT_BEND_HI)
    r.movx_load()
    r.mov_r_a(3)
    r.inc_dptr()
    r.movx_load()
    r.mov_r_a(5)
    r.lcall(scales.SEND)
    r.mov_dptr(INTERNAL_SEND)
    r.clr_a()
    r.movx_store()
    _pop(r, (0xE0, 0x82, 0x83, 0xF0, 0, 1, 2, 3, 4, 5, 6, 7))
    r.ret()
    return r.finish()
APPLY_GLIDE = 0x9700


def build_tilt_scale() -> bytes:
    """Preserve tiny tilt amounts: floor(magnitude * amount * reference * 128 / (100 * span))."""
    r = Asm(TILT_SCALE)
    r.emit(0xC0, 0x03)  # preserve the native tilt sender's member channel
    r.mov_dptr(GLIDE_RANGE_CODE)
    r.movx_load()
    r.jz_far("fallback")
    r.cjne_a(26, "glide_limit")
    r.ljmp("fallback")
    r.label("glide_limit")
    r.jnc_far("fallback")
    r.dec_a()
    r.mov_r_a(1)
    r.mov_dptr(TILT_REFERENCE_RANGE)
    r.movx_load()
    r.jz_far("fallback")
    r.cjne_a(25, "reference_limit")
    r.ljmp("fallback")
    r.label("reference_limit")
    r.jnc_far("fallback")
    r.mov_r_a(0)
    r.mov_a_r(1)
    r.clr_c()
    r.subb_r(0)
    r.rel(0x50, "span_ready")
    r.mov_a_r(0)
    r.mov_r_a(1)
    r.label("span_ready")
    r.mov_dptr(TILT_REFERENCE_AMOUNT)
    r.movx_load()
    r.mov_r_a(4)  # gain high
    r.inc_dptr()
    r.movx_load()
    r.mov_r_a(2)  # gain low
    # Reject an invalid sidecar rather than producing an oversized bend.
    r.mov_a_r(0)
    r.emit(0x75, 0xF0, 100, 0xA4)
    r.mov_r_a(5)
    r.mov_a_r(2)
    r.clr_c()
    r.subb_r(5)
    r.mov_r_a(5)
    r.mov_a_r(4)
    r.emit(0x95, 0xF0)
    r.rel(0x40, "valid_amount")
    r.emit(0x4D)
    r.jnz_far("fallback")
    r.label("valid_amount")
    # Dividing by 25 * span lets the numerator use *32 rather than *128.
    r.mov_a_r(1)
    r.emit(0x75, 0xF0, 25, 0xA4, 0xF5, 0x82, 0x85, 0xF0, 0x83)
    r.mov_a_r(7)
    r.emit(0xF5, 0xF0)
    r.mov_a_r(2)
    r.emit(0xA4)
    r.mov_r_a(0)
    r.emit(0xE5, 0xF0)
    r.mov_r_a(1)
    r.mov_a_r(7)
    r.emit(0xF5, 0xF0)
    r.mov_a_r(4)
    r.emit(0xA4, 0x29)
    r.mov_r_a(1)
    r.emit(0xE5, 0xF0, 0x34, 0)
    r.mov_r_a(2)
    r.mov_r(3, 5)
    r.label("shift_numerator")
    r.clr_c()
    for register in (0, 1, 2):
        r.mov_a_r(register)
        r.emit(0x33)
        r.mov_r_a(register)
    r.emit(0xDB, 0)
    r.fixups.append((len(r.code) - 1, "shift_numerator"))
    for register in (4, 5, 6, 7):
        r.mov_r(register, 0)
    r.emit(0x75, 0xF0, 24)
    r.label("divide")
    r.clr_c()
    for register in (0, 1, 2, 7, 6):
        r.mov_a_r(register)
        r.emit(0x33)
        r.mov_r_a(register)
    r.mov_a_r(7)
    r.clr_c()
    r.emit(0x95, 0x82)
    r.mov_r_a(3)
    r.mov_a_r(6)
    r.emit(0x95, 0x83)
    r.rel(0x40, "zero_bit")
    r.mov_r_a(6)
    r.mov_a_r(3)
    r.mov_r_a(7)
    r.emit(0xD3)
    r.ljmp("quotient_bit")
    r.label("zero_bit")
    r.clr_c()
    r.label("quotient_bit")
    for register in (5, 4):
        r.mov_a_r(register)
        r.emit(0x33)
        r.mov_r_a(register)
    r.emit(0xD5, 0xF0, 0)
    r.fixups.append((len(r.code) - 1, "divide"))
    r.mov_a_r(4)
    r.mov_r_a(6)
    r.mov_a_r(5)
    r.mov_r_a(7)
    r.emit(0xD0, 0x03)
    r.ret()
    r.label("fallback")
    r.emit(0xD0, 0x03)
    r.ljmp_abs(tilt.SCALE14)
    return r.finish()


def build_image(base_image: bytes) -> tuple[bytes, bytes]:
    """Layer pressure-glide hooks and data over the completed custom image."""
    if not (scales.CONSUMED_KEY_MARKS + scales.CONSUMED_KEY_MARK_BYTES
            <= PRESSURE_BY_CHANNEL and PRESSURE_BY_CHANNEL + 16 <= KEY_BY_CHANNEL):
        raise ValueError("mapped pressure cache overlaps release bitmap or glide state")
    patched = bytearray(base_image)
    if len(patched) != 0x10000:
        raise ValueError("expected a 64 KiB application image")
    expected_keyon = bytes((0x02, scales.KEYON >> 8, scales.KEYON & 255))
    expected_keyoff = bytes((0x02, scales.KEYOFF >> 8, scales.KEYOFF & 255))
    if bytes(patched[0x4C4F:0x4C52]) != expected_keyon:
        raise ValueError("scale key-on hook differs")
    if bytes(patched[0x62D7:0x62DA]) != expected_keyoff:
        raise ValueError("scale key-off hook differs")
    if bytes(patched[0x7DF4:0x7DF7]) != bytes((0x90, 0x08, 0x8B)):
        raise ValueError("stock two-byte pressure sender differs")
    native_tilt = tilt.build_hires_bend()
    if bytes(patched[tilt.HIRES_BEND:tilt.HIRES_BEND + len(native_tilt)]) != native_tilt:
        raise ValueError("high-resolution tilt helper differs")
    scale_call = bytes((0x12, tilt.SCALE14 >> 8, tilt.SCALE14 & 255))
    if native_tilt.count(scale_call) != 1:
        raise ValueError("high-resolution tilt scaler call is not unique")
    scale_address = tilt.HIRES_BEND + native_tilt.index(scale_call)
    patched[scale_address:scale_address + 3] = bytes((0x12, TILT_SCALE >> 8, TILT_SCALE & 255))
    # The helper is inserted into the scale sender after quantization. Requiring
    # its address in that routine prevents a silently inert pressure table.
    scale_send = scales.build_send(glide_send=PRESSURE)
    if scale_send not in patched[scales.SEND:scales.SEND + len(scale_send)]:
        raise ValueError("scale MIDI sender does not include the pressure-glide hook")
    patched[0x4C4F:0x4C52] = bytes((0x02, KEYON >> 8, KEYON & 255))
    patched[0x62D7:0x62DA] = bytes((0x02, KEYOFF_ROUTINE >> 8, KEYOFF_ROUTINE & 255))
    patched[0x7DF4:0x7DF7] = bytes((0x02, PRESSURE_SEND >> 8, PRESSURE_SEND & 255))
    if patched[0x25F6:0x25F9] != bytes((0x90, 0x08, 0x97)):
        raise ValueError("stock raw sensor scan differs")
    patched[0x25F6:0x25F9] = bytes((0x02, RAW_SCAN >> 8, RAW_SCAN & 255))
    routines = {
        RAW_SCAN: build_raw_scan(), RAW_RATIO: build_raw_ratio(),
        RAW_INIT: build_raw_init(), RESET_MEMBER: build_reset_member(),
        REARM_TILT: build_rearm_tilt(), BLEND_TILT: build_blend_tilt(),
        OUTPUT_PRESSURE: build_output_pressure(), RESEND_PRESSURE: build_resend_pressure(),
        KEYON: build_keyon(), KEYOFF_ROUTINE: build_keyoff(),
        UPDATE_BEND: build_update_bend(), PRESSURE_SAMPLE: build_pressure_sample(),
        BEND_SAMPLE: build_bend_sample(), APPLY_GLIDE: build_apply_glide(),
        CALCULATE: build_calculate(), RATIO: build_ratio(),
        OUTPUT_SCALE: build_output_scale(),
        PRESSURE_SEND: build_pressure_send(),
        PHYSICAL_LED: build_physical_led(),
        TILT_SCALE: build_tilt_scale(),
        PRESSURE: build_pressure_handler(), BEND_TABLE: glide_bend_table(),
    }
    spans = sorted((address, address + len(data)) for address, data in routines.items())
    for (_, end), (start, _) in zip(spans, spans[1:]):
        if end > start:
            raise ValueError(f"pressure-glide regions overlap near 0x{start:04X}")
    for address, data in routines.items():
        if patched[address:address + len(data)] != b"\xff" * len(data):
            raise ValueError(f"pressure-glide region at 0x{address:04X} is occupied")
        patched[address:address + len(data)] = data
    records, _ = extract(velocity.STOCK_SYX)
    return bytes(patched), pack(records_from_image(records, patched))
