# Handoff notes for coding agents

Last updated 2026-09-30 by Codex. Read `README.md` first, then
`firmware_analysis/relative_tilt_findings.md` (addresses, hook tables, captures).

## State of the retail K-Board

- Custom firmware **1.2.3** is flashed and confirmed by the updater and identity request. It includes
  independent relative tilt, relative Bend Pad, bend-combination flags, sensitivity menus, Cyclone,
  and the scale quantizer. The latest image consumes matching releases for keys pressed in the scale
  selector and sensitivity menus so the MPE voice count cannot underflow. The user explicitly
  approved this image on 2026-09-30. KMI SendSysEx v0.15.0 accepted all 354 chunks and confirmed
  application version 1.2.3. Physical Tilt and Pressure buttons may need re-enabling after flashing.
- The flashed menu-release image is at
  `firmware_analysis/kboard-custom-firmware-1.2.3-menu-keyoff-fix-candidate.{bin,syx}`.
  Image SHA-256 `6fe6e76a37de15aa421b4824b8093155a58bce4ce9ded80cfd4fd1596bb13d21`;
  SysEx SHA-256 `79d441c0b2e8bf79c876e78d4b7024bdad0e0e14ad3abe22988890b723d481cb`.
  It marks key presses consumed by menu states 2, 3, and 5 so the existing key-off hook skips their
  stock release. The real-byte emulator reproduces the installed image's 0-to-255 voice-count
  underflow after a Tilt menu value edit. Regression tests cover all three menu modes, white and
  black keys, delayed releases, and visits with no piano key press. The full suite passes 76 tests,
  one skipped. The user reports that the features now work together on the device.
- Firmware **v8** (2.5 s slider hold) was an intermediate build and was not flashed. V11 was
  exercised before v12; see `firmware_analysis/relative_tilt_findings.md` for history.
- The preset was restored and verified byte-identically after the latest flash. Backup pair:
  `backups/kboard-slot0-before-menu-keyoff-fix-2026-09-30.*` and
  `backups/kboard-slot0-after-menu-keyoff-fix-2026-09-30.*` (SHA-256
  `41f363050398529cc3a91915da6db8bffbbe7a82aa0b724ff6c626c19e2d5b95`).
- Physical Tilt and Press buttons turn off after every firmware flash; the user re-enables them.

## Rules

- **Never flash without the user's explicit go-ahead for that flash.** Approval for one image does not
  cover the next.
- Before a flash: `PYTHONPATH=. python firmware_tools/preset_backup.py backup backups/<name>`.
  Flashing wipes the preset (pages `0xF000–0xF7FF`). After: `... send <name>.restore.syx`, then
  take another `backup` and confirm the image is byte-identical.
- Flash with KMI's official updater (`SendSysEx` v0.15.0, commit `8a587c1`; the previous build was at
  `/tmp/kboard-sendsysex/build/SendSysEx`, and `/tmp` is volatile):
  `SendSysEx --fw-update K-Board -f <image>.syx --fw-version 1.2.3` for the custom image.
- Rollback: flash `firmware_stock/K-Board Firmware v1.2.2_cs512.syx`, then send the restore file.
- Build the complete current image with `python firmware_tools/build_custom_firmware.py`; component
  builders assemble it in memory and validate the exact stock input. The final image reports 1.2.3.
  Do not flash it without a new explicit go-ahead.
- Run the full suite after every firmware change and before every flash. Use
  `PYTHONPATH=. python -m unittest tests.test_protocol tests.test_firmware_extract
  tests.test_relative_tilt_patch tests.test_velocity_slider_patch tests.test_sensor_config_patch
  tests.test_cyclone_game_patch tests.test_scale_quantizer_patch`.

## Things that are easy to get wrong

- Preset image layout: every field with "Gain" in its name except `Globals_Gain` is 2 bytes big endian.
  XRAM address = `0x347` + offset in the 470-byte image. Do not assume one byte per field.
- The known safe expansion flash region is `0x8E15–0xEDFF`. The current scale tables extend through
  `0xED6F`, leaving 144 contiguous bytes at the end; earlier gaps include `0x981E–0x9FFF` (2,018
  bytes), `0xA94A–0xAFFF` (1,718 bytes), and `0x8E15–0x92FF` (1,259 bytes). The image has about
  13.4 KiB of `0xFF` bytes across this region, but they are fragmented and include possible data
  bytes, so do not treat that total as one allocatable block. The page at
  `0xEE00` is the stock button-state log and is erased at runtime. Used XRAM: `0x0F00–0x0F3F` (tilt patch) and
  `0x0F40–0x0F51` (slider and sensor menus), `0x0F52–0x0F64` (Cyclone), and `0x0F65–0x0F8C`
  (scale selector and consumed-key marks). This leaves 115 bytes at the top of 4 KiB XRAM.
  A static audit found no stock indexed accesses above `0x00FF`:
  stock does not write the XRAM page register (`EMI0CN`, SFR `0xAA`). Startup does clear all 4 KB via DPTR.
  Static DPTR references top out at `0x09C7`, but computed DPTR bounds are not fully audited; don't call all
  of `0x0A00–0x0FFF` definitively free yet.
- The stock velocity toggle fires about 0.25 s into a press, while the button is still held, not on
  release. Any patch that watches the Velocity button must account for that.
- `firmware_tools/mcs51.py` runs real firmware bytes; prefer testing patches against stock routines
  with stubs (see `tests/test_velocity_slider_patch.py`) over modelling them by hand.
- Scale-selector and sensitivity-menu key presses consume stock note-on at `0x4C4F`; their releases
  must also consume stock note-off at `0x62D7`. Otherwise release decrements XRAM `0x00AD` (MPE active voice count)
  from 0 to 255. The stock allocator `0x6FE5` then sends every note on channel 1 and member tilt
  scaling is bypassed. The patch marks consumed keys at XRAM `0x0F74–0x0F8C` and clears each mark
  in the release hook, even if a menu has already closed. The flashed image marks selector and
  sensitivity-menu keys.
- When simulating long MIDI streams, stub `0x313D` as a drained USB writer. The interpreter has no
  USB interrupt, so letting the stock FIFO fill indefinitely can write into low XRAM and create
  emulator-only voice-state corruption.

## Open ideas, none started

- Save the slider gain to flash (stock erase/write at `0x775A`/`0x77DB`; power loss mid-write is the risk).
- Ignore the Mode/Tilt/Press buttons while the slider is active.
- Identify sensor channels 2 and 3 (see findings). `Globals_Gain` (`0x34C`, not in the editor) scales two
  values in the key scan; its purpose is unknown.
- In MPE mode, allow Pressure output to use a selected CC instead of forcing channel pressure. The
  editor already exposes channel pressure or CC 0–127, but profile validation currently forces
  `pressure_cc = -1` when MPE is enabled; inspect the firmware pressure sender and preset handling.
- Add velocity response curve selection to the on-board Velocity settings menu. The editor supports
  Linear, Logarithmic, Sine, Cosine, Exponential, and Invert. Consider using the black-key LEDs as
  curve selectors while white keys continue to select the existing 1–15 velocity sensitivity.
- Semitone transposition is part of the scale selector, where the existing octave arrows shift the
  scale root one semitone per press rather than acting as a general transpose shortcut.
- The scale selector needs hands-on validation after the latest flash. Entry: Tilt + Pressure
  held for one second. White keys directly select the 15 scale options; octave arrows transpose by
  semitone within ±12; Tilt or Pressure exits. State lasts until reboot. See
  `firmware_analysis/scale_quantizer_design.md`.
