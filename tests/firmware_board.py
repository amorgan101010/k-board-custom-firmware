"""Complete-image fixture; only hardware acquisition/writers are replaced."""

from functools import lru_cache

from firmware_tools import build_custom_firmware as custom
from firmware_tools import build_cyclone_game_patch as game
from firmware_tools import build_pressure_glide_patch as glide
from firmware_tools import build_scale_quantizer_patch as scales
from firmware_tools import build_sensor_config_patch as menus
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.mcs51 import Machine
from kboard_protocol import DEFAULT_PROFILE, _decode_7bit, build_sysex


@lru_cache(maxsize=1)
def complete_image():
    return custom.build_image()


class MidiWire:
    """Incremental channel MIDI decoder, including running status/realtime."""

    def __init__(self):
        self.bytes = []
        self.messages = []
        self.status = None
        self.data = []
        self.notes = set()
        self.bends = {}

    def feed(self, byte):
        self.bytes.append(byte)
        if byte >= 0xF8:
            return
        if byte & 0x80:
            if not 0x80 <= byte < 0xF0 or self.data:
                raise AssertionError(f"Unexpected MIDI status {byte:02x}")
            self.status = byte
            return
        if self.status is None:
            raise AssertionError("MIDI data without a status")
        self.data.append(byte)
        size = 1 if self.status & 0xF0 in (0xC0, 0xD0) else 2
        if len(self.data) < size:
            return
        message = (self.status, *self.data)
        self.messages.append(message)
        kind, channel = self.status & 0xF0, self.status & 15
        if kind == 0x90 and self.data[1]:
            self.notes.add((channel, self.data[0]))
        elif kind == 0x80 or kind == 0x90:
            self.notes.discard((channel, self.data[0]))
        elif kind == 0xB0 and self.data[0] in (120, 123):
            self.notes = {note for note in self.notes if note[0] != channel}
        elif kind == 0xE0:
            self.bends[channel] = self.data[0] + 128 * self.data[1]
        self.data = []

    def clear(self):
        """Clear captured traffic, retaining receiver and running-status state."""
        assert not self.data, "Incomplete MIDI message"
        self.bytes.clear()
        self.messages.clear()


class WatchedXram(bytearray):
    """Record accesses outside target internal RAM, without inventing aliases."""

    def __init__(self):
        super().__init__(65536)
        self.outside = set()

    def _record(self, index):
        if isinstance(index, int) and index >= 4096:
            self.outside.add(index)

    def __getitem__(self, index):
        self._record(index)
        return super().__getitem__(index)

    def __setitem__(self, index, value):
        self._record(index)
        return super().__setitem__(index, value)


