# Pressure glide design

## Current flashed firmware 1.2.11

Firmware 1.2.11 fixes aftertouch handoff to the companion after primary
release. It was flashed with explicit user approval on 2026-10-02; the updater
and a separate identity request confirmed 1.2.11 in application mode. Slot 0
was backed up fresh, restored, and read back byte-identically. The full suite
ran 130 tests, one skipped, immediately before flashing. Hands-on validation
of this pressure handoff remains pending. Re-enable the physical Tilt and
Pressure buttons after flashing. Implementation and backup details follow.

## Previous flashed firmware 1.2.10

Firmware 1.2.10 now blends tilt from both glide keys using their sensor-force
ratio. It was flashed with explicit approval on 2026-10-02, and slot 0 was
restored and read back byte-identically. The updater and a separate identity
request both confirmed 1.2.10. On 2026-10-02, the user reports that tilt
seems to be working on the keyboard. This is initial hands-on feedback;
release/recontact, Bend Pad combinations, and other modes have not been
exhaustively validated. The implementation and flash record follow.

The user subsequently reports that the companion's pressure stops controlling
non-glide expression after the primary is released. This is reproduced in the
real stock MIDI sender offline: 1.2.10 consumes companion aftertouch in every
pair state. Firmware 1.2.11 below fixes that handoff.

## Firmware 1.2.11: aftertouch handoff (2026-10-02)

While both keys are held, the primary continues to own the shared voice's
aftertouch. Once it is physically released, the companion's mapped pressure
is sent immediately on the retained primary channel, and every subsequent
companion pressure update goes to that channel. Messages from the released
primary are cached but suppressed, so its late release zero cannot overwrite
the companion. Recontact returns ownership to the primary and sends its latest
pre-note-on contact pressure without allocating another voice.

These are the stock mapped MIDI pressure values, preserving the configured
pressure sensitivity and curve. They do not change sensor-force acquisition,
glide weighting, or the force-weighted tilt blend. The handoff sends nothing
when the physical Pressure button is off. Ordinary keys and independent glide
pairs retain their channels. Both release orders clear the caches when their
channels are freed; the next contact reading is retained for allocation.

The real held-key scan sends its final zero while the primary is still marked
held, then the release hook immediately restores the companion value. After
the handoff, later primary zeros remain suppressed. Integration tests execute
the actual stock cutoff, pressure mapper, note/release routines, and two-byte
MIDI sender, with the USB writer stubbed as drained.

Sixteen cached mapped pressure bytes occupy previously free XRAM
`0x0F78–0x0F87`, after the four-byte consumed-key bitmap. The builder guards
against overlap with that bitmap and the glide state table at `0x0F8D`.
OUTPUT_PRESSURE occupies `0x9E00–0x9E46`; RESEND_PRESSURE occupies
`0x9E80–0x9EE9`. They preserve registers and use INTERNAL_SEND to prevent
synthetic messages from overwriting physical caches. Key-on ends at `0x8FD8`,
key-off at `0x9270`, and PRESSURE_SAMPLE at `0x9547`. The editor recognizes
1.2.11. Only the version byte and aftertouch cache/routing, release/recontact,
and new helper regions differ from 1.2.10.

Software validation: 130 tests run, one skipped. Eight new regressions cover
immediate and continuous companion aftertouch, stale primary suppression,
recontact, stock sensitivity mapping, stock release cutoff, both final release
orders and channel reuse, independent pairs, and the Pressure-off case.
The 305-chunk SysEx validates and decodes exactly to the 64 KiB image; the
stock tail from `0xEE00` onward is unchanged.

Artifacts: `kboard-custom-firmware-1.2.11-glide-aftertouch-handoff-candidate.bin`
and `.syx`. Image SHA-256:
`e7b2c2bde65694a6479c1570e9f29e80d72055971a593ccb830422ad2d05fb64`;
SysEx SHA-256:
`3cd3df30cbdfde88764c13832d4809e9d04371e8a8ead3f2a8e2861c372d6b71`.
The builder writes this flashed image. On 2026-10-02 the user explicitly
authorized the flash. KMI SendSysEx v0.15.0 (commit `8a587c1`, with ALSA
support) accepted all 305 chunks and confirmed application version 1.2.11.
A separate identity request also confirmed 1.2.11 in application mode.

Fresh slot 0 backup and restored readback were captured on ALSA `36:0` with
`aseqdump --raw` while SendSysEx sent the request. The fragmented SysEx events
were joined and validated before decoding each 470-byte image. The restore
payload was generated offline, verified against the backup, and sent with
SendSysEx. The post-flash readback matches every pre-flash byte. Backup pair:

- `backups/kboard-slot0-before-glide-aftertouch-handoff-1.2.11-2026-10-02.*`
- `backups/kboard-slot0-after-glide-aftertouch-handoff-1.2.11-2026-10-02.*`

Both preset images have SHA-256
`a2d034f99df88e9640e42c2c9f1c9b035b7351889620c963a9bc094982ff3ab8`.
Preserved settings include glide range 2, tilt amount 10%, tilt reference 2,
landing deadzone 3, pressure sensitivity 254, velocity sensitivity 60, tilt
sensitivity 65, Logarithmic velocity curve, combined relative Bend Pad, and
MPE with 15 member channels. Flash, restore, and identity logs use prefix
`backups/kboard-glide-aftertouch-handoff-1.2.11-` and date `2026-10-02`.
Physical Tilt and Pressure buttons need re-enabling. Playing validation of
the aftertouch handoff is pending; any next flash needs its own approval.

## Previous flashed firmware 1.2.9

