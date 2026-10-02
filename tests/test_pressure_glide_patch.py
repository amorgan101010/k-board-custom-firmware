"""Exercise pressure-weighted glide routines in the project 8051 interpreter."""

from __future__ import annotations

import unittest

from firmware_tools import build_custom_firmware as custom
from firmware_tools import build_cyclone_game_patch as cyclone
from firmware_tools import build_pressure_glide_patch as glide
from firmware_tools import build_scale_quantizer_patch as scales
from firmware_tools.mcs51 import Machine
from kboard_protocol import DEFAULT_PROFILE, build_sysex, _decode_7bit


class PressureGlideTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, _ = custom.build_image()

    def test_glide_table_covers_full_range_and_is_monotonic(self):
        table = glide.glide_bend_table()
        self.assertEqual(len(table), 25 * 129 * 2)
        for interval in range(25):
            values = [
                table[(interval * 129 + ratio) * 2] * 128
                + table[(interval * 129 + ratio) * 2 + 1]
                for ratio in range(129)
            ]
            self.assertEqual(values, sorted(values))
        self.assertEqual(table[-2:], bytes((64, 0)))

    def _paired_machine(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[0x0351] = 1
        machine.xram[0x093E] = 3
        machine.xram[glide.GLIDE_RANGE_CODE] = 25
        machine.xram[0x0060] = 1
        machine.xram[0x0061] = 2
        machine.xram[glide.PENDING_KEY] = 0
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: True
        machine.call(0x7EF9, r7=60, r5=100, r3=1)
        machine.xram[glide.PENDING_KEY] = 1
        machine.call(0x7EF9, r7=64, r5=100, r3=2)
        return machine

    def test_second_note_joins_nearest_voice_and_stays_off_synth(self):
        machine = self._paired_machine()
        self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1], glide.FLAG_PRIMARY)
        self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 2], glide.FLAG_SECONDARY)
        self.assertEqual(machine.xram[glide.PARTNER_BY_CHANNEL + 1], 2)
        self.assertEqual(machine.xram[glide.PARTNER_BY_CHANNEL + 2], 1)
        self.assertEqual(machine.cy, 1)

    def test_pair_joins_closest_of_multiple_held_voices(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[0x0351] = 1
        machine.xram[glide.GLIDE_RANGE_CODE] = 1  # build an unpaired chord
        for channel, pitch in ((1, 60), (2, 63), (3, 69)):
            machine.xram[glide.PENDING_KEY] = channel - 1
            machine.call(glide.PRESSURE, r7=0x90 | channel, r5=pitch, r3=100)
        machine.xram[glide.GLIDE_RANGE_CODE] = 7  # six-semitone pairing window
        machine.xram[glide.PENDING_KEY] = 3
        machine.call(glide.PRESSURE, r7=0x94, r5=64, r3=100)
        self.assertEqual(machine.xram[glide.PARTNER_BY_CHANNEL + 4], 2)
        for channel in (1, 3):
            self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + channel], glide.FLAG_NORMAL)

    def _scan(self, machine, key, coarse, fraction=0):
        machine.iram[0x36] = key
        machine.iram[0x4D] = 30
        machine.xram[0x034C] = 100
        machine.xram[0x7A + key] = coarse
        word = coarse * 128 + fraction
        machine.iram[0x34] = word >> 8
        machine.iram[0x35] = word & 255
        machine.iram[0x2F] = 0
        machine.iram[0x30] = 0
        machine.stubs[0x25F9] = lambda m: True
        machine.call(glide.RAW_SCAN)

    def _weight(self, machine, channel):
        return (machine.xram[glide.WEIGHT_BY_CHANNEL + channel] * 256
                + machine.xram[glide.WEIGHT_LOW_BY_CHANNEL + channel])

    def test_sensor_scan_seeds_note_on_and_midi_pressure_cannot_change_weight(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[0x0351] = 1
        machine.xram[glide.GLIDE_RANGE_CODE] = 25
        machine.xram[0x0060] = 255
        self._scan(machine, 0, 43, 67)
        machine.xram[glide.PENDING_KEY] = 0
        machine.call(glide.PRESSURE, r7=0x91, r5=60, r3=100)
        self.assertEqual(self._weight(machine, 1), 13 * 128 + 67)
        for pressure in (0, 19, 76, 127):
            machine.call(glide.PRESSURE, r7=0xD1, r5=pressure)
            self.assertEqual(self._weight(machine, 1), 13 * 128 + 67)

    def test_pending_scan_for_another_key_cannot_seed_reused_channel(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[0x0351] = 1
        machine.xram[0x0060] = 255
        self._scan(machine, 0, 55)
        machine.xram[glide.PENDING_KEY] = 1
        machine.call(glide.PRESSURE, r7=0x91, r5=60, r3=100)
        self.assertEqual(self._weight(machine, 1), 0)

    def test_scale_sender_consumes_companion_note_off(self):
        machine = self._paired_machine()
        sent = []
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3))) or True
        machine.call(scales.SEND, r7=0x82, r5=64, r3=0)
        self.assertEqual(sent, [])
        machine.call(scales.SEND, r7=0x81, r5=60, r3=0)
        self.assertEqual(sent, [(0x81, 60, 0)])

    def test_sensor_force_reaches_zero_at_release_boundary(self):
        machine = self._paired_machine()
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: True
        for coarse, fraction in ((90, 35), (31, 127), (30, 1), (30, 0), (29, 127), (0, 0)):
            self._scan(machine, 0, coarse, fraction)
            self.assertEqual(self._weight(machine, 1),
                             max(0, (coarse - 30) * 128 + fraction))

    def test_raw_scan_preserves_stock_registers_and_displaced_dptr(self):
        machine = self._paired_machine()
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: True
        for register in range(8):
            machine.set_r(register, register + 100)
        machine.a = 211
        machine.b = 177
        machine.cy = 1
        self._scan(machine, 0, 61, 43)
        self.assertEqual([machine.r(i) for i in range(8)], list(range(100, 108)))
        self.assertEqual(machine.a, 211)
        self.assertEqual(machine.b, 177)
        self.assertEqual(machine.cy, 1)
        self.assertEqual(machine.dptr, 0x0897)

    def test_force_sweep_produces_many_intermediate_pitches_independent_of_sensitivity(self):
        outputs = []
        for gain in (60, 185):
            machine = self._paired_machine()
            sent = []
            machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
                (m.r(7), m.r(5), m.r(3))) or True
            machine.xram[0x0364:0x0366] = gain.to_bytes(2, 'big')
            machine.xram[0x0F31] = 64
            self._scan(machine, 0, 70)
            bends = []
            for force in range(30 * 128, 100 * 128, 32):
                self._scan(machine, 1, force >> 7, force & 127)
                _, low, high = sent[-1]
                bends.append(high * 128 + low)
            self.assertEqual(bends, sorted(bends))
            self.assertGreater(len(set(bends)), 70)
            outputs.append(bends)
        self.assertEqual(*outputs)

    def test_sixteen_bit_ratio_matches_integer_fraction_including_low_bytes(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[glide.BEST_CHANNEL] = 2
        for primary, secondary in ((1, 1), (1, 0), (0, 1), (0, 0),
                                    (16383, 16383), (1, 16383), (16383, 1),
                                    (1793, 2559), (128, 129), (255, 256)):
            for channel, weight in ((1, primary), (2, secondary)):
                machine.xram[glide.WEIGHT_BY_CHANNEL + channel] = weight >> 8
                machine.xram[glide.WEIGHT_LOW_BY_CHANNEL + channel] = weight & 255
            machine.call(glide.RAW_RATIO, r6=1)
            expected = 128 * secondary // (primary + secondary) if primary + secondary else 0
            self.assertEqual(machine.r(2), expected)

    def test_force_fraction_uses_larger_sensor_and_nonunity_gain_keeps_gate_origin(self):
        machine = self._paired_machine()
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: True
        self._scan(machine, 0, 50, 1)
        machine.iram[0x2F] = 26
        machine.iram[0x30] = 43
        machine.call(glide.RAW_SCAN)
        self.assertEqual(self._weight(machine, 1), 20 * 128 + 43)
        machine.xram[0x034C] = 80
        machine.call(glide.RAW_SCAN)
        self.assertEqual(self._weight(machine, 1), 20 * 128)

    def test_scan_force_and_real_stock_cutoff_agree_without_pressure_jump(self):
        for raw in (36, 33, 31, 30, 29):
            with self.subTest(raw=raw):
                machine = self._paired_machine()
                sent = []
                machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
                    (m.r(7), m.r(5), m.r(3))) or True
                machine.stubs[0x313D] = lambda m: True
                machine.stubs[0x7FF3] = lambda m: True
                machine.stubs[0x6C72] = lambda m: m.set_r(7, 64) or True
                machine.stubs[0x5BCB] = lambda m: m.set_r(7, 0) or True
                machine.xram[0x033B:0x033D] = bytes((0x2E, 0x3D))
                machine.xram[0x0364:0x0366] = bytes((0, 60))
                machine.xram[0x0366] = 127
                machine.xram[0x09C1:0x09C3] = bytes((0, 0xDA))
                machine.xram[0x00DA] = 2
                machine.xram[0x0044] = 64
                machine.xram[0x0287] = raw
                machine.xram[0x00F6] = 99
                machine.xram[0x00AD] = 2
                machine.xram[0x0079] = 1
                machine.xram[0x025A] = 1
                machine.xram[0x093E] = 3
                machine.xram[0x0F31] = 64
                machine.iram[0x4E] = 35
                machine.iram[0x4C] = 100
                self._scan(machine, 1, 70)
                self._scan(machine, 0, raw)
                before = [msg for msg in sent if msg[0] == 0xE1][-1]
                machine.call(0x2625, dptr=0x0044)
                self.assertEqual(self._weight(machine, 1), max(0, raw - 30) * 128)
                after = [msg for msg in sent if msg[0] == 0xE1][-1]
                self.assertEqual(before, after)
                self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1],
                                 glide.FLAG_PRIMARY_RELEASED if raw < 30 else glide.FLAG_PRIMARY)

    def test_unchanged_scan_does_not_resend_bend(self):
        machine = self._paired_machine()
        sent = []
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3))) or True
        self._scan(machine, 0, 70, 43)
        count = len(sent)
        self._scan(machine, 0, 70, 43)
        self.assertEqual(len(sent), count)

    def test_pressure_only_glide_uses_center_instead_of_stale_native_tilt(self):
        for stale in (0, 50, 64, 80, 127):
            machine = self._paired_machine()
            machine.xram[0x093E] = 2
            machine.xram[0x0F31] = stale
            machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 10
            machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 10
            sent = []
            machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
                (m.r(7), m.r(5), m.r(3))) or True
            machine.call(glide.UPDATE_BEND, r6=1)
            _, low, high = sent[-1]
            self.assertEqual(high * 128 + low, 8192 + 683)

    def test_new_pressure_only_member_resets_receiver_before_note_on(self):
        for buttons, range_code, expected_reset in ((2, 25, True), (3, 25, False),
                                                    (2, 1, False)):
            machine = Machine(self.image)
            machine.sp = 0x51
            machine.xram[0x0351] = 1
            machine.xram[0x093E] = buttons
            machine.xram[glide.GLIDE_RANGE_CODE] = range_code
            machine.xram[0x0F31] = 127
            machine.xram[0x0F01] = 127
            sent = []
            machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
                (m.r(7), m.r(5), m.r(3))) or True
            machine.call(scales.SEND, r7=0x91, r5=60, r3=100)
            self.assertEqual(sent, [(0xE1, 0, 64), (0x91, 60, 100)]
                             if expected_reset else [(0x91, 60, 100)])

    def test_pressure_only_member_reset_retains_combined_pad_offset(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[0x0351] = 1
        machine.xram[0x093E] = 2
        machine.xram[glide.GLIDE_RANGE_CODE] = 25
        machine.xram[0x04EF] = 1
        machine.xram[0x0F20] = 1
        machine.xram[0x0F22] = 80
        sent = []
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3))) or True
        machine.call(scales.SEND, r7=0x91, r5=60, r3=100)
        self.assertEqual(sent, [(0xE1, 0, 80), (0x91, 60, 100)])

    def test_pressure_only_stock_allocator_reuses_channels_without_wrong_single_notes(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        profile = DEFAULT_PROFILE | {'mpe_active': True, 'pressure_cc': -1,
                                     'mpe_member_channels': 15, 'pressure_glide_range': 24}
        preset = _decode_7bit(build_sysex(profile)[8:-1])[10:480]
        machine.xram[0x0347:0x051D] = preset
        machine.xram[0x0060:0x0079] = b'\xff' * 25
        machine.xram[0x0276:0x0286] = b'\xff' * 16
        machine.xram[0x093E] = 2  # Pressure on, Tilt off, as reported
        machine.xram[0x033B:0x033D] = bytes((0x2E, 0x3D))
        machine.stubs[0x313D] = lambda m: True  # drained USB writer
        machine.stubs[glide.velocity.STOCK_SET_LED] = lambda m: True
        messages = []
        def trace(m, pc, _op):
            if pc == 0x7C6A:
                messages.append((m.r(7), m.r(5), m.r(3)))
        machine.trace = trace
        bends = {}
        for cycle in range(12):
            first = len(messages)
            for key in (0, 12):
                self._scan(machine, key, 70)
                machine.call(0x4C4F, r7=key)
            self._scan(machine, 0, 31)
            for key in ((0, 12) if cycle % 2 == 0 else (12, 0)):
                self._scan(machine, key, 0)
                machine.call(0x62D7, r7=key)
            self._scan(machine, 5, 70)
            machine.call(0x4C4F, r7=5)
            self._scan(machine, 5, 0)
            machine.call(0x62D7, r7=5)
            note_ons = []
            for status, low, high in messages[first:]:
                channel = status & 15
                if status & 0xF0 == 0xE0:
                    bends[channel] = high * 128 + low
                if status & 0xF0 == 0x90:
                    self.assertEqual(bends[channel], 8192)
                    note_ons.append(low)
            self.assertEqual(note_ons, [24, 29])
            self.assertEqual(machine.xram[0x00AD], 0)
            self.assertEqual(machine.xram[0x0060:0x0079], b'\xff' * 25)

    def test_deferred_primary_release_clears_only_its_physical_led(self):
        for led_mode in (0, 1):
            machine = self._paired_machine()
            machine.xram[0x035D] = led_mode
            machine.xram[0x0F31] = 64
            machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: True
            leds = []
            machine.stubs[glide.velocity.STOCK_SET_LED] = lambda m: leds.append(
                (m.r(7), m.r(5))) or True
            machine.call(0x62D7, r7=0)
            self.assertEqual(leds, [(0, 0)] if led_mode == 1 else [])
            self.assertEqual(machine.xram[0x0060], 1)
            self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1],
                             glide.FLAG_PRIMARY_RELEASED)
            machine.call(glide.KEYON, r7=0)
            self.assertEqual(leds, [(0, 0), (0, 255)] if led_mode == 1 else [])
            self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1],
                             glide.FLAG_PRIMARY)

    def test_primary_release_is_deferred_in_either_release_order(self):
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                machine = self._paired_machine()
                machine.xram[0x0060] = 1
                machine.xram[0x0061] = 2
                released = []
                machine.stubs[0x62DA] = lambda m: released.append(m.r(7)) or True
                for key in order:
                    machine.call(0x62D7, r7=key)
                self.assertEqual(len(released), 2)
                self.assertEqual(set(released), {0, 1})
                self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1], glide.FLAG_FREE)
                self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 2], glide.FLAG_FREE)

    def test_real_stock_release_frees_channels_and_sends_one_audible_note_off(self):
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                machine = self._paired_machine()
                sent = []
                machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
                    (m.r(7), m.r(5), m.r(3))) or True
                machine.stubs[0x313D] = lambda m: True  # drained USB writer
                machine.xram[0x00AD] = 2
                machine.xram[0x02BB] = 60
                machine.xram[0x02BC] = 64
                machine.xram[0x093E] = 3  # physical Tilt and Pressure enabled
                for channel in (1, 2):
                    machine.xram[0x0F30 + channel] = 64
                machine.call(0x62D7, r7=order[0])
                self.assertFalse(any(msg[0] & 0xF0 == 0x80 for msg in sent))
                if order[0] == 0:
                    self.assertEqual(machine.xram[0x0060], 1)
                    self.assertEqual(machine.xram[0x00AD], 2)
                else:
                    self.assertEqual(machine.xram[0x0061], 0xFF)
                    self.assertEqual(machine.xram[0x00AD], 1)
                machine.call(0x62D7, r7=order[1])
                self.assertEqual([msg for msg in sent if msg[0] & 0xF0 == 0x80],
                                 [(0x81, 60, 0)])
                self.assertEqual(machine.xram[0x00AD], 0)
                self.assertEqual(machine.xram[0x0060:0x0062], bytes((0xFF, 0xFF)))
                for channel in (1, 2):
                    self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + channel],
                                     glide.FLAG_FREE)

    def test_repressing_released_primary_reuses_its_reserved_voice(self):
        machine = self._paired_machine()
        sent = []
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3))) or True
        machine.stubs[0x313D] = lambda m: True
        machine.xram[0x00AD] = 2
        machine.xram[0x02BB] = 60
        machine.xram[0x02BC] = 64
        machine.xram[0x093E] = 3
        machine.xram[0x0F31] = 64
        machine.call(0x62D7, r7=0)
        ordinary_keyon = []
        machine.stubs[scales.KEYON] = lambda m: ordinary_keyon.append(m.r(7)) or True
        machine.call(0x4C4F, r7=0)
        self.assertEqual(ordinary_keyon, [])
        self.assertEqual(machine.xram[0x0060], 1)
        self.assertEqual(machine.xram[0x00AD], 2)
        self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1], glide.FLAG_PRIMARY)
        self._scan(machine, 0, 40)
        self.assertEqual(self._weight(machine, 1), 10 * 128)
        for key in (0, 1):
            machine.call(0x62D7, r7=key)
        self.assertEqual(machine.xram[0x00AD], 0)
        self.assertEqual(machine.xram[0x0060:0x0062], bytes((0xFF, 0xFF)))
        self.assertEqual([msg for msg in sent if msg[0] & 0xF0 == 0x80], [(0x81, 60, 0)])

    def test_releasing_primary_immediately_reaches_companion_pitch(self):
        machine = self._paired_machine()
        sent = []
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3))) or True
        machine.xram[0x0F30 + 1] = 64
        machine.xram[0x0F00 + 1] = 0
        machine.call(0x62D7, r7=0)
        status, lsb, msb = sent[-1]
        self.assertEqual(status, 0xE1)
        self.assertEqual((msb - 64) * 128 + lsb, 4 * 8192 // 24)
        self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1],
                         glide.FLAG_PRIMARY_RELEASED)

    def test_glide_offset_tracks_negative_pitch_intervals(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[glide.GLIDE_RANGE_CODE] = 25
        machine.xram[glide.PARTNER_BY_CHANNEL + 1] = 2
        machine.xram[glide.PITCH_BY_CHANNEL + 1] = 64
        machine.xram[glide.PITCH_BY_CHANNEL + 2] = 60
        machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 20
        machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 10
        machine.xram[glide.RAW_BEND_HI] = 64
        machine.xram[glide.RAW_BEND_LO] = 0
        machine.call(glide.APPLY_GLIDE, r6=1)
        value = machine.xram[glide.OUT_BEND_HI] * 128 + machine.xram[glide.OUT_BEND_LO]
        self.assertEqual(value, 8192 - 448)

    def test_full_keyboard_endpoints_saturate_to_midi_bend_limits(self):
        for first, second, expected in ((0, 24, (127, 127)), (24, 0, (0, 0))):
            with self.subTest(first=first, second=second):
                machine = Machine(self.image)
                machine.sp = 0x51
                machine.xram[glide.GLIDE_RANGE_CODE] = 25
                machine.xram[glide.PARTNER_BY_CHANNEL + 1] = 2
                machine.xram[glide.PITCH_BY_CHANNEL + 1] = first
                machine.xram[glide.PITCH_BY_CHANNEL + 2] = second
                machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 0
                machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 127
                machine.xram[glide.RAW_BEND_HI] = 64
                machine.xram[glide.RAW_BEND_LO] = 0
                machine.call(glide.APPLY_GLIDE, r6=1)
                self.assertEqual(
                    (machine.xram[glide.OUT_BEND_HI], machine.xram[glide.OUT_BEND_LO]),
                    expected,
                )

    def test_glide_adds_to_full_resolution_tilt_and_clamps_both_directions(self):
        # Include low-byte sums crossing 127 and high-byte underflow before
        # subtraction of a low-byte borrow, rather than just centered tilt.
        for interval in (1, 4, 24):
            for direction in (-1, 1):
                for raw in (0, 100, 127, 128, 300, 8064, 8192, 8292, 16000, 16383):
                    with self.subTest(interval=interval, direction=direction, raw=raw):
                        machine = Machine(self.image)
                        machine.sp = 0x51
                        machine.xram[glide.GLIDE_RANGE_CODE] = 25
                        machine.xram[glide.PARTNER_BY_CHANNEL + 1] = 2
                        machine.xram[glide.PITCH_BY_CHANNEL + 1] = 60
                        machine.xram[glide.PITCH_BY_CHANNEL + 2] = 60 + direction * interval
                        machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 2
                        machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 1
                        machine.xram[glide.RAW_BEND_HI] = raw >> 7
                        machine.xram[glide.RAW_BEND_LO] = raw & 127
                        machine.call(glide.APPLY_GLIDE, r6=1)
                        output = (machine.xram[glide.OUT_BEND_HI] * 128
                                  + machine.xram[glide.OUT_BEND_LO])
                        delta = round(interval * 42 * 8192 / (24 * 128))
                        self.assertEqual(output, max(0, min(16383, raw + direction * delta)))

    def test_glide_pitch_matches_configured_receiver_span(self):
        for span in (1, 5, 12, 24):
            for ratio in (0, 42, 64, 128):
                for interval in (1, span):
                    with self.subTest(span=span, ratio=ratio, interval=interval):
                        machine = Machine(self.image)
                        machine.sp = 0x51
                        machine.xram[glide.GLIDE_RANGE_CODE] = span + 1
                        machine.xram[glide.TILT_REFERENCE_RANGE] = span
                        machine.set_r(4, interval)
                        machine.set_r(2, ratio)
                        machine.call(glide.OUTPUT_SCALE)
                        if span == 24:
                            self.assertEqual(machine.cy, 0)  # use existing table
                        else:
                            output = (machine.xram[glide.OUT_BEND_HI] * 128
                                      + machine.xram[glide.OUT_BEND_LO])
                            self.assertEqual(output, interval * ratio * 64 // span)

        # A five-semitone pairing window retains a twelve-semitone receiver
        # when tilt was calibrated against twelve. A four-semitone interval at
        # equal pressure must therefore bend two semitones, not one.
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[glide.GLIDE_RANGE_CODE] = 6
        machine.xram[glide.TILT_REFERENCE_RANGE] = 12
        machine.xram[glide.PARTNER_BY_CHANNEL + 1] = 2
        machine.xram[glide.PITCH_BY_CHANNEL + 1] = 60
        machine.xram[glide.PITCH_BY_CHANNEL + 2] = 64
        machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 1
        machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 1
        machine.xram[glide.RAW_BEND_HI] = 64
        machine.call(glide.APPLY_GLIDE, r6=1)
        output = machine.xram[glide.OUT_BEND_HI] * 128 + machine.xram[glide.OUT_BEND_LO]
        self.assertEqual(output, 8192 + 8192 * 2 // 12)

    def test_tiny_tilt_keeps_its_original_semitone_range_with_wide_glide(self):
        for reference, window, amount in ((12, 24, 1), (12, 24, 3), (12, 24, 5),
                                           (8, 24, 25), (24, 5, 1), (12, 0, 1),
                                           (24, 24, 100), (12, 24, 100), (24, 24, 99)):
            for movement in (-64, -1, 1, 64):
                with self.subTest(reference=reference, window=window, amount=amount,
                                  movement=movement):
                    machine = Machine(self.image)
                    machine.sp = 0x51
                    profile = DEFAULT_PROFILE | {
                        'mpe_active': True, 'pressure_cc': -1,
                        'relative_tilt_amount': amount, 'relative_tilt_range': reference,
                        'pressure_glide_range': window,
                    }
                    preset = _decode_7bit(build_sysex(profile)[8:-1])[10:480]
                    machine.xram[0x0347:0x0347 + len(preset)] = preset
                    sent = []
                    machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
                        (m.r(7), m.r(5), m.r(3))) or True
                    # The native relative tilt sensor has seven-bit readings.
                    baseline = 64 if movement < 0 else 0
                    machine.xram[0x0F10 + 1] = baseline + 1
                    machine.call(0x7FF3, r7=baseline + movement, r3=1)
                    status, low, high = sent[-1]
                    self.assertEqual(status, 0xE1)
                    span = max(reference, window)
                    delta = abs(movement) * amount * reference * 128 // (100 * span)
                    self.assertEqual(high * 128 + low,
                                     min(16383, 8192 + (delta if movement > 0 else -delta)))

    def test_pressure_resend_keeps_combined_bend_pad_offset(self):
        machine = self._paired_machine()
        sent = []
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3))) or True
        machine.xram[0x04EF] = 1  # combine pad with tilt
        machine.xram[0x0F20] = 1  # relative pad is active
        machine.xram[0x0F22] = 80
        machine.xram[0x0F30 + 1] = 64
        machine.xram[0x0F00 + 1] = 0
        machine.call(glide.UPDATE_BEND, r6=1)
        self.assertEqual(sent[-1], (0xE1, 0, 80))

    def _set_tilt(self, machine, channel, value):
        machine.xram[0x0F30 + channel] = value >> 7
        machine.xram[0x0F00 + channel] = value & 127

    def _bend_messages(self, machine):
        sent = []
        machine.stubs[cyclone.GAME_SEND_HOOK] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3))) or True
        return sent

    def test_blended_tilt_matches_force_ratio_at_full_midi_resolution(self):
        machine = self._paired_machine()
        values = (0, 1, 127, 128, 8063, 8192, 8193, 16000, 16383)
        for first in values:
            for second in values:
                self._set_tilt(machine, 1, first)
                self._set_tilt(machine, 2, second)
                for ratio in range(129):
                    machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 128 - ratio
                    machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = ratio
                    machine.call(glide.BLEND_TILT, r6=1)
                    result = (machine.xram[glide.RAW_BEND_HI] * 128
                              + machine.xram[glide.RAW_BEND_LO])
                    self.assertEqual(result, ((128-ratio)*first + ratio*second) // 128,
                                     (first, second, ratio))
                    self.assertEqual(machine.r(6), 1)
        # The physically released primary has no influence, even with stale force.
        machine.xram[glide.FLAGS_BY_CHANNEL + 1] = glide.FLAG_PRIMARY_RELEASED
        machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 127
        machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 0
        machine.call(glide.BLEND_TILT, r6=1)
        self.assertEqual(machine.xram[glide.RAW_BEND_HI] * 128
                         + machine.xram[glide.RAW_BEND_LO], 16383)

    def test_native_tilt_from_either_key_changes_only_shared_voice(self):
        for flags in (0x77, 0x7D, 0x73):  # hires relative, legacy relative, absolute
            with self.subTest(flags=flags):
                machine = self._paired_machine()
                machine.xram[0x04EF] = flags
                machine.xram[0x04F8] = 100
                machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 1
                machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 1
                sent = self._bend_messages(machine)
                for channel in (1, 2):
                    machine.call(0x7FF3, r3=channel, r7=64)
                machine.call(0x7FF3, r3=2, r7=80)
                self.assertEqual(sent[-1], (0xE1, 43, 77))  # 8192+1024+683
                machine.call(0x7FF3, r3=1, r7=48)
                self.assertEqual(sent[-1], (0xE1, 43, 69))  # opposing tilt cancels
                self.assertTrue(all(status == 0xE1 for status, _, _ in sent))

    def test_force_updates_move_tilt_blend_with_glide(self):
        machine = self._paired_machine()
        self._set_tilt(machine, 1, 8192 - 1024)
        self._set_tilt(machine, 2, 8192 + 2048)
        sent = self._bend_messages(machine)
        self._scan(machine, 0, 70)
        self._scan(machine, 1, 70)
        self.assertEqual(sent[-1], (0xE1, 43, 73))  # midpoint tilt + midpoint pitch
        self._scan(machine, 0, 30)
        self.assertEqual(sent[-1], (0xE1, 85, 90))  # all companion tilt and pitch
        self._scan(machine, 1, 30)
        self.assertEqual(sent[-1], (0xE1, 0, 56))  # both zero: original-key tilt

    def test_released_primary_hands_off_tilt_and_recontact_rebases(self):
        machine = self._paired_machine()
        machine.stubs[0x313D] = lambda m: True
        machine.xram[0x00AD] = 2
        machine.xram[0x02BB:0x02BD] = bytes((60, 64))
        machine.xram[0x04EF] = 0x77
        machine.xram[0x04F8] = 100
        self._scan(machine, 0, 70)
        self._scan(machine, 1, 70)
        for channel in (1, 2):
            machine.call(0x7FF3, r3=channel, r7=64)
        sent = self._bend_messages(machine)
        machine.call(0x62D7, r7=0)
        machine.call(0x7FF3, r3=2, r7=80)
        self.assertEqual(sent[-1], (0xE1, 85, 90))
        self._scan(machine, 0, 70)
        machine.call(0x4C4F, r7=0)
        self.assertEqual(machine.xram[0x0F11], 0x81)
        machine.call(0x7FF3, r3=1, r7=90)
        self.assertEqual(sent[-1], (0xE1, 43, 77))  # new landing is centered
        machine.call(0x7FF3, r3=1, r7=89)
        self.assertEqual(sent[-1], (0xE1, 107, 76))
        self.assertTrue(all(status == 0xE1 for status, _, _ in sent))
        self.assertEqual(machine.xram[0x00AD], 2)

    def test_companion_release_restores_primary_tilt(self):
        machine = self._paired_machine()
        machine.stubs[0x313D] = lambda m: True
        machine.xram[0x00AD] = 2
        machine.xram[0x02BB:0x02BD] = bytes((60, 64))
        self._set_tilt(machine, 1, 8315)
        self._set_tilt(machine, 2, 16000)
        machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 1
        machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 1
        sent = self._bend_messages(machine)
        machine.call(0x62D7, r7=1)
        self.assertEqual(sent[-1], (0xE1, 123, 64))
        self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1], glide.FLAG_NORMAL)
        self.assertFalse(any(status == 0xE2 for status, _, _ in sent))

    def test_pad_resends_preserve_blend_and_add_shared_offset_once(self):
        machine = self._paired_machine()
        machine.xram[0x04EF] = 0x77
        machine.xram[0x0F20] = 1
        machine.xram[0x0F22] = 64
        machine.xram[glide.WEIGHT_BY_CHANNEL + 1] = 1
        machine.xram[glide.WEIGHT_BY_CHANNEL + 2] = 1
        self._set_tilt(machine, 1, 8192 - 128)
        self._set_tilt(machine, 2, 8192 + 128)
        sent = self._bend_messages(machine)
        # Relative pad landing is center. Raw input R6:R7 is value*64.
        machine.xram[0x0F21] = 64
        machine.call(0x7EA6, r5=0, r6=40, r7=0)
        member = [msg for msg in sent if msg[0] != 0xE0]
        self.assertTrue(member)
        self.assertTrue(all(msg == (0xE1, 43, 85) for msg in member))

    def test_candidate_contains_pressure_glide_hook_and_table(self):
        self.assertEqual(self.image[custom.VERSION_BYTE_ADDRESS], custom.CUSTOM_VERSION_BYTE)
        self.assertEqual(self.image[0x4C4F:0x4C52],
                         bytes((0x02, glide.KEYON >> 8, glide.KEYON & 255)))
        self.assertEqual(self.image[0x62D7:0x62DA],
                         bytes((0x02, glide.KEYOFF_ROUTINE >> 8, glide.KEYOFF_ROUTINE & 255)))
        self.assertEqual(self.image[glide.BEND_TABLE:glide.BEND_TABLE + 2], bytes((0, 0)))
        self.assertEqual(self.image[glide.PRESSURE], 0xC0)
        self.assertEqual(self.image[0x7DF4:0x7DF7],
                         bytes((0x02, glide.PRESSURE_SEND >> 8, glide.PRESSURE_SEND & 255)))

    def test_glide_pairing_uses_lydian_white_key_interval(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[scales.INIT_DONE] = 1
        machine.xram[scales.SCALE_ID] = 4
        machine.xram[scales.TRANSPOSE] = 12
        machine.xram[0x0351] = 1
        machine.xram[glide.GLIDE_RANGE_CODE] = 2  # one-semitone pairing window
        sent = []
        machine.stubs[0x7C6A] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3)))
        for key, channel, pitch in ((4, 1, 64), (5, 2, 65)):
            machine.xram[glide.PENDING_KEY] = key
            machine.xram[0x0060 + key] = channel
            machine.call(0x7C67, r7=0x90 | channel, r5=pitch, r3=90)
        self.assertEqual(sent, [(0xE1, 0, 64), (0x91, 64, 90),
                                (0xE2, 0, 64), (0x92, 66, 90)])
        self.assertEqual(machine.xram[glide.PITCH_BY_CHANNEL + 1], 64)
        self.assertEqual(machine.xram[glide.PITCH_BY_CHANNEL + 2], 66)
        self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 1], glide.FLAG_NORMAL)
        self.assertEqual(machine.xram[glide.FLAGS_BY_CHANNEL + 2], glide.FLAG_NORMAL)


if __name__ == "__main__":
    unittest.main()
