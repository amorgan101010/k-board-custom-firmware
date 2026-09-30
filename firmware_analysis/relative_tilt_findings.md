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
image SHA-256 is `948d4419c61f03d042abb4c63d5933f6890c0444520ec884f239e2fe496eb657`; SysEx SHA-256 is
`2995462956c81db2ac5f4bc93b4029e7d38338a55c602599d4fd6e176a9d9f34`.

The preset editor now has independent switches for relative tilt, relative Bend Pad, and combining
pad movement with per-note tilt. The mode bits use the otherwise unused CV 1 maximum byte at XRAM
`0x04EF`, while CV 2 maximum at `0x04F8` disambiguates one value from the v12 all-stock marker. The
all-enabled default preserves the stock preset value `0x7F`. The old v12 marker `0x7E` with CV 2
maximum `0x7F` still decodes as all three switches off. The custom controls are shown only when a
connected device reports firmware 1.2.3.

The Bend Pad hook supports relative or absolute pad position and either member-channel combination
or a separate master-channel bend. The tilt hook supports relative or absolute per-note bend and
combines the cached tilt with the pad only when selected. The full suite ran 51 tests successfully
with one skip. The skipped case looks for an optional stock firmware copy at
`/tmp/kmi-sendsysex/syx/K-Board/K-Board Firmware v1.2.2_cs512.syx`; it does not exercise patched
behavior. The updater accepted all 197 chunks and confirmed version 1.2.3. The pre-flash preset was
restored and its 470-byte image read back byte-identically (SHA-256
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`). The independent bend modes
still need hands-on playing verification. The physical Tilt and Press buttons reset to off after the
flash and need to be re-enabled.

## Cyclone game build

`firmware_tools/build_cyclone_game_patch.py` layers the game after the unified sensitivity-menu
image. Hold Tilt, Pressure, and Velocity together for 1,000 ms, then release all three to begin.
The entry chord takes priority over a pending individual sensitivity hold. Game entry is deferred
while a sensitivity page or the Velocity slider is already active; exit that mode first. The game
restores the three button-state bits saved when the full chord begins and swallows those buttons
until they are released after game exit.

The flashed game uses the 15 natural-note key LEDs. Each level chooses one of eight target positions
between neighboring black-note LEDs. Its path mode and direction are selected again at each level:
wraparound or back-and-forth, ascending or descending. Sustain stops the cursor. A stop on the target
increments the score, selects a new target and path, and reduces the interval from 240 ms by 12 ms
to a 48 ms minimum. Passing a target does not fail; a stop anywhere else immediately ends the game.
Game over displays a score bar plus the cursor and target pair. Tilt, Pressure, or Velocity exits.

The game dispatcher wraps the existing sensitivity-menu dispatcher at `0x8B60`; its MIDI wrapper
preserves the menu hook from `0x8720`. The key LED setter is gated at `0x7B26`, so stock key scans
cannot overwrite the game display. Game LED draws use the same stock setter while XRAM
`0x0F62` is set. Game state occupies XRAM `0x0F52–0x0F62`.

`tests/test_cyclone_game_patch.py` executes the emitted game routines and the stock sensor dispatcher
in the 8051 interpreter. It covers the chord and release flow, timer wrap, sensitivity-menu
coexistence, targets and LED locations, both movement modes, success and immediate failure,
acceleration and speed floor, MIDI/LED ownership, exit, preset-page preservation, and SysEx image
round-trip. The complete project suite now passes 66 tests with one existing optional-firmware
skip.

The unified builder produces image SHA-256
`a1dd4086205fedc1bf1a02db75ef8dc44f85dd6c986527a27ad60775d29a1b47` and SysEx SHA-256
`3b06f3e3fa01010de7c59f619ce18511a626c506acf439d873d2227631a5bd97`. The retail K-Board was
updated on September 30, 2026 using KMI SendSysEx v0.15.0. The updater accepted all 221 chunks and
confirmed application version 1.2.3 after reboot. Slot 0 was backed up before flashing to
`../backups/kboard-slot0-before-cyclone-game-2026-09-30.*`, restored from its `.restore.syx` file,
and read back byte-identically at
`../backups/kboard-slot0-after-cyclone-game-2026-09-30.*`. Both preset images have SHA-256
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. The user reports that this
initial game version works well. The Tilt and Pressure buttons may need to be re-enabled after
installation.

## Cyclone gameplay refinements (flashed 2026-09-30)

The current source limits goals to D, G, and A in each available octave. Each goal uses an adjacent
black-key pair with exactly one white key between them. D#/F# and A#/C# are excluded because each
pair brackets two white keys. Target choices use an eight-bit Galois LFSR mixed with the low byte of
the millisecond tick at each level. The low three bits select among six targets; values 6 and 7
fold to 0 and 1, and if the chosen target repeats the previous one it advances to the next target.
This is lightweight pseudorandom variation, not a source of true randomness, and the adjustment
guarantees there are no consecutive repeats.

Touching the Bend Pad stops the cursor, using the stock touch state at XRAM `0x0999`; the touch must
be released before another stop can register. On a miss, the score bar is dim and the final cursor
blinks brightly over it. Pressing Sustain on the game-over
screen starts a new game and preserves the failed target long enough to avoid repeating it. Toggle
has no game action.

The flashed builder emits image SHA-256
`98f34894f26dce60c4f8289b9a979fb76b74ca02b69f02f8e12cdec801c69e82` and SysEx SHA-256
`da3feabbec5b43eff8e802e4e8ddfc4d75e138ba94dd6681041a163013bb973f`. The revised image was
flashed to the retail K-Board using KMI SendSysEx v0.15.0. The updater accepted all 223 chunks and
confirmed application version 1.2.3 after reboot. Slot 0 was backed up before flashing to
`../backups/kboard-slot0-before-cyclone-refinements-2026-09-30.*`, restored from its `.restore.syx`
file, and read back byte-identically at
`../backups/kboard-slot0-after-cyclone-refinements-2026-09-30.*`. Both preset images have SHA-256
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. The user reports the gameplay refinements work well. A report that the score stopped at 11 prompted a source review: successful stops have no cap at 11; only the LED score bar saturates at 15. The 11-point speed is 108 ms per cursor step, and the speed continues decreasing to its 48 ms floor at score 16.

## Cyclone game-over blink fix (flashed 2026-09-30)

The cursor LED is set to brightness zero during the hidden phase, including when it overlaps a score LED; the overlapped bar LED therefore disappears briefly during that phase. The image SHA-256 is
`95e18f7d851e90680e7f73d29e38c798598980a373327f95f3535add0acf54b2` and SysEx SHA-256 is
`ebe0f6a231641f941b0ef74230a3fef9c4c1945c7baa48b415eac39c36ca9e1f`. KMI SendSysEx v0.15.0 accepted all 223 chunks and confirmed application version 1.2.3. Slot 0 was backed up to `../backups/kboard-slot0-before-cyclone-blink-fix-2026-09-30.*`, restored from its `.restore.syx`, and read back byte-identically at `../backups/kboard-slot0-after-cyclone-blink-fix-2026-09-30.*`. Both preset images have SHA-256 `6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. As with earlier updates, the physical Tilt and Pressure buttons reset to off and may need to be re-enabled.

The user verified the updated game on hardware, scoring 13 and confirming that the cursor blinks correctly over the score display. This also confirms the score continues past 11 as intended.

## Cyclone always-bouncing cursor build (flashed 2026-09-30)

The user requested that the cursor always bounce between the two ends of the 15-white-key row instead of randomly choosing wraparound or bounce movement. The source now reverses direction at either endpoint on every level. The starting direction and cursor position remain randomized. The rebuilt image SHA-256 is `cb2a8fd64ce9332d01168621532f7e7eec5bde61d429bb492346b257f37f60ac`; SysEx SHA-256 is `f18ab113ee09262f5d69623cf732d9d3eec40a20420032fea60c2224d18d93c5`. KMI SendSysEx v0.15.0 accepted all 223 chunks and confirmed application version 1.2.3. Slot 0 was backed up to `../backups/kboard-slot0-before-cyclone-bounce-2026-09-30.*`, restored from its `.restore.syx`, and read back byte-identically at `../backups/kboard-slot0-after-cyclone-bounce-2026-09-30.*`. Both preset images have SHA-256 `6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. The physical Tilt and Pressure buttons reset to off after flashing and may need to be re-enabled.

## Scale quantizer flashed (2026-09-30)

The 15-scale selector was layered after Cyclone. Hold Tilt + Pressure for one second to enter;
press a white key to select its scale; octave arrows shift the root by semitones within ±12; Tilt or
Pressure exits. The root blinks in the shifted LED map, and scale/transposition reset after reboot.
The scale table is listed in `scale_quantizer_design.md`. The image SHA-256 is
`22057ddf870093c032cc16fb31c77c1b87720495e429d7904b310b89369c559f`; SysEx SHA-256 is
`095fae33be633d9e97e6c2a4afd0d8312ebbc8fb9cd3e60557295d128813870f`. KMI SendSysEx v0.15.0
accepted all 352 chunks and confirmed application version 1.2.3; an identity request confirmed
application mode. Slot 0 was backed up to
`../backups/kboard-slot0-before-scale-quantizer-2026-09-30.*`, restored from its `.restore.syx`, and
read back byte-identically at
`../backups/kboard-slot0-after-scale-quantizer-2026-09-30.*`; both preset images have SHA-256
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. Scale-menu behavior still
needs hands-on validation. As with prior updates, the physical Tilt and Pressure buttons reset to
off after flashing and may need to be re-enabled.

The user reported that completing Tilt + Pressure freezes the device until a power cycle. Emulator
tracing reproduced it: the stock MIDI sender clobbered R4, the counter in the 16-channel All Notes
Off loop. The loop now saves/restores its counter across each send. The corrected image was flashed
after a fresh backup; SendSysEx v0.15.0 accepted 352 chunks and confirmed 1.2.3. Image SHA-256
`a9657cc0e5f78ea22219f1e06596fd7d6afcf72a48fe45db3d7d21d9879542ce`; SysEx SHA-256
`122c0bf3383d0f2330378ced8ff565fb7ebd745636fac3c168ecc9a663457dd9`. Slot 0 was restored and read
back byte-identically from `../backups/kboard-slot0-before-scale-quantizer-fix-2026-09-30.*` and
`../backups/kboard-slot0-after-scale-quantizer-fix-2026-09-30.*`; both images hash to
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. The expanded suite passes 67
tests with one skipped, including a real stock-sensor-loop emulator regression for entry, release,
transpose, and scale selection. The corrected hardware interaction still needs user validation. The
physical Tilt and Pressure buttons reset to off after flashing and may need to be re-enabled.

## Scale selector LED fixes built (2026-09-30)

The user reported that Tilt/Pressure exit left key LEDs illuminated and that a fresh boot showed all
white keys lit rather than Chromatic with C blinking. The all-white display was an XRAM alias: the
scale ID overlaid the menu mode byte, so a nonzero mode selected Ionian. Scale-private state is now
in `0x0F65–0x0F73`, clear of the menu/slider state at `0x0F40–0x0F51` and Cyclone at
`0x0F52–0x0F64`. The redraw force branch also now bypasses the blink timer, entry initializes the
blink phase, and Tilt/Pressure exit explicitly turns off all 25 key LEDs.

The focused selector test and complete suite pass (67 tests, one skipped). The user authorized this
flash on 2026-09-30. KMI SendSysEx v0.15.0 accepted all 351 chunks and confirmed application version
1.2.3. Slot 0 was restored from
`../backups/kboard-slot0-before-scale-ui-fix-2026-09-30.restore.syx`; a new backup at
`../backups/kboard-slot0-after-scale-ui-fix-2026-09-30.*` matches byte-for-byte. Both preset image
hashes are `6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. Flashed image SHA-256:
`0c882a7dcd47ca67e3383842dd191f5ef6990c833ea8ce79dfaf582172f03391`; SysEx SHA-256:
`7f3ccae668625f1932ea4f35883186cc75e936d7a6373c1a9a7dd8ba2b1ad966`. Firmware updates reset the
physical Tilt and Pressure buttons to off; they may need to be re-enabled. Hands-on validation of
the fixed selector remains.

## Normal key MIDI path fix flashed (2026-09-30)

After the scale UI flash, the user reported no Bitwig input. With Bitwig closed, raw MIDI capture
while keys were pressed recorded repeated `0xFF` bytes and no Note On/Off. The stock key routine at
`0x4C4F` is a complete note-processing function: after its initial `MOV DPTR,#0x0889`, it continues
through note/velocity processing and the common sender. The scale wrapper's normal path replayed
only the initial instruction and returned, skipping the rest. It now resumes stock execution at
`0x4C52`; scale-selector key presses remain consumed. A real-byte emulator check confirms that a
normal key-on reaches the stock sender with `0x90` status. The full suite passes 68 tests with one
skipped. After the user's explicit go-ahead, KMI SendSysEx v0.15.0 accepted all 351 chunks and
confirmed application version 1.2.3. Slot 0 was restored from
`../backups/kboard-slot0-before-midi-path-fix-2026-09-30.restore.syx` and read back byte-identically
in `../backups/kboard-slot0-after-midi-path-fix-2026-09-30.*`; both preset image hashes are
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. Image SHA-256:
`f01ae1c7dc45befc07d82e7d1b5d18fce722a801a54fd85ff25650f7b11d1650`; SysEx SHA-256:
`8b59d69223ff2ceff422c6b69c134fa5faf98da734a46eda563fc06ad91e82a7`. A post-flash raw MIDI
The user subsequently confirmed that normal MIDI and quantization work. They then reported that the
sensitivity menus no longer open and tilt pitch bend stops after entering the selector until a
restart. The follow-up fix below addresses those reports.

## Scale selector sensor and bend fix flashed (2026-09-30)

After confirming the normal note path works, the user reported that the sensitivity settings view
could no longer be opened and that entering the scale selector disabled tilt pitch bend until a
restart. The image added R7 preservation around scale chord detection, but this did not restore
settings access: the menu body reloads its reading from XRAM `0x0977`. Selector entry sends All Notes Off
directly, bypassing the relative-tilt note-off hook, so the candidate also clears all 16 per-note
tilt active flags after that sequence. It preserves the pre-selector Tilt/Pressure toggle snapshot
and restores it on exit, avoiding an exit-button press from toggling off those controls. The full
image passed the full suite (68 tests, one skipped). After the user's explicit go-ahead, the image
was flashed with KMI SendSysEx v0.15.0; the updater process finished, the device returned to
application mode, and an identity request confirmed 1.2.3. Image SHA-256:
`39e0636729adfd6ba3ecf4432970db51f3748d394d38e616a2a68f0c227b74b1`; SysEx SHA-256:
`7d3583fd64ee5f5ebe2ca5e6468cb0371268fddbd92fe51c93d4c2e0d6bd7eaa`. Slot 0 was restored from
`../backups/kboard-slot0-before-scale-menu-bend-fix-2026-09-30.restore.syx` and read back
byte-identically at `../backups/kboard-slot0-after-scale-menu-bend-fix-2026-09-30.*`; both preset
image hashes are `2ecae9a803c30b0802404d48552354026a9b570d2b2a2bd03b743f0998f9ca0b`. The user has not
yet validated tilt bend after this flash. They confirmed that Tilt, Pressure, and Velocity settings
still cannot be opened by long press; see the confirmed cause below. Tilt and Pressure toggles may
need to be re-enabled after flashing.

## Sensitivity menu dispatch regression and flashed fix (2026-09-30)

The scale selector and sensitivity menus share XRAM state byte `0x0F40`: states 1–5 belong to the
menus, while states 6–8 belong to the selector. The scale dispatcher at `0xC200` used `JNZ` after
running its chord helper to decide whether to return. Once a single button press put the menu in
state 1, every later sensor sample returned there and never reached the menu body. In the real-byte
8051 emulator, all three buttons remained in state 1 after more than one second; the menu-only and
Cyclone images reached state 2. The dispatcher now returns only for state 6 or higher. Regression
coverage drives the stock sensor loop for Tilt, Pressure, and Velocity through the one-second hold
and release, and opens the Velocity page after entering and exiting a nonchromatic scale. The full
suite passes 70 tests with one skipped. After the user's explicit go-ahead, KMI SendSysEx v0.15.0
accepted all 352 chunks and confirmed application version 1.2.3. Image SHA-256:
`416f9a4034a60ce35ec2bf9c6d16827925e996776b523bcdf61da8f6a147f3fe`; SysEx SHA-256:
`994113404c2ad4dec7fe84a6f15fb6055ca77cdd18dd332475e6c3b7279f1c2b`. Slot 0 was restored from
`../backups/kboard-slot0-before-settings-state-fix-2026-09-30.restore.syx` and read back
byte-identically at `../backups/kboard-slot0-after-settings-state-fix-2026-09-30.*`; both preset
image hashes are `2ecae9a803c30b0802404d48552354026a9b570d2b2a2bd03b743f0998f9ca0b`. Hardware
validation of sensitivity-menu access and tilt pitch bend after this flash is pending.

## Hardware MPE channel collapse after scale selection (2026-09-30)

The user confirmed the sensitivity menus open but their white keys cannot edit values, and tilt
sounds like an unscaled full-range bend after using the scale selector. With the user's go-ahead,
a MIDI capture recorded notes on member channels 2 and 3 before selection, then notes on shared
channel 1 after selection. Before selection, member bend ranges were 6912–9600 and 7680–8192;
after selection, channel 1 bends reached both 0 and 16383 and alternated rapidly as two held keys
moved in opposite directions. The capture's 16 consecutive CC 123 messages identify selector
entry. A subsequent read-only slot 0 dump matched the saved preset byte for byte, including MPE
mode 1, bend flags `0x7F`, and reduction 52.

The selector intercepted scale-choice key-on at `0x4C4F` but let the matching stock key-off at
`0x62D7` run. The stock key-off decrements XRAM `0x00AD`, the active MPE voice count. Because no
voice was allocated for the selector key, the emulator reproduced a 0-to-255 underflow; the next
call to stock allocator `0x6FE5` returned channel 0. Relative tilt only scales active member
channels, so the fallback leaves full-range stock bend on the shared channel. The updated firmware
records selector key presses and consumes matching releases, then resumes ordinary key-offs at
`0x62DA`. The menu key-on routing, pointer overflow, and register preservation fixes are documented
in `scale_quantizer_design.md`. The full suite passes 74 tests, one skipped. The user's explicit
approval covered flashing this image on 2026-09-30; KMI's updater accepted all 353 chunks and
confirmed 1.2.3. Slot 0 was restored and read back byte-identically. Hands on confirmation is pending.

## Sensitivity-menu key releases repeat the MPE failure (2026-09-30)

The user confirmed the flashed scale-selector release fix restored MPE, but reported the same
shared-channel tilt behavior after using Tilt, Pressure, or Velocity settings. The menu key-on
handler changes white-key values without running stock note-on. On the flashed image, the emulator
reproduced a Tilt menu key press and release: XRAM `0x00AD` wrapped from 0 to 255, and the next
stock MPE allocation returned channel 0. The flashed fix marks all valid keys pressed
in menu states 2, 3, and 5 and consumes their matching releases. Tests cover each menu, white and
black keys, releases after exit, and visits without a piano key press. Full suite: 76 tests, one
skipped. KMI's updater accepted all 354 chunks and confirmed firmware 1.2.3. Slot 0 was restored
and read back byte-identically. The user subsequently reported that all features work together on
the device. See `scale_quantizer_design.md`
for image hashes and details.

## Follow-up ideas discussed (2026-09-30)

- **MPE pressure output as a configurable CC.** The preset editor already offers Channel Pressure
  or CC 0–127 for Pressure output, but `validate_profile()` currently forces `pressure_cc = -1`
  whenever MPE is enabled. Investigate the stock pressure sender and preset behavior to see whether
  MPE member-channel pressure can be emitted as the selected CC, while retaining Channel Pressure as
  an option. The on-board Pressure menu currently adjusts sensitivity only; the desired output
  assignment is a separate setting to design.
- **On-board velocity response curves.** The editor supports Linear, Logarithmic, Sine, Cosine,
  Exponential, and Invert. Add curve choice to the existing Velocity settings menu if the preset
  curve field can be safely updated there. White keys can keep selecting sensitivity levels 1–15;
  black-key LEDs could select and visibly indicate the six curves, making the active choice
  identifiable on the instrument. Verify that menu input handling can consume those black-key
  presses without sending notes, and determine a clear LED encoding before implementation.
- **Semitone transpose is now part of scale selection.** The octave down/up arrows shift the scale
  root by one semitone per press while the scale selector is open.
- **Game direction:** Whack-a-mole remains a promising next game. It can build on Cyclone's key LED
  display and timing, with physical key presses as hits; the game would need to intercept those
  presses so they do not also play notes.