On 2026-10-02, firmware 1.2.9 was flashed with explicit user approval to fix
Lydian white-key mapping. Pressure-glide code and state are unchanged from
1.2.8. The full suite ran 116 tests, one skipped; the updater and a separate
identity request confirmed 1.2.9. Slot 0 was backed up fresh, restored, and
read back byte-identically. Preserved settings include glide range 2, tilt
amount 5%, tilt reference 2, pressure sensitivity 88, and velocity
sensitivity 60. See `scale_quantizer_design.md` for the complete flash
record and backup paths. Re-enable physical Tilt and Pressure after flashing.

## Firmware 1.2.10: force-weighted tilt from both glide keys (2026-10-02)

The user reports that only the first key has tilt during pressure glide and
chooses to blend both keys' tilt with the same sensor-force weighting used
for the glide. Firmware 1.2.9 discards companion bends in `BEND_SAMPLE` and
uses only the primary's cached tilt for resends. The companion already has
its own native relative-tilt baseline and full-resolution bend cache; its
note-on is suppressed only after the native note-on hook initializes them.

Firmware 1.2.10 computes the shared tilt from both cached native values:

```text
q = floor(128 * force_2 / (force_1 + force_2))
    # 0 if both forces are zero; 128 when the primary is physically released
blended_tilt = floor(((128 - q) * tilt_1 + q * tilt_2) / 128)
output = clamp(blended_tilt + combined_pad_offset + glide_offset, 0, 16383)
```

Here the tilt values are full 14-bit MIDI bends centered at 8192. Both keys
keep independent landing baselines, amounts, deadzones, and receiver-range
compensation. An update from either key now recomputes and sends the blend
on the original voice's channel. The companion remains silent as a separate
voice. Force changes also update the tilt blend. A physically released
primary has no tilt influence; the held companion continues to control tilt.
Releasing the companion first restores the primary's native tilt. Repressing
a reserved primary centers its cache and arms a fresh landing baseline before
rejoining, avoiding stale tilt from its previous contact.

Physical Tilt off still selects center regardless of either cache. A combined
Bend Pad offset is applied once after the tilt blend, retaining the existing
pad clamp and cached low-byte behavior. Force acquisition, glide ratio and
pitch arithmetic, aftertouch routing, scale mapping, and LED behavior retain
their existing implementations.

The blend helper occupies `0x9D00–0x9DF6` and the recontact tilt helper
`0xAF00–0xAF2D`, both previously erased. Updated key-on ends at `0x8FD5`,
UPDATE_BEND at `0x939F`, and BEND_SAMPLE at `0x9632`. No new XRAM is allocated;
blend scratch uses registers and the stack. The builder validates all new
regions against the unmodified component image. The editor recognizes 1.2.10.

Software validation: 122 tests run, one skipped. Six new regression tests
cover all 129 blend ratios over 81 pairs of 14-bit values, native relative
and absolute tilt from either key, force-driven handoff, both release orders,
primary recontact, and shared Bend Pad resends. The pair fixture runs the real
stock note-on formatter and native tilt initialization. The SysEx validates
303 chunks and decodes byte-identically to the complete 64 KiB image; the
stock tail from `0xEE00` onward is unchanged. Only the version byte and the
key-on, tilt blend/routing, and recontact helper regions differ from 1.2.9.

Artifacts: `kboard-custom-firmware-1.2.10-blended-glide-tilt-candidate.bin` and
`.syx`. Image SHA-256:
`290b6a8c9bc7405f9c3eba89d3fffc5d80ac9ce4b24a658ccb18d6ea593b1aae`;
SysEx SHA-256:
`a92be0df3ce1b5f58da84e225a45574daa3953bb02ca56c61abdfd1248b9c6f2`.
On 2026-10-02 the user explicitly authorized this flash. The full suite ran
122 tests, one skipped, immediately before flashing. KMI SendSysEx v0.15.0
(commit `8a587c1`, rebuilt at `/tmp/kboard-sendsysex/build/SendSysEx` with
ALSA support) accepted all 303 chunks and confirmed application version
1.2.10. A separate identity request also confirmed application mode and 1.2.10.

Fresh slot 0 was captured on ALSA `36:0` with `aseqdump --raw` while the
updater sent the settings request. Its fragmented SysEx events were joined
before validating the dump checksum and decoding the 470-byte image. The
561-byte restore payload was generated offline and verified against that
image. SendSysEx restored it after flashing, then a fresh readback matched
every byte of the pre-flash image. Backup pair:

- `backups/kboard-slot0-before-blended-glide-tilt-1.2.10-2026-10-02.*`
- `backups/kboard-slot0-after-blended-glide-tilt-1.2.10-2026-10-02.*`

Both preset images have SHA-256
`a2d034f99df88e9640e42c2c9f1c9b035b7351889620c963a9bc094982ff3ab8`.
Preserved settings include glide range 2, tilt amount 10%, tilt reference 2,
landing deadzone 3, pressure sensitivity 254, velocity sensitivity 60, tilt
sensitivity 65, Logarithmic velocity curve, and MPE with 15 member channels.
Flash, restore, and identity logs use prefix
`backups/kboard-blended-glide-tilt-1.2.10-` and date `2026-10-02`.

Firmware 1.2.10 is **flashed**. Re-enable the physical Tilt and Pressure
buttons after a flash. On 2026-10-02, the user reports: "cool tilt seems to
be working." This supports the tilt fix in hands-on use; it does not establish
exhaustive validation of release/recontact, Bend Pad combinations, or other modes. Any subsequent flash requires its own explicit
approval and a fresh, validated preset backup and verified restore.

## Previous flashed firmware 1.2.8: wrong single-note pitch with Tilt off

After flashing 1.2.7, the user reports substantially improved glide feel, but
sometimes single keys sound at the wrong pitch. The user confirmed that only
the physical Pressure button was lit.

