"""Execute the emitted 8051 hook bytes for the native tilt firmware patch."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from firmware_tools.build_relative_tilt_patch import (
    BEND, NOTE_OFF, NOTE_ON, PAD, SCALE14, STOCK_SYX,
    XDATA_BEND_FLAGS, XDATA_BEND_FLAGS_ESCAPE, XDATA_TILT_BASELINE,
    XDATA_TILT_CURRENT, XDATA_TILT_LOW, build_image, build_scale14,
)
from firmware_tools.extract_kmi_firmware import build_image as decode_image, extract
from firmware_tools.mcs51 import Machine


class HookMachine:
    """The 8051 instructions used in the patch, with real branch bytes."""

    def __init__(self, image: bytes):
        self.image = image
        self.xram = bytearray(4096)  # stock startup clears all 4 KB
        self.iram = bytearray(128)
        self.iram[0x4D] = 10  # Bend Pad touch threshold
        self.r = [0] * 8
        self.a = 0
        self.b = 0
        self.carry = 0
        self.dptr = 0
        self.stack = []
        self.sent = []

    def run(self, start: int, end: int) -> bool:
        pc = start
        returns = []
        for _ in range(1000):
            if pc == end:
                return True
            op = self.image[pc]
            if op == 0x90:  # MOV DPTR,#address
                self.dptr = self.image[pc + 1] << 8 | self.image[pc + 2]
                pc += 3
            elif op == 0xE0:  # MOVX A,@DPTR
                self.a = self.xram[self.dptr]
                pc += 1
            elif op == 0xF0:  # MOVX @DPTR,A
                self.xram[self.dptr] = self.a
                pc += 1
            elif op == 0xE4:  # CLR A
                self.a = 0
                pc += 1
            elif op == 0x74:  # MOV A,#data
                self.a = self.image[pc + 1]
                pc += 2
            elif 0xE8 <= op <= 0xEF:  # MOV A,Rn
                self.a = self.r[op & 7]
                pc += 1
            elif 0xF8 <= op <= 0xFF:  # MOV Rn,A
                self.r[op & 7] = self.a
                pc += 1
            elif op == 0xAE:  # MOV R6,direct (stock instructions replayed by hook)
                assert self.image[pc + 1] == 0x07
                self.r[6] = self.r[7]
                pc += 2
            elif 0x78 <= op <= 0x7F:  # MOV Rn,#data
                self.r[op & 7] = self.image[pc + 1]
                pc += 2
            elif op == 0x54:  # ANL A,#data
                self.a &= self.image[pc + 1]
                pc += 2
            elif op == 0x44:  # ORL A,#data
                self.a |= self.image[pc + 1]
                pc += 2
            elif op == 0xF5:  # MOV direct,A
                register = self.image[pc + 1]
                if register == 0x82:
                    self.dptr = self.dptr & 0xFF00 | self.a
                elif register == 0xF0:
                    self.b = self.a
                else:
                    raise AssertionError(f"unexpected direct register 0x{register:02x}")
                pc += 2
            elif op == 0xE5:  # MOV A,direct
                assert self.image[pc + 1] == 0xF0
                self.a = self.b
                pc += 2
            elif op == 0x75:  # MOV direct,#data
                register, value = self.image[pc + 1:pc + 3]
                if register == 0x83:
                    self.dptr = value << 8 | self.dptr & 0xFF
                else:
                    raise AssertionError(f"unexpected direct register 0x{register:02x}")
                pc += 3
            elif op == 0xC3:  # CLR C
                self.carry = 0
                pc += 1
            elif 0x98 <= op <= 0x9F:  # SUBB A,Rn
                result = self.a - self.r[op & 7] - self.carry
                self.carry = int(result < 0)
                self.a = result & 0xFF
                pc += 1
            elif op == 0x94:  # SUBB A,#data
                result = self.a - self.image[pc + 1] - self.carry
                self.carry = int(result < 0)
                self.a = result & 0xFF
                pc += 2
            elif op == 0x95:  # SUBB A,direct
                direct = self.image[pc + 1]
                result = self.a - self.iram[direct] - self.carry
                self.carry = int(result < 0)
                self.a = result & 0xFF
                pc += 2
            elif op == 0x24:  # ADD A,#data
                result = self.a + self.image[pc + 1]
                self.carry = int(result > 255)
                self.a = result & 0xFF
                pc += 2
            elif 0x28 <= op <= 0x2F:  # ADD A,Rn
                result = self.a + self.r[op & 7]
                self.carry = int(result > 255)
                self.a = result & 255
                pc += 1
            elif op == 0x04:  # INC A
                self.a = self.a + 1 & 0xFF
                pc += 1
            elif op == 0x14:  # DEC A
                self.a = self.a - 1 & 0xFF
                pc += 1
            elif op == 0xF4:  # CPL A
                self.a ^= 0xFF
                pc += 1
            elif op == 0x03:  # RR A
                self.a = (self.a >> 1 | self.a << 7) & 0xFF
                pc += 1
            elif op == 0x23:  # RL A
                self.a = (self.a << 1 | self.a >> 7) & 0xFF
                pc += 1
            elif 0x48 <= op <= 0x4F:  # ORL A,Rn
                self.a |= self.r[op & 7]
                pc += 1
            elif op == 0xA4:  # MUL AB
                product = self.a * self.b
                self.a, self.b = product & 0xFF, product >> 8
                self.carry = 0
                pc += 1
            elif op == 0xC0:  # PUSH direct
                direct = self.image[pc + 1]
                assert direct in (0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0xF0)
                self.stack.append((direct, self.r[direct] if direct != 0xF0 else self.b))
                pc += 2
            elif op == 0xD0:  # POP direct
                direct = self.image[pc + 1]
                saved_direct, value = self.stack.pop()
                assert direct == saved_direct
                if direct == 0xF0:
                    self.b = value
                else:
                    self.r[direct] = value
                pc += 2
            elif op == 0xB4:  # CJNE A,#data,relative
                value = self.image[pc + 1]
                self.carry = int(self.a < value)
                pc = self._relative(pc + 3, self.image[pc + 2]) if self.a != value else pc + 3
            elif op == 0xBA:  # CJNE R2,#data,relative
                value = self.image[pc + 1]
                self.carry = int(self.r[2] < value)
                pc = self._relative(pc + 3, self.image[pc + 2]) if self.r[2] != value else pc + 3
            elif op == 0x0A:  # INC R2
                self.r[2] = self.r[2] + 1 & 0xFF
                pc += 1
            elif op == 0x12:  # LCALL
                target = self.image[pc + 1] << 8 | self.image[pc + 2]
                if target == 0x7C67:
                    self.sent.append((self.r[7], self.r[5], self.r[3]))
                    for index in range(2, 8):
                        self.r[index] = 0xEE  # deliberately model clobbered registers
                    pc += 3
                else:
                    returns.append(pc + 3)
                    pc = target
            elif op == 0x22:  # RET
                if returns:
                    pc = returns.pop()
                else:
                    return False
            elif op in (0x20, 0x30):  # JB/JNB ACC.6 or ACC.7,relative
                bit = self.image[pc + 1]
                assert bit in (0xE6, 0xE7)
                set_bit = bool(self.a & (1 << (bit - 0xE0)))
                branch = set_bit if op == 0x20 else not set_bit
                pc = self._relative(pc + 3, self.image[pc + 2]) if branch else pc + 3
            elif op in (0x60, 0x70, 0x40, 0x50, 0x80):  # JZ/JNZ/JC/JNC/SJMP
                branch = (op == 0x80 or op == 0x60 and self.a == 0 or
                          op == 0x70 and self.a != 0 or op == 0x40 and self.carry or
                          op == 0x50 and not self.carry)
                pc = self._relative(pc + 2, self.image[pc + 1]) if branch else pc + 2
            elif op == 0x02:  # LJMP
                pc = self.image[pc + 1] << 8 | self.image[pc + 2]
            else:
                raise AssertionError(f"unexpected 8051 opcode 0x{op:02x} at 0x{pc:04x}")
        raise AssertionError(f"hook at 0x{start:04x} did not reach 0x{end:04x}")

    @staticmethod
    def _relative(base: int, displacement: int) -> int:
        return base + (displacement if displacement < 128 else displacement - 256)

    def note_on(self, channel: int) -> None:
        self.r[7] = 0x90 | channel
        self.run(NOTE_ON, 0x7C67)

    def note_off(self, channel: int) -> None:
        self.r[7] = 0x80 | channel
        self.run(NOTE_OFF, 0x7C67)

    def bend(self, channel: int, raw: int) -> int | None:
        self.r[3] = channel
        self.r[7] = raw
        sent = self.run(BEND, 0x7FF6)
        if not sent:
            return None
        self.assert_replayed_value()
        if self.stack:
            raise AssertionError("hook left values on the stack")
        return self.r[7]

    def pad(self, contact: int, value: int, channel: int = 0) -> list[tuple[int, int, int]]:
        self.xram[0x0999] = contact
        self.r[2] = 77
        self.r[5] = channel  # MPE master channel is zero
        self.r[6], self.r[7] = divmod(value, 256)
        before = (self.r[2], self.r[5], self.r[6], self.r[7])
        self.sent.clear()
        assert self.run(PAD, 0x7EA9)
        assert (self.r[2], self.r[5]) == before[:2]
        if self.xram[0x0351] and channel == 0:
            assert (self.r[6], self.r[7]) == (0x20, 0)
        else:
            assert (self.r[6], self.r[7]) == before[2:]
        assert self.a == self.r[7] & 0x7F  # original bytes replayed
        assert not self.stack
        return self.sent.copy()

    def assert_replayed_value(self) -> None:
        if self.r[6] != self.r[7] or self.a != self.r[7]:
            raise AssertionError("original 0x7FF3 instructions were not replayed")


class RelativeTiltPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image, cls.sysex = build_image()

    def test_preset_pages_and_firmware_round_trip(self) -> None:
        stock, _, _ = decode_image(extract(STOCK_SYX)[0])
        self.assertEqual(self.image[0xF000:0xF800], stock[0xF000:0xF800])
        with TemporaryDirectory(prefix="kboard-relative-tilt-") as directory:
            path = Path(directory) / "roundtrip.syx"
            path.write_bytes(self.sysex)
            decoded, _, _ = decode_image(extract(path)[0])
            self.assertEqual(decoded, self.image)

    def test_per_channel_zero_and_saturation(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1  # MPE enabled
        m.note_on(2)
        self.assertEqual(m.bend(2, 40), 64)
        self.assertEqual(m.bend(2, 45), 69)
        self.assertEqual(m.bend(2, 35), 59)
        self.assertEqual(m.bend(3, 110), 110)  # channel 3 was not armed
        m.note_on(3)
        self.assertEqual(m.bend(3, 110), 64)
        self.assertEqual(m.bend(2, 45), 69)  # independent baseline
        self.assertEqual(m.bend(3, 0), 0)
        m.note_on(4)
        self.assertEqual(m.bend(4, 0), 64)
        self.assertEqual(m.bend(4, 127), 127)

    def test_all_seven_bit_tilt_pairs(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        for baseline in range(128):
            m.note_on(8)
            self.assertEqual(m.bend(8, baseline), 64)
            for current in range(128):
                expected = max(0, min(127, 64 + current - baseline))
                self.assertEqual(m.bend(8, current), expected,
                                 (baseline, current))

    def test_amount_and_deadzone(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        for width, deadzone in ((16, 3), (0, 0), (64, 12), (1, 0)):
            m.xram[0x04F1] = 64 - width
            m.xram[0x04FA] = deadzone
            for baseline in range(128):
                m.note_on(8)
                self.assertEqual(m.bend(8, baseline), 64)
                for current in range(128):
                    distance = max(0, abs(current - baseline) - deadzone)
                    scaled = min(distance, 64) * width // 64
                    signed = scaled if current >= baseline else -scaled
                    expected = max(0, min(127, 64 + signed))
                    self.assertEqual(m.bend(8, current), expected,
                                     (width, deadzone, baseline, current))

    def test_invalid_amount_is_safely_centered(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        m.xram[0x04F1] = 255
        m.note_on(2)
        self.assertEqual(m.bend(2, 20), 64)
        self.assertEqual(m.bend(2, 110), 64)

    def test_bend_pad_is_relative_and_adds_to_independent_tilt(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        m.note_on(2)
        m.note_on(4)
        self.assertEqual(m.bend(2, 40), 64)
        self.assertEqual(m.bend(4, 90), 64)
        self.assertEqual(m.bend(2, 50), 74)
        self.assertEqual(m.bend(4, 80), 54)
        self.assertEqual(m.pad(1, 0x3000), [(0xE2, 0, 74), (0xE4, 0, 54)])
        self.assertEqual(m.xram[0x0F20], 1)
        self.assertEqual(m.pad(1, 0x3F80), [(0xE2, 0, 105), (0xE4, 0, 85)])
        self.assertEqual(m.bend(2, 60), 115)  # tilt +20 and pad +31
        self.assertEqual(m.bend(4, 100), 105)  # tilt +10 and pad +31
        self.assertEqual(m.pad(1, 0x2000), [(0xE2, 0, 52), (0xE4, 0, 42)])
        m.note_on(6)
        self.assertEqual(m.bend(6, 75), 32)  # new note inherits current pad
        self.assertEqual(m.bend(6, 80), 37)
        m.note_off(6)
        self.assertEqual(m.pad(0, 0x3100), [(0xE2, 0, 86), (0xE4, 0, 76)])
        self.assertEqual(m.pad(0, 0x2000), [(0xE2, 0, 84), (0xE4, 0, 74)])
        self.assertEqual(m.xram[0x0F20], 0)
        self.assertEqual((m.xram[0x0F12], m.xram[0x0F14]), (41, 91))
        self.assertEqual(m.xram[0x0F16], 0)
        self.assertEqual(m.bend(2, 60), 84)  # tilt baseline remains stable
        self.assertEqual(m.bend(4, 100), 74)

    def test_tilt_state_sentinels_and_biased_extreme_baseline(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        m.note_on(8)
        self.assertEqual(m.xram[0x0F18], 0x81)  # armed sentinel
        self.assertEqual(m.bend(8, 127), 64)
        self.assertEqual(m.xram[0x0F18], 128)  # baseline 127, biased by one
        self.assertEqual(m.bend(8, 126), 63)
        m.note_off(8)
        self.assertEqual(m.xram[0x0F18], 0)

    def test_switch_from_absolute_tilt_rearms_relative_baseline(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        m.xram[0x04EF] = 0x78  # legacy MPE absolute tilt
        self.assertEqual(m.bend(8, 55), 55)
        self.assertEqual(m.xram[0x0F18], 0x82)

        m.xram[0x04EF] = 0x7C  # legacy relative tilt while note is held
        self.assertEqual(m.bend(8, 90), 64)
        self.assertEqual(m.xram[0x0F18], 91)
        self.assertEqual(m.bend(8, 91), 65)

    def test_hires_scaler_emitted_bytes_cover_every_setting_and_movement(self) -> None:
        scaler = build_scale14()
        code = bytearray(0x10000)
        code[SCALE14:SCALE14 + len(scaler)] = scaler
        machine = Machine(code)
        for movement in range(65):
            for percent in range(101):
                machine.call(SCALE14, r7=movement, r6=percent)
                self.assertEqual(
                    machine.r(6) * 256 + machine.r(7), movement * percent * 32 // 25,
                    (movement, percent),
                )

    def test_hires_relative_tilt_centers_scales_caches_and_combines(self) -> None:
        machine = Machine(self.image)
        sent = []
        machine.stubs[0x7C67] = lambda m: sent.append((m.r(7), m.r(5), m.r(3)))
        machine.xram[XDATA_BEND_FLAGS] = 0x77  # 100%, tilt/pad/combine enabled
        machine.xram[XDATA_BEND_FLAGS_ESCAPE] = 100
        machine.xram[0x0351] = 1
        channel = 3
        machine.xram[XDATA_TILT_BASELINE + channel] = 0x81
        machine.call(BEND, r3=channel, r7=50)
        self.assertEqual(sent[-1], (0xE3, 0, 64))
        self.assertEqual(machine.xram[XDATA_TILT_BASELINE + channel], 51)

        machine.xram[XDATA_BEND_FLAGS_ESCAPE] = 1
        machine.call(BEND, r3=channel, r7=51)
        self.assertEqual(sent[-1], (0xE3, 1, 64))  # one raw 14-bit count
        self.assertEqual(machine.xram[XDATA_TILT_CURRENT + channel], 64)
        self.assertEqual(machine.xram[XDATA_TILT_LOW + channel], 1)

        # The common Bend Pad hook also retains the cached tilt low byte when
        # it resends every active member channel.
        before_pad = len(sent)
        machine.xram[0x0999] = 1
        machine.call(PAD, r2=0, r3=0, r5=0, r6=0x20, r7=0)
        self.assertIn((0xE3, 1, 64), sent[before_pad:])

        machine.xram[XDATA_BEND_FLAGS_ESCAPE] = 100
        machine.xram[0x0F20] = 1
        machine.xram[0x0F22] = 65  # +1 seven-bit step, 128 raw counts
        machine.call(BEND, r3=channel, r7=52)
        self.assertEqual(sent[-1], (0xE3, 0, 67))
        self.assertEqual(machine.xram[XDATA_TILT_LOW + channel], 0)

        machine.xram[0x0F20] = 0
        machine.call(BEND, r3=channel, r7=51)
        self.assertEqual(sent[-1], (0xE3, 0, 65))

        machine.call(BEND, r3=channel, r7=114)
        self.assertEqual(sent[-1], (0xE3, 127, 127))
        machine.call(BEND, r3=channel, r7=0)
        self.assertEqual(sent[-1], (0xE3, 0, 14))

    def test_hires_mode_keeps_stock_absolute_behavior_outside_mpe(self) -> None:
        machine = Machine(self.image)
        sent = []
        machine.stubs[0x7C67] = lambda m: sent.append((m.r(7), m.r(5), m.r(3)))
        machine.xram[XDATA_BEND_FLAGS] = 0x77
        machine.xram[XDATA_BEND_FLAGS_ESCAPE] = 100
        machine.call(BEND, r3=2, r7=64)
        self.assertEqual(sent[-1], (0xE2, 0, 64))

    def test_pad_amount_deadzone_and_saturation(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        m.xram[0x04F0] = 48  # pad amount 16 / 64
        m.xram[0x04F9] = 3
        m.note_on(2)
        self.assertEqual(m.bend(2, 60), 64)
        self.assertEqual(m.pad(1, 0x3000), [(0xE2, 0, 64)])
        self.assertEqual(m.pad(1, 0x3200), [(0xE2, 0, 64)])
        self.assertEqual(m.pad(1, 0x3F80), [(0xE2, 0, 71)])
        self.assertEqual(m.pad(1, 0x0000), [(0xE2, 0, 48)])
        m.xram[0x04F0] = 255
        self.assertEqual(m.pad(1, 0x3F80), [(0xE2, 0, 64)])

    def test_bend_pad_passthrough_outside_mpe(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 0
        self.assertEqual(m.pad(20, 0x3100), [])
        self.assertEqual(m.xram[0x0F20], 0)
        m.xram[0x0351] = 1
        self.assertEqual(m.pad(1, 0x3100, channel=3), [])
        self.assertEqual(m.xram[0x0F20], 0)

    def test_note_off_and_normal_midi(self) -> None:
        m = HookMachine(self.image)
        m.xram[0x0351] = 1
        m.note_on(5)
        self.assertEqual(m.bend(5, 50), 64)
        m.note_off(5)
        self.assertEqual(m.bend(5, 55), 55)
        m.xram[0x0351] = 0
        m.note_on(6)
        self.assertEqual(m.bend(6, 90), 90)
        m.xram[0x0351] = 1
        m.note_on(0)
        self.assertEqual(m.bend(0, 60), 60)


if __name__ == "__main__":
    unittest.main()
