"""Run the velocity-slider patch on an 8051 interpreter next to the real stock code.

The sensor transaction, flash writes and MIDI transmit are replaced by Python
stand-ins; every other instruction executed is stock firmware or patch bytes.
"""

from __future__ import annotations

import random
import unittest

from firmware_tools import build_velocity_slider_patch as slider
from firmware_tools.build_relative_tilt_patch import build_image as build_v6
from firmware_tools.mcs51 import Machine

STOCK_IMAGE = None
V6_IMAGE = None
V7_IMAGE = None

SENSOR_TRANSACTION = 0x6894
FLASH_ERASE, FLASH_WRITE = 0x775A, 0x77DB
SEND_BODY = slider.STOCK_SEND_BODY
FRAMEBUFFER = 0x0819
LED_TABLE = 0x5DBB
ST, T0, STEP, SAVED = slider.XRAM_STATE, slider.XRAM_T0, slider.XRAM_STEP, slider.XRAM_SAVED
BITS, GAIN = slider.XRAM_BUTTON_BITS, slider.XRAM_VELOCITY_GAIN
VELOCITY = 4
WHITE_KEYS = [k for k in range(25) if k % 12 in slider.WHITE_PITCH_CLASSES]


def images():
    global STOCK_IMAGE, V6_IMAGE, V7_IMAGE
    if V7_IMAGE is None:
        V6_IMAGE, _ = build_v6()
        V7_IMAGE, _ = slider.build_image()
        from firmware_tools.build_relative_tilt_patch import ROOT
        STOCK_IMAGE = (ROOT / "firmware_analysis/kboard_1.2.2.bin").read_bytes()
    return STOCK_IMAGE, V6_IMAGE, V7_IMAGE


class Board:
    """Stock button/LED code with scripted sensors."""

    def __init__(self, image: bytes, per_call_ms: int = 10, start_tick: int = 0):
        self.m = Machine(image)
        self.readings = {channel: 0 for channel in range(8)}
        self.flash = []
        self.sent = []
        self.tick = start_tick
        self.per_call_ms = per_call_ms
        self.m.stubs[SENSOR_TRANSACTION] = self._sensor
        self.m.stubs[FLASH_WRITE] = lambda m: self.flash.append(("write", m.r(5)))
        self.m.stubs[FLASH_ERASE] = lambda m: self.flash.append(("erase",))
        self.m.stubs[SEND_BODY] = lambda m: self.sent.append((m.r(7), m.r(5), m.r(3)))
        self.m.xram[BITS] = 0xF7
        self._set_tick()

    def _sensor(self, m):
        m.set_r(7, self.readings[m.xram[slider.XRAM_CHANNEL]])

    def _set_tick(self):
        self.m.iram[0x4B] = self.tick >> 8 & 0xFF
        self.m.iram[0x4C] = self.tick & 0xFF

    @property
    def velocity_on(self):
        return bool(self.m.xram[BITS] & 4)

    def run(self, ms: int, **levels):
        """Advance about `ms` milliseconds, one sensor channel per call."""
        for channel, level in levels.items():
            self.readings[int(channel[1:])] = level
        end = self.tick + ms
        while self.tick < end:
            self.tick += self.per_call_ms
            self._set_tick()
            self.m.call(0x56E8)
    def press(self, ms, channel=VELOCITY, level=0x7F):
        self.run(ms, **{f"c{channel}": level})
        self.readings[channel] = 0

    def release(self, ms=100, channel=VELOCITY):
        self.run(ms, **{f"c{channel}": 0})

    def key_on(self, key):
        self.m.call(0x4C4F, r7=key)

    def send(self, status, d1=0x40, d2=0x40):
        self.m.call(0x7C67, r7=status, r5=d1, r3=d2)

    def led(self, key):
        row, col = self.m.code[LED_TABLE + 2 * key], self.m.code[LED_TABLE + 2 * key + 1]
        return self.m.xram[FRAMEBUFFER + row * 8 + col]

    @property
    def state(self):
        return self.m.xram[ST]

    @property
    def gain(self):
        return self.m.xram[GAIN] << 8 | self.m.xram[GAIN + 1]

    def set_gain(self, value):
        self.m.xram[GAIN], self.m.xram[GAIN + 1] = value >> 8, value & 0xFF

    def hold_into_mode(self):
        """Hold the Velocity button until the slider takes over, then release."""
        self.run(slider.HOLD_MS + 1000, **{f"c{VELOCITY}": 0x7F})
        assert self.state == 2, self.state
        self.release(200)
        assert self.state == 3, self.state


class SliderPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock, cls.v6, cls.v7 = images()

    # -- build ------------------------------------------------------------
    def test_patch_changes_only_hooks_and_free_flash(self):
        changed = [i for i in range(len(self.v6)) if self.v6[i] != self.v7[i]]
        hook_bytes = {a + i for a in slider.HOOKS for i in range(3)}
        routines = {slider.SEND_HOOK: 33, slider.KEYON_HOOK: 60, slider.TICK: 12,
                    slider.DRAW: 45, slider.READ_HOOK: 45, slider.READ_BODY: 300,
                    slider.WHITE_TABLE: 25, slider.GAIN_TABLE: 15}
        allowed = set(hook_bytes)
        for start, length in routines.items():
            allowed |= set(range(start, start + length))
        self.assertTrue(changed)
        self.assertEqual(set(changed) - allowed, set())
        for address in slider.HOOKS:
            self.assertNotIn(address, {0x7FF3, 0x7F10, 0x7FEF, 0x7EA6})
        self.assertEqual(self.v7[0xF000:0xF800], self.stock[0xF000:0xF800])

    def test_tables(self):
        gains = list(slider.gain_table())
        self.assertEqual((gains[0], gains[-1], len(gains)), (60, 254, 15))
        self.assertEqual(gains, sorted(set(gains)))
        white = list(slider.white_table())
        self.assertEqual([w for w in white if w != 0xFF], list(range(15)))
        self.assertEqual([k for k, w in enumerate(white) if w != 0xFF], WHITE_KEYS)

    # -- transparency -----------------------------------------------------
    def test_short_and_ordinary_presses_match_stock(self):
        for per_call in (1, 10, 40):
            for duration in (30, 150, 240, 400, 1500, slider.HOLD_MS - 200):
                stock = Board(self.stock, per_call)
                patched = Board(self.v7, per_call)
                for board in (stock, patched):
                    board.press(duration)
                    board.release(600)
                self.assertEqual(stock.m.xram[BITS], patched.m.xram[BITS], (per_call, duration))
                self.assertEqual(stock.flash, patched.flash, (per_call, duration))
                self.assertEqual(patched.state, 0)

    def test_random_button_scripts_match_stock(self):
        rng = random.Random(7)
        for _ in range(8):
            script = [(rng.choice((0, 1, 2, 3, 4, 6, 7)), rng.choice((80, 300, 900, slider.HOLD_MS - 300)),
                       rng.choice((0x7A, 0x7F)), rng.choice((50, 400)))
                      for _ in range(6)]
            stock, patched = Board(self.stock, 10), Board(self.v7, 10)
            for board in (stock, patched):
                for channel, duration, level, gap in script:
                    board.press(duration, channel, level)
                    board.release(gap, channel)
            for address in (BITS, 0x5F, 0x0350, 0x0306, 0x0963):
                self.assertEqual(stock.m.xram[address], patched.m.xram[address], (script, hex(address)))
            self.assertEqual(stock.flash, patched.flash, script)

    def test_other_channels_pass_through_during_slider(self):
        board = Board(self.v7)
        board.hold_into_mode()
        octave = board.m.xram[0x5F]
        board.press(1500, channel=7)
        board.release(300, channel=7)
        self.assertEqual(board.m.xram[0x5F], min(octave + 1, 5))
        self.assertEqual(board.state, 3)

    # -- entering ---------------------------------------------------------
    def test_hold_enters_and_undoes_the_toggle(self):
        for per_call in (1, 10, 40):
            for start_on in (True, False):
                board = Board(self.v7, per_call)
                board.m.xram[BITS] = 0xF7 if start_on else 0xF3
                board.run(slider.HOLD_MS - 200, **{f"c{VELOCITY}": 0x7F})
                self.assertEqual(board.state, 1, per_call)
                self.assertNotEqual(board.velocity_on, start_on)  # stock toggled at 0.25 s
                board.run(400)
                self.assertEqual(board.state, 2, per_call)
                self.assertEqual(board.velocity_on, start_on)
                self.assertEqual(board.m.xram[BITS] & 0xF0, 0xF0)
                self.assertEqual(board.flash[-1], ("write", board.m.xram[BITS]))
                board.run(3000)  # holding on must not toggle again
                self.assertEqual(board.velocity_on, start_on)
                self.assertEqual(board.state, 2)

    def test_hold_across_tick_wrap(self):
        board = Board(self.v7, 10, start_tick=0xFFFF - 1500)
        board.run(slider.HOLD_MS - 200, **{f"c{VELOCITY}": 0x7F})
        self.assertEqual(board.state, 1)
        board.run(400)
        self.assertEqual(board.state, 2)

    def test_torn_tick_read_is_retried(self):
        board = Board(self.v7)
        m = board.m
        m.iram[0x4B], m.iram[0x4C] = 0x12, 0xFF
        fired = []

        def trace(machine, pc, op):
            if pc == slider.TICK + 3 and not fired:  # between the high and low reads
                fired.append(True)
                machine.iram[0x4B], machine.iram[0x4C] = 0x13, 0x00
        m.trace = trace
        m.call(slider.TICK)
        m.trace = None
        self.assertEqual((m.r(6), m.r(7)), (0x13, 0x00))
        self.assertTrue(fired)

    def test_jittery_finger_still_enters_and_restores_the_bit(self):
        for start_on in (True, False):
            board = Board(self.v7)
            board.m.xram[BITS] = 0xF7 if start_on else 0xF3
            for _ in range(slider.HOLD_MS // 760 + 1):  # dips well below the stock release threshold
                board.run(700, **{f"c{VELOCITY}": 0x7F})
                board.run(60, **{f"c{VELOCITY}": 0x62})
            board.run(400, **{f"c{VELOCITY}": 0x7F})
            self.assertEqual(board.state, 2)
            self.assertEqual(board.velocity_on, start_on)
            for _ in range(4):  # and dips while still holding after entry
                board.run(500, **{f"c{VELOCITY}": 0x7F})
                board.run(60, **{f"c{VELOCITY}": 0x62})
                self.assertEqual(board.state, 2)
            self.assertEqual(board.velocity_on, start_on)
            board.release(200)
            self.assertEqual(board.state, 3)
    def test_letting_go_below_the_hold_threshold_cancels_the_hold(self):
        board = Board(self.v7)
        part = slider.HOLD_MS * 3 // 5
        board.run(part, **{f"c{VELOCITY}": 0x7F})
        board.run(100, **{f"c{VELOCITY}": 0x20})
        self.assertEqual(board.state, 0)
        board.run(part, **{f"c{VELOCITY}": 0x7F})
        self.assertEqual(board.state, 1)
    def test_release_before_the_hold_never_enters(self):
        board = Board(self.v7)
        board.press(slider.HOLD_MS - 100)
        board.release(500)
        self.assertEqual(board.state, 0)

    def test_registers_are_preserved_by_the_read_hook(self):
        board = Board(self.v7)
        m = board.m
        rng = random.Random(3)
        for channel in range(8):
            for state in range(5):
                m.xram[slider.XRAM_CHANNEL] = channel
                m.xram[slider.XRAM_READING] = rng.choice((0, 0x7F, 0x75))
                m.xram[ST] = state
                m.xram[slider.XRAM_T0:slider.XRAM_T0 + 2] = bytes((0, 0))
                before = [rng.randrange(256) for _ in range(9)]
                for n in range(8):
                    m.set_r(n, before[n])
                m.b = before[8]
                sp = m.sp
                m.call(slider.READ_HOOK)
                after = [m.r(n) for n in range(8)] + [m.b]
                self.assertEqual(after, before, (channel, state))
                self.assertEqual(m.sp, sp)
                self.assertEqual(m.dptr, slider.XRAM_READING)

    # -- slider behaviour -------------------------------------------------
    def test_start_step_is_nearest_current_gain(self):
        table = list(slider.gain_table())
        for gain in (0, 59, 60, 66, 67, 74, 100, 127, 129, 199, 240, 253, 254, 255, 256, 900):
            board = Board(self.v7)
            board.set_gain(gain)
            board.hold_into_mode()
            candidates = [i for i, value in enumerate(table) if value <= gain + 6]
            expected = max(candidates) if candidates else 0
            if gain > 255:
                expected = 14
            self.assertEqual(board.m.xram[STEP], expected, gain)
            self.assertEqual(board.gain, gain)  # entering alone changes nothing

    def test_bar_lights_white_keys_up_to_the_step(self):
        for step in (0, 1, 7, 13, 14):
            board = Board(self.v7)
            board.set_gain(int(slider.gain_table()[step]))
            board.hold_into_mode()
            board.run(200)
            for key in range(25):
                lit = key in WHITE_KEYS and WHITE_KEYS.index(key) <= step
                self.assertEqual(board.led(key), 0xFF if lit else 0, (step, key))

    def test_white_keys_set_gain_and_black_keys_do_nothing(self):
        board = Board(self.v7)
        board.hold_into_mode()
        table = list(slider.gain_table())
        for step, key in enumerate(WHITE_KEYS):
            board.m.xram[0x0361] = 0
            board.m.call(slider.KEYON_HOOK, r7=key)
            self.assertEqual(board.gain, table[step], key)
            self.assertEqual(board.m.xram[STEP], step)
            self.assertEqual(board.m.dptr, 0x0889)
        before = (board.gain, board.m.xram[STEP])
        for key in (1, 3, 6, 8, 10, 13, 15, 18, 20, 22, 25, 60, 255):
            board.m.call(slider.KEYON_HOOK, r7=key)
            self.assertEqual((board.gain, board.m.xram[STEP]), before, key)
        board.run(200)  # one sensor channel is polled per call, so allow a full round
        for key in range(25):
            lit = key in WHITE_KEYS
            self.assertEqual(board.led(key), 0xFF if lit else 0, key)

    def test_keys_do_not_change_gain_outside_the_slider(self):
        for setup in (lambda b: None, lambda b: b.press(200)):
            board = Board(self.v7)
            board.set_gain(100)
            setup(board)
            board.m.call(slider.KEYON_HOOK, r7=0)
            self.assertEqual(board.gain, 100)

    def test_midi_is_muted_only_while_the_slider_is_active(self):
        board = Board(self.v7)
        board.send(0x92)
        board.send(0xB1)
        self.assertEqual(len(board.sent), 2)
        board.sent.clear()
        board.hold_into_mode()
        board.send(0x80)
        board.send(0x8F)
        board.send(0xF0)
        board.send(0xFE)
        board.send(0x45)
        passed = [s[0] for s in board.sent]
        self.assertEqual(passed, [0x80, 0x8F, 0xF0, 0xFE, 0x45])
        board.sent.clear()
        for status in (0x90, 0x9F, 0xA3, 0xB0, 0xB7, 0xC2, 0xD1, 0xE0, 0xEF):
            board.send(status)
        self.assertEqual(board.sent, [])

    def test_real_stock_note_on_is_muted_only_in_the_slider(self):
        stock = Board(self.stock)
        idle = Board(self.v7)
        active = Board(self.v7)
        for board in (stock, idle, active):
            board.m.xram[0x0352] = 1      # USB channel, as in a stored preset
        active.hold_into_mode()
        for board in (stock, idle, active):
            board.key_on(9)
        self.assertEqual(len(stock.sent), 1)
        self.assertEqual(idle.sent, stock.sent)     # untouched outside the slider
        self.assertEqual(active.sent, [])          # no note-on while choosing
        self.assertEqual(active.gain, slider.gain_table()[WHITE_KEYS.index(9)])
        active.run(200)
        for key in WHITE_KEYS:
            lit = WHITE_KEYS.index(key) <= WHITE_KEYS.index(9)
            self.assertEqual(active.led(key), 0xFF if lit else 0, key)
    def test_send_hook_keeps_arguments_for_the_stock_sender(self):
        board = Board(self.v7)
        board.send(0x93, 0x3C, 0x51)
        self.assertEqual(board.sent, [(0x93, 0x3C, 0x51)])
        self.assertEqual(board.m.dptr, 0x08A5)

    # -- exiting ----------------------------------------------------------
    def test_exit_press_clears_leds_and_is_never_seen_by_stock(self):
        for start_on in (True, False):
            board = Board(self.v7)
            board.m.xram[BITS] = 0xF7 if start_on else 0xF3
            board.hold_into_mode()
            board.run(200)
            board.run(1200, **{f"c{VELOCITY}": 0x7F})  # long exit press
            self.assertEqual(board.state, 4)
            self.assertEqual(board.velocity_on, start_on)
            self.assertTrue(all(board.led(k) == 0 for k in range(25)))
            flash_before = len(board.flash)
            board.release(200)
            self.assertEqual(board.state, 0)
            self.assertEqual(board.velocity_on, start_on)
            self.assertEqual(len(board.flash), flash_before)
            board.send(0x90)  # music plays again
            self.assertEqual(board.sent[-1][0], 0x90)
            # The stock toggle works again afterwards.
            board.press(600)
            board.release(300)
            self.assertNotEqual(board.velocity_on, start_on)

    def test_exit_press_is_muted_out_of_slider_but_notes_play_during_exit(self):
        board = Board(self.v7)
        board.hold_into_mode()
        board.run(100, **{f"c{VELOCITY}": 0x7F})
        self.assertEqual(board.state, 4)
        board.send(0x90)
        self.assertEqual([s[0] for s in board.sent], [0x90])
        before = board.gain
        board.m.call(slider.KEYON_HOOK, r7=2)
        self.assertEqual(board.gain, before)  # keys play normally while the exit press is held

    def test_slider_can_be_reentered(self):
        board = Board(self.v7)
        board.hold_into_mode()
        board.run(100, **{f"c{VELOCITY}": 0x7F})
        board.release(200)
        self.assertEqual(board.state, 0)
        board.hold_into_mode()
        self.assertEqual(board.state, 3)

    def test_gain_survives_leaving(self):
        board = Board(self.v7)
        board.hold_into_mode()
        board.m.call(slider.KEYON_HOOK, r7=12)
        expected = board.gain
        board.run(100, **{f"c{VELOCITY}": 0x7F})
        board.release(200)
        self.assertEqual(board.gain, expected)
        self.assertEqual(expected, slider.gain_table()[WHITE_KEYS.index(12)])


if __name__ == "__main__":
    unittest.main()
