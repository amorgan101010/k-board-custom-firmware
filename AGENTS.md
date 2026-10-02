# Handoff notes for coding agents

Last updated 2026-10-02 by Codex. Read `README.md` first, then
`firmware_analysis/relative_tilt_findings.md` (addresses, hook tables, captures).

## State of the retail K-Board

- Previous custom firmware **1.2.3** was flashed and confirmed by the updater and identity request. It includes
  independent relative tilt, relative Bend Pad, bend-combination flags, sensitivity menus, Cyclone,
  and the scale quantizer with compact shared scale/quantizer tables. The latest image consumes
  matching releases for keys pressed in the scale selector and sensitivity menus so the MPE voice
  count cannot underflow. On 2026-09-30, the user authorized flashing the XRAM-savings candidate.
  KMI SendSysEx v0.15.0 accepted all 242 chunks and confirmed application version 1.2.3. Image:
  `firmware_analysis/kboard-custom-firmware-1.2.3-xram-savings-candidate.bin`, SHA-256
  `af7603406088eb42423d675835f2502a297cda4954dd9e5f95715d8f55a1d087`; SysEx SHA-256
  `f33c46e0a862f77d9c7cd3e57e92dce1cc489a7271a6f6a9e1c15ff9572f51c5`. It merges tilt channel
  state with the baseline and packs the scale/menu release marks into four bytes, freeing 37 bytes
  of named XRAM. The full suite passes 80 tests, one skipped. Physical Tilt and Pressure buttons
  may need re-enabling after flashing.
- Firmware **v8** (2.5 s slider hold) was an intermediate build and was not flashed. V11 was
  exercised before v12; see `firmware_analysis/relative_tilt_findings.md` for history.
- The preset was restored and verified byte-identically after the XRAM-savings flash. Backup pair:
  `backups/kboard-slot0-before-xram-savings-2026-09-30.*` and
  `backups/kboard-slot0-after-xram-savings-2026-09-30.*` (both image SHA-256
  `01615d521800aa7865565ecbcd8fa7f6d3d26bb7f2b002745e0afbce04680a6b`).
- Physical Tilt and Press buttons turn off after every firmware flash; the user re-enables them.
- A 1.2.4 high-resolution relative-tilt candidate was built on 2026-09-30 and is **not flashed**.
  It changes tilt amount to 0–100%, uses a 14-bit scaler, and allocates 16 cached low bend bytes
  at XRAM `0x0F00–0x0F0F` (previously free). Candidate image SHA-256
  `0536cd3de18236182a821a3439f8af119051a3564412b9b9be4b0535eb10101d`; SysEx SHA-256
  `ab4abe8cf22f63c97ea0285352c1a09f21ef9fc41e65a51b78496b39240313aa`. Software validation passes
  83 tests, one skipped. Its tilt changes are included in the flashed 1.2.5 image.
