"""Exercise the scale-selector entry path in the project 8051 interpreter."""

from __future__ import annotations

import unittest

from firmware_tools import build_custom_firmware as custom
from firmware_tools import build_scale_quantizer_patch as scale
from firmware_tools import build_sensor_config_patch as menus
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.build_relative_tilt_patch import NOTE_OFF
from firmware_tools.mcs51 import Machine


class ScaleQuantizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, _ = custom.build_image()

    @staticmethod
    def key_leds(machine):
        return [
            machine.xram[0x0819 + machine.code[0x5DBB + 2 * key] * 8
                         + machine.code[0x5DBC + 2 * key]]
            for key in range(scale.KEY_COUNT)
        ]

    @staticmethod
    def key_marked(machine, key):
        byte = scale.CONSUMED_KEY_MARKS + key // 8
        return bool(machine.xram[byte] & (1 << (key % 8)))

    # Expected degrees are explicit so the regression does not duplicate the
    # table builder's mapping algorithm.
    SEVEN_NOTE_DEGREES = {
        1: (0, 2, 4, 5, 7, 9, 11),   # Ionian
        2: (0, 2, 3, 5, 7, 9, 10),   # Dorian
        3: (0, 1, 3, 5, 7, 8, 10),   # Phrygian
        4: (0, 2, 4, 6, 7, 9, 11),   # Lydian
        5: (0, 2, 4, 5, 7, 9, 10),   # Mixolydian
        6: (0, 2, 3, 5, 7, 8, 10),   # Aeolian
        7: (0, 1, 3, 5, 6, 8, 10),   # Locrian
        11: (0, 2, 3, 5, 7, 8, 11),  # Harmonic minor
        14: (0, 1, 4, 5, 7, 8, 10),  # Phrygian dominant
    }

    def test_seven_note_scales_follow_white_keys_on_both_octaves(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[scale.INIT_DONE] = 1
        sent = []
        machine.stubs[0x7C6A] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3)))
        white_keys = (0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24)
        for scale_id, degrees in self.SEVEN_NOTE_DEGREES.items():
            machine.xram[scale.SCALE_ID] = scale_id
            for transpose in range(-12, 13):
                machine.xram[scale.TRANSPOSE] = transpose + 12
                pitches = []
                for index, key in enumerate(white_keys):
                    expected = 48 + 12 * (index // 7) + degrees[index % 7] + transpose
                    for status in (0x91, 0x92, 0x81, 0x82):
                        machine.call(0x7C67, r7=status, r5=48 + key, r3=90)
                        self.assertEqual(sent[-1], (status, expected, 90),
                                         (scale_id, transpose, key, status))
                    pitches.append(expected)
                self.assertTrue(all(a < b for a, b in zip(pitches, pitches[1:])))

    def test_lydian_white_f_stock_note_on_and_off_send_f_sharp(self):
        machine = Machine(self.image)
        machine.sp = 0x51
        machine.xram[scale.INIT_DONE] = 1
        machine.xram[scale.SCALE_ID] = 4
        machine.xram[scale.TRANSPOSE] = 12
        sent = []
        machine.stubs[0x7C6A] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3)))
        machine.call(0x4C4F, r7=5)
        machine.call(0x62D7, r7=5)
        self.assertEqual([(status, pitch) for status, pitch, _ in sent
                          if status & 0xF0 in (0x80, 0x90)],
                         [(0x90, 30), (0x80, 30)])

    def test_tilt_pressure_entry_returns_and_opens_selector(self):
        machine = Machine(self.image)
        levels = {channel: 0 for channel in range(8)}
        levels.update({0: 0x7F, 1: 0x7F})
        machine.stubs[0x6894] = lambda m: m.set_r(
            7, levels[m.xram[velocity.XRAM_CHANNEL]])

        # Simulate notes held when selector entry sends its stock CC 123 burst.
        machine.xram[0x0F10:0x0F20] = b"\x82" * 16

        # Drive the real stock sensor loop through the one-second chord and
        # the 16-channel All Notes Off path that runs on entry.
        for tick in range(1010):
            machine.iram[0x4B] = tick >> 8 & 0xFF
            machine.iram[0x4C] = tick & 0xFF
            machine.call(0x56E8)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 6)
        self.assertEqual(machine.xram[0x0F10:0x0F20], bytes(16))
        self.assertEqual(machine.sp, 0x07)
        self.assertTrue(all(self.key_leds(machine)))  # Chromatic includes every key.
        self.assertEqual(machine.xram[scale.ROOT_PHASE], 1)

        # Releasing the entry chord transitions to interactive selection.
        levels.update({0: 0, 1: 0})
        for tick in range(1010, 1070):
            machine.iram[0x4B] = tick >> 8 & 0xFF
            machine.iram[0x4C] = tick & 0xFF
            machine.call(0x56E8)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 7)

        # The root starts bright, then its C keys blink dark after 128 ms.
        for tick in range(1070, 1140):
            machine.iram[0x4B] = tick >> 8 & 0xFF
            machine.iram[0x4C] = tick & 0xFF
            machine.call(0x56E8)
        self.assertEqual(machine.xram[scale.ROOT_PHASE], 0)
        leds = self.key_leds(machine)
        self.assertEqual([leds[key] for key in (0, 12, 24)], [0, 0, 0])

        # Octave up transposes by one semitone; white key #2 selects scale 1.
        levels[7] = 0x7F
        for tick in range(1140, 1150):
            machine.iram[0x4B] = tick >> 8 & 0xFF
            machine.iram[0x4C] = tick & 0xFF
            machine.call(0x56E8)
        self.assertEqual(machine.xram[scale.TRANSPOSE], 13)
        machine.call(0x4C4F, r7=2)
        self.assertEqual(machine.xram[scale.SCALE_ID], 1)
        # Forced redraw applies the newly selected transposed major mask.
        leds = self.key_leds(machine)
        self.assertEqual(leds[2], 0)  # D natural is outside C-sharp major.
        self.assertNotEqual(leds[3], 0)

        # Exiting immediately clears all scale-menu key LEDs.
        levels[0] = 0x7F
        for tick in range(1150, 1160):
            machine.iram[0x4B] = tick >> 8 & 0xFF
            machine.iram[0x4C] = tick & 0xFF
            machine.call(0x56E8)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 8)
        self.assertFalse(any(self.key_leds(machine)))

    def test_normal_key_press_continues_stock_note_routine(self):
        machine = Machine(self.image)
        sent = []
        machine.stubs[0x7C67] = lambda m: sent.append(
            (m.r(7), m.r(6), m.r(5), m.r(3)))

        # The scale hook replaces the stock note routine's MOV DPTR at entry.
        # In normal mode it must resume the body after that instruction.
        machine.call(0x4C4F, r7=2)

        self.assertTrue(sent)
        self.assertEqual(sent[0][0], 0x90)
        self.assertNotEqual(sent[0][0], 0xFF)

    def test_selector_key_release_keeps_mpe_allocator_count(self):
        machine = Machine(self.image)
        machine.xram[0x0351] = 1
        machine.xram[0x035F] = 8
        machine.xram[0x00AD] = 0  # stock active MPE voice count
        machine.xram[0x0276:0x027E] = b"\xff" * 8
        machine.xram[scale.SCALE_STATE] = 7
        machine.xram[0x0065] = 0xFF  # no channel allocated to selector key 5
        sent = []
        machine.stubs[0x7C6A] = lambda m: sent.append(
            (m.r(7), m.r(5), m.r(3)))

        machine.call(0x4C4F, r7=5)   # select Phrygian
        self.assertTrue(self.key_marked(machine, 5))
        machine.call(0x62D7, r7=5)   # real stock key release entry
        self.assertFalse(self.key_marked(machine, 5))
        self.assertEqual(machine.xram[0x00AD], 0)
        self.assertEqual(sent, [])

        machine.xram[scale.SCALE_STATE] = 0
        machine.xram[0x0889] = 2
        machine.call(0x6FE5, r7=2)
        self.assertNotEqual(machine.r(7), 0)
        self.assertEqual(machine.xram[0x00AD], 1)

        # Releasing a selector key after menu exit must also be ignored.
        machine.xram[scale.SCALE_STATE] = 7
        machine.call(0x4C4F, r7=7)
        machine.xram[scale.SCALE_STATE] = 0
        machine.call(0x62D7, r7=7)
        self.assertEqual(machine.xram[0x00AD], 1)
        self.assertFalse(self.key_marked(machine, 7))

    def test_sensitivity_menu_key_release_keeps_mpe_allocator_count(self):
        for mode in (0, 1, 2):  # Velocity, Pressure, Tilt
            for state in (2, 3, 5):
                for key in (0, 1):  # white value key and black consumed key
                    with self.subTest(mode=mode, state=state, key=key):
                        machine = Machine(self.image)
                        machine.xram[0x0351] = 1
                        machine.xram[0x035F] = 8
                        machine.xram[0x0276:0x027E] = b"\xff" * 8
                        machine.xram[0x0060 + key] = 0xFF
                        machine.xram[velocity.XRAM_STATE] = state
                        machine.xram[menus.MODE] = mode
                        sent = []
                        machine.stubs[0x7C6A] = lambda m: sent.append(
                            (m.r(7), m.r(5), m.r(3)))

                        machine.call(0x4C4F, r7=key)
                        self.assertTrue(self.key_marked(machine, key))
                        # The release may arrive after a short menu exit.
                        machine.xram[velocity.XRAM_STATE] = 0
                        machine.call(0x62D7, r7=key)
                        self.assertEqual(machine.xram[0x00AD], 0)
                        self.assertFalse(self.key_marked(machine, key))
                        self.assertEqual(sent, [])
                        machine.xram[0x0889] = key
                        machine.call(0x6FE5, r7=key)
                        self.assertNotEqual(machine.r(7), 0)
                        self.assertEqual(machine.xram[0x00AD], 1)

    def test_consumed_key_bitmap_preserves_simultaneous_keys(self):
        machine = Machine(self.image)
        keys = (0, 7, 8, 15, 16, 23, 24)
        for key in keys:
            machine.call(scale.MARK_KEY, r7=key)
        self.assertEqual(sum(self.key_marked(machine, key) for key in keys), len(keys))
        self.assertEqual(machine.xram[scale.CONSUMED_KEY_MARKS + 4], 0)

        # Each key-off clears only its own bit, including at byte boundaries.
        for key in keys:
            machine.call(scale.KEYOFF, r7=key)
            self.assertFalse(self.key_marked(machine, key))
            self.assertTrue(all(self.key_marked(machine, other)
                                for other in keys if other > key))

    def test_menu_visit_without_key_press_keeps_mpe_allocator_count(self):
        for channel in (0, 1, 4):
            with self.subTest(channel=channel):
                machine = Machine(self.image)
                machine.xram[velocity.XRAM_BUTTON_BITS] = 0xF7
                machine.xram[0x0351] = 1
                machine.xram[0x035F] = 8
                machine.xram[0x0276:0x027E] = b"\xff" * 8
                machine.stubs[menus.PRESET_ERASE] = lambda _m: None
                machine.stubs[menus.PRESET_WRITE] = lambda _m: None
                level = {"value": 0x7F}
                machine.stubs[0x6894] = lambda m, c=channel: m.set_r(
                    7, level["value"] if m.xram[velocity.XRAM_CHANNEL] == c else 0)

                def scan(start, stop):
                    for tick in range(start, stop):
                        machine.iram[0x4B] = tick >> 8 & 255
                        machine.iram[0x4C] = tick & 255
                        machine.call(0x56E8)

                scan(0, 1020)
                self.assertEqual(machine.xram[velocity.XRAM_STATE], 2)
                level["value"] = 0
                scan(1020, 1040)
                self.assertEqual(machine.xram[velocity.XRAM_STATE], 3)
                level["value"] = 0x7F
                scan(1040, 1050)
                level["value"] = 0
                scan(1050, 1070)
                self.assertEqual(machine.xram[velocity.XRAM_STATE], 0)
                self.assertEqual(machine.xram[0x00AD], 0)

    def test_two_mpe_voices_stay_separate_after_selector_key_release(self):
        machine = Machine(self.image)
        machine.xram[0x0351] = 1
        machine.xram[0x035F] = 8
        machine.xram[0x034F] = 65
        machine.xram[0x04EF] = 0x7F
        machine.xram[0x04F1] = 52  # maximum relative tilt movement: 12/64
        machine.xram[0x04FA] = 6
        machine.xram[velocity.XRAM_BUTTON_BITS] = 0xF7
        machine.xram[0x0276:0x027E] = b"\xff" * 8
        machine.xram[0x0060:0x0079] = b"\xff" * 25
        # Let the stock sender build bytes, then hand them to a drained USB
        # endpoint. The emulator has no interrupt to empty the hardware FIFO.
        machine.stubs[0x313D] = lambda _m: None
        levels = {channel: 0 for channel in range(8)}
        machine.stubs[0x6894] = lambda m: m.set_r(
            7, levels[m.xram[velocity.XRAM_CHANNEL]])
        sent = []
        machine.trace = lambda m, pc, _op: sent.append(
            (m.r(7), m.r(5), m.r(3))) if pc == 0x7C6A else None

        def scan(start, stop):
            for tick in range(start, stop):
                machine.iram[0x4B] = tick >> 8 & 255
                machine.iram[0x4C] = tick & 255
                machine.call(0x56E8)

        # A normal pair allocates and releases two distinct member channels.
        for key in (2, 4):
            machine.call(0x4C4F, r7=key)
        self.assertEqual(machine.xram[0x00AD], 2)
        for key in (2, 4):
            machine.call(0x62D7, r7=key)
        self.assertEqual(machine.xram[0x00AD], 0)

        levels[0] = levels[1] = 0x7F
        scan(0, 1020)
        levels[0] = levels[1] = 0
        scan(1020, 1060)
        machine.call(0x4C4F, r7=5)
        machine.call(0x62D7, r7=5)  # release scale selection key
        self.assertEqual(machine.xram[0x00AD], 0)
        levels[0] = 0x7F
        scan(1060, 1080)
        levels[0] = 0
        scan(1080, 1100)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 0)

        for key in (2, 4):
            machine.call(0x4C4F, r7=key)
            machine.xram[0x0224 + key] = 3
            machine.xram[0x0205 + key] = 0
        channel_up, channel_down = machine.xram[0x0062], machine.xram[0x0064]
        self.assertNotEqual(channel_up, 0)
        self.assertNotEqual(channel_down, 0)
        self.assertNotEqual(channel_up, channel_down)
        self.assertEqual(machine.xram[0x00AD], 2)
        for key, sample in ((2, 50), (4, 85), (2, 90), (4, 45)):
            machine.xram[0x0240 + key] = sample
            machine.call(0x5895, r7=key)
        up_bends = [data[2] for data in sent if data[0] == 0xE0 | channel_up]
        down_bends = [data[2] for data in sent if data[0] == 0xE0 | channel_down]
        self.assertGreater(up_bends[-1], 64)
        self.assertLess(down_bends[-1], 64)
        self.assertLessEqual(up_bends[-1], 76)
        self.assertGreaterEqual(down_bends[-1], 52)

    def test_each_single_button_hold_reaches_its_sensitivity_page(self):
        for channel, expected_mode in ((0, 2), (1, 1), (4, 0)):
            with self.subTest(channel=channel):
                machine = Machine(self.image)
                machine.xram[velocity.XRAM_BUTTON_BITS] = 0xF7
                machine.xram[0x0351] = 1
                machine.xram[0x035F] = 8
                machine.xram[0x0276:0x027E] = b"\xff" * 8
                machine.xram[0x0060] = 0xFF
                level = {"value": 0x7F}
                machine.stubs[0x6894] = lambda m, c=channel: m.set_r(
                    7, level["value"] if m.xram[velocity.XRAM_CHANNEL] == c else 0)

                # Use the stock sensor loop, including the scale and Cyclone
                # dispatchers, for the entire one-second hold.
                for tick in range(1020):
                    machine.iram[0x4B] = tick >> 8 & 0xFF
                    machine.iram[0x4C] = tick & 0xFF
                    machine.call(0x56E8)
                self.assertEqual(machine.xram[velocity.XRAM_STATE], 2)
                self.assertEqual(machine.xram[menus.MODE], expected_mode)

                level["value"] = 0
                for tick in range(1020, 1040):
                    machine.iram[0x4B] = tick >> 8 & 0xFF
                    machine.iram[0x4C] = tick & 0xFF
                    machine.call(0x56E8)
                self.assertEqual(machine.xram[velocity.XRAM_STATE], 3)

                machine.call(0x4C4F, r7=0)  # first white key selects step 1
                if expected_mode == 2:
                    self.assertEqual(machine.xram[menus.TILT_SENSITIVITY], 70)
                else:
                    address = (menus.PRESSURE_GAIN if expected_mode == 1
                               else velocity.XRAM_VELOCITY_GAIN)
                    self.assertEqual(
                        int.from_bytes(machine.xram[address:address + 2], "big"),
                        velocity.gain_table()[0])
                machine.call(0x62D7, r7=0)
                self.assertEqual(machine.xram[0x00AD], 0)
                machine.xram[velocity.XRAM_STATE] = 0
                machine.xram[0x0889] = 0
                machine.call(0x6FE5, r7=0)
                self.assertNotEqual(machine.r(7), 0)

    def test_sensitivity_page_opens_after_using_scale_selector(self):
        machine = Machine(self.image)
        levels = {channel: 0 for channel in range(8)}
        machine.xram[velocity.XRAM_BUTTON_BITS] = 0xF7
        machine.stubs[0x6894] = lambda m: m.set_r(
            7, levels[m.xram[velocity.XRAM_CHANNEL]])

        def scan(start, stop):
            for tick in range(start, stop):
                machine.iram[0x4B] = tick >> 8 & 0xFF
                machine.iram[0x4C] = tick & 0xFF
                machine.call(0x56E8)

        levels[0] = levels[1] = 0x7F
        scan(0, 1020)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 6)
        levels[0] = levels[1] = 0
        scan(1020, 1060)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 7)
        machine.call(0x4C4F, r7=2)  # select Ionian
        self.assertEqual(machine.xram[scale.SCALE_ID], 1)
        levels[0] = 0x7F
        scan(1060, 1080)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 8)
        levels[0] = 0
        scan(1080, 1100)
        self.assertEqual(machine.xram[scale.SCALE_STATE], 0)

        levels[4] = 0x7F
        scan(1100, 2130)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 2)
        self.assertEqual(machine.xram[menus.MODE], 0)

    def test_tilt_midi_before_and_after_scale_selection(self):
        machine = Machine(self.image)
        machine.xram[0x0351] = 1      # MPE
        machine.xram[0x034F] = 65     # tilt sensitivity
        machine.xram[0x04EF] = 0x7F   # relative tilt, pad, and combination
        machine.xram[0x04F1] = 52     # twelve-step bend amount
        machine.xram[0x04FA] = 6      # landing deadzone
        machine.xram[velocity.XRAM_BUTTON_BITS] = 0xF7

        # The stock voice allocator needs the runtime voice pool initialized.
        # Fix only its chosen channel; run the real note, tilt, MIDI sender,
        # and front-button sensor code around it.
        machine.stubs[0x6FE5] = lambda m: m.set_r(
            7, 2 if m.xram[0x0889] == 2 else 3)
        levels = {channel: 0 for channel in range(8)}
        machine.stubs[0x6894] = lambda m: m.set_r(
            7, levels[m.xram[velocity.XRAM_CHANNEL]])
        messages, wire = [], []
        machine.stubs[0x313D] = lambda m: wire.append(m.r(7))

        def trace(m, pc, _op):
            if pc == 0x7C6A:  # final stock MIDI sender, after all hooks
                messages.append((m.r(7), m.r(5), m.r(3)))

        machine.trace = trace

        def scan(start, stop):
            for tick in range(start, stop):
                machine.iram[0x4B] = tick >> 8 & 255
                machine.iram[0x4C] = tick & 255
                machine.call(0x56E8)

        def gesture():
            first_message, first_wire = len(messages), len(wire)
            for key in (2, 4):
                machine.call(0x4C4F, r7=key)
                machine.xram[0x0224 + key] = 3
                machine.xram[0x0205 + key] = 0
            # The two held keys tilt in opposite directions at the same time.
            for key, sample in ((2, 50), (4, 85), (2, 90), (4, 45),
                                (2, 110), (4, 30)):
                machine.xram[0x0240 + key] = sample
                machine.call(0x5895, r7=key)
            for channel, pitch in ((2, 26), (3, 28)):
                machine.call(NOTE_OFF, r7=0x80 | channel, r5=pitch, r3=0)
            return messages[first_message:], wire[first_wire:]

        def select(start, physical_key):
            levels[0] = levels[1] = 0x7F
            scan(start, start + 1020)
            self.assertEqual(machine.xram[scale.SCALE_STATE], 6)
            levels[0] = levels[1] = 0
            scan(start + 1020, start + 1060)
            self.assertEqual(machine.xram[scale.SCALE_STATE], 7)
            machine.call(0x4C4F, r7=physical_key)
            levels[0] = 0x7F
            scan(start + 1060, start + 1080)
            levels[0] = 0
            scan(start + 1080, start + 1100)
            self.assertEqual(machine.xram[scale.SCALE_STATE], 0)

        chromatic, chromatic_wire = gesture()
        select(0, 5)                 # fourth white key: Phrygian
        self.assertEqual(machine.xram[scale.SCALE_ID], 3)
        phrygian, phrygian_wire = gesture()
        select(1100, 0)              # first white key: Chromatic
        self.assertEqual(machine.xram[scale.SCALE_ID], 0)
        restored, restored_wire = gesture()

        expected_bends = [(0xE2, 0, 64), (0xE3, 0, 64),
                          (0xE2, 0, 64), (0xE3, 0, 64),
                          (0xE2, 0, 70), (0xE3, 0, 58),
                          (0xE2, 0, 74), (0xE3, 0, 55)]
        for stream, first_pitch, second_pitch in (
                (chromatic, 26, 28), (phrygian, 25, 27),
                (restored, 26, 28)):
            self.assertEqual([item for item in stream if item[0] in (0xE2, 0xE3)],
                             expected_bends)
            self.assertEqual([item for item in stream if item[0] == 0x92],
                             [(0x92, first_pitch, 0)])
            self.assertEqual([item for item in stream if item[0] == 0x93],
                             [(0x93, second_pitch, 0)])
            self.assertEqual([item for item in stream if item[0] == 0x82],
                             [(0x82, first_pitch, 0)])
            self.assertEqual([item for item in stream if item[0] == 0x83],
                             [(0x83, second_pitch, 0)])
        self.assertIn([0x92, 26, 0], [chromatic_wire[i:i + 3]
                                       for i in range(len(chromatic_wire) - 2)])
        self.assertIn([0x92, 25, 0], [phrygian_wire[i:i + 3]
                                       for i in range(len(phrygian_wire) - 2)])
        self.assertIn([0x92, 26, 0], [restored_wire[i:i + 3]
                                       for i in range(len(restored_wire) - 2)])
        for data in (chromatic_wire, phrygian_wire, restored_wire):
            self.assertIn([0xE2, 0, 74], [data[i:i + 3]
                                          for i in range(len(data) - 2)])
            self.assertIn([0xE3, 0, 55], [data[i:i + 3]
                                          for i in range(len(data) - 2)])

    def test_packed_tables_preserve_all_scale_and_transpose_rows(self):
        machine = Machine(self.image)
        machine.xram[scale.INIT_DONE] = 1
        machine.xram[scale.ROOT_PHASE] = 1
        observed = []
        machine.stubs[0x7C6A] = lambda m: observed.append(
            (m.r(7), m.r(5), m.r(3)))
        for scale_id in range(scale.SCALE_COUNT):
            for transpose_class in range(12):
                machine.xram[scale.SCALE_ID] = scale_id
                machine.xram[scale.TRANSPOSE] = 12 + transpose_class
                machine.xram[scale.DRAW_FORCE] = 1
                machine.call(scale.DRAW)
                leds = self.key_leds(machine)
                for key, level in enumerate(leds):
                    pc = (key - transpose_class) % 12
                    expected = 255 if scale.scale_membership(scale_id, pc) else 0
                    if pc == 0:
                        expected = 255
                    self.assertEqual(level, expected,
                                     (scale_id, transpose_class, key))
                for pc in range(12):
                    observed.clear()
                    machine.call(0x7C67, r7=0x92, r5=60 + pc, r3=100)
                    expected_note = 60 + pc + transpose_class
                    expected_note = min(127, expected_note)
                    delta = min(
                        range(-6, 7),
                        key=lambda d: (0 if scale.scale_membership(
                            scale_id, (expected_note + d - transpose_class) % 12)
                            else 1, abs(d), 0 if d <= 0 else 1))
                    if scale_id in self.SEVEN_NOTE_DEGREES and pc in (0, 2, 4, 5, 7, 9, 11):
                        degree = (0, 2, 4, 5, 7, 9, 11).index(pc)
                        delta = self.SEVEN_NOTE_DEGREES[scale_id][degree] - pc
                    self.assertEqual(observed[-1],
                                     (0x92, expected_note + delta, 100),
                                     (scale_id, transpose_class, pc))

    def test_scale_led_and_quantizer_share_compact_records(self):
        records, transpose_mod = scale.packed_scale_records()
        self.assertEqual(len(records), scale.SCALE_COUNT * 12)
        self.assertEqual(len(transpose_mod), 25)
        for scale_id in range(scale.SCALE_COUNT):
            for pitch_class in range(12):
                record = records[scale_id * 12 + pitch_class]
                led_state = record >> 4 & 0x03
                delta = record & 0x0F
                if delta & 0x08:
                    delta -= 16
                self.assertEqual(
                    led_state,
                    2 if pitch_class == 0 else int(
                        scale.scale_membership(scale_id, pitch_class)))
                self.assertIn(delta, range(-6, 7))


if __name__ == "__main__":
    unittest.main()
