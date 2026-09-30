# Relative tilt firmware patch findings

## Target behavior

In MPE mode, the first pitch-bend sample associated with a newly pressed note
should be treated as that note's neutral point. Later tilt samples on that
member channel should be sent as offsets from that first sample, centered at
MIDI pitch-bend value 8192. This avoids the absolute-position jump at note-on.

## Firmware 1.2.2 path

- `0x7FF3` is the per-key tilt pitch-bend sender. It receives the seven-bit
  tilt value in `R7` and its MIDI channel in `R3`, expands it into pitch-bend
  data bytes, and sends status `R3 | 0xE0` through `0x7C67`.
- The `0x4D22` call sends center before note-on, `0x594D` sends subsequent
  per-key tilt changes, and `0x6328` sends center after note-off.
- `0x7EA6` is a separate 14-bit bend sender used by other sensor paths. It is
  left untouched by this patch.
- `0x7EF9` is the note-on output routine. It sends status `channel | 0x90`.
- `0x7FDB` is the note-off output routine. It sends status `channel | 0x80`.
- `0x4C4F` is in the per-key scan path and calls the note output helper.
- The channel rotation/MPE setting is XRAM `0x0351`, directly corresponding to
  field 9 of the preset image. Stock startup at `0x6232` clears all 4 KB of
  XRAM, including the patch's state bytes at `0x0F00`.

## Stock indexed XRAM audit

The stock 1.2.2 disassembly contains ten `MOVX @R0` / `MOVX @R1` instructions:
`0x2AC8`, `0x2AEF`, `0x2B0E`, `0x2BF1`, `0x2BF5`, `0x2C29`, `0x2C2D`,
`0x2C54`, `0x2C58`, and `0x6266`. On the Silicon Labs C8051F34x-compatible
memory map, these use `EMI0CN` (`SFR 0xAA`) as the upper address byte and R0/R1
as the lower byte. The stock firmware has no instruction that writes `SFR
0xAA`; the disassembly has no references to that SFR, and a raw-image scan for
the direct-write instruction forms also found none. The documented reset page
is `0x00`, so these accesses are confined to XRAM `0x0000–0x00FF`. The stock
image also leaves `EMI0CF` in its reset Internal-Only mode.