- Previous flashed firmware **1.2.5** adds one nearby second key, weights bend from each key's pressure
  above its first-touch baseline normalized to remaining MIDI pressure headroom, adds cached native
  tilt, and defers primary note-off until both keys are released. Candidate image SHA-256
  `81cfb7e9fe20f77bd1cbf8cf7a149b8d45554c196094e023d442f67c5c843d85`; SysEx SHA-256
  `8a3f3d66d6c0e6469546c9fff2e6ff2f1d1ba72e2fe962ea88a235af1f1f572b`. The full suite passes
  104 tests, one skipped. The generated SysEx validates and decodes byte-identically to the 64 KiB
  image (296 chunks); stock log/preset/tail bytes from `0xEE00` onward are unchanged.
  On 2026-10-01 the user performed overlapping pressure gestures captured
  passively on ALSA `36:0`. Fifteen complete gestures show contact pressure 38–52 immediately
  **before note-on**, later pressure reaching 127, and zero before release. This corrected the
  baseline capture and exposed the stock two-byte pressure sender at `0x7DF4` (via `0x8247`), which
  bypasses `0x7C67` and now has its own hook. The local ignored capture is
  `firmware_analysis/kboard-pressure-calibration-2026-10-01.txt.gz`; uncompressed SHA-256
  `86da70d96b7f60aa692b4f71f25d21a3e98baf3c39d126529b0e095127d18cad`. See
  `firmware_analysis/pressure_glide_design.md` for analysis, corrected release integration, full
  resolution bend arithmetic, and receiver scaling. Tilt compensation reads the original amount
  from the two-byte CV2 Gain at `0x04F6–0x04F7`, so tiny tilt settings survive wider glide spans.
  Repressing a released primary reuses its reserved channel, preventing a mapping collision.
  On 2026-10-01 the user explicitly authorized this image. KMI SendSysEx v0.15.0
  (commit `8a587c1`, rebuilt with ALSA support) accepted all 296 chunks and confirmed 1.2.5.
  Fresh slot 0 backup and restored readback are saved as
  `backups/kboard-slot0-before-pressure-glide-2026-10-01.*` and
  `backups/kboard-slot0-after-pressure-glide-2026-10-01.*`; both 470-byte images have SHA-256
  `fc41297dabeaadf3c53fc69ac89388aa5e5b1d86f10d5c502ff78c7c3bec456d`.
  The preset was restored byte-identically, including pressure glide range 0 (off).
  Flash log: `backups/kboard-pressure-glide-flash-2026-10-01.log`. Match the
  receiving MPE instrument and Bitwig to the editor's **Receiver MPE bend range**, the larger of
  tilt reference range and glide interval (±24 semitones for full-keyboard glides). Playing feel
  and glide MIDI output still need physical validation with glide enabled.

- Previous flashed firmware **1.2.6** replaces contact-baseline weighting with current pressure and fixes
  physical LED handling on deferred primary release/repress. Shared LED helper: `0x9B20`.
  Image SHA-256 `f8371bbbdd46494909a7c53c3763de0a1398b5194dd02e062d35adbad78e61ab`;
  SysEx SHA-256 `7f1c909e65ea8ea342dc0998a94e627a6a1fe5c89f4c274a5d41c2b5219b7251`.
  The full suite passes 109 tests, one skipped. Transport validates (296 chunks),
  matching the full image and unchanged stock tail.
  The user reported a three-position glide feel on 1.2.5 and agreed to try lower pressure
  sensitivity. The previous capture was saturated at 127 on 70.8% of pressure messages;
  a new capture was blocked by Bitwig's exclusive raw MIDI handle. Lower sensitivity improved
  the feel but it remained awkward; the user suggested dropping first-touch subtraction.
  Firmware 1.2.6 uses current pressure directly, applies bend when the pair forms, and
  caches the latest pre-note-on reading only as current pressure. Its handler ends at
  `0xEDD6` (41 erased bytes before the stock log); physical feel remains unvalidated.
  On 2026-10-01 the user explicitly authorized flashing this image. KMI SendSysEx v0.15.0
  accepted all 296 chunks and confirmed 1.2.6. Slot 0 was backed up fresh, restored, and
  read back byte-identically. Backup pair:
  `backups/kboard-slot0-before-pressure-glide-1.2.6-2026-10-01.*` and
  `backups/kboard-slot0-after-pressure-glide-1.2.6-2026-10-01.*`; image SHA-256 for both:
  `3c093f2874336da3fd4c8c1ed43e16f7aeab650c54ba818a0551c04acac73063`.
  Restored settings include glide 12, tilt amount 5%, tilt reference 12, pressure sensitivity 60.
  Updater log: `backups/kboard-pressure-glide-1.2.6-flash-2026-10-01.log`.
  Physical Tilt and Pressure buttons may need re-enabling. See `pressure_glide_design.md`.

