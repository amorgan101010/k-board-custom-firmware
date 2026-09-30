"""Exercise the v11 on-board menus and preset rewrite on the 8051 model."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from firmware_tools import build_sensor_config_patch as config
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.mcs51 import Machine
from kboard_protocol import MODEL


class SensorConfigPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.sysex = config.build_image()

    def test_white_keys_set_each_sensor_field(self):
        machine = Machine(self.image)
        white_keys = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24]
        fields = (
            (0, velocity.XRAM_VELOCITY_GAIN, velocity.gain_table(), False),
            (1, config.PRESSURE_GAIN, velocity.gain_table(), False),
            (2, config.TILT_SENSITIVITY, config.build_tilt_table(), True),
        )
        for mode, address, values, inverse in fields:
            machine.xram[velocity.XRAM_STATE] = 3
            machine.xram[config.MODE] = mode
            for step, key in enumerate(white_keys):
                machine.call(0x4C4F, r7=key)
                expected = 70 - values[step] if inverse else values[step]
                actual = (machine.xram[address] if inverse else
                          machine.xram[address] * 256 + machine.xram[address + 1])
                self.assertEqual(actual, expected, (mode, step))

    @staticmethod
    def _sensor_call(machine, channel, level, tick):
        machine.xram[velocity.XRAM_CHANNEL] = channel
        machine.xram[velocity.XRAM_READING] = level
        machine.iram[0x4B] = (tick >> 8) & 0xFF
        machine.iram[0x4C] = tick & 0xFF
        machine.call(velocity.READ_HOOK)

    @staticmethod
    def _button_led(machine, led_index):
        row = machine.code[0x5DBB + 2 * led_index]
        column = machine.code[0x5DBB + 2 * led_index + 1]
        return machine.xram[0x0819 + row * 8 + column]

    def test_one_second_entry_switch_short_exit_and_button_led_restore(self):
        machine = Machine(self.image)
        logged, saved = [], []
        machine.stubs[velocity.STOCK_LOG_STATE] = lambda m: logged.append(m.xram[velocity.XRAM_BUTTON_BITS])
        machine.stubs[config.SAVE_ROUTINE] = lambda m: saved.append(m.xram[velocity.XRAM_STATE])
        # All three expression buttons were on before opening settings.
        machine.xram[velocity.XRAM_BUTTON_BITS] = 0xF7

        self._sensor_call(machine, 4, 0x7F, 0)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 1)
        self._sensor_call(machine, 4, 0x7F, 1000)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 2)
        self.assertEqual(machine.xram[config.MODE], 0)
        machine.call(0x764D)
        self.assertEqual(self._button_led(machine, 29), 0xFF)  # Velocity
        self.assertEqual(self._button_led(machine, 26), 0)     # Pressure
        self.assertEqual(self._button_led(machine, 25), 0)     # Tilt
        self._sensor_call(machine, 4, 0, 1010)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 3)

        # A one-second hold on Pressure switches pages without exiting.
        self._sensor_call(machine, 1, 0x7F, 1020)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 5)
        self._sensor_call(machine, 1, 0x7F, 2020)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 2)
        self.assertEqual(machine.xram[config.MODE], 1)
        machine.call(0x764D)
        self.assertEqual(self._button_led(machine, 29), 0)
        self.assertEqual(self._button_led(machine, 26), 0xFF)
        self.assertEqual(self._button_led(machine, 25), 0)
        self._sensor_call(machine, 1, 0, 2030)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 3)

        # A short Tilt press exits; all original button states return.
        self._sensor_call(machine, 0, 0x7F, 2040)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 5)
        self._sensor_call(machine, 0, 0, 2200)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 0)
        self.assertEqual(machine.xram[velocity.XRAM_BUTTON_BITS] & 0x07, 0x07)
        self.assertEqual(len(saved), 1)
        machine.call(0x764D)
        for led in (25, 26, 29):
            self.assertEqual(self._button_led(machine, led), 0xFF)
        self.assertTrue(logged)

    def test_short_exit_restores_buttons_that_were_all_off(self):
        machine = Machine(self.image)
        machine.stubs[velocity.STOCK_LOG_STATE] = lambda m: None
        machine.stubs[config.SAVE_ROUTINE] = lambda m: None
        machine.xram[config.SESSION_BITS] = 0
        machine.xram[velocity.XRAM_BUTTON_BITS] = 0xF4  # temporary Velocity page light
        machine.xram[velocity.XRAM_STATE] = 5
        machine.xram[config.MODE] = 0
        machine.xram[config.PENDING_MODE] = 1
        self._sensor_call(machine, 1, 0, 10)
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 0)
        self.assertEqual(machine.xram[velocity.XRAM_BUTTON_BITS] & 0x07, 0)
        machine.call(0x764D)
        for led in (25, 26, 29):
            self.assertEqual(self._button_led(machine, led), 0)

    def test_pressure_and_tilt_addresses_match_the_preset_image_layout(self):
        fields = {"Keyboard_CC_00_Gain": config.PRESSURE_GAIN,
                  "Globals_Tilt_Sensitivity": config.TILT_SENSITIVITY}
        cursor = 1  # preset image byte zero is the slot number
        addresses = {}
        for name in MODEL:
            if name == "Preset_Name":
                continue
            if name in fields:
                addresses[name] = 0x0347 + cursor
            cursor += 2 if "Gain" in name and name != "Globals_Gain" else 1
        self.assertEqual(addresses, fields)

    def test_exit_rewrites_the_complete_slot_zero_preset(self):
        machine = Machine(self.image)
        erased, writes = [], []
        machine.stubs[config.PRESET_ERASE] = lambda m: erased.append(m.r(7))
        machine.stubs[config.PRESET_WRITE] = lambda m: writes.append(
            (m.r(3), m.r(2), m.r(1), m.r(5)))
        machine.stubs[velocity.STOCK_LOG_STATE] = lambda m: None
        preset = bytes((index * 37 + 11) & 0xFF for index in range(config.PRESET_LENGTH))
        machine.xram[config.PRESET_IMAGE:config.PRESET_IMAGE + config.PRESET_LENGTH] = preset
        machine.xram[velocity.XRAM_STATE] = 5
        machine.xram[config.MODE] = 2
        machine.xram[config.PENDING_MODE] = 2
        machine.xram[velocity.XRAM_CHANNEL] = 0
        machine.xram[velocity.XRAM_READING] = 0

        machine.call(velocity.READ_HOOK)

        self.assertEqual(erased, [0])
        self.assertEqual(len(writes), config.PRESET_LENGTH)
        self.assertEqual(writes, [
            (0xFF, 0xF0 + (index >> 8), index & 0xFF, value)
            for index, value in enumerate(preset)
        ])
        self.assertEqual(machine.xram[velocity.XRAM_STATE], 0)

    def test_image_round_trip_preserves_stock_preset_pages(self):
        from firmware_tools.build_relative_tilt_patch import STOCK_SYX
        from firmware_tools.extract_kmi_firmware import extract

        stock_records, _ = extract(STOCK_SYX)
        with TemporaryDirectory() as directory:
            sysex_path = Path(directory) / "v10.syx"
            sysex_path.write_bytes(self.sysex)
            records, _ = extract(sysex_path)
        from firmware_tools.extract_kmi_firmware import build_image
        stock, _, _ = build_image(stock_records)
        patched, _, _ = build_image(records)
        self.assertEqual(patched, self.image)
        self.assertEqual(self.image[0xF000:0xF800], stock[0xF000:0xF800])


if __name__ == "__main__":
    unittest.main()
