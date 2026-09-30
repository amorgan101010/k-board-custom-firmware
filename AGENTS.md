# Handoff notes for coding agents

Last updated 2026-09-29 by Claude Code. Read `README.md` first, then
`firmware_analysis/relative_tilt_findings.md` (addresses, hook tables, captures).

## State of the retail K-Board

- Firmware **v12** is flashed and identifies as 1.2.2. It includes relative tilt and Bend Pad
  behavior plus the on-board sensitivity menus. The 1.2.3 image is a new local build and has not
  been flashed or hands-on verified.
- Firmware **v8** (2.5 s slider hold) was an intermediate build and was not flashed. V11 was
  exercised before v12; see `firmware_analysis/relative_tilt_findings.md` for history.
- The preset was restored and verified byte-identically after the v12 flash. Latest pre-flash backup:
  `backups/kboard-slot0-before-relative-tilt-v12-2026-09-29.*` (`.restore.syx` writes it back).
- Physical Tilt and Press buttons turn off after every firmware flash; the user re-enables them.

## Rules

- **Never flash without the user's explicit go-ahead for that flash.** Approval for one image does not
  cover the next.
- Before a flash: `PYTHONPATH=. python firmware_tools/preset_backup.py backup backups/<name>`.
  Flashing wipes the preset (pages `0xF000–0xF7FF`). After: `... send <name>.restore.syx`, then
  take another `backup` and confirm the image is byte-identical.
- Flash with KMI's official updater (`SendSysEx` v0.15.0, commit `8a587c1`; the previous build was at
  `/tmp/kboard-sendsysex/build/SendSysEx`, and `/tmp` is volatile):
  `SendSysEx --fw-update K-Board -f <image>.syx --fw-version 1.2.2`.
- Rollback: flash `firmware_stock/K-Board Firmware v1.2.2_cs512.syx`, then send the restore file.
- Build the complete current image with `python firmware_tools/build_custom_firmware.py`; component
  builders assemble it in memory and validate the exact stock input. The final image reports 1.2.3.
  Do not flash it without a new explicit go-ahead.
- Run tests with `PYTHONPATH=. python -m unittest tests.test_protocol tests.test_firmware_extract
  tests.test_relative_tilt_patch tests.test_velocity_slider_patch tests.test_sensor_config_patch`.

## Things that are easy to get wrong

- Preset image layout: every field with "Gain" in its name except `Globals_Gain` is 2 bytes big endian.
  XRAM address = `0x347` + offset in the 470-byte image. Do not assume one byte per field.
- Free flash is `0x8E15–0xEDFF`; v6 uses up to about `0x8715`, v7/v8 use `0x8720–0x8B2E`. The page at
  `0xEE00` is the stock button-state log and is erased at runtime. Used XRAM: `0x0F00–0x0F3F` (tilt patch) and
  `0x0F40–0x0F51` (slider and sensor menus). A static audit found no stock indexed accesses above `0x00FF`:
  stock does not write the XRAM page register (`EMI0CN`, SFR `0xAA`). Startup does clear all 4 KB via DPTR.
  Static DPTR references top out at `0x09C7`, but computed DPTR bounds are not fully audited; don't call all
  of `0x0A00–0x0FFF` definitively free yet.
- The stock velocity toggle fires about 0.25 s into a press, while the button is still held, not on
  release. Any patch that watches the Velocity button must account for that.
- `firmware_tools/mcs51.py` runs real firmware bytes; prefer testing patches against stock routines
  with stubs (see `tests/test_velocity_slider_patch.py`) over modelling them by hand.

## Open ideas, none started

- Save the slider gain to flash (stock erase/write at `0x775A`/`0x77DB`; power loss mid-write is the risk).
- Ignore the Mode/Tilt/Press buttons while the slider is active.
- Identify sensor channels 2 and 3 (see findings). `Globals_Gain` (`0x34C`, not in the editor) scales two
  values in the key scan; its purpose is unknown.