- After the 1.2.6 flash, the user confirms the LED fixes work but glide remains wrong.
  A timestamped octave-pair gesture capture on ALSA `32:0` contains 581 events,
  69 distinct bends, and visible pressure 0–76 (no saturation). Primary pressure
  drops 19 → 0, causing a 2.44-semitone endpoint jump, then returns 0 → 30,
  causing a 3.47-semitone jump back. Companion contacts appear on channels 5/6/7,
  consistent with release/reallocation during motion; ongoing companion pressure is hidden.
  Capture: `firmware_analysis/kboard-pressure-glide-1.2.6-octave-2026-10-01-1.txt.gz`;
  uncompressed SHA-256 `190bea6fc6d5b523d4002af6c0daa6cc0550dd3d203f5116bef17f12f6f1f699`.
  The subsequent offline trace identified the stock cutoff; see the next entry and design notes.
  No firmware or preset changes were made during this capture.

- Stock pressure cutoff investigation is complete offline. `firmware_tools/trace_pressure_cutoff.py`
  runs real held-key scan and mapper bytes with the latest preset. At pressure gain 60, raw
  127 maps to MIDI 76; natural playing already reached that mapped maximum. With touch
  threshold 30, held keys release below raw 30 and pressure is forcibly cleared (raw 30 →
  MIDI 18, raw 29 → MIDI 0 through the scan). Pressure is the maximum of the two sensor
  values at XRAM `0x007A + key`, not IRAM `0x32:0x33` (which holds tilt). Candidate early
  hook `0x25F6` sees both 16-bit sensor values at IRAM `0x34:0x35` and `0x2F:0x30` before
  the key-state cutoff. A future glide weight must approach zero at the release boundary
  and handle recontact hysteresis; using raw pressure unchanged would still jump.
  No firmware or preset changes were made for this investigation.

- **Previous flashed firmware 1.2.7** implements force weighting before the stock cutoff at
  `0x25F6`. Force is above the fixed release threshold, independent of MIDI aftertouch
  sensitivity; it is not relative to first-touch pressure. At Globals Gain 100 it uses
  fractional sensor bits; other gains use scaled coarse force. Weights now have high
  bytes at `0x0FAD` and low bytes at `0x0FBD`; latest scan key/high/low uses
  `0x0F2A–0x0F2C`. Helpers: `0xAA00–0xAB17`, `0xAC00–0xAC6D`, `0xAD00–0xAD49`.
  MIDI pressure no longer updates weights; it still passes for the primary and is
  consumed for the companion. Note allocation/rejoin seeds matching current sensor
  force; unchanged scans do not resend bends. Existing LED/release fixes remain.
  The ratio retains 129 positions and stock recontact hysteresis remains. Hardware
  feel is unvalidated. The full suite ran 109 tests, one skipped; transport validates
  298 chunks, exact image round trip and unchanged stock tail. Image SHA-256
  `080d09ee7afbbbb5956f1e5a0653ebe9a2ed59d1fbaac4b3da0a79c5aaa21862`;
  SysEx SHA-256 `ab95f698b47c99da88c78a42ccc0bf4f683aa50111b3f3cb3b1ddaf97a96853f`.
  Paths: `firmware_analysis/kboard-custom-firmware-1.2.7-pressure-glide-candidate.*`.
  On 2026-10-01 the user explicitly authorized this flash. KMI SendSysEx v0.15.0
  accepted all 298 chunks and confirmed application version 1.2.7. Before flashing,
  the full suite ran 109 tests, one skipped. Slot 0 was backed up fresh, restored,
  and read back byte-identically. Backup pair:
  `backups/kboard-slot0-before-pressure-glide-1.2.7-2026-10-01.*` and
  `backups/kboard-slot0-after-pressure-glide-1.2.7-2026-10-01.*`; both image SHA-256:
  `3c093f2874336da3fd4c8c1ed43e16f7aeab650c54ba818a0551c04acac73063`.
  Preserved glide range 12, tilt amount 5%, tilt reference 12, pressure sensitivity 60.
  Flash log: `backups/kboard-pressure-glide-1.2.7-flash-2026-10-01.log`.
  Physical Tilt and Pressure buttons need re-enabling after the flash.

