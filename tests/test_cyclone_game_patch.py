"""Exercise the Cyclone game hooks with the project 8051 interpreter."""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from firmware_tools import build_cyclone_game_patch as game
from firmware_tools import build_custom_firmware as custom
from firmware_tools import build_sensor_config_patch as menus
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.extract_kmi_firmware import build_image as decode_image, extract
from firmware_tools.mcs51 import Machine


WHITE_KEYS = (0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24)
GOALS = {
    1: (1, 3), 4: (6, 8), 5: (8, 10),
    8: (13, 15), 11: (18, 20), 12: (20, 22),
}


def set_tick(machine: Machine, tick: int) -> None:
    machine.iram[0x4B] = tick >> 8 & 0xFF
    machine.iram[0x4C] = tick & 0xFF


def sensor(machine: Machine, routine: int, channel: int, reading: int, tick: int) -> None:
    machine.xram[velocity.XRAM_CHANNEL] = channel
    machine.xram[velocity.XRAM_READING] = reading
    set_tick(machine, tick)
    machine.call(routine)


class CycloneGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.sysex = custom.build_image()

    def machine(self):
        m = Machine(self.image)
        m.stubs[velocity.STOCK_SET_LED] = lambda _: None
        m.stubs[velocity.STOCK_LOG_STATE] = lambda _: None
        return m

    def test_chord_enters_after_one_second_and_waits_for_release(self):
        m = self.machine()
        m.xram[velocity.XRAM_BUTTON_BITS] = 0xF7
        for channel in (0, 1, 4):
            sensor(m, game.IDLE_INPUT, channel, 0x7F, 10)
        self.assertEqual(m.xram[game.BUTTON_MASK], 7)
        self.assertEqual(m.xram[game.GAME_STATE], 0)
        sensor(m, game.IDLE_INPUT, 0, 0x7F, 1009)
        self.assertEqual(m.xram[game.GAME_STATE], 0)
        sensor(m, game.IDLE_INPUT, 0, 0x7F, 1010)
        self.assertEqual(m.xram[game.GAME_STATE], 1)
        self.assertEqual(m.xram[velocity.XRAM_STATE], 0)
        self.assertEqual(m.xram[velocity.XRAM_BUTTON_BITS] & 7, 7)
        for channel in (0, 1, 4):
            sensor(m, game.WAIT_INPUT, channel, 0, 1011 + channel)
        self.assertEqual(m.xram[game.GAME_STATE], 2)
        self.assertEqual(m.xram[game.SCORE], 0)
        self.assertEqual(m.xram[game.SPEED], game.START_SPEED)
        self.assertIn(m.xram[game.TARGET], GOALS)
        self.assertIn(m.xram[game.CURSOR], range(15))

    def test_real_stock_sensor_loop_enters_game_and_restores_button_toggles(self):
        m = self.machine()
        levels = {channel: 0 for channel in range(8)}
        levels.update({0: 0x7F, 1: 0x7F, 4: 0x7F})
        m.stubs[0x6894] = lambda machine: machine.set_r(7, levels[machine.xram[velocity.XRAM_CHANNEL]])
        m.xram[velocity.XRAM_BUTTON_BITS] = 0xF2
        for tick in range(1700):
            set_tick(m, tick)
            m.call(0x56E8)
        self.assertEqual(m.xram[game.GAME_STATE], 1)
        self.assertEqual(m.xram[velocity.XRAM_BUTTON_BITS] & 7, 2)
        levels.update({0: 0, 1: 0, 4: 0})
        for tick in range(1700, 1750):
            set_tick(m, tick)
            m.call(0x56E8)
        self.assertEqual(m.xram[game.GAME_STATE], 2)
        self.assertEqual(m.xram[velocity.XRAM_BUTTON_BITS] & 7, 2)

    def test_draws_only_cursor_white_key_and_two_goal_black_keys(self):
        m = Machine(self.image)
        m.xram[game.GAME_STATE] = 2
        m.xram[game.CURSOR] = 1
        m.xram[game.TARGET] = 1
        m.call(game.DRAW_GAME)
        lit = set()
        for key in range(25):
            row = m.code[0x5DBB + 2 * key]
            column = m.code[0x5DBC + 2 * key]
            if m.xram[0x0819 + row * 8 + column]:
                lit.add(key)
        self.assertEqual(lit, {1, 2, 3})
        self.assertEqual(m.xram[game.DRAW_FLAG], 0)

    def test_entry_timer_uses_tick_wrap_and_aborts_if_chord_breaks(self):
        m = self.machine()
        start = 0xFFF0
        for channel in (0, 1, 4):
            sensor(m, game.IDLE_INPUT, channel, 0x7F, start)
        sensor(m, game.IDLE_INPUT, 0, 0x7F, 0x0020)
        self.assertEqual(m.xram[game.GAME_STATE], 0)
        sensor(m, game.IDLE_INPUT, 0, 0, 0x0030)
        self.assertEqual(m.xram[game.BUTTON_MASK], 6)
        sensor(m, game.IDLE_INPUT, 0, 0x7F, 0x0040)
        self.assertEqual(m.xram[game.HOLD_ACTIVE], 1)
        self.assertEqual(m.xram[game.HOLD_LO], 0x40)
        self.assertEqual(m.xram[game.GAME_STATE], 0)

    def test_active_menu_defers_game_entry(self):
        m = self.machine()
        m.xram[velocity.XRAM_STATE] = 3
        for channel in (0, 1, 4):
            sensor(m, game.IDLE_INPUT, channel, 0x7F, 0)
        sensor(m, game.IDLE_INPUT, 0, 0x7F, 1000)
        self.assertEqual(m.xram[game.GAME_STATE], 0)
        self.assertEqual(m.xram[velocity.XRAM_STATE], 3)

    def test_target_table_pairs_exact_black_key_leds(self):
        self.assertEqual(len(game.build_tables()[game.WHITE_STEPS]), 15)
        goals = game.build_tables()
        for index, step in enumerate(goals[game.GOAL_STEPS]):
            self.assertEqual((goals[game.GOAL_LEFT][index], goals[game.GOAL_RIGHT][index]), GOALS[step])
            self.assertLess(goals[game.GOAL_LEFT][index], WHITE_KEYS[step])
            self.assertLess(WHITE_KEYS[step], goals[game.GOAL_RIGHT][index])

    def test_hit_scores_and_speeds_up_but_miss_ends_immediately(self):
        m = self.machine()
        m.xram[game.GAME_STATE] = 2
        m.xram[game.CURSOR] = 5
        m.xram[game.TARGET] = 5
        m.xram[game.SPEED] = 84
        m.xram[game.LAST_TICK] = 0
        m.xram[game.PAD_TOUCH_STATE] = 1
        sensor(m, game.PLAY_INPUT, 2, 0x7F, 1)
        self.assertEqual(m.xram[game.GAME_STATE], 2)
        self.assertEqual(m.xram[game.SCORE], 1)
        self.assertEqual(m.xram[game.SPEED], 72)
        self.assertEqual(m.xram[game.STOP_HELD], 1)

        # Releasing the Bend Pad re-arms the stop; the next bad stop is final.
        m.xram[game.PAD_TOUCH_STATE] = 0
        sensor(m, game.PLAY_INPUT, 2, 0, 2)
        self.assertEqual(m.xram[game.STOP_HELD], 0)
        m.xram[game.CURSOR] = (m.xram[game.TARGET] + 1) % 15
        m.xram[game.PAD_TOUCH_STATE] = 1
        sensor(m, game.PLAY_INPUT, 2, 0x7F, 3)
        self.assertEqual(m.xram[game.GAME_STATE], 3)
        self.assertEqual(m.xram[game.SCORE], 1)

    def test_cursor_can_pass_target_without_failing_and_speed_has_a_floor(self):
        m = self.machine()
        m.xram[game.GAME_STATE] = 2
        m.xram[game.TARGET] = 7
        m.xram[game.CURSOR] = 0
        m.xram[game.SPEED] = game.START_SPEED
        m.xram[game.LAST_TICK] = 0
        for tick in range(1, 1000):
            set_tick(m, tick)
            m.call(game.MOVE_CURSOR)
        self.assertEqual(m.xram[game.GAME_STATE], 2)
        self.assertNotEqual(m.xram[game.CURSOR], 0)

        # Repeated accurate stops reduce the period to its 48 ms floor.
        m.xram[game.STOP_HELD] = 0
        for level in range(20):
            m.xram[game.CURSOR] = m.xram[game.TARGET]
            m.xram[game.PAD_TOUCH_STATE] = 1
            sensor(m, game.PLAY_INPUT, 2, 0x7F, 1000 + level)
            m.xram[game.PAD_TOUCH_STATE] = 0
            sensor(m, game.PLAY_INPUT, 2, 0, 1000 + level)
        self.assertEqual(m.xram[game.SPEED], game.MIN_SPEED)
        self.assertEqual(m.xram[game.SCORE], 20)

    def test_game_over_keeps_failure_context_on_the_leds(self):
        m = Machine(self.image)
        m.xram[game.GAME_STATE] = 2
        m.xram[game.SCORE] = 0
        m.xram[game.CURSOR] = 6
        m.xram[game.TARGET] = 1
        m.call(game.FAIL_GAME)
        lit = set()
        for key in range(25):
            row = m.code[0x5DBB + 2 * key]
            column = m.code[0x5DBC + 2 * key]
            if m.xram[0x0819 + row * 8 + column]:
                lit.add(key)
        self.assertEqual(lit, {1, 3, 11})
        self.assertEqual(m.xram[game.GAME_STATE], 3)

    def test_cursor_cycles_and_bounces_at_both_ends(self):
        m = self.machine()
        m.xram[game.SPEED] = 48
        for direction, cursor, tick, expected_cursor, expected_dir in (
            (1, 14, 48, 13, 0),
            (0, 14, 96, 13, 0),
            (0, 0, 144, 1, 1),
            (1, 0, 192, 1, 1),
        ):
            m.xram[game.DIRECTION] = direction
            m.xram[game.CURSOR] = cursor
            m.xram[game.LAST_TICK] = tick - 48
            set_tick(m, tick)
            m.call(game.MOVE_CURSOR)
            self.assertEqual(m.xram[game.CURSOR], expected_cursor)
            self.assertEqual(m.xram[game.DIRECTION], expected_dir)

    def test_each_level_advances_random_target_and_direction(self):
        m = self.machine()
        m.xram[game.GAME_STATE] = 2
        set_tick(m, 100)
        targets, directions = set(), set()
        for _ in range(16):
            m.call(game.START_GAME)
            targets.add(m.xram[game.TARGET])
            directions.add(m.xram[game.DIRECTION])
        self.assertEqual(directions, {0, 1})
        self.assertTrue(targets.issubset(set(game.build_tables()[game.GOAL_STEPS])))
        self.assertGreater(len(targets), 1)

    def test_exit_is_swallowed_until_release_and_restores_button_bits(self):
        for exit_channel in (0, 1, 4):
            with self.subTest(exit_channel=exit_channel):
                m = self.machine()
                m.xram[game.GAME_STATE] = 2
                m.xram[game.SAVED_VALID] = 1
                m.xram[game.SAVED_BITS] = 5
                m.xram[velocity.XRAM_BUTTON_BITS] = 0xF2
                sensor(m, game.PLAY_INPUT, exit_channel, 0x7F, 1)
                self.assertEqual(m.xram[game.GAME_STATE], 4)
                self.assertEqual(m.xram[velocity.XRAM_BUTTON_BITS] & 7, 5)
                for channel in (0, 1, 4):
                    sensor(m, game.EXIT_WAIT_INPUT, channel, 0, 10 + channel)
                self.assertEqual(m.xram[game.GAME_STATE], 0)

    def test_game_suppresses_midi_and_non_game_key_led_writes(self):
        m = self.machine()
        sent = []
        m.stubs[velocity.STOCK_SEND_BODY] = lambda machine: sent.append(machine.r(7))
        m.xram[game.GAME_STATE] = 2
        for status in (0x90, 0xA0, 0xB0, 0xE0):
            m.call(0x7C67, r7=status)
        self.assertEqual(sent, [])
        m.call(0x7C67, r7=0x80)
        self.assertEqual(sent, [0x80])

        before = bytes(m.xram[0x0819:0x0839])
        m.call(0x7AFB, r7=7, r5=0xFF)
        self.assertEqual(bytes(m.xram[0x0819:0x0839]), before)

    def test_menu_path_and_stock_preset_pages_survive_game_layer(self):
        slider_image, _ = velocity.build_image()
        menu_image, _ = menus.build_image(slider_image)
        image, _ = game.build_image(menu_image)
        stock_records, _ = extract(velocity.STOCK_SYX)
        stock, _, _ = decode_image(stock_records)
        self.assertEqual(image[0xF000:0xF800], stock[0xF000:0xF800])
        m = self.machine()
        m.xram[game.GAME_STATE] = 0
        m.xram[velocity.XRAM_CHANNEL] = 2
        m.xram[velocity.XRAM_READING] = 0x55
        called = []
        m.stubs[game.MENU_DISPATCH] = lambda machine: called.append(machine.r(7))
        m.call(game.GAME_DISPATCH, r7=0x55)
        self.assertEqual(called, [0x55])

    def test_unflashed_update_sysex_roundtrips_complete_game_image(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cyclone.syx"
            path.write_bytes(self.sysex)
            records, _ = extract(path)
        rebuilt, _, _ = decode_image(records)
        self.assertEqual(bytes(rebuilt), self.image)
        self.assertEqual(self.image[custom.VERSION_BYTE_ADDRESS], custom.CUSTOM_VERSION_BYTE)


if __name__ == "__main__":
    unittest.main()
