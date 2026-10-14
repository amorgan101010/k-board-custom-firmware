"""Octave refresh gating against emitted firmware and stock note routines."""

import unittest

from firmware_tools import build_octave_patch as octave
from firmware_tools import build_pressure_glide_patch as glide
from firmware_tools import build_scale_quantizer_patch as scales
from tests.firmware_board import FirmwareBoard


class OctavePatchTests(unittest.TestCase):
    def run_refresh(self, board, enabled, next_octave=None):
        m = board.machine
        m.xram[octave.MODE_BYTE] = 0x70 | (octave.RETRIGGER_BIT if enabled else 0)
        if next_octave is not None:
            m.a = next_octave
        m.call(octave.OCTAVE_DISPATCH)

    def test_held_chord_and_repeated_octave_changes_keep_original_notes(self):
        for mpe in (False, True):
            with self.subTest(mpe=mpe):
                b = FirmwareBoard(members=2 if mpe else 15, mpe_active=mpe,
                                  pressure_glide_range=0)
                b.machine.xram[0x5F] = 2
                b.key_on(3)
                b.key_on(8)
                original = [msg for msg in b.wire.messages if msg[0] & 0xF0 == 0x90]
                self.assertEqual(len(original), 2)
                state_before = bytes(b.machine.xram[0x00AD:0x00AE])
                channels = [b.machine.xram[0x0060 + key] for key in (3, 8)]
                notes = [b.machine.xram[0x02BB + key] for key in (3, 8)]
                sounding_before = set(b.wire.notes)
                b.wire.clear()

                for shift in (3, 4, 5, 4, 2):
                    self.run_refresh(b, False, shift)
                    self.assertEqual(b.wire.messages, [])
                    self.assertEqual(b.machine.xram[0x5F], shift)
                    self.assertEqual(b.machine.xram[0x0259], 0)
                    self.assertEqual(bytes(b.machine.xram[0x00AD:0x00AE]), state_before)
                    self.assertEqual([b.machine.xram[0x0060 + key] for key in (3, 8)], channels)
                    self.assertEqual(b.machine.xram[0x02BB + 3], notes[0])
                    self.assertEqual(b.machine.xram[0x02BB + 8], notes[1])
                    self.assertEqual(b.wire.notes, sounding_before)

                b.key_off(8)
                b.key_off(3)
                releases = [m for m in b.wire.messages if m[0] & 0xF0 == 0x80]
                self.assertEqual(releases, [(0x80 | channels[1], notes[1], 0),
                                            (0x80 | channels[0], notes[0], 0)])
                self.assertFalse(b.wire.notes)
                self.assertEqual(b.machine.xram[0x00AD], 0)

                # Once released, the next allocation uses the latest octave.
                b.machine.xram[0x5F] = 5
                b.key_on(3)
                next_on = [m for m in b.wire.messages if m[0] & 0xF0 == 0x90][-1]
                self.assertEqual(next_on[1], notes[0] + 36)
                if mpe:
                    self.assertIn(next_on[0] & 15, channels)
                b.key_off(3)
                b.assert_consistent(self)

    def test_checked_option_calls_the_stock_refresh_helper(self):
        for enabled, expected in ((False, 0), (True, 1)):
            with self.subTest(enabled=enabled):
                b = FirmwareBoard(mpe_active=False, pressure_glide_range=0)
                calls = []
                b.machine.stubs[0x600E] = lambda m: calls.append(m.pc)
                self.run_refresh(b, enabled)
                self.assertEqual(len(calls), expected)
                self.assertEqual(b.machine.sp, 0x51)

    def test_factory_stock_mode_byte_defaults_to_future_notes_only(self):
        b = FirmwareBoard(mpe_active=False, pressure_glide_range=0)
        b.machine.xram[octave.MODE_BYTE] = 0x7F
        b.machine.xram[octave.MODE_MARKER] = 127
        calls = []
        b.machine.stubs[0x600E] = lambda m: calls.append(m.pc)
        b.machine.a = 3
        b.machine.call(octave.OCTAVE_DISPATCH)
        self.assertEqual(calls, [])
        self.assertEqual(b.machine.xram[0x5F], 3)
        self.assertEqual(b.machine.xram[octave.MODE_BYTE], 0x7F)

    def test_checked_option_preserves_stock_octave_state_and_mode_byte(self):
        b = FirmwareBoard(mpe_active=False, pressure_glide_range=0)
        b.machine.xram[octave.MODE_BYTE] = 0x78
        b.machine.xram[0x5F] = 2
        b.key_on(3)
        b.wire.clear()
        b.scan_buttons(80, **{'7': 127})
        self.assertEqual(b.machine.xram[0x5F], 3)
        self.assertEqual(b.machine.xram[octave.MODE_BYTE], 0x78)
        self.assertEqual(b.machine.xram[0x02BE], 51)
        self.assertEqual(b.wire.messages, [])
        b.key_off(3)
        self.assertEqual(b.wire.messages, [(0x80, 51, 0)])

    def test_checked_button_flow_matches_unpatched_stock_call_sequence(self):
        patched = FirmwareBoard(mpe_active=True, pressure_glide_range=0)
        stock_path = FirmwareBoard(mpe_active=True, pressure_glide_range=0)
        for address in octave.HOOKS:
            stock_path.machine.code[address:address + 3] = bytes.fromhex("12 60 0e")
        patched.machine.xram[octave.MODE_BYTE] = 0x78
        patched.machine.xram[0x5F] = 2
        stock_path.machine.xram[0x5F] = 2
        for board in (patched, stock_path):
            board.key_on(3)
            board.wire.clear()
            board.scan_buttons(80, **{'7': 127})
        self.assertEqual(patched.wire.messages, stock_path.wire.messages)
        self.assertEqual(patched.machine.xram[0x5F], stock_path.machine.xram[0x5F])
        self.assertEqual(patched.machine.xram[0x00AD], stock_path.machine.xram[0x00AD])
        self.assertEqual(patched.machine.xram[0x02BE], stock_path.machine.xram[0x02BE])
        for board in (patched, stock_path):
            board.key_off(3)
        self.assertEqual(patched.wire.messages, stock_path.wire.messages)
        self.assertEqual(patched.machine.xram[0x00AD], stock_path.machine.xram[0x00AD])

    def test_real_button_path_keeps_stock_octave_limits(self):
        b = FirmwareBoard(mpe_active=False, pressure_glide_range=0)
        b.machine.xram[0x5F] = 0
        b.scan_buttons(100, **{'6': 127})
        self.assertEqual(b.machine.xram[0x5F], 0)
        b.scan_buttons(50, **{'6': 0})
        for expected in range(1, 6):
            b.scan_buttons(40, **{'7': 127})
            self.assertEqual(b.machine.xram[0x5F], expected)
            b.scan_buttons(30, **{'7': 0})
        b.scan_buttons(40, **{'7': 127})
        self.assertEqual(b.machine.xram[0x5F], 5)

    def test_scale_selector_still_uses_octave_arrows_for_semitones(self):
        b = FirmwareBoard(mpe_active=False, pressure_glide_range=0)
        b.machine.xram[0x5F] = 2
        b.enter_selector()
        b.wire.clear()
        b.scan_buttons(30, **{'7': 127})
        self.assertEqual(b.machine.xram[scales.TRANSPOSE], 13)
        self.assertEqual(b.machine.xram[0x5F], 2)
        self.assertEqual(b.wire.messages, [])

    def test_glide_pair_keeps_original_voice_and_release_order(self):
        b = FirmwareBoard(mpe_active=True, pressure_glide_range=12)
        b.machine.xram[0x5F] = 2
        b.key_on(0)
        b.key_on(12)
        audible_on = [m for m in b.wire.messages if m[0] & 0xF0 == 0x90]
        self.assertEqual(len(audible_on), 1)
        host_channel, host_note = audible_on[0][0] & 15, audible_on[0][1]
        b.wire.clear()
        for shift in (3, 4, 5):
            self.run_refresh(b, False, shift)
        self.assertEqual(b.wire.messages, [])
        b.key_off(0)  # Deferred while companion remains held.
        self.assertFalse(any(m[0] & 0xF0 == 0x80 for m in b.wire.messages))
        b.key_off(12)
        releases = [m for m in b.wire.messages if m[0] & 0xF0 == 0x80]
        self.assertEqual(releases, [(0x80 | host_channel, host_note, 0)])
        self.assertFalse(b.wire.notes)
        b.assert_consistent(self)

    def test_separate_octave_option_turns_shifted_glide_target_into_second_voice(self):
        paired = FirmwareBoard(mpe_active=True, pressure_glide_range=24)
        split = FirmwareBoard(mpe_active=True, pressure_glide_range=24,
                              pressure_glide_separate_octave=True)
        for board in (paired, split):
            board.machine.xram[0x005F] = 2
            board.key_on(0)
            board.wire.clear()
            board.machine.a = 3
            board.machine.call(octave.OCTAVE_DISPATCH)
            board.key_on(1)

        paired_on = [m for m in paired.wire.messages if m[0] & 0xF0 == 0x90]
        self.assertEqual(len(paired_on), 0)
        primary = paired.machine.xram[0x0060]
        secondary = paired.machine.xram[0x0061]
        self.assertEqual(paired.machine.xram[glide.FLAGS_BY_CHANNEL + primary],
                         glide.FLAG_PRIMARY)
        self.assertEqual(paired.machine.xram[glide.FLAGS_BY_CHANNEL + secondary],
                         glide.FLAG_SECONDARY)
        self.assertEqual(paired.machine.xram[glide.PITCH_BY_CHANNEL + secondary],
                         paired.machine.xram[glide.PITCH_BY_CHANNEL + primary] + 13)
        self.assertEqual(paired.wire.notes, {(primary, paired.machine.xram[glide.PITCH_BY_CHANNEL + primary])})

        split_on = [m for m in split.wire.messages if m[0] & 0xF0 == 0x90]
        self.assertEqual(len(split_on), 1)
        self.assertEqual(split_on[0][1], 61)
        split_primary = split.machine.xram[0x0060]
        split_secondary = split.machine.xram[0x0061]
        self.assertNotEqual(split_primary, split_secondary)
        self.assertEqual(split.machine.xram[glide.FLAGS_BY_CHANNEL + split_primary],
                         glide.FLAG_NORMAL)
        self.assertEqual(split.machine.xram[glide.FLAGS_BY_CHANNEL + split_secondary],
                         glide.FLAG_NORMAL)
        self.assertEqual(split.wire.notes, {(split_primary, 48), (split_secondary, 61)})

        for board in (paired, split):
            board.key_off(1)
            board.key_off(0)
            self.assertFalse(board.wire.notes)
            board.assert_consistent(self)

    def test_separate_octave_option_keeps_same_octave_glides_paired(self):
        b = FirmwareBoard(mpe_active=True, pressure_glide_range=24,
                          pressure_glide_separate_octave=True)
        b.machine.xram[0x005F] = 2
        b.key_on(0)
        b.key_on(1)
        self.assertEqual([m for m in b.wire.messages if m[0] & 0xF0 == 0x90],
                         [(0x91, 48, 127)])
        self.assertEqual(b.machine.xram[glide.FLAGS_BY_CHANNEL + b.machine.xram[0x0060]],
                         glide.FLAG_PRIMARY)
        self.assertEqual(b.machine.xram[glide.FLAGS_BY_CHANNEL + b.machine.xram[0x0061]],
                         glide.FLAG_SECONDARY)
        b.key_off(1)
        b.key_off(0)
        b.assert_consistent(self)

    def test_glide_pair_retains_onset_octave_after_companion_release(self):
        b = FirmwareBoard(mpe_active=True, pressure_glide_range=24,
                          pressure_glide_separate_octave=True)
        b.machine.xram[0x005F] = 2
        b.key_on(0)
        b.key_on(1)
        b.machine.a = 3
        b.machine.call(octave.OCTAVE_DISPATCH)
        b.key_off(1)  # primary becomes a normal voice again
        b.wire.clear()
        b.key_on(2)
        note_ons = [m for m in b.wire.messages if m[0] & 0xF0 == 0x90]
        self.assertEqual(note_ons, [(0x93, 62, 127)])
        primary = b.machine.xram[0x0060]
        shifted = b.machine.xram[0x0062]
        self.assertNotEqual(primary, shifted)
        self.assertEqual(b.machine.xram[glide.FLAGS_BY_CHANNEL + primary],
                         glide.FLAG_NORMAL)
        self.assertEqual(b.machine.xram[glide.PARTNER_BY_CHANNEL + primary], 2)
        self.assertEqual(b.wire.notes, {(primary, 48), (shifted, 62)})
        b.key_off(2)
        b.key_off(0)
        self.assertFalse(b.wire.notes)
        b.assert_consistent(self)

    def test_stock_octave_call_sites_are_the_only_modified_stock_bytes(self):
        image = __import__("tests.firmware_board", fromlist=["complete_image"]).complete_image()[0]
        self.assertEqual(image[0x5FEC:0x5FEF],
                         bytes((0x12, octave.OCTAVE_DISPATCH >> 8,
                                octave.OCTAVE_DISPATCH & 255)))
        self.assertEqual(image[0x6001:0x6004],
                         bytes((0x12, octave.OCTAVE_DISPATCH >> 8,
                                octave.OCTAVE_DISPATCH & 255)))


if __name__ == "__main__":
    unittest.main()