- **Previous flashed firmware 1.2.8:** user reports 1.2.7 glide feels much better, but single
  keys sometimes sound wrong with only physical Pressure enabled. Offline real stock
  allocator/MIDI tests reproduced missing channel bend resets when Tilt is off and
  uninitialized/stale native tilt cache use. The fix centers newly allocated members
  before note-on with glide enabled and Tilt off, and uses center for glide's native
  base when Tilt is off. Combined Bend Pad offsets are retained. Force weighting is
  unchanged. Reset helper `0xAE00–0xAEB5`; UPDATE_BEND ends `0x93F5`; MIDI handler
  ends `0xEDA1` (94 erased bytes before stock log). Full suite ran 113 tests, one skipped.
  Transport validates 299 chunks,
  exact image round trip, unchanged stock tail. Image SHA-256
  `944631299302723d723c1ad242d9536e78ecc79223a7e4091215e5a2a439cb82`;
  SysEx SHA-256 `50108a15a35936399c92f448654c1aa1818745558ff5c8bd9c67a194bd6b75c5`.
  Paths: `firmware_analysis/kboard-custom-firmware-1.2.8-pressure-glide-candidate.*`.
  On 2026-10-01 the user explicitly authorized flashing this image. KMI SendSysEx
  v0.15.0 accepted all 299 chunks and confirmed application version 1.2.8. The
  full suite ran 113 tests, one skipped, immediately before flashing. Slot 0 was
  backed up fresh, restored, and read back byte-identically. Backup pair:
  `backups/kboard-slot0-before-pressure-glide-1.2.8-2026-10-01.*` and
  `backups/kboard-slot0-after-pressure-glide-1.2.8-2026-10-01.*`; both image SHA-256:
  `db8cb4c7c524b14d1db4e7296054655ff5303a414dcdaee586217bdaf1c0a052`.
  The latest preset differs from the earlier backup: glide 2, tilt amount 3%,
  tilt reference 12, pressure sensitivity 88, velocity sensitivity 60; all restored.
  Flash log: `backups/kboard-pressure-glide-1.2.8-flash-2026-10-01.log`.
  Physical Tilt and Pressure buttons turn off after flashing. On 2026-10-02 the
  user reports 1.2.8 feels very good and no pressure-glide-related issues have been
  noticed. The current implementation is satisfactory for now; further refinement
  is deferred. This is hands-on feedback, not an exhaustive validation of every mode.

- **Previous flashed firmware 1.2.9 (2026-10-02):** fixes the scale selector's fifth
  choice, Lydian: white F now plays F♯ instead of duplicating E. Seven-note scale
  records map C–D–E–F–G–A–B to consecutive scale degrees; the other eight seven-note
  scales already matched this layout. Black-key snapping, other scale sizes, LEDs,
  and pressure glide retain their existing behavior. Only two image bytes differ
  from flashed 1.2.8: version `0x5CF8` and Lydian F record `0xCF35`. The editor
  recognizes 1.2.9. Full suite: 116 tests run, one skipped; transport: 299 chunks,
  exact image round trip, unchanged stock tail from `0xEE00` onward. Paths:
  `firmware_analysis/kboard-custom-firmware-1.2.9-white-key-scales-candidate.*`.
  Image SHA-256 `f65548504e43f3f70695701a1aab20a2f79c136911d91588b7bb1f4e50699ef6`;
  SysEx SHA-256 `7df3d239d8385804bc36904d5c823022c5b057894e27d816d65a83bf268ec77c`.
  The user explicitly authorized this flash on 2026-10-02. KMI SendSysEx v0.15.0
  (commit `8a587c1`) sent all 299 chunks and confirmed application version 1.2.9;
  a separate identity request also confirmed application mode and 1.2.9. The full
  suite ran 116 tests, one skipped, immediately before flashing. Slot 0 was backed
  up fresh, restored, and read back byte-identically. Backup pair:
  `backups/kboard-slot0-before-white-key-scales-1.2.9-2026-10-02.*` and
  `backups/kboard-slot0-after-white-key-scales-1.2.9-2026-10-02.*`; both 470-byte
  images have SHA-256 `17e23e9b877e0f9ce6544c2540ada63725e397dd6e36072e48f884611e7ad723`.
  Preserved glide range 2, tilt amount 5%, tilt reference 2, pressure sensitivity 88,
  velocity sensitivity 60, MPE enabled with 15 member channels. Flash log:
  `backups/kboard-white-key-scales-1.2.9-flash-2026-10-02.log`.
  Physical Tilt and Pressure buttons need re-enabling after flashing. On 2026-10-02,
  the user confirmed the scale-mapping fix works on the keyboard. Any further flash
  requires its own explicit approval.

