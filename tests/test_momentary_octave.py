"""Emitted-image tests for momentary scale-mode black-key octave controls."""

import unittest

from firmware_tools import build_momentary_octave_patch as momentary
from firmware_tools import build_octave_patch as octave
from firmware_tools import build_pressure_glide_patch as glide
from firmware_tools import build_scale_quantizer_patch as scales
from firmware_tools import build_velocity_slider_patch as velocity
from tests.firmware_board import FirmwareBoard


class MomentaryOctaveTests(unittest.TestCase):
    def board(self, *, scale_id=1, base=2, **options):
        b = FirmwareBoard(**options)
        b.enter_selector()
        select_key = scales.WHITE_KEY_INDICES[scale_id]
        b.key_on(select_key)
        b.key_off(select_key)
        b.scan_buttons(30, **{'0': 127})
        b.scan_buttons(30, **{'0': 0})
        self.assertEqual(b.machine.xram[scales.SCALE_STATE], 0)
        b.machine.xram[0x005F] = base
        b.machine.xram[scales.INIT_DONE] = base + 1
        b.wire.clear()
        return b

    @staticmethod
    def rank(b, slot):
        packed = b.machine.xram[momentary.RANKS + slot // 2]
        return packed >> 4 if slot & 1 else packed & 15

    def test_every_black_key_copy_is_a_silent_momentary_modifier(self):
        offsets = {1: -2, 3: -1, 6: 0, 8: 1, 10: 2}
        for key in (1, 3, 6, 8, 10, 13, 15, 18, 20, 22):
            base_key = key if key < 12 else key - 12
            slot = (1, 3, 6, 8, 10).index(base_key) + (5 if key >= 12 else 0)
            with self.subTest(key=key):
                b = self.board(mpe_active=True, pressure_glide_range=0)
                b.key_on(key)
                self.assertEqual(b.machine.xram[0x005F], max(0, min(5, 2 + offsets[base_key])))
                self.assertEqual(self.rank(b, slot), 1)
                self.assertEqual(b.machine.xram[0x00AD], 0)
                self.assertEqual(b.machine.xram[0x0060 + key], 0xFF)
                self.assertEqual(b.wire.messages, [])
                # The stock two-byte pressure path and common expression
                # sender must also stay silent while the modifier is held.
                b.machine.call(0x8247, r7=100, r5=0)
                b.machine.call(0x7C67, r7=0xE0, r5=0, r3=64)
                self.assertEqual(b.wire.messages, [])
                b.key_off(key)
                self.assertEqual(b.machine.xram[0x005F], 2)
                self.assertEqual(b.machine.xram[0x00AD], 0)
                self.assertEqual(b.wire.messages, [])
                b.assert_consistent(self)

    def test_modifier_release_clears_its_key_follow_led(self):
        for key in (1, 3, 6, 8, 10, 13, 15, 18, 20, 22):
            with self.subTest(key=key):
                b = self.board(mpe_active=False, pressure_glide_range=0)
                b.key_on(key)
                baseline = bytes(b.machine.xram[0x0819:0x0839])
                # The stock scan lights the physical key independently of the
                # silent note-on path; reproduce that LED update before release.
                b.machine.call(velocity.STOCK_SET_LED, r7=key, r5=0x7F)
                lit = bytes(b.machine.xram[0x0819:0x0839])
                self.assertNotEqual(lit, baseline)
                b.key_off(key)
                self.assertEqual(bytes(b.machine.xram[0x0819:0x0839]), baseline)
                self.assertEqual(b.wire.messages, [])

    def test_overlap_uses_latest_held_key_and_reveals_previous_keys(self):
        b = self.board(mpe_active=False, pressure_glide_range=0)
        for key, expected in ((1, 0), (13, 0), (3, 1), (18, 2), (10, 4)):
            b.key_on(key)
            self.assertEqual(b.machine.xram[0x005F], expected)
        self.assertEqual(self.rank(b, 0), 1)
        self.assertEqual(self.rank(b, 5), 2)  # mirrored C-sharp is distinct
        self.assertEqual(self.rank(b, 1), 3)
        self.assertEqual(self.rank(b, 7), 4)  # F-sharp is a neutral override
        self.assertEqual(self.rank(b, 4), 5)
        for key, expected in ((10, 2), (18, 1), (3, 0), (13, 0), (1, 2)):
            b.key_off(key)
            self.assertEqual(b.machine.xram[0x005F], expected)
        self.assertEqual(b.machine.xram[momentary.RANKS:momentary.RANKS + 5], bytes(5))
        b.assert_consistent(self)

        b = self.board(mpe_active=False, pressure_glide_range=0)
        b.key_on(1)
        b.key_on(3)
        self.assertEqual(b.machine.xram[0x005F], 1)
        b.key_off(1)  # Releasing an older modifier leaves the latest active.
        self.assertEqual(b.machine.xram[0x005F], 1)
        b.key_off(3)
        self.assertEqual(b.machine.xram[0x005F], 2)

    def test_new_notes_keep_onset_pitch_and_channel_after_modifier_release(self):
        for mpe, members, on_status, off_status in (
                (False, 15, 0x90, 0x80), (True, 8, 0x91, 0x81)):
            with self.subTest(mpe=mpe):
                b = self.board(mpe_active=mpe, mpe_member_channels=members,
                               pressure_glide_range=0)
                b.key_on(1)  # C-sharp: two octaves down from the normal setting.
                channel = b.key_on(0)
                note_on = [m for m in b.wire.messages if m[0] & 0xF0 == 0x90]
                self.assertEqual(len(note_on), 1)
                self.assertEqual(note_on[0][0], on_status)
                sounded = (note_on[0][0] & 15, note_on[0][1])
                b.key_off(1)
                self.assertIn(sounded, b.wire.notes)
                self.assertEqual(b.machine.xram[0x005F], 2)
                b.key_off(0)
                note_off = [m for m in b.wire.messages if m[0] & 0xF0 == 0x80]
                self.assertEqual(note_off, [(off_status | (sounded[0] & 15), sounded[1], 0)])
                self.assertFalse(b.wire.notes)
                self.assertEqual(b.machine.xram[0x00AD], 0)
                b.assert_consistent(self)

    def test_front_octave_buttons_change_hidden_normal_octave_during_hold(self):
        b = self.board(mpe_active=False, pressure_glide_range=0)
        b.key_on(1)
        self.assertEqual(b.machine.xram[0x005F], 0)
        b.scan_buttons(30, **{'7': 127})  # normal octave up
        self.assertEqual(b.machine.xram[scales.INIT_DONE] - 1, 3)
        self.assertEqual(b.machine.xram[0x005F], 1)
        b.scan_buttons(30, **{'7': 0})
        b.scan_buttons(30, **{'6': 127})  # normal octave down
        self.assertEqual(b.machine.xram[scales.INIT_DONE] - 1, 2)
        self.assertEqual(b.machine.xram[0x005F], 0)
        b.key_off(1)
        self.assertEqual(b.machine.xram[0x005F], 2)

    def test_effective_octave_obeys_both_stock_limits(self):
        for base, key, expected in ((0, 1, 0), (0, 3, 0), (5, 8, 5), (5, 10, 5)):
            with self.subTest(base=base, key=key):
                b = self.board(base=base, mpe_active=False, pressure_glide_range=0)
                b.key_on(key)
                self.assertEqual(b.machine.xram[0x005F], expected)
                b.key_off(key)
                self.assertEqual(b.machine.xram[0x005F], base)

    def test_chromatic_black_keys_still_play_and_release_notes(self):
        b = self.board(scale_id=0, mpe_active=False, pressure_glide_range=0)
        b.key_on(1)
        note_on = [m for m in b.wire.messages if m[0] & 0xF0 == 0x90]
        self.assertEqual(len(note_on), 1)
        sounded = note_on[0][1]
        self.assertEqual(b.machine.xram[momentary.RANKS:momentary.RANKS + 5], bytes(5))
        b.key_off(1)
        self.assertIn((0x80, sounded, 0), b.wire.messages)
        self.assertFalse(b.wire.notes)

    def test_selector_entry_clears_modifiers_and_keeps_arrow_transpose(self):
        b = self.board(mpe_active=False, pressure_glide_range=0)
        b.key_on(1)
        self.assertEqual(b.machine.xram[0x005F], 0)
        b.enter_selector()
        self.assertEqual(b.machine.xram[0x005F], 2)
        self.assertEqual(b.machine.xram[momentary.RANKS:momentary.RANKS + 5], bytes(5))
        b.key_off(1)  # consumed release even though selector state cleared the rank
        b.key_on(7)   # black key remains a silent selector input
        b.key_off(7)
        b.scan_buttons(30, **{'7': 127})
        self.assertEqual(b.machine.xram[scales.TRANSPOSE], 13)
        self.assertEqual(b.machine.xram[0x005F], 2)
        self.assertEqual(b.machine.xram[0x00AD], 0)
        b.scan_buttons(30, **{'7': 0})
        b.scan_buttons(30, **{'0': 127})
        b.scan_buttons(30, **{'0': 0})
        self.assertEqual(b.machine.xram[scales.SCALE_STATE], 0)
        self.assertEqual(b.machine.xram[scales.TRANSPOSE], 13)

    def test_pressure_glide_uses_effective_onset_octaves(self):
        for separate, expected_roles in ((False, (glide.FLAG_PRIMARY, glide.FLAG_SECONDARY)),
                                         (True, (glide.FLAG_NORMAL, glide.FLAG_NORMAL))):
            with self.subTest(separate=separate):
                b = self.board(mpe_active=True, pressure_glide_range=24,
                               pressure_glide_separate_octave=separate)
                primary_channel = b.key_on(0)
                b.key_on(1)  # move effective octave down by two
                companion_channel = b.key_on(12)
                self.assertEqual(b.machine.xram[glide.FLAGS_BY_CHANNEL + primary_channel],
                                 expected_roles[0])
                self.assertEqual(b.machine.xram[glide.FLAGS_BY_CHANNEL + companion_channel],
                                 expected_roles[1])
                if separate:
                    self.assertEqual(b.wire.notes,
                                     {(primary_channel, 48), (companion_channel, 36)})
                else:
                    self.assertEqual(b.wire.notes, {(primary_channel, 48)})
                # Modifier release restores the normal octave without ending
                # or reallocating either sounding voice.
                before = set(b.wire.notes)
                b.key_off(1)
                self.assertEqual(b.machine.xram[0x005F], 2)
                self.assertEqual(b.wire.notes, before)
                b.key_off(12)
                b.key_off(0)
                self.assertFalse(b.wire.notes)
                self.assertEqual(b.machine.xram[0x00AD], 0)
                b.assert_consistent(self)

    def test_momentary_changes_never_retrigger_even_when_front_option_is_on(self):
        b = self.board(mpe_active=False, pressure_glide_range=0)
        b.machine.xram[octave.MODE_BYTE] = 0x78
        b.machine.xram[octave.MODE_MARKER] = 100
        b.key_on(0)
        b.wire.clear()
        sounding = set(b.wire.notes)
        b.key_on(1)
        b.key_off(1)
        self.assertEqual(b.wire.messages, [])
        self.assertEqual(b.wire.notes, sounding)
        b.key_off(0)
        self.assertFalse(b.wire.notes)


if __name__ == "__main__":
    unittest.main()