Conclusion: stock indexed accesses do not reach `0x0A00–0x0FFF`. Startup at
`0x6243` does clear the full 4 KB XRAM using `MOVX @DPTR` before normal
operation; that is initialization, not a runtime indexed allocation. The
disassembly's resolved XRAM references top out at `0x09C7` (plus high address
aliases that wrap below `0x0E00`). However, 322 `MOVX @DPTR` instructions do
not have a statically resolved XRAM reference in the listing. Their computed
pointer ranges have not all been bounded, so the indexed-access result alone
does not certify every byte above `0x09C7` as unused. The current patches do
not change `EMI0CN`.
The register behavior is documented in Silicon Labs' [C8051F34x data sheet,
Section 13](https://www.silabs.com/documents/public/data-sheets/C8051F34x.pdf).

## Initial relative tilt image (v1)

The image in `kboard_1.2.2-relative-tilt-v1.syx` hooks the
actual note-on and note-off output calls and the entry of `0x7FF3`. A note-on
arms its MPE member channel. The first following tilt sample saves the
baseline and sends seven-bit value 64, which the stock helper encodes as pitch
bend center (8192). Later samples send
`clamp(64 + current - baseline, 0, 127)`. A note-off clears the channel state.
Ordinary MIDI mode and unarmed MPE channels use the stock value.

The three out-of-line helpers live in erased flash at `0x8300`, `0x8320`, and
`0x8340`. They use 32 bytes of unused XRAM starting at `0x0F00`. The builder
requires exact SHA-256 matches for the original 1.2.2 SysEx and flat image,
and exact original bytes at all three hook locations. It writes a flat image
and repacked SysEx in this directory.

## Validation of v1

- `tests/test_relative_tilt_patch.py` executes the current emitted 8051 hook bytes in a
  small instruction emulator. It checks first touch center, every pair of
  seven-bit landing and movement values, saturation, channel independence,
  note-off reset, and ordinary MIDI passthrough.
- The patched SysEx decodes back into the complete patched image; the preset
  flash pages `0xF000–0xF7FF` match stock byte for byte.
- Hardware validation on September 29, 2026 captured nine note gestures
  across MPE channels 2–9, including simultaneous notes and channel reuse.
  Every gesture's first pitch bend after note-on was exactly 8192; subsequent
  tilt values changed independently by channel. The capture also showed
  note-off returning channels 2 and 3 to center. This confirms the intended
  MIDI behavior on the development board.

  | MIDI channel | Notes captured | First bend for every note | Observed bend range |
  | --- | --- | --- | --- |
  | 2 | 50, 52 | 8192 | 3840–16128 |
  | 3 | 55, 63 | 8192 | 0–16383 |
  | 4 | 63 | 8192 | 0–9344 |
  | 6 | 62 | 8192 | 5504–13312 |
  | 7 | 67 | 8192 | 8192–15616 |
  | 8 | 64 | 8192 | 7808–16383 |
  | 9 | 51 | 8192 | 3840–16383 |

## Adjustable amount and landing deadzone (v3)

The v3 builder emitted `kboard_1.2.2-relative-tilt-v3.syx`. It keeps the
per-note baseline and computes a signed movement from it. The preset field
`CV_In_CV_1_Offset` at XRAM `0x04F1` stores `64 - amount`, where `amount` is
0–64; `CV_In_CV_2_Offset` at `0x04FA` stores the 0–12 step deadzone. These
fields were zero in the K-Board's stock preset, so a newly flashed board has
the v1 amount (64) and no deadzone until the editor sends other values. They
are unused CV input fields in this keyboard's generic preset layout.

For each tilt sample, the helper sends seven-bit bend value
`clamp(64 + sign(delta) * floor(min(max(abs(delta) - deadzone, 0), 64) * amount / 64), 0, 127)`.
The stock helper expands it to 14-bit MIDI pitch bend. Clamping the movement
at 64 steps guarantees the chosen amount is a maximum even if the finger
lands near a physical edge. The firmware uses `MUL AB` for the scaling and
preserves B on return. At amount 64 and deadzone 0, output matches v1 for
every seven-bit landing and current value.

The first adjustable build, v2, was flashed and live captured with amount 16
(shown as 25%) and deadzone 3. It correctly centered the first bend and
reduced movement, but did not cap movement beyond 64 steps: one of nine notes
reached MIDI bend 12032 (3840 above center), larger than the intended
16-step/2048-unit maximum. V3 adds that cap; v2 remains archived for
reproducibility. The test emulator checks all
128×128 landing/current combinations for the default and four amount/deadzone
combinations, along with note-off and normal MIDI passthrough.

V3 was flashed through the same official updater: all 165 chunks were accepted,
the application rebooted as 1.2.2, and the saved eight-channel MPE preset was
restored with amount 16 and deadzone 3. A fresh device settings readback
matched the intended profile. The firmware reboot left the physical Tilt and
Press buttons off; they were enabled before the final expression capture.

The final live capture covered 24 note gestures across MPE channels 2–9. Of
these, 19 produced a tilt bend while held and every first bend was 8192;
the five other gestures had no held-note tilt sample. All 4,391 pitch-bend
messages stayed within 6144–10240, the exact ±16 seven-bit-step limit for
amount 16. Held chords showed independent channel bends and channel pressure
was present. This verifies the hard amount limit on hardware. The precise
3-step deadzone cannot be measured from MIDI alone because the firmware does
not emit its raw tilt sensor readings; its boundary is covered by the 8051
emulator's exhaustive input test.

## Hardware update and recovery

The official Muse Kinetics `sendsysex` v0.15.0 (commit `8a587c1`) built on
Linux with its ALSA backend. Its read-only identity request saw the connected
board in application mode, version 1.2.2, bootloader 1.0.32. Its family
database supports `--fw-update K-Board -f <custom.syx>` and supplies the
K-Board's first-chunk delay and identity handshakes.

Immediately before the update, slot 0 was read again and saved as
`../backups/kboard-slot0-before-relative-tilt-2026-09-29.dump.syx` and
`.image.bin`. Its SHA-256 matches the earlier backup. The generated
`.restore.syx` decodes to **exactly** that 470-byte preset image.

The tested flash command was:

```sh
/tmp/kboard-sendsysex/build/SendSysEx --fw-update K-Board \
  -f firmware_analysis/kboard_1.2.2-relative-tilt.syx --fw-version 1.2.2
```

For the current build, use `firmware_analysis/kboard_1.2.2-relative-tilt-v6.syx`
in place of the older image in that command. The development board already
runs v6; no further flash is needed for the current changes.

The updater reported success for all 164 chunks and confirmed application
version 1.2.2 after reboot. The flash reset slot 0 to factory settings, so
the saved `.restore.syx` was sent and a new dump matched the original 470-byte
image exactly.

If the hardware result later proves wrong, the same updater can install
`firmware_stock/K-Board Firmware v1.2.2_cs512.syx`; then the saved preset can
be sent with the editor or its `.restore.syx` file.

The saved settings dump at `../backups/kboard-slot0-2026-09-29.image.bin` is a
470-byte preset backup. It is separate from firmware flash and does not replace
the original firmware image as a recovery source.

## Bend Pad priority (v5)

The [K-Board-C manual](https://files.keithmcmillen.com/products/k-board/documentation/K-Board-Manual.pdf)
specifies that, in MPE, the Bend Pad sends global pitch bend on channel 1,
per-note tilt uses member channels, and the stock "Bend Range (Tilt)" setting
has no effect. A prepatch capture confirmed 5,796 channel-1 pad bends while
member-channel tilt bends continued during the same gestures. On this Bitwig
setup the two sources sounded like they were competing.

V4 first hooked one pad sender at `0x3FAB`, but a live capture still showed
member bends interleaving with the global pad bend. That hook did not cover
the active send path. V4 is archived as an unsuccessful hardware experiment.

V5 hooks the common 14-bit sender at `0x7EA6` and checks for an MPE master
channel bend. It uses the pad scan state at XRAM `0x0999`, which the stock
routine checks at `0x3DA5` and clears on release at `0x45C3`. A noncenter
master bend also counts as active if the scan state has not updated yet. On
first pad contact, the helper centers each armed member channel and sets a
priority flag at `0x0F20`; while set, the per-note bend hook returns without
sending tilt bends. On release, the helper clears the flag and rearms each
member so the next tilt sample becomes its new neutral point. The common
sender's original three bytes are replayed before returning to stock code.
The existing 11%-amount/6-step-deadzone preset and both 90 sensitivity
settings were backed up byte for byte before the v4 and v5 flashes.

V5 was flashed with the official updater (166 chunks accepted); the board
rebooted as 1.2.2 and its restored 470-byte preset image matched the
preflash backup exactly. The final raw MIDI capture is preserved in
`kboard-v5-pad-priority-midi.csv.gz` as timestamp, status, data1, data2.
It captured 2,273 global bends and 780 member bends. There were two
noncenter Bend Pad intervals, lasting 4.19 and 2.94 seconds, with **zero**
member bend messages during either interval. At the second pad onset,
three active member channels were centered immediately before the master
bend. After each release, the next member bends started at 8192, then
continued with finger movement. This confirms pad priority, chord handling,
and tilt rebasing on the development board.

## Relative Bend Pad mixed into each note (v6)

The requested behavior is a sum of the Bend Pad and key tilt on the device.
V6 keeps each member channel's latest tilt bend at XRAM `0x0F30` through
`0x0F3F`. The common 14-bit sender at `0x7EA6` captures the first Bend Pad
position as the pad's zero point, computes later movement relative to it,
adds that offset to every active member's cached tilt, and keeps channel
1's bend centered. The per-key tilt hook also adds the current pad offset
to every new tilt sample. Release clears the pad offset and sends the
still-current tilt value; it does not rebase the tilt sensor.

Two unused CV input preset bytes hold pad controls: `CV_In_CV_1_Min` at
XRAM `0x04F0` stores `64 - relative_pad_amount`, and `CV_In_CV_2_Min` at
`0x04F9` stores `relative_pad_deadzone`. The amount spans 0–64 and the
deadzone spans 0–12 seven-bit steps. The stock Bend Pad range remains an
input-span setting; 12 gives the patch full raw pad travel. The user's
preflash preset had this stock setting at 1. Its exact image is in
`../backups/kboard-slot0-before-relative-tilt-v6-2026-09-29.image.bin`.

V6 flashed successfully through the official updater (167 chunks,
application identity 1.2.2). Its restored preset read back byte for byte:
MPE with eight members, tilt amount 7/64 and deadzone 6, pad input span
12, pad amount 16/64 and deadzone 3. A 75-second raw MIDI capture is in
`kboard-v6-relative-pad-midi.csv.gz`. It contains 13,518 master-channel
bends, all exactly 8192, and 30,300 member-channel bends across channels
2–9. The member range was 6144–10240, exactly ±16 seven-bit steps around
center, matching the configured 25% pad limit in this capture. The first
member output at pad contact was centered, then held channels tracked
the pad together. The user has not yet had time to test the sound in Bitwig.
`kboard_1.2.2-relative-tilt-v7.bin` and `.syx` are built by
`firmware_tools/build_velocity_slider_patch.py` on top of v6. They have been
tested only on an 8051 interpreter (`firmware_tools/mcs51.py`), never on the
board. Hold the Velocity button for five seconds: the keys stop playing and
the 15 white keys become a slider from gain 60 to 254, with the white-key LEDs
up to the chosen step lit. Press Velocity again to leave. The gain is written
to the live preset copy and is **not saved to flash**, so it resets on power
cycle and when the stock code reloads a preset.
How the stock firmware works, as found while writing this:
- The seven front buttons are analog sensor channels 0, 1, 2, 3, 4, 6 and 7,
  polled one per call by `0x56E8` (channel in XRAM `0x978`, reading `0x977`).
  Channel 0 is Tilt, 1 Pressure, 4 Velocity, 6/7 octave down/up; 2 and 3 are
  the remaining two. A press starts at a reading of 0x79 or more and ends
  below 0x73.
- The stock velocity toggle fires **while the button is still down**, about
  0.25 s after the press (`0x4D9A` flips bit 2 of XRAM `0x93E` and appends it
  to a flash state log at `0xEE00` through `0x6661`). A tap shorter than that
  does nothing. LED index for button channel *n* is 25+*n* (24+*n* for 6 and 7).
- The 16-bit millisecond tick is IRAM `0x4B:0x4C`, incremented by the Timer 0
  interrupt (Timer 0 reload `0xF05F` with SYSCLK 48 MHz / 12 gives about 1 ms).
  Confirmed on hardware: the 5000-tick hold in v7 measured 5.0 s by stopwatch,
  so a tick is 1 ms (SYSCLK 48 MHz).
- Preset fields are one byte each except every field with "Gain" in its name
  other than `Globals_Gain`, which is two bytes big endian. XRAM address =
  `0x347` + the field's offset in the 470-byte image. The velocity block is
  Curve `0x395`, Gain `0x396:0x397`, Max `0x398`, Min `0x399`, Offset `0x39A`.
  The note-on code loads pointer `0x0395` and calls the generic mapper `0x6183`:
  scaled = raw × Gain / 100, plus Offset, clamped 0–127, then Curve, then Min/Max.
  Gain 100 leaves velocity unchanged; the editor's 60–254 range is ×0.6 to ×2.54.
- Key LEDs are a 4×8 framebuffer at XRAM `0x819`, set by `0x7AFB` (R7 = LED
  index, R5 = level). LEDs 0–24 are the keys in chromatic order from C.
  `0x764D` redraws the button LEDs from `0x93E` every main-loop pass.
The patch adds three hooks, each replacing one three-byte stock instruction,
plus about 480 bytes in the erased flash at `0x8720–0x8B2E`:
| Hook | Replaces | Purpose |
| --- | --- | --- |
| `0x575C` | `MOV DPTR,#0x977` in the sensor dispatcher | watch the Velocity channel; while the slider owns the button, replace its reading with 0 so stock never sees it |
| `0x4C4F` | `MOV DPTR,#0x889` in the per-key note-on | in slider mode a white key selects its step and writes the gain; the stock note-on still runs so note-off bookkeeping stays consistent |
| `0x7C67` | `MOV DPTR,#0x8A5` in the common MIDI sender | in slider mode drop status bytes `0x90`–`0xEF`; note-off and system messages pass |
State is XRAM `0x0F40` (0 idle, 1 timing, 2 entered with button still down,
3 active, 4 exit press being swallowed), `0x0F41:0F42` press time, `0x0F43`
step, `0x0F44` the velocity bit before the press. On entry the velocity bit is
restored to its pre-press value, whatever the stock code did in between, and
logged with the stock `0x6661`. The hold tolerates dips in the reading down to
0x40, because a stock re-toggle during the hold is harmless (it is restored)
while a reset of the five-second clock would not be.
Tests (`tests/test_velocity_slider_patch.py`, 23 in this file) run the real
stock `0x56E8`, `0x4D9A`, `0x4C4F` and `0x7AFB` bytes with only the sensor
transaction, flash writes and MIDI transmit replaced. They check that presses
of many lengths and random multi-button scripts leave the toggle state and
flash log identical to stock, that the toggle is undone on entry and never
fires during the exit press, that the bar and gain are correct for every step
and key, that the start step is the nearest one to any current gain, that
muting is limited to slider mode, that registers are preserved across the
sensor hook, and that the hold works across a tick wrap and a torn tick read.
The emulator itself was checked against the stock 16-bit multiply and divide
routines on random inputs.
Known limits: not saved to flash; the tick-to-seconds assumption above; the
per-call cadence of `0x56E8` on real hardware is unmeasured (tests use 1, 10
and 40 ms per call); the Mode button (channel 3) and the other buttons are not
blocked while the slider is active.

V7 was flashed on September 29, 2026 with the official `SendSysEx` updater
(178 chunks accepted, application version 1.2.2 confirmed). The flash reset
slot 0 to factory settings; the pre-flash preset was restored from
`../backups/kboard-slot0-before-velocity-slider-v7-2026-09-29.restore.syx`
and a fresh dump matched the pre-flash image byte for byte. The restore file
generator (`firmware_tools/preset_backup.py`) reproduces two earlier
hand-made restore files exactly. To go back to stock, flash
`firmware_stock/K-Board Firmware v1.2.2_cs512.syx` the same way and send the
restore file again.

Hardware result: v7 works. Entering with the hold, choosing a level with the
white keys, and leaving with a second press all behave as designed. The
5.0 s hold was judged too long, so `HOLD_MS` is now 2500 (2.5 s). That is
**v8**, `kboard_1.2.2-relative-tilt-v8.bin` and `.syx`: it differs from v7 in
exactly two bytes (`0x8895`, `0x8898`, the halves of the hold constant), passes
the same 23 tests, and has **not been flashed**; the user has not yet asked
for it. v7 files are kept unchanged as the record of what is on the board.
## Pressure and Tilt menus with preset persistence (v9/v10)

`firmware_tools/build_sensor_config_patch.py` layers the three sensitivity
menus on the byte-verified v8 image. It checks the v8 SHA-256 before building.
V9 was flashed to the development board on September 29, 2026; v8 remains
unflashed. KMI's official `SendSysEx` v0.15.0 updater accepted all 191 chunks
and confirmed application version 1.2.2 after reboot. The flash reset slot 0,
so it was restored from
`../backups/kboard-slot0-before-velocity-pressure-tilt-v9-2026-09-29.restore.syx`.
A fresh slot 0 dump at
`../backups/kboard-slot0-after-velocity-pressure-tilt-v9-2026-09-29.image.bin`
matches the preflash image byte for byte (SHA-256
`7a1cedd8ae0e90570a4008914be60c27d3500766c99ffa48d3462466edd82c61`).
As with the earlier firmware updates, the physical Tilt and Press buttons
reset to off and need to be re-enabled on the device.

Hold Velocity (channel 4), Pressure (channel 1), or Tilt (channel 0) for 2.5
seconds. The menu mutes key output and turns the 15 white keys into a selector;
the corresponding button exits after the selection. Velocity and Pressure
use gains 60–254 in the same table. Tilt uses values 0–70 in steps of five.
V9 got the Velocity field right at XRAM `0x0396:0x0397`, but its Pressure and
Tilt targets were each one byte early. The correct preset fields are Pressure
gain at `0x0364:0x0365` and Tilt sensitivity at `0x034F` (stored as
`70 - sensitivity`). V10 corrects those targets.

On exit, the patch calls the stock slot erase routine `0x7A5A` for slot 0,
then calls stock byte-write routine `0x77DB` for all 470 bytes from XRAM
`0x0347–0x051C` to flash `0xF000–0xF1D5`. This project’s desktop editor reads
and writes slot 0; no user-facing slot selector was found. Erase and rewrite
are not atomic, so losing power during the save can leave slot 0 incomplete.
Saving is synchronous on menu exit and its duration has not been measured on
hardware.

After the user exercised v9's menus, the board still enumerated as a K-Board
and answered identity requests. The editor's settings read failed because
Pressure gain in slot 0 had become `0xAB5A` (43,866), outside the editor's
60–254 range; before the flash it was `0x005A` (90). The old Tilt target also
changed `Globals_On_Thresh`, confirming the address error. The editor reports
any settings decode exception as “K-Board unavailable,” which made this look
like a USB discovery failure. The pre-v9 preset backup above remains intact.

V10 corrects the field addresses and is now flashed on the development board.
The updater accepted 191 chunks and confirmed firmware 1.2.2. The current
damaged slot 0 was backed up as a raw dump and image at
`../backups/kboard-slot0-before-relative-tilt-v10-2026-09-29.*`; it was invalid
and could not produce a SysEx restore file. Per the user's instruction, the
clean pre-v9 backup was restored from
`../backups/kboard-slot0-before-velocity-pressure-tilt-v9-2026-09-29.restore.syx`.
The subsequent slot 0 image at
`../backups/kboard-slot0-after-velocity-pressure-tilt-v10-2026-09-29.image.bin`
matches that clean backup byte for byte. `Device.read_settings()` now succeeds
and reports Pressure 90, Tilt 70, Velocity 254. The physical Tilt and Press
buttons reset to off after the flash and need to be re-enabled.

`tests/test_sensor_config_patch.py` exercises the emitted key paths, validates
the Pressure/Tilt XRAM addresses against `preset_model.json`, and checks the
470-byte flash write in the 8051 model with flash routines stubbed. The v9
test results did not catch the bug because they asserted the same incorrect
addresses as the builder. The v10 targeted tests (4) and existing
firmware/protocol suite (45, one skipped) pass. This confirms the addresses in
software; hands-on menu/power-cycle behavior still needs testing.

## Menu usability revision (v11)

V11 is built on v10's corrected field addresses. Entry hold is 1,000 ms. While
a settings page is active, a short press of any of the three settings buttons
exits and saves; holding a different settings button for 1,000 ms changes to
that page. Holding the current page's button exits when released. During a
page view the stock button-state bits drive the indicator LEDs so only the
edited button is lit. The firmware snapshots the three settings-button states
before entry, restores them at exit, and logs the restored state. A page change
updates the single lit indicator.

`tests/test_sensor_config_patch.py` now has six tests, including an emulator
flow for one-second entry, a long-press switch from Velocity to Pressure,
short-press exit with a different button, and restoring the original three
button indicators when initially on or off. Those six tests pass, as do the existing 45 firmware and
protocol tests (one skipped). V11 is
`kboard_1.2.2-relative-tilt-v11.bin` / `.syx`, SHA-256
`35c4a8760cb2fce774c3eb032eba7fa95c9e22616486191765ecf5c1a1fc0762` for the
flat image and
`0789589c826feab9c8ae39aa5083d9b131755cb56db20581e39d5b13357b7dfc` for
SysEx. V11 was flashed on 2026-09-29 with KMI SendSysEx v0.15.0; all 192
chunks were acknowledged and the updater confirmed application version 1.2.2.
The slot 0 preset was backed up before flashing, restored afterwards, and read
back byte-identically. Before/after images are in
`../backups/kboard-slot0-before-relative-tilt-v11-2026-09-29.image.bin` and
`../backups/kboard-slot0-after-relative-tilt-v11-2026-09-29.image.bin` (SHA-256
`4cbd01d40a7d1f1bd42da49c7b904918380cbec8095767db026e2d3ed9130bdf`). The
physical Tilt and Press buttons reset to off after flashing and need to be
re-enabled. The user reports that all three menus work well on the flashed unit.

The Bend Pad is not one of the seven button channels and has no known LED.

## Stock bend behavior option (v12 source)

The v12 patch adds an editor setting that restores the stock MPE bend paths while retaining the
sensitivity menus: key tilt sends its absolute bend, the Bend Pad sends global pitch bend on the
master channel, and the two are not combined. The editor stores the mode marker in the otherwise
unused `CV_In_CV_1_Max` preset byte at XRAM `0x04EF`; value `0x7E` selects stock behavior. The
relative tilt and pad controls remain in the profile but are disabled in the editor while this mode
is selected.

The patched tilt hook replays the three original bytes at `0x7FF3` and re-arms that note's baseline
state for a clean switch back to relative mode. The Bend Pad hook replays the original sender bytes
at `0x7EA6`, clears relative-pad state, and restores its neutral offset. The final v12 image is
generated by `build_sensor_config_patch.py` on top of the v12 slider image.

V12 was flashed to the retail K-Board on September 29, 2026. The updater accepted all 192 chunks
and confirmed application version 1.2.2. Slot 0 was restored from
`../backups/kboard-slot0-before-relative-tilt-v12-2026-09-29.restore.syx`; the subsequent 470-byte
image matches the pre-flash backup exactly (SHA-256
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`). The Stock behavior option
has not yet been hands-on verified. As with other firmware updates, the physical Tilt and Press
buttons reset to off and need to be re-enabled.

## Unified 1.2.3 build and independent bend controls

The current source builds the relative tilt patch, Velocity slider, and three sensitivity menus in
memory through `firmware_tools/build_custom_firmware.py`. This is the single documented build
command and writes only `kboard-custom-firmware-1.2.3.bin` and `.syx`. It changes the application
identity byte at `0x5CF8` from 2 to 3 so the editor can recognize compatible firmware. The generated
image SHA-256 is `96df9ef56d5001c7159636f3feb40e92414ff55190ce738d0bfca89b3677ad52`; SysEx SHA-256 is
`821b9566186f32a210f8ea5db32e2e6d6e02840d63df4b1cfeedbf8d1f344131`. It has not been flashed.

The preset editor now has independent switches for relative tilt, relative Bend Pad, and combining
pad movement with per-note tilt. The three bits are stored in the otherwise unused CV 1 maximum
byte at XRAM `0x04EF`; values `0x70`–`0x77` encode the switches. The old v12 all-stock marker `0x7E`
is still decoded as all three switches off. The custom controls are shown only when a connected
device reports firmware 1.2.3.

The Bend Pad hook supports relative or absolute pad position and either member-channel combination
or a separate master-channel bend. The tilt hook supports relative or absolute per-note bend and
combines the cached tilt with the pad only when selected. These source changes have been assembled
into a firmware image, but the independent modes have not yet been tested in the 8051 suite or on
hardware.
