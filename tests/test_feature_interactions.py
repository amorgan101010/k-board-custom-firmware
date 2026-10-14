"""Lifecycle regressions against the complete firmware and actual MIDI bytes."""

import unittest
import itertools
import random
from pathlib import Path
from tempfile import TemporaryDirectory

from firmware_tools import build_cyclone_game_patch as game
from firmware_tools import build_pressure_glide_patch as glide
from firmware_tools import build_scale_quantizer_patch as scales
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools import build_sensor_config_patch as menus
from firmware_tools import build_relative_tilt_patch as tilt
from firmware_tools.extract_kmi_firmware import extract, build_image as decode_image, split_sysex
from tests.firmware_board import FirmwareBoard, MidiWire, complete_image
from kboard_protocol import decode_preset_image


class FeatureInteractionTests(unittest.TestCase):
    def test_game_entry_retires_existing_pairs_in_both_physical_orders(self):
        for first, second in ((0, 12), (12, 0)):
            for released in (False, True):
                with self.subTest(first=first, released=released):
                    b = FirmwareBoard(buttons=2)
                    b.key_on(first)
                    b.key_on(second)
                    if released:
                        b.key_off(first)
                    b.enter_game()
                    self.assertFalse(b.wire.notes)
                    self.assertEqual(b.machine.xram[0x00AD], 0)
                    b.exit_ui()
                    for key in list(b.held):
                        b.key_off(key)
                    # A previously deferred primary must not have a stale
                    # consumed-release bit swallowing its next real release.
                    b.key_on(first)
                    b.key_off(first)
                    b.assert_consistent(self)
                    self.assertFalse(b.wire.notes)

    def test_nested_voice_retirement_preserves_caller_registers_and_stack(self):
        b = FirmwareBoard()
        b.key_on(0)
        b.key_on(2)
        m = b.machine
        for i in range(8):
            m.set_r(i, 100+i)
        m.a, m.b, m.dptr, m.cy = 211, 177, 0x0123, 1
        m.call(glide.RETIRE_VOICES)
        self.assertEqual([m.r(i) for i in range(8)], list(range(100, 108)))
        self.assertEqual((m.a, m.b, m.dptr, m.cy, m.sp), (211, 177, 0x0123, 1, 0x51))
        self.assertFalse(b.wire.notes)
        b.assert_consistent(self)

    def test_game_keys_do_not_become_unheard_glide_candidates(self):
        b = FirmwareBoard(buttons=2)
        b.enter_game()
        b.wire.clear()
        b.key_on(0)
        self.assertEqual(b.wire.messages, [])
        b.exit_ui()
        b.key_on(12)
        self.assertEqual(len(b.wire.notes), 1)
        b.assert_consistent(self)
        b.key_off(0)
        b.key_off(12)
        b.assert_consistent(self)

    def test_selector_entry_retires_held_voices_and_consumes_later_releases(self):
        for kind in ('single', 'pair', 'released_primary'):
            with self.subTest(kind=kind):
                b = FirmwareBoard(buttons=2)
                b.key_on(0)
                if kind != 'single':
                    b.key_on(2)
                if kind == 'released_primary':
                    b.key_off(0)
                b.enter_selector()
                self.assertFalse(b.wire.notes)
                self.assertEqual(b.machine.xram[0x00AD], 0)
                b.key_on(5)
                b.key_off(5)
                b.exit_ui()
                b.key_on(12)
                self.assertEqual(len(b.wire.notes), 1)
                for key in sorted(b.held):
                    b.key_off(key)
                b.assert_consistent(self)

    def test_both_pressure_routes_obey_game_and_menu_gates(self):
        for game_state, menu_state in ((0, 2), (0, 3), (0, 5),
                                        (1, 0), (2, 0), (3, 0), (4, 0)):
            with self.subTest(game_state=game_state, menu_state=menu_state):
                b = FirmwareBoard()
                b.machine.xram[game.GAME_STATE] = game_state
                b.machine.xram[velocity.XRAM_STATE] = menu_state
                for entry in (0x7C67, 0x7DF4):
                    b.machine.call(entry, r7=0xD1, r5=76, r3=0)
                self.assertEqual(b.wire.messages, [])
                b.machine.xram[game.GAME_STATE] = 0
                b.machine.xram[velocity.XRAM_STATE] = 0
                b.machine.call(0x8247, r7=76, r5=1)
                self.assertEqual(b.wire.messages, [(0xD1, 76)])

    def test_wire_decoder_handles_running_status_and_realtime(self):
        wire = MidiWire()
        for byte in (0x91, 60, 0xF8, 100, 64, 90, 0xD1, 30, 40, 0x81, 60, 0):
            wire.feed(byte)
        self.assertEqual(wire.messages,
                         [(0x91, 60, 100), (0x91, 64, 90), (0xD1, 30),
                          (0xD1, 40), (0x81, 60, 0)])
        self.assertEqual(wire.notes, {(1, 64)})

    def test_nonzero_glide_tilt_and_pad_agree_for_every_update_order(self):
        for order in itertools.permutations(('force', 'tilt', 'pad')):
            with self.subTest(order=order):
                b = FirmwareBoard(relative_tilt_amount=17)
                c1, c2 = b.key_on(0), b.key_on(4)
                b.tilt(0, 64)
                b.tilt(4, 64)
                b.pad(0x1000)
                b.wire.clear()
                for action in order:
                    if action == 'force':
                        b.force(0, 60, 31)
                        b.force(4, 90, 91)
                    elif action == 'tilt':
                        b.tilt(0, 71)
                        b.tilt(4, 85)
                    else:
                        b.pad(0x1800)
                m = b.machine
                weight = lambda c: (m.xram[glide.WEIGHT_BY_CHANNEL+c] * 256
                                    + m.xram[glide.WEIGHT_LOW_BY_CHANNEL+c])
                native = lambda c: m.xram[tilt.XDATA_TILT_CURRENT+c] * 128 + m.xram[tilt.XDATA_TILT_LOW+c]
                ratio = 128 * weight(c2) // (weight(c1) + weight(c2))
                blended = ((128-ratio) * native(c1) + ratio * native(c2)) // 128
                pad = (m.xram[tilt.XDATA_PAD_OFFSET] - 64) * 128
                expected = min(16383, max(0, blended + pad + round(4 * ratio * 8192 / (24 * 128))))
                self.assertEqual(b.wire.bends[c1], expected)
                self.assertTrue(any(m.xram[tilt.XDATA_TILT_LOW+c] for c in (c1, c2)))
                self.assertFalse(any(msg[0] == 0xE0 | c2 for msg in b.wire.messages))
                b.assert_consistent(self)

    def test_buttons_and_live_glide_range_changes_keep_release_and_handoff_coherent(self):
        for buttons in (0, 1, 2, 3):
            for range_code in (1, 3, 25):
                for order in ((0, 2), (2, 0)):
                    with self.subTest(buttons=buttons, range_code=range_code, order=order):
                        b = FirmwareBoard()
                        c1, c2 = b.key_on(0), b.key_on(2)
                        b.tilt(0, 64)
                        b.tilt(2, 64)
                        b.tilt(2, 80)
                        b.pressure(0, 44)
                        b.pressure(2, 72)
                        b.machine.xram[0x093E] = buttons
                        b.machine.xram[glide.GLIDE_RANGE_CODE] = range_code
                        b.wire.clear()
                        b.key_off(order[0])
                        if order[0] == 0:
                            pressures = [msg for msg in b.wire.messages if msg[0] & 0xF0 == 0xD0]
                            self.assertEqual(pressures, [(0xD0 | c1, 72)] if buttons & 2 else [])
                        b.assert_consistent(self)
                        b.key_off(order[1])
                        b.assert_consistent(self)
                        self.assertFalse(b.wire.notes)

    def test_quantized_intervals_duplicates_and_pairing_window_boundaries(self):
        # Lydian E/F -> E/F# is two semitones. In non-Chromatic scales, black
        # keys are octave controls, so the Ionian interval uses white C/D.
        for scale_id, keys, interval in ((4, (4, 5), 2), (1, (0, 2), 2),
                                         (0, (4, 0), -4)):
            for transpose in (-12, 0, 12):
                for window in (1, 2, 4):
                    with self.subTest(scale=scale_id, transpose=transpose, window=window):
                        b = FirmwareBoard(glide_range=window, buttons=2)
                        m = b.machine
                        m.xram[scales.INIT_DONE] = 1
                        m.xram[scales.SCALE_ID] = scale_id
                        m.xram[scales.TRANSPOSE] = transpose + 12
                        c1, c2 = (b.key_on(k) for k in keys)
                        pitch1 = m.xram[glide.PITCH_BY_CHANNEL + c1]
                        pitch2 = m.xram[glide.PITCH_BY_CHANNEL + c2]
                        self.assertEqual(pitch2-pitch1, interval)
                        self.assertEqual(m.xram[glide.FLAGS_BY_CHANNEL+c2],
                                         glide.FLAG_SECONDARY if abs(interval) <= window else glide.FLAG_NORMAL)
                        b.assert_consistent(self)
                        for key in reversed(keys):
                            b.key_off(key)
                        self.assertFalse(b.wire.notes)
                        b.assert_consistent(self)

    def test_closest_pairing_tie_chooses_lowest_eligible_member(self):
        b = FirmwareBoard(glide_range=0, buttons=2)
        channels = [b.key_on(k) for k in (0, 4)]
        b.machine.xram[glide.GLIDE_RANGE_CODE] = 3
        companion = b.key_on(2)
        self.assertEqual(b.machine.xram[glide.PARTNER_BY_CHANNEL+companion] & 0x0F, channels[0])
        for key in list(b.held):
            b.key_off(key)
        b.assert_consistent(self)

    def test_stock_exhaustion_uses_master_and_reuse_restores_member_bend(self):
        for members in (1, 2, 8, 15):
            with self.subTest(members=members):
                b = FirmwareBoard(members=members, glide_range=1, buttons=2)
                for key in range(members):
                    b.key_on(key)
                    b.assert_consistent(self)
                self.assertEqual(b.key_on(members), 0)  # deliberate stock fallback
                self.assertEqual(b.machine.xram[0x00AD], members)
                self.assertIn((0, 24+members), b.wire.notes)
                for key in list(b.held):
                    b.key_off(key)
                self.assertFalse(b.wire.notes)
                b.assert_consistent(self)
                b.wire.clear()
                channel = b.key_on(24)
                self.assertGreater(channel, 0)
                self.assertEqual(b.wire.bends[channel], 8192)
                b.key_off(24)
                b.assert_consistent(self)

    def test_staggered_chords_and_tick_wrap_choose_one_ui(self):
        for start in (0, 0xFFF0):
            for cadence in (1, 7, 31):
                with self.subTest(start=start, cadence=cadence):
                    b = FirmwareBoard(tick=start, buttons=2)
                    b.scan_buttons(80, cadence, **{'0': 127})
                    b.scan_buttons(80, cadence, **{'1': 127})
                    # One channel is acquired per call: allow a complete
                    # eight-channel round after the last physical press.
                    b.scan_buttons(1080 + 8*cadence, cadence, **{'4': 127})
                    self.assertNotEqual(b.machine.xram[game.GAME_STATE], 0)
                    self.assertEqual(b.machine.xram[velocity.XRAM_STATE], 0)
                    b.scan_buttons(200, cadence, **{'0': 0, '1': 0, '4': 0})
                    self.assertEqual(b.machine.xram[game.GAME_STATE], 2)
                    b.exit_ui()
                    self.assertEqual(b.machine.xram[0x093E] & 7, 2)
                    b.assert_consistent(self)

    def test_partial_chord_cancels_then_single_button_opens_settings(self):
        b = FirmwareBoard()
        b.scan_buttons(300, **{'0': 127, '1': 127})
        b.scan_buttons(150, **{'0': 0, '1': 0})
        self.assertEqual(b.machine.xram[scales.SCALE_STATE], 0)
        b.scan_buttons(1080, **{'4': 127})
        b.scan_buttons(80, **{'4': 0})
        self.assertEqual(b.machine.xram[velocity.XRAM_STATE], 3)
        self.assertEqual(b.machine.xram[menus.MODE], 0)
        b.exit_ui()
        b.assert_consistent(self)

    def test_settings_page_switch_save_preserves_custom_sidecars_and_force(self):
        b = FirmwareBoard(relative_tilt_amount=17, relative_tilt_range=2, glide_range=2)
        before = bytes(b.machine.xram[0x0347:0x051D])
        b.scan_buttons(1080, **{'4': 127})
        b.scan_buttons(80, **{'4': 0})
        b.key_on(24)
        b.key_off(24)
        b.scan_buttons(1080, **{'1': 127})
        b.scan_buttons(80, **{'1': 0})
        b.key_on(0)
        b.key_off(0)
        b.exit_ui()
        after = bytes(b.machine.xram[0x0347:0x051D])
        changed = {i for i, (a, z) in enumerate(zip(before, after)) if a != z}
        allowed = {velocity.XRAM_VELOCITY_GAIN-0x347, velocity.XRAM_VELOCITY_GAIN+1-0x347,
                   menus.PRESSURE_GAIN-0x347, menus.PRESSURE_GAIN+1-0x347}
        self.assertLessEqual(changed, allowed)
        self.assertEqual(decode_preset_image(after)['relative_tilt_amount'], 17)
        self.assertEqual(decode_preset_image(after)['pressure_glide_range'], 2)
        # Assert the real save loop wrote every byte, not merely the visible fields.
        preset_writes = [(hi, lo, value) for _, hi, lo, value in b.writes
                         if hi in (0xF0, 0xF1)]
        self.assertEqual(preset_writes, [(0xF0+(i>>8), i&255, v) for i, v in enumerate(after)])
        c1, c2 = b.key_on(0), b.key_on(2)
        weights = bytes(b.machine.xram[glide.WEIGHT_BY_CHANNEL:glide.FLAGS_BY_CHANNEL])
        b.key_off(0)
        b.wire.clear()
        for raw in (40, 80, 120):
            b.mapped_pressure(2, raw)
        self.assertEqual([msg for msg in b.wire.messages if msg[0] & 0xF0 == 0xD0],
                         [(0xD0 | c1, value) for value in (24, 48, 72)])
        # A separate scan supplies force; mapped aftertouch does not change it.
        self.assertEqual(bytes(b.machine.xram[glide.WEIGHT_BY_CHANNEL+c2:glide.WEIGHT_BY_CHANNEL+c2+1]),
                         weights[c2:c2+1])
        b.key_off(2)
        b.assert_consistent(self)

    def test_sustain_passes_to_receiver_without_changing_pair_ownership(self):
        b = FirmwareBoard(buttons=2)
        b.key_on(0)
        b.key_on(2)
        b.machine.call(0x7C67, r7=0xB0, r5=64, r3=127)
        b.key_off(0)
        self.assertEqual(len(b.wire.notes), 1)
        b.key_on(0)
        b.key_off(2)
        b.key_off(0)
        self.assertEqual(b.machine.xram[0x00AD], 0)
        b.machine.call(0x7C67, r7=0xB0, r5=64, r3=0)
        self.assertEqual([msg for msg in b.wire.messages if msg[:2] == (0xB0, 64)],
                         [(0xB0, 64, 127), (0xB0, 64, 0)])
        b.assert_consistent(self)

    def test_force_origin_tracks_configurable_threshold_and_sensor_max(self):
        for threshold in (5, 30, 60, 121):
            for gain in (80, 100, 120):
                for delta in (-1, 0, 1):
                    with self.subTest(threshold=threshold, gain=gain, delta=delta):
                        b = FirmwareBoard(buttons=2)
                        channel = b.key_on(0)
                        coarse = threshold+delta
                        b.force(0, coarse, 1, threshold=threshold, gain=gain,
                                right=coarse*128+91)
                        m = b.machine
                        weight = m.xram[glide.WEIGHT_BY_CHANNEL+channel]*256 + m.xram[glide.WEIGHT_LOW_BY_CHANNEL+channel]
                        expected = 0 if delta < 0 else delta*128 + (91 if gain == 100 else 0)
                        self.assertEqual(weight, expected)

    def test_real_stock_global_gain_scaling_feeds_the_force_hook(self):
        for gain in (80, 100, 120):
            for left, right in ((10, 20), (50, 60), (120, 127)):
                with self.subTest(gain=gain, left=left, right=right):
                    b = FirmwareBoard(buttons=2)
                    channel = b.key_on(0)
                    b.force(0, left, 13, gain=gain, right=right*128+71)
                    m = b.machine
                    m.iram[0x2E], m.iram[0x31] = left, right
                    # Real stock 0x372D scales by (Globals Gain + 60)/100;
                    # 0x258F..0x25F6 clamps both sensors and takes their max.
                    m.call(0x258F)
                    coarse = min(127, right * (gain+60)//100)
                    self.assertEqual(m.xram[0x007A], coarse)
                    expected = max(0, coarse-30)*128
                    if coarse >= 30 and gain == 100:
                        expected += 71
                    weight = m.xram[glide.WEIGHT_BY_CHANNEL+channel]*256 + m.xram[glide.WEIGHT_LOW_BY_CHANNEL+channel]
                    self.assertEqual(weight, expected)

    def test_real_stock_cutoff_matches_force_origin_for_configured_thresholds(self):
        for threshold in (5, 30, 60, 121):
            for raw in (threshold-1, threshold, threshold+1):
                with self.subTest(threshold=threshold, raw=raw):
                    b = FirmwareBoard(buttons=2, on_thresh=threshold)
                    c1, c2 = b.key_on(0), b.key_on(2)
                    m = b.machine
                    # Enter the real held-key decision, mapper and release
                    # after acquisition, as in the stock cutoff trace tool.
                    m.stubs[0x6C72] = lambda q: q.set_r(7, 64)
                    m.stubs[0x5BCB] = lambda q: q.set_r(7, 0)
                    m.xram[0x09C1:0x09C3] = bytes((0, 0xDA))
                    m.xram[0x00DA] = 2
                    m.xram[0x0044] = 64
                    m.xram[0x0287] = raw
                    m.xram[0x00F6] = 99
                    m.xram[0x0079] = 1
                    m.xram[0x025A] = 1
                    m.iram[0x4E] = min(126, threshold+5)
                    m.iram[0x4C] = 100
                    b.force(2, 127, threshold=threshold)
                    b.force(0, raw, threshold=threshold)
                    before = b.wire.bends[c1]
                    m.call(0x2625, dptr=0x0044)
                    self.assertEqual(b.wire.bends[c1], before)
                    self.assertEqual(m.xram[glide.FLAGS_BY_CHANNEL+c1],
                                     glide.FLAG_PRIMARY_RELEASED if raw < threshold else glide.FLAG_PRIMARY)
                    self.assertEqual(m.xram[glide.WEIGHT_BY_CHANNEL+c1]*256
                                     + m.xram[glide.WEIGHT_LOW_BY_CHANNEL+c1],
                                     max(0, raw-threshold)*128)
                    b.assert_consistent(self)

    def test_deterministic_valid_action_sequences_preserve_voice_invariants(self):
        for seed in (7, 31, 901):
            b = FirmwareBoard(buttons=2)
            rng = random.Random(seed)
            history = []
            for _ in range(100):
                available = sorted(set(range(12))-b.held)
                action = rng.choice(('on', 'off', 'force', 'pressure', 'selector', 'game'))
                key = rng.choice(sorted(b.held) or available)
                try:
                    if action == 'on' and available:
                        key = rng.choice(available)
                        b.key_on(key)
                    elif action == 'off' and key in b.held:
                        b.key_off(key)
                    elif action in ('force', 'pressure') and key in b.held and b.machine.xram[0x60+key] != 255:
                        if action == 'force':
                            b.force(key, rng.randrange(30, 128), rng.randrange(128))
                        else:
                            b.pressure(key, rng.randrange(128))
                    elif action in ('selector', 'game'):
                        (b.enter_selector if action == 'selector' else b.enter_game)()
                        b.exit_ui()
                    history.append((action, key))
                    b.assert_consistent(self)
                except Exception as exc:
                    self.fail(f'seed={seed}, first failing prefix={history + [(action, key)]}: {exc}')
            for key in sorted(b.held):
                b.key_off(key)
            b.assert_consistent(self)
            self.assertFalse(b.wire.notes)

    def test_complete_image_transport_pages_tail_and_patch_ownership(self):
        image, sysex = complete_image()
        stock, _, _ = decode_image(extract(tilt.STOCK_SYX)[0])
        self.assertEqual(image[0xEE00:], stock[0xEE00:])
        messages = split_sysex(sysex)
        self.assertTrue(all(len(msg) <= 512 for msg in messages))
        with TemporaryDirectory() as directory:
            path = Path(directory)/'candidate.syx'
            path.write_bytes(sysex)
            records, _ = extract(path)
        self.assertEqual(decode_image(records)[0], image)
        for chunk in {r.chunk for r in records}:
            rows = [r for r in records if r.chunk == chunk and r.record_type == 0]
            self.assertLessEqual(len({r.address//512 for r in rows}), 1)
            for row in rows:
                self.assertEqual(row.address//512, (row.address+len(row.data)-1)//512)
        for address, emitter in ((glide.UI_KEYON, glide.build_ui_keyon),
                                 (glide.UI_KEYOFF, glide.build_ui_keyoff),
                                 (glide.UI_MUTED, glide.build_ui_muted),
                                 (glide.RETIRE_VOICES, glide.build_retire_voices)):
            code = emitter()
            self.assertEqual(image[address:address+len(code)], code)
            self.assertEqual(stock[address:address+len(code)], b'\xff'*len(code))
            self.assertGreaterEqual(address, 0x8E15)
            self.assertLessEqual(address+len(code), 0xEE00)

    def test_menu_recontact_and_release_cannot_destroy_reserved_primary(self):
        b = FirmwareBoard(buttons=2)
        c1, c2 = b.key_on(0), b.key_on(2)
        b.pressure(2, 72)
        b.key_off(0)
        b.scan_buttons(1080, **{'1': 127})
        b.scan_buttons(80, **{'1': 0})
        self.assertEqual(b.machine.xram[velocity.XRAM_STATE], 3)
        b.key_on(0)  # settings choice, not a performance recontact
        b.key_off(0)
        self.assertEqual(b.machine.xram[glide.FLAGS_BY_CHANNEL+c1], glide.FLAG_PRIMARY_RELEASED)
        self.assertEqual(b.machine.xram[glide.PARTNER_BY_CHANNEL+c2] & 0x0F, c1)
        b.assert_consistent(self)
        b.exit_ui()
        b.key_off(2)
        b.assert_consistent(self)
        self.assertFalse(b.wire.notes)


if __name__ == '__main__':
    unittest.main()