An offline reproduction running the real stock allocator, key-on/key-off and
MIDI sender confirmed two defects when physical Tilt is off:

1. Stock key-on and key-off skip sending a centered member pitch bend when
   Tilt is disabled. A glide's last bend can remain on a freed MIDI channel;
   a later single note on that channel inherits it.
2. Glide resends read the native tilt cache even though Tilt off means stock
   never initializes that cache. It can contain zero or an earlier tilt offset,
   making the glide start from the wrong base pitch.

Candidate 1.2.8 resets newly allocated MPE members before note-on when glide
is enabled and physical Tilt is off. It initializes the native tilt cache to
center and sends a centered bend, including the existing combined Bend Pad
setting if active. Glide resends explicitly use center as their base when
physical Tilt is off. With Tilt enabled, the existing native tilt path remains
responsible for its initialization and offset. Hidden companion channel resets
do not change the primary's pitch. Force weighting is unchanged.

The new reset helper occupies `0xAE00–0xAEB5`. UPDATE_BEND ends at `0x93F5`;
the MIDI handler ends at `0xEDA1`, leaving 94 erased bytes before the stock log.
Regression coverage includes both release orders, repeated real allocator
channel reuse after glides, uninitialized/stale native caches, ordinary
single-note pitch, Tilt enabled, glide disabled, and combined Bend Pad offsets.
The full suite ran 113 tests successfully, one skipped.
The generated 299-chunk SysEx validates, decodes byte-identically to the image,
and preserves the stock tail from `0xEE00` onward.

Artifacts:

- `firmware_analysis/kboard-custom-firmware-1.2.8-pressure-glide-candidate.bin`:
  SHA-256 `944631299302723d723c1ad242d9536e78ecc79223a7e4091215e5a2a439cb82`.
- `firmware_analysis/kboard-custom-firmware-1.2.8-pressure-glide-candidate.syx`:
  SHA-256 `50108a15a35936399c92f448654c1aa1818745558ff5c8bd9c67a194bd6b75c5`.

On 2026-10-01 the user explicitly authorized flashing 1.2.8. KMI SendSysEx
v0.15.0 accepted all 299 chunks and confirmed application version **1.2.8**.
The full suite ran 113 tests, one skipped, immediately before flashing.
Slot 0 was backed up fresh, restored, and read back byte-identically:

- `backups/kboard-slot0-before-pressure-glide-1.2.8-2026-10-01.*`
- `backups/kboard-slot0-after-pressure-glide-1.2.8-2026-10-01.*`

Both preset images have SHA-256
`db8cb4c7c524b14d1db4e7296054655ff5303a414dcdaee586217bdaf1c0a052`.
The user's latest settings were preserved: glide range 2, tilt amount 3%,
tilt reference 12, pressure sensitivity 88, velocity sensitivity 60.
Flash log: `backups/kboard-pressure-glide-1.2.8-flash-2026-10-01.log`.
Physical buttons reset to off after flashing.

### User feedback, 2026-10-02

The user reports that 1.2.8 feels very good and no issues related to pressure
glide have been noticed. The implementation is satisfactory for now; further
refinement can wait. This records successful hands-on playing feedback without
claiming exhaustive validation of every setting or gesture.

## Previous flashed firmware: 1.2.7

On 2026-10-01 the user authorized implementing force weighting before the
stock cutoff, then explicitly authorized flashing 1.2.7. KMI SendSysEx v0.15.0
accepted all 298 chunks and confirmed application version **1.2.7**.
The full suite ran 109 tests, one skipped, immediately before flashing.
Slot 0 was backed up fresh, restored, and read back byte-identically:

- `backups/kboard-slot0-before-pressure-glide-1.2.7-2026-10-01.*`
- `backups/kboard-slot0-after-pressure-glide-1.2.7-2026-10-01.*`

Both preset images have SHA-256
`3c093f2874336da3fd4c8c1ed43e16f7aeab650c54ba818a0551c04acac73063`.
Glide range 12, tilt amount 5%, tilt reference 12 and pressure sensitivity 60
were preserved. Flash log:
`backups/kboard-pressure-glide-1.2.7-flash-2026-10-01.log`.
Re-enable the physical Tilt and Pressure buttons after flashing. Physical
feel of 1.2.7 remains to be checked.

```text
force = max(0, (scaled_sensor_max - stock_off_threshold) * 128 + fraction)
ratio = floor(128 * companion_force / (primary_force + companion_force))
glide_offset = (companion_pitch - primary_pitch) * ratio / 128
```

This subtracts the fixed physical release boundary, not pressure at first touch.
At the current threshold of 30, coarse force 31 has weight 128, force 30 has
weight zero (plus any fractional sensor force), and force below 30 has zero
weight before stock code clears the pressure source. Equal nonzero forces give
the midpoint. Both zero gives ratio zero; a physically released primary still
selects the companion endpoint until the pair ends.

The new hook at `0x25F6` runs after stock computes the maximum pressure sensor
and before stock key-state release. It preserves PSW, A, B, DPTR and R0–R7,
then executes the displaced `MOV DPTR,#0x0897` and resumes at `0x25F9`.
At Globals Gain 100, it retains the low seven bits of the larger full sensor
word (`IRAM 0x34:0x35` / `0x2F:0x30`). At other Globals Gain values it uses only
the scaled coarse reading, so the zero point remains aligned with stock release.
Aftertouch curve, gain, minimum, maximum, and offset do not affect glide weight;
the normal MIDI aftertouch output remains unchanged. Companion aftertouch is
still consumed because the companion has no audible voice.