- **Previous flashed firmware 1.2.10 (2026-10-02):** fixes missing companion tilt in pressure
  glide. The user chose to blend both keys' cached 14-bit tilt with the existing sensor-force
  ratio. Either key's tilt now updates the original voice; primary release gives full tilt
  control to the companion. Primary recontact rearms its landing baseline and centers its
  cache. Physical Tilt off still uses center; combined Bend Pad is added once. No new XRAM.
  Helpers: blend `0x9D00–0x9DF6`, rearm `0xAF00–0xAF2D`. Key-on ends `0x8FD5`,
  UPDATE_BEND `0x939F`, BEND_SAMPLE `0x9632`. Full suite: 122 tests run, one skipped.
  Transport: 303 chunks, exact 64 KiB round trip, stock tail unchanged from `0xEE00`.
  Image SHA-256 `290b6a8c9bc7405f9c3eba89d3fffc5d80ac9ce4b24a658ccb18d6ea593b1aae`;
  SysEx SHA-256 `a92be0df3ce1b5f58da84e225a45574daa3953bb02ca56c61abdfd1248b9c6f2`.
  Paths: `firmware_analysis/kboard-custom-firmware-1.2.10-blended-glide-tilt-candidate.*`.
  The editor recognizes 1.2.10. The builder produced this image. On 2026-10-02 the
  user explicitly authorized flashing it. KMI SendSysEx v0.15.0 (commit `8a587c1`, rebuilt
  with ALSA support) accepted all 303 chunks and confirmed application version 1.2.10;
  a separate identity request confirmed 1.2.10 in application mode. Immediately before
  flashing, the full suite ran 122 tests, one skipped. Slot 0 was backed up fresh on ALSA
  `36:0`, restored with SendSysEx, and read back byte-identically. Backup pair:
  `backups/kboard-slot0-before-blended-glide-tilt-1.2.10-2026-10-02.*` and
  `backups/kboard-slot0-after-blended-glide-tilt-1.2.10-2026-10-02.*`; both 470-byte images
  have SHA-256 `a2d034f99df88e9640e42c2c9f1c9b035b7351889620c963a9bc094982ff3ab8`.
  Preserved glide range 2, tilt amount 10%, tilt reference 2, landing deadzone 3, pressure
  sensitivity 254, velocity sensitivity 60, tilt sensitivity 65, and MPE with 15 members.
  Flash log: `backups/kboard-blended-glide-tilt-1.2.10-flash-2026-10-02.log`.
  Physical Tilt and Pressure buttons need re-enabling after a flash. On 2026-10-02 the
  user reports that tilt seems to be working on the keyboard. This is initial hands-on
  feedback, not exhaustive validation of release/recontact, Bend Pad combinations, or
  other modes. Any further flash requires its own explicit approval. See
  `pressure_glide_design.md`.