class FirmwareBoard:
    def __init__(self, *, members=15, glide_range=24, buttons=3, tick=0, **profile):
        self.machine = Machine(complete_image()[0])
        self.machine.xram = WatchedXram()
        self.machine.sp = 0x51
        self.wire = MidiWire()
        self.levels = dict.fromkeys(range(8), 0)
        self.tick = tick
        self.held = set()
        self.erased, self.writes = [], []
        self.stack_peak = self.machine.sp
        self.profile = DEFAULT_PROFILE | {
            'mpe_active': True, 'pressure_cc': -1, 'mpe_member_channels': members,
            'pressure_glide_range': glide_range,
        } | profile
        m = self.machine
        m.xram[0x0347:0x051D] = self.preset(self.profile)
        m.xram[0x0060:0x0079] = b'\xff' * 25
        m.xram[0x0276:0x0286] = b'\xff' * 16
        m.xram[0x033B:0x033D] = bytes((0x2E, 0x3D))
        m.xram[0x093E] = buttons  # Velocity off gives genuine velocity 127.
        m.stubs[0x6894] = lambda q: q.set_r(7, self.levels[q.xram[velocity.XRAM_CHANNEL]])
        m.stubs[0x313D] = lambda q: self.wire.feed(q.r(7))
        m.stubs[menus.PRESET_ERASE] = lambda q: self.erased.append(q.r(7))
        m.stubs[menus.PRESET_WRITE] = lambda q: self.writes.append(
            (q.r(3), q.r(2), q.r(1), q.r(5)))
        # Button log uses flash hardware too; run its stock control flow.
        m.stubs[0x775A] = lambda q: None
        m.trace = self._trace

    @staticmethod
    def preset(profile):
        return _decode_7bit(build_sysex(profile)[8:-1])[10:480]

    def _trace(self, m, pc, op):
        self.stack_peak = max(self.stack_peak, m.sp)

    def scan_buttons(self, duration, cadence=1, **levels):
        self.levels.update({int(k): v for k, v in levels.items()})
        end = self.tick + duration
        while self.tick < end:
            self.tick = min(end, self.tick + cadence)
            self.machine.iram[0x4B] = self.tick >> 8 & 255
            self.machine.iram[0x4C] = self.tick & 255
            self.machine.call(0x56E8)

    def enter_selector(self):
        self.scan_buttons(1080, **{'0': 127, '1': 127})
        self.scan_buttons(60, **{'0': 0, '1': 0})
        assert self.machine.xram[scales.SCALE_STATE] == 7

    def enter_game(self):
        self.scan_buttons(1080, **{'0': 127, '1': 127, '4': 127})
        self.scan_buttons(60, **{'0': 0, '1': 0, '4': 0})
        assert self.machine.xram[game.GAME_STATE] == 2

    def exit_ui(self, channel=0):
        self.scan_buttons(60, **{str(channel): 127})
        self.scan_buttons(80, **{str(channel): 0})
        assert self.machine.xram[game.GAME_STATE] == 0
        assert self.machine.xram[velocity.XRAM_STATE] == 0

    def force(self, key, coarse=70, fraction=0, threshold=30, gain=100, right=None):
        m = self.machine
        m.iram[0x36], m.iram[0x4D] = key, threshold
        m.xram[0x034C], m.xram[0x007A + key] = gain, coarse
        word = coarse * 128 + fraction
        m.iram[0x34:0x36] = word.to_bytes(2, 'big')
        m.iram[0x2F:0x31] = (right or 0).to_bytes(2, 'big')
        # Acquire force from the real early hook; stock scan continuation is
        # outside this operation. Cutoff/mapper tests enter their real routines.
        m.stubs[0x25F9] = lambda q: None
        m.call(0x25F6)

    def key_on(self, key, force=70):
        assert key not in self.held
        self.force(key, force)
        self.machine.call(0x4C4F, r7=key)
        self.held.add(key)
        return self.machine.xram[0x0060 + key]

    def key_off(self, key):
        assert key in self.held
        self.force(key, 0)
        self.machine.call(0x62D7, r7=key)
        self.held.remove(key)

    def tilt(self, key, value):
        channel = self.machine.xram[0x0060 + key]
        assert channel != 255
        self.machine.call(0x7FF3, r7=value, r3=channel)

    def pressure(self, key, value):
        channel = self.machine.xram[0x0060 + key]
        assert channel != 255
        self.machine.call(0x8247, r7=value, r5=channel)

    def mapped_pressure(self, key, raw):
        self.machine.xram[0x0287 + key] = raw
        self.machine.call(0x38FB, r7=key, r5=0)

    def pad(self, value, active=True):
        self.machine.xram[0x0999] = int(active)
        self.machine.call(0x7EA6, r2=0, r3=0, r5=0,
                          r6=value >> 8, r7=value & 255)

    def assert_consistent(self, test):
        m = self.machine
        # Stock exhaustion falls back to shared master-channel notes; they
        # are not member allocations and do not increment the member count.
        mapped = [c for c in m.xram[0x0060:0x0079] if c not in (0, 255)]
        test.assertEqual(len(mapped), len(set(mapped)))
        test.assertEqual(m.xram[0x00AD], len(mapped))
        for c in mapped:
            test.assertIn(c, range(1, self.profile['mpe_member_channels'] + 1))
            role = m.xram[glide.FLAGS_BY_CHANNEL + c]
            if role in (glide.FLAG_PRIMARY, glide.FLAG_PRIMARY_RELEASED, glide.FLAG_SECONDARY):
                partner = m.xram[glide.PARTNER_BY_CHANNEL + c] & 0x0F
                test.assertIn(partner, mapped)
                test.assertEqual(m.xram[glide.PARTNER_BY_CHANNEL + partner] & 0x0F, c)
            if role in (glide.FLAG_NORMAL, glide.FLAG_PRIMARY, glide.FLAG_PRIMARY_RELEASED):
                test.assertIn((c, m.xram[glide.PITCH_BY_CHANNEL + c]), self.wire.notes)
        test.assertFalse(m.xram.outside, sorted(m.xram.outside))
        test.assertEqual(m.sp, 0x51)
        test.assertLess(self.stack_peak, 0xF0)
        test.assertEqual(m.xram[0x0FFA:0x1000], bytes(6))