Weights are unsigned 16-bit words, stored high at `0x0FAD + channel` and low
at `0x0FBD + channel` (repurposed from the old contact cache). The latest key
scan is cached at `0x0F2A–0x0F2C` and seeds note allocation or primary rejoin
only when its key matches. New helpers occupy `0xAA00–0xAB17`,
`0xAC00–0xAC6D`, and `0xAD00–0xAD49`. The shorter MIDI handler now ends at
`0xED9E`, leaving 97 erased bytes before the stock log. Unchanged sensor scans
do not resend bends. Tilt and Bend Pad offsets retain their previous scaling.

**Limits:** the blend ratio still has 129 positions (0–128), so an octave glide
has approximately 9.4-cent steps. Stock attack/recontact hysteresis remains;
a released primary rejoins only at stock note-on, using its current sensor
force. Firmware simulation does not establish scan timing or physical feel.

Validation: full suite ran 109 tests, one skipped. Tests cover fractional force,
16-bit ratio endpoints and arithmetic, monotonic sweeps, independence from
pressure sensitivity, stock cutoff integration, unchanged-scan suppression,
register preservation, release orders, reserved-channel rejoin, LEDs and tilt.
The 298-chunk SysEx validates and decodes byte-identically to the 64 KiB image;
stock bytes from `0xEE00` onward match the flashed 1.2.6 image.

Artifacts:

- `firmware_analysis/kboard-custom-firmware-1.2.7-pressure-glide-candidate.bin`:
  SHA-256 `080d09ee7afbbbb5956f1e5a0653ebe9a2ed59d1fbaac4b3da0a79c5aaa21862`.
- `firmware_analysis/kboard-custom-firmware-1.2.7-pressure-glide-candidate.syx`:
  SHA-256 `ab95f698b47c99da88c78a42ccc0bf4f683aa50111b3f3cb3b1ddaf97a96853f`.

The remaining sections record the design and findings for earlier firmware.

## Target behavior

In MPE mode, a note can absorb one nearby key press. The second key contributes
pressure and pitch, but does not start a second audible voice. The active voice
glides between the two pitches according to each key's current pressure.
Flashed 1.2.6 removes the first-touch subtraction used in previous firmware 1.2.5.
Releasing either key removes its contribution; releasing the
first key while the second is still held leaves the voice at the second pitch.
The voice ends only after both keys are released. Repressing the first key
while the companion remains held restores its pressure contribution on the
same reserved voice without another note-on.

Pitch bend has one value per MPE member channel, so the pressure-glide offset
and the existing per-note tilt offset must be summed on the primary channel.
The bend result is clamped to the MIDI range. The receiver's MPE bend range is
global for both effects; a ±24-semitone receiver setting permits full-keyboard
glides, while tilt must be scaled in semitones so its existing small range is
preserved.

## Pressure weighting

The current flashed 1.2.6 firmware uses current MIDI channel pressure
directly as each key's weight. First-touch pressure is never subtracted:

```text
weight_i = current_pressure_i
ratio = floor(128 * weight_2 / (weight_1 + weight_2))
    # ratio = 0 if both weights are zero; 128 if only the companion has weight
glide_offset = (pitch_2 - pitch_1) * ratio / 128
```

Equal pressures give the midpoint, including two lightly held keys. Increasing
one key's pressure moves toward its pitch. Only one physically held key gives
that key's exact pitch. The blend is recalculated when the second key joins.
Stock pressure received before note-on seeds the current value; repeated
pre-note-on samples retain the latest reading. Those readings are not used as
baselines. With no pre-note-on reading, the first subsequent sample contributes
immediately. Releasing a key removes its contribution.

Pressure sensitivity still affects the values used here. Saturation at 127
remains a limit: variation in force beyond it cannot affect this blend.
This direct weighting is an experiment following the user's report that
lower sensitivity helped but the blend remained awkward. The captured
measurements below and the original 1.2.5 flash record are historical.

## First passive pressure capture (2026-09-30)

Captured passively from ALSA sequencer port `32:0` with `aseqdump --raw`; no
device state was changed. These were single-key gestures, not a two-key
calibration. One showed initial channel pressure 36, followed by values near
70 and 127; later gestures began at 38 and 52. A separate short capture began
at 60 on channel 15 and 43 on channel 1; both reached 127. Values returned to
zero on release. This supports per-key contact baselines and shows initial
readings vary, but it does not characterize the pressure curve or timing. No
useful simultaneous-key sequence was captured, so weighting feel, hysteresis,
and deadband remain uncalibrated.

## Two-key pressure capture (2026-10-01)

The user performed overlapping gestures on notes 53/65 and 67/52 while a
passive listener recorded ALSA port `36:0`. The listener stopped when the user
said "done". The local raw log is saved as
`firmware_analysis/kboard-pressure-calibration-2026-10-01.txt.gz` (ignored by
Git); the uncompressed SHA-256 is
`86da70d96b7f60aa692b4f71f25d21a3e98baf3c39d126529b0e095127d18cad`.
It contains 2,616 MIDI events: 16 note-ons, 16 note-offs, 946 channel-pressure
messages, and 1,638 pitch-bend messages. The beginning includes an incomplete
held-note sequence; the following 15 complete gestures supply the pressure
observations below.

Every complete gesture had a pressure message immediately before note-on.
Those contact readings were 38–52, while the first pressure readings after
note-on were 74–114. All 15 gestures reached 127 and returned to zero before
note-off. For example:

| Note | Channel in log | Pressure before note-on | First pressure after note-on |
| --- | --- | --- | --- |
| 53 | 10 | 43 | 81 |
| 65 | 11 | 38 | 79 |
| 67 | 1 | 41 | 79 |
| 52 | 2 | 40 | 85 |