- **Current flashed firmware 1.2.11 (2026-10-02):** fixes the user's report that the companion
  does not control aftertouch after primary release. While both keys are held, aftertouch
  remains with the primary. On primary release, send the companion's cached mapped pressure
  immediately and forward further updates to the retained primary channel. Suppress late
  released-primary pressure; primary recontact returns ownership with its latest contact value.
  Sensitivity/curve mapping, sensor force, tilt blending, and glide arithmetic remain unchanged.
  Physical Pressure off prevents synthetic handoff messages. Sixteen mapped-pressure cache
  bytes use previously free XRAM `0x0F78–0x0F87`; the builder checks release-bitmap/state-table
  boundaries. Helpers: OUTPUT_PRESSURE `0x9E00–0x9E46`, RESEND_PRESSURE `0x9E80–0x9EE9`.
  Key-on ends `0x8FD8`, key-off `0x9270`, PRESSURE_SAMPLE `0x9547`. Full suite: 130 tests
  run, one skipped. Transport: 305 chunks, exact 64 KiB round trip, unchanged stock tail.
  Image SHA-256 `e7b2c2bde65694a6479c1570e9f29e80d72055971a593ccb830422ad2d05fb64`;
  SysEx SHA-256 `3cd3df30cbdfde88764c13832d4809e9d04371e8a8ead3f2a8e2861c372d6b71`.
  Paths: `firmware_analysis/kboard-custom-firmware-1.2.11-glide-aftertouch-handoff-candidate.*`.
  The editor recognizes 1.2.11; the builder writes this flashed image. The user explicitly
  authorized flashing it on 2026-10-02. KMI SendSysEx v0.15.0 (commit `8a587c1`, ALSA)
  accepted all 305 chunks and confirmed application version 1.2.11; a separate identity request
  confirmed 1.2.11 in application mode. The full suite ran 130 tests, one skipped, immediately
  before flashing. Slot 0 was backed up fresh on ALSA `36:0`, restored, and read back
  byte-identically. Backup pair:
  `backups/kboard-slot0-before-glide-aftertouch-handoff-1.2.11-2026-10-02.*` and
  `backups/kboard-slot0-after-glide-aftertouch-handoff-1.2.11-2026-10-02.*`; both 470-byte images
  have SHA-256 `a2d034f99df88e9640e42c2c9f1c9b035b7351889620c963a9bc094982ff3ab8`.
  Preserved glide range 2, tilt amount 10%, tilt reference 2, landing deadzone 3, pressure
  sensitivity 254, velocity sensitivity 60, tilt sensitivity 65, and MPE with 15 members.
  Flash log: `backups/kboard-glide-aftertouch-handoff-1.2.11-flash-2026-10-02.log`.
  Physical Tilt and Pressure buttons need re-enabling. Hands-on validation of the pressure
  handoff is pending. Any further flash requires its own approval. See `pressure_glide_design.md`.

## Rules

- **Never flash without the user's explicit go-ahead for that flash.** Approval for one image does not
  cover the next.
- Before a flash, make and validate a fresh slot 0 backup. Flashing wipes the preset (pages
  `0xF000–0xF7FF`). Restore with KMI SendSysEx, then read slot 0 again and confirm the image is
  byte-identical. Avoid Python Mido for device I/O; the verified 2026-09-30 procedure used
  `aseqdump --port=32:0 --raw` to capture the reply while KMI SendSysEx sent a request, then
  validated the dump with `preset_image_from_dump()` and generated the restore payload offline with
  `image_to_sysex()`.
- Flash with KMI's official updater (`SendSysEx` v0.15.0, commit `8a587c1`; the previous build was at
  `/tmp/kboard-sendsysex/build/SendSysEx`, and `/tmp` is volatile):
  Use `--fw-version 1.2.11` for the currently flashed glide-aftertouch-handoff image; `1.2.3` and `1.2.4`
  identify the earlier images.
- Rollback: flash `firmware_stock/K-Board Firmware v1.2.2_cs512.syx`, then send the restore file.
- Build the complete current image with `python firmware_tools/build_custom_firmware.py`; component
  builders assemble it in memory and validate the exact stock input. The current builder writes the
  flashed 1.2.11 glide-aftertouch-handoff image. Any subsequent flash
  requires its own explicit go-ahead.