This changed the implementation: an idle member channel now caches the
initial nonzero pressure before note-on, and note-on preserves that value as
its contact baseline. A zero reading cancels a pending contact, and channel
release clears it. Without this correction, a contact of 43 followed by 81
would incorrectly establish a baseline of 81 and lose 38 units of usable
pressure travel. With the captured baseline, that second sample contributes
`floor(128 * (81 - 43) / (127 - 43)) = 57` normalized weight units.

The stock pressure path is the two-byte MIDI sender at `0x7DF4`, reached from
`0x8247`; it bypasses the three-byte sender at `0x7C67`. The candidate now
hooks both paths. The captured contact/pressure sequence is exercised through
the real stock `0x8247` routine in the emulator, alongside the note sender.

Of 877 pressure messages during the complete held-note gestures, 644 were
127. These are message counts, not durations: the log has no timestamps or
force measurements. The capture therefore supports using 127 as the upper
endpoint and preserving the initial contact value; it does not justify an
invented nonlinear force curve or time-based smoothing. Response tuning in
this candidate uses those measured endpoints with per-key normalization.
The playing feel and any future noise deadband need validation after a
separately authorized flash.

## Firmware integration constraints

- Existing note-on processing at `0x4C4F` knows the physical key index; the
  common MIDI sender at `0x7C67` sees the allocated channel, note, velocity,
  channel pressure, and pitch bend.
- A joined second key needs an internal member channel so stock pressure
  sensing remains active, even when its note-on is hidden from the receiver.
- A hidden second note-on must not cause a receiver voice. Its pressure and
  pitch-bend messages must also be hidden; only the calculated bend on the
  primary channel is sent to the receiver.
- If the primary key is released first, its stock member-channel allocation
  must remain reserved until the companion releases. Otherwise the allocator
  could reuse the channel while the receiver is still sustaining the glide.
- The released primary note-off is sent when the pair ends, and both internal
  channel allocations must then be returned exactly once.
- Only a single companion key is supported in the first version. Other keys
  allocate normal independent voices.
- The scale quantizer's output pitch is used for range checks and glide
  endpoints so the bend matches the notes the receiver actually hears.

## Initial settings

The editor stores glide range plus one in CV2 Channel, tilt reference range in
CV2 CC Number, and the unscaled tilt amount times its reference range in the
two-byte CV2 Gain word (`0x04F6–0x04F7`). The candidate's tilt scaler reads
that exact product so widening the receiver range preserves even a 1% tilt
setting. CV2 Max retains a rounded percent as a fallback for older firmware.
For sensor movement magnitude `m` (0–64), the native tilt offset is
`floor(m * amount_percent * reference_span * 128 / (100 * receiver_span))`
14-bit bend units. The receiving MPE
instrument and Bitwig controller script must match the larger of the tilt
reference range and glide interval, shown as **Receiver MPE bend range** in
the editor; ±24 semitones covers the keyboard. An on-board UI is deferred
until the note lifecycle and pressure response have been validated on hardware.

## Firmware implementation

The 1.2.5 image installs a 25 × 129-entry 14-bit bend table at
`0xD28B`, then hooks the scale quantizer's sender with the pressure handler so
pairing sees the quantized pitch. The handler keeps key, pitch, pressure
baseline, weight, role, and partner by member channel in XRAM `0x0F8D–0x0FEC`.
The physical key-off hook defers the primary release and returns both internal
channels exactly once in either release order. Pressure updates recompute the
bend from cached native tilt, preserve an enabled combined Bend Pad offset,
and send their sum. Secondary note-on, pressure, bend, and note-off messages
are consumed after the pair is assigned. Stock initial bend and pressure
messages precede note-on and still reach the unused companion channel; they
do not start a receiver voice. The separate pressure sender is intercepted
at `0x7DF4`. The table is used for a 24-semitone receiver span; a helper at
`0x9A00` computes the matching magnitude directly for smaller receiver spans.
The two-byte sender bridge is at `0x9B00`, and the exact tilt compensation
helper is at `0x9C00`. Older presets without the reference fields retain the
previous percent-based tilt scaler.

The custom image builder and 8051 emulator tests cover table bounds, pair
creation, captured pressure-before-note-on ordering, normalized per-key
baseline subtraction, full-resolution tilt addition, receiver span scaling,
and both release orders. Tiny tilt tests encode real editor presets and run
the actual native tilt sender, checking that widening to ±24 retains offsets
of a fraction of a semitone. Release integration tests execute the real stock
key-off routine with Tilt and Pressure enabled, proving the active voice count
returns to zero, both channel maps clear, and one audible note-off is sent.
The release hook preserves its primary channel on the stack while nested
pressure and bend sends use shared calculation scratch bytes. Candidate
glide output and combined Bend Pad modes still need physical validation. A
release/repress test also checks that the primary's reserved channel map is
retained and both allocations are returned when the gesture ends.

The final software suite passes 104 tests, one skipped. The built 1.2.5 image
and SysEx match after decoding, and the stock button log, preset pages, and
tail region at `0xEE00–0xFFFF` remain byte-identical. The editor's displayed
receiver span was checked locally with device probing disabled. See `AGENTS.md`
for final image hashes. The image was flashed with explicit user approval on 2026-10-01;
see the flash record below.

## Validation still required

- Raw overlapping pressure gestures are recorded above. Validate the glide
  response on the flashed firmware with glide enabled, varying each key's pressure
  independently and releasing the keys in both orders.
- Confirm the second note-on is absent, pressure is isolated to the pair,
  the primary channel remains allocated after its physical release, and the
  final note-off is emitted once.
- Test glide combined with tilt and with the relative Bend Pad modes.
- A firmware flash requires separate explicit approval and a fresh
  validated slot 0 backup.

## Hardware capture recipe

For a pressure calibration capture on the previous 1.2.3 firmware,
record raw MIDI from the K-Board output with
`aseqdump --port=32:0 --raw | tee pressure-glide-capture.txt` (adjust the ALSA
port if it changes; stop the command with Ctrl-C after the gestures). Use two
keys within the proposed range;
the stock firmware should report independent channel pressure for both. Note
the chosen key pair and each action in a side log; raw MIDI alone does not
identify finger force or physical key state. This captures pressure response,
not pressure-glide output.

1. Press the lower key and hold it at a steady, comfortable pressure for about
   two seconds. This gives a single-key pressure ramp and release reference.
2. Press the higher key very lightly while keeping the first key steady. Hold
   both, then gradually increase only the second key's pressure.
3. Keep the second key steady and gradually increase the first key's pressure.
   This checks that either key moves the weighted target in the expected
   direction.
4. Release the first key while holding the second, then release the second.
   Repeat with a fresh pair and release the second key first.
5. Repeat once with a different initial pressure on the first key. Stop the
   recording and save the raw log plus the side log together.

Keep actions slow and separated by a short steady hold so pressure plateaus
can be compared. After this raw pressure pass informs any response tuning,
candidate hardware validation can check that the second note-on is hidden,
pressure is isolated to the pair, and the final note-off is sent once. Repeat
that validation with tilt and Bend Pad enabled separately to confirm their
offsets add without changing pressure weighting. This output validation
requires the candidate firmware; before flashing, follow the fresh-backup and
explicit-approval rules above.


## Authorized flash — 2026-10-01

The user authorized flashing the final 1.2.5 image. The full suite passed
104 tests, one skipped, immediately before flashing. The firmware SysEx
validated and decoded byte-identically to the approved image. KMI SendSysEx
v0.15.0 at commit `8a587c1` was rebuilt at `/tmp/kboard-sendsysex/build/SendSysEx`
with ALSA enabled and JACK disabled. It accepted all 296 chunks on ALSA
`32:0`, returned to application mode, and confirmed firmware 1.2.5.

Slot 0 was captured before flashing using `aseqdump --raw` while SendSysEx
sent the settings request. Its 553-byte dump validated, yielding a 470-byte
preset image and a 561-byte restore payload. After flashing, SendSysEx
restored that exact payload; a fresh readback validated and matched every
image byte. Both images have SHA-256
`fc41297dabeaadf3c53fc69ac89388aa5e5b1d86f10d5c502ff78c7c3bec456d`.

Backup pairs: `backups/kboard-slot0-before-pressure-glide-2026-10-01.*`
and `backups/kboard-slot0-after-pressure-glide-2026-10-01.*`. Updater log:
`backups/kboard-pressure-glide-flash-2026-10-01.log`. The restored preset
has pressure glide range 0 (off); enable the desired range in the editor
and match the receiver bend range before judging glide behavior. Physical
Tilt and Pressure buttons may need re-enabling. Playing feel and emitted
glide MIDI still need physical validation.


## First hardware feedback and 1.2.6 fixes — 2026-10-01

After enabling glide on 1.2.5, the user reported an apparent three-position
blend (first pitch, midpoint, second pitch), and the primary key's LED
sometimes remaining lit after physical release.

Two firmware defects were identified and corrected in an **unflashed 1.2.6
candidate**:

- A deferred primary release bypassed stock LED-off at `0x62E3–0x62EA`.
  The patch now updates that physical key's LED in key-follow mode without
  returning its reserved voice. Repressing the primary restores its LED.
  A shared register-preserving helper is at `0x9B20`.
- Below-baseline pressure cleared A but wrote stale R4 as the normalized
  weight. It now explicitly clears R4; unloading pressure cannot resurrect
  an arbitrary contribution.

A new regression sweep calls the stock pressure sender `0x8247` for
companion pressure 40–127 with primary pressure fixed at 84 and both
baselines at 40. It produces more than 50 distinct, monotonic intermediate
pitches. This establishes arithmetic continuity for unsaturated input; it
does not establish smooth physical response. Other regressions cover
below-baseline readings with dirty R4 and physical release/repress LEDs.

The earlier calibration capture had 35 distinct pressure values, but
670 of 946 pressure messages (70.8%) were 127. Once both values saturate,
normalized weights both reach 127 and the blend remains at the midpoint;
changes in actual force beyond saturation are unavailable to this MIDI
pressure-based hook. This is a plausible contributor to the reported
three-position feel, not a confirmed diagnosis of the current gesture.
The user agreed to try reduced pressure sensitivity. An attempted passive
MIDI capture could not subscribe because BitwigAudioEngine held
`/dev/snd/midiC4D0` exclusively; no new playing data was recorded.
Await the user's listening result. If saturation remains a problem, trace
pressure before its sensitivity/curve mapping rather than masking lost
input variation with time smoothing.

Candidate image SHA-256:
`7a0d40c5a68e1a691934038c377238d2c346ed28918e3513c7af058c2f711a62`;
SysEx SHA-256:
`15465cc8edbc90c172be44e071c54c43c93de3f2c24be96e04f7de4fa11b2f34`.
The full software suite passes 107 tests, one skipped. The 297 SysEx chunks validate and decode byte-identically to the 64 KiB
image; bytes from `0xEE00` onward match the previous image. The editor
recognizes 1.2.6 using the same profile format. The retail board remains
on 1.2.5. A new flash requires explicit approval and a fresh preset backup.


## Revised 1.2.6 current-pressure blend — 2026-10-01