- Run the full suite after every firmware change and before every flash. Use
  `PYTHONPATH=. python -m unittest tests.test_protocol tests.test_firmware_extract
  tests.test_relative_tilt_patch tests.test_velocity_slider_patch tests.test_sensor_config_patch
  tests.test_cyclone_game_patch tests.test_scale_quantizer_patch tests.test_pressure_glide_patch`.

## Things that are easy to get wrong

- Preset image layout: every field with "Gain" in its name except `Globals_Gain` is 2 bytes big endian.
  XRAM address = `0x347` + offset in the 470-byte image. Do not assume one byte per field.
- The known safe expansion flash region is `0x8E15–0xEDFF`. The compact scale records occupy
  `0xCF00–0xCFB3` and the transpose lookup occupies `0xD200–0xD218`; scale key-off code ends at
  `0xD28A`. The 1.2.5 pressure table and handler occupy `0xD28B–0xEDCE`, leaving 49 erased bytes
  through `0xEDFF`. The 1.2.4 candidate has 19,693 erased
  bytes across the safe region, fragmented into smaller runs elsewhere. The page at
  `0xEE00` is the stock button-state log and is erased at runtime. Used XRAM in the 1.2.4 candidate:
  `0x0F00–0x0F0F` (cached low bend bytes), `0x0F10–0x0F1F` (combined tilt state/baselines),
  `0x0F20–0x0F22` (Bend Pad), and `0x0F30–0x0F3F` (cached high tilt bends). The earlier flashed
  1.2.3 image left `0x0F00–0x0F0F` free. Other patch ranges are
  `0x0F40–0x0F51` (slider and sensor menus), `0x0F52–0x0F64` (Cyclone), and
  `0x0F65–0x0F77` (scale selector and four-byte consumed-key bitmap). The rest of the reserved
  `0x0F00–0x0F8C` spans contain 45 unallocated bytes in the 1.2.4 candidate. The computed-DPTR trace in
  `firmware_analysis/resource_audit.md` establishes `0x0F8D–0x0FF9` (109 bytes) as free for stock
  1.2.2 under the firmware-managed queue invariants; stock uses `0x0FFA–0x0FFF` for a six-byte
  cursor window. Startup clears all 4 KB of XRAM. See the audit for the trace scope and caveats.
- The stock velocity toggle fires about 0.25 s into a press, while the button is still held, not on
  release. Any patch that watches the Velocity button must account for that.
- `firmware_tools/mcs51.py` runs real firmware bytes; prefer testing patches against stock routines
  with stubs (see `tests/test_velocity_slider_patch.py`) over modelling them by hand.
- Scale-selector and sensitivity-menu key presses consume stock note-on at `0x4C4F`; their releases
  must also consume stock note-off at `0x62D7`. Otherwise release decrements XRAM `0x00AD` (MPE active voice count)
  from 0 to 255. The stock allocator `0x6FE5` then sends every note on channel 1 and member tilt
  scaling is bypassed. The patch marks consumed keys in a bitmap at XRAM `0x0F74–0x0F77` and clears each mark
  in the release hook, even if a menu has already closed. The flashed image marks selector and
  sensitivity-menu keys.
- When simulating long MIDI streams, stub `0x313D` as a drained USB writer. The interpreter has no
  USB interrupt, so letting the stock FIFO fill indefinitely can write into low XRAM and create
  emulator-only voice-state corruption.
- Pressure uses the stock **two-byte** sender at `0x7DF4`; a hook only at `0x7C67` never sees it.
  Stock contact pressure precedes note-on. Cache it with `FLAG_CONTACT_PENDING` until allocation;
  zero cancels that pending contact. The normal note-on fallback captures a later first sample.
  Stock key-off emits pressure and bend messages, which can overwrite glide calculation scratch.
  The release hook saves the primary channel on the stack across these calls. Its integration
  tests execute real stock releases with both physical Tilt and Pressure bits enabled.
  The key-on hook also checks for a released primary before allowing a new allocation for that key.

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