The user reported that reducing pressure sensitivity improved the blend but
it still felt awkward, suggested ignoring the pre-note-on pressure amount,
and confirmed that touching a second key already pulls the pitch toward it.
The candidate now uses `p2 / (p1 + p2)` without first-touch subtraction or
headroom normalization. The pending pre-note-on sample seeds the latest
current pressure only. The initial blend is emitted when the pair forms.
Release/repress LED fixes remain included. This supersedes the earlier
1.2.6 candidate and its hashes recorded above.

Regressions check immediate contributions, light equal-pressure midpoint,
unequal first-touch readings with equal current pressure, pressure below the
old contact value, maximum initial pressure, latest pending pressure, initial
pair bend, and a monotonic 0–127 pressure sweep with over 50 distinct pitches.

Revised image SHA-256:
`f8371bbbdd46494909a7c53c3763de0a1398b5194dd02e062d35adbad78e61ab`;
SysEx SHA-256:
`7f1c909e65ea8ea342dc0998a94e627a6a1fe5c89f4c274a5d41c2b5219b7251`.
The full suite passes 109 tests, one skipped. The 296 transport chunks
validate and decode exactly to the 64 KiB image.
Stock tail bytes from `0xEE00` are unchanged; the pressure handler ends at
`0xEDD6`, leaving 41 erased bytes before the stock log. The board remains
on 1.2.5. Revised physical feel still needs validation after a newly
authorized flash with a fresh preset backup.


## Authorized 1.2.6 flash — 2026-10-01

The user explicitly authorized the revised current-pressure image. Before
flashing, the suite passed 109 tests, one skipped; hashes matched the revised
candidate above and all 296 transport chunks decoded to the exact image.
Bitwig initially held the raw MIDI port exclusively. Once the port was free,
a fresh slot 0 backup was captured with `aseqdump --port=32:0 --raw` while
KMI SendSysEx sent the settings request. The dump validated and the exact
restore payload was generated offline.

KMI SendSysEx v0.15.0 (commit `8a587c1`) accepted all 296 chunks and
confirmed application version 1.2.6. The preset was restored with SendSysEx
and a new readback validated and matched all 470 image bytes. Both preset
images have SHA-256:
`3c093f2874336da3fd4c8c1ed43e16f7aeab650c54ba818a0551c04acac73063`.

Backup pair:
`backups/kboard-slot0-before-pressure-glide-1.2.6-2026-10-01.*` and
`backups/kboard-slot0-after-pressure-glide-1.2.6-2026-10-01.*`. Updater log:
`backups/kboard-pressure-glide-1.2.6-flash-2026-10-01.log`. The restored
preset includes glide range 12, tilt reference 12, tilt amount 5%, and
pressure sensitivity 60. Receiver range is therefore ±12 semitones.
Physical Tilt and Pressure buttons may need re-enabling. Current-pressure
glide feel and the physical LED fixes still need hands-on validation.


## Octave gesture capture on 1.2.6 — 2026-10-01

The user reports that the LED release fixes work, but glide still feels wrong.
They requested a capture of holding an octave pair and transferring pressure
in the motion they expect to produce a smooth bend. The ALSA `32:0` listener
was confirmed ready before playing and stopped when the user said done.
Capture timestamps are host monotonic arrival times, not sensor timestamps.

Local ignored capture:
`firmware_analysis/kboard-pressure-glide-1.2.6-octave-2026-10-01-1.txt.gz`.
Uncompressed SHA-256:
`190bea6fc6d5b523d4002af6c0daa6cc0550dd3d203f5116bef17f12f6f1f699`.
581 MIDI events: 360 bends, 219 channel-pressure messages, one audible note-on
and one audible note-off. Audible note 48 remains on channel 4 from
5.615205 to 17.960393 seconds. The companion pitch is hidden; its octave
relationship is from the user's gesture description. Initial companion
pressure is visible on channels 5, 6, and 7 at 7.181141, 11.185524, and
14.874040 seconds, consistent with the companion allocation being released
and reallocated during the repeated motion. Its ongoing pressure and
note-on/off are suppressed by the glide patch, so this capture cannot
measure all companion samples or exact physical release events.

The visible pressure spans 0–76, with no value 127. There are 69 distinct
bend values spanning 0–8191; this capture does not show a three-value
calculation or visible pressure saturation. Bend steps during ordinary
movement are mostly multiples of 64, corresponding to the current 7-bit
ratio and the octave interval at receiver ±12. Each 64 units is 9.375 cents.

The large discontinuities cluster at pressure dropout and recontact.
Using the restored ±12 receiver range, bend semitones = value × 12 / 8192:

| Time (s) | Visible event | Bend change (semitones above note 48) |
| --- | --- | --- |
| 7.832086 | Primary pressure 19 → 0 | 9.5625 → 11.9985 |
| 9.539067 | Primary pressure 0 → 30 | 11.9985 → 8.53125 |
| 9.935496 | Companion contribution ends; its pressure/off hidden | 2.8125 → 0 |
| 11.185558 | New companion contact, pressure 48 | 0 → 4.59375 |
| 15.216986 | Primary pressure 21 → 0 | 9.375 → 11.9985 |
| 16.871605 | Primary pressure 0 → 24 | 11.9985 → 9.09375 |
| 17.153400 | Companion contribution ends; its pressure/off hidden | 2.90625 → 0 |

At 7.832, pitch reaches the upper endpoint immediately on the pressure-zero
message and is resent at that endpoint. At 9.539, the returning primary
pressure is already 30 and pitch jumps back by about 3.47 semitones. These
are observed MIDI discontinuities, regardless of whether the performer
intended an actual release. The outgoing pressure hook sees no intervening
values from 19 to zero or zero to 30. Direct pressure weighting therefore
still receives discontinuous input at the light-touch/release boundary.

Next investigation: trace the sensor value before pressure gating, mapping,
and note release. Determine whether there is useful continuous sensor data
below the current MIDI cutoff, and separate physical key state from glide
weight. Do not assume a fixed subtraction floor or time smoothing alone
solves it. Recontact and companion release transitions also need treatment
if the user's intended gesture should remain continuous through them. No
firmware or preset changes were made for this capture.


## Stock pressure cutoff traced offline — 2026-10-01

The user authorized tracing the pressure before the stock cutoff and clarified
that the octave capture was natural playing, not an attempt to reach maximum
pressure. Natural force must remain the target for glide. No new firmware
image, device capture, or preset change was needed for this investigation.

### Signal and key-state path

- Scan entry: `0x2443`; physical key index in IRAM `0x36`.
- The two sensor working values are 16-bit pairs IRAM `0x34:0x35` and
  `0x2F:0x30`. After stock scaling/processing, their 7-bit values are
  IRAM `0x2E` and `0x31`. Their maximum is written to XRAM `0x007A + key`
  at `0x25D3–0x25F5`. This maximum drives pressure and the note gate.
- IRAM `0x32:0x33` is the output of `0x742D` used for tilt, **not** raw
  pressure. Do not hook it as the pressure source. Tilt is stored separately
  at XRAM `0x0044 + key` through `0x261D–0x2622`.
- Touch threshold is XRAM `0x034E`. `0x2485–0x24C3` computes the attack
  threshold `on_thresh + 5` (or 126 if the configured value is already
  at least 126) in IRAM `0x4E`, then subtracts five
  for the release threshold in IRAM `0x4D`. With the current preset these
  are 35 and 30. Attack requires exceeding 35 and the stock timing/state
  checks; the already-held key releases when its maximum falls below 30.
- Held-key release decision is `0x263A–0x2643`. It reaches `0x264F–0x265E`,
  clearing both `0x007A + key` and the previous scan pressure at
  `0x0287 + key`. Stock emits the forced zero and calls `0x62D7` to end
  the physical key's allocation (the custom glide hook defers the primary).
- `0x38FB` dispatches sensor output. MPE pressure (`sensor index 0`) uses
  the previous scan's cached pressure at `0x0287 + key`, applies the
  pressure profile through `0x3B2D` → `0x6183`, and sends via `0x8247`.
  The scan updates that cache at its end, including `0x29CB–0x29CE`.
- Pressure profile is at `0x0363` (curve), `0x0364–0x0365` (16-bit gain),
  `0x0366` (maximum), `0x0367` (minimum), and `0x0368` (offset).

### Reproduction against real stock bytes

Added offline tool `firmware_tools/trace_pressure_cutoff.py`. It reconstructs
the exact stock image from local SysEx, loads the freshly backed-up 470-byte
preset, initializes the stock linear curve pointer (`0x033B` → ROM `0x2E3D`),
and executes real `0x6183` mapping and the held-key scan from `0x2625`.
The steady-input fixture seeds the previous scan pressure to the same input.
Acquisition, global pressure/tilt aggregation, USB transport, and tilt output
are stubbed; key-state decisions, pressure mapping, and the stock release
body execute real firmware bytes. This is an offline trace, not a sensor or
interrupt simulation.

```sh
python firmware_tools/trace_pressure_cutoff.py \
  backups/kboard-slot0-before-pressure-glide-1.2.6-2026-10-01.image.bin
```

| Raw maximum | Mapper alone | Through held-key scan | Channel after scan |
| --- | --- | --- | --- |
| 127 | 76 | 76 | remains allocated |
| 50 | 30 | 30 | remains allocated |
| 35 | 21 | 21 | remains allocated |
| 33 | 19 | 19 | remains allocated |
| 32 | 19 | 19 | remains allocated |
| 31 | 18 | 18 | remains allocated |
| 30 | 18 | 18 | remains allocated |
| 29 | 17 | 0 | released |
| 28 | 16 | 0 | released |

The current profile has linear pressure, gain 60, minimum 0, maximum 127,
and offset 0. Thus MIDI pressure is `floor(raw * 60 / 100)` before the
key-state cutoff. Even raw 127 yields only 76 with these settings. The user
reached 76 while playing naturally, so the previous claim that this gesture
needed more force to reach MIDI 127 would be wrong for the current preset.
The earlier saturated calibration used different sensitivity settings.

The capture's 19 → 0 drop is consistent with crossing the raw release
boundary from approximately 32/33 to below 30; the exact raw values were
not in that MIDI capture. Returning MIDI pressure 30 maps to raw 50. This
is not a ratio arithmetic discontinuity: the stock pressure source and key
state are both discontinuous at the boundary.

### Hook and weighting direction for the next patch

`0x25F6` is a candidate scan hook, after the raw sensor maximum has been
computed and before key-state release can clear it. Its displaced instruction
is `MOV DPTR,#0x0897`. The two full sensor working values are still available
there. The hook can cache glide force independently of mapped MIDI pressure
and feed the existing pair without changing normal aftertouch.

Reading unmodified raw force alone still leaves a jump: physical release
occurs while raw force is about 30, not zero. Glide weight needs to approach
zero at the fixed release boundary, for example force above that boundary,
using the 16-bit sensor working values for finer resolution. This is a
fixed key-gate reference, **not** the discarded per-gesture first-touch
subtraction. Weighting should be independent of aftertouch sensitivity.
Attack/recontact hysteresis (on >35, off <30), scan-cache timing, both-zero
weights, and the hidden companion's allocation/release still need deliberate
handling. A pressure-only bypass does not solve those transitions by itself.
No firmware change or flash has been performed as part of this trace.
