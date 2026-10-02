# Scale quantizer design

## Agreed interaction

- Hold Tilt and Pressure together for one second to enter scale selection mode.
- Pressing one of the 15 white keys directly selects its corresponding scale from the scale bank.
- Octave down/up arrows transpose down/up by one semitone per press. The key LEDs move with the
  transpose; the root note blinks.
- Tilt or Pressure exits the mode. The selected scale and transpose remain active for this boot
  session and reset to defaults after reboot.
- Quantize played notes to the selected scale and transposed root.

## Recommended first scale set

Use a curated Digitone-inspired set rather than putting all 38 Digitone modes behind repeated
button presses. Proposed order:

1. Chromatic
2. Ionian (Major)
3. Dorian
4. Phrygian
5. Lydian
6. Mixolydian
7. Aeolian (Natural Minor)
8. Locrian
9. Major Pentatonic
10. Minor Pentatonic
11. Blues
12. Harmonic Minor
13. Whole Tone
14. Hirajoshi
15. Phrygian Dominant

The fifteen choices map directly to the fifteen white keys, in the order shown. This covers a
neutral bypass, the seven modes, familiar pentatonic/blues sounds, and a few distinctive colors. C
is the untransposed root. Each arrow press shifts the transposition by one semitone, bounded to
-12..+12; the scale mask and blinking root indicator move together. A boot starts in Chromatic at
zero transpose, and the chosen scale/transpose remain in XRAM for the session only. The official Digitone
manual lists these modes among its keyboard scales and describes its keyboard setup as showing
playable notes and setting scale/root. See [Elektron Digitone User Manual, Keyboard Setup and
Appendix D](https://elektron.se/wp-content/uploads/2024/09/Digitone_User_Manual_ENG_OS1.41_231108.pdf).

The 25 chromatic key LEDs show notes in the selected, transposed scale; the root pitch class blinks.
White-key presses choose scales while in the menu, and do not send notes. Tilt or Pressure exits.
On exit, ordinary note/LED behavior resumes with the selected scale and transpose active until
reboot.

The chord is distinct from the existing individual Tilt, Pressure, and Velocity sensitivity-menu
gestures and from Cyclone's Tilt + Pressure + Velocity chord. The scale dispatcher tracks the
Tilt/Pressure pair, gives Cyclone's three-button chord priority, and waits for the entry buttons to
release. The mode owns the key LED display and suppresses note-on/off output while open.

## Quantizer behavior

- In firmware 1.2.9, map the seven white pitch classes C–D–E–F–G–A–B to
  successive degrees of any seven-note scale. For example, C Lydian produces
  C–D–E–F♯–G–A–B on those white keys. Repeat the mapping each octave, then
  shift it by the selected transpose. This includes the seven modes, Harmonic
  Minor, and Phrygian Dominant.
- Black keys and scales with other note counts retain nearest-scale-pitch
  quantization, choosing the lower pitch on an exact tie. Chromatic remains
  a transpose-only mapping. Clamp to MIDI note range 0–127. The sender hook
  applies the same mapping to note-on and note-off; the
  selector sends All Notes Off before suppressing note traffic, so changing the mapping cannot leave
  a note held under its previous pitch.
- The selector LEDs continue to show actual output pitch classes in the scale,
  rather than the physical white-key layout used to play its degrees.

### White-key mapping candidate — 2026-10-02

The user reported duplicate adjacent white-key pitches in the fifth scale,
Lydian. Nearest-pitch snapping chose E for physical F because E and F♯ were
equidistant. F♯ was therefore available only on the black key. The record
builder now explicitly assigns seven-note scale degrees to white pitch
classes, keeping nearest-pitch snapping on black keys and for other scale
sizes. Of the current bank, only Lydian's F record changes; the other eight
seven-note scales already satisfied this layout.

The 1.2.9 image changes only the version byte at `0x5CF8` and the Lydian
F record at `0xCF35` compared with flashed 1.2.8. No flash or XRAM allocation
is added. This image was flashed with explicit approval on 2026-10-02;
see the flash record below.
Tests reproduce the old E/F duplication, exercise all nine seven-note scales
on both octaves at every transpose from −12 through +12 with note-on/off on
two channels, execute stock Lydian F press/release, and verify pressure-glide
pairing uses the corrected two-semitone E–F♯ interval. The packed-record
regression checks every pitch class for all scales and transpose classes,
including the existing LED display.

The full suite ran 116 tests with one skipped. SysEx validation passes all
299 chunks, decodes to the exact 64 KiB image, and preserves stock bytes from
`0xEE00` onward. Candidate files:
`kboard-custom-firmware-1.2.9-white-key-scales-candidate.bin` and `.syx`.
Image SHA-256:
`f65548504e43f3f70695701a1aab20a2f79c136911d91588b7bb1f4e50699ef6`;
SysEx SHA-256:
`7df3d239d8385804bc36904d5c823022c5b057894e27d816d65a83bf268ec77c`.

### Authorized 1.2.9 flash — 2026-10-02

The user explicitly authorized the 1.2.9 image. The full suite ran 116 tests,
one skipped, immediately before flashing; the approved hashes and exact
SysEx image round trip were revalidated. KMI SendSysEx v0.15.0, commit
`8a587c1`, sent all 299 chunks and confirmed application version 1.2.9.
A separate identity request confirmed 1.2.9 in application mode.

Slot 0 was captured fresh using `aseqdump --port=32:0 --raw` while SendSysEx
sent the settings request. Its validated 553-byte dump yielded a 470-byte
image and a 561-byte restore payload. SendSysEx restored that payload;
a fresh slot 0 readback validated and matched every byte. Backup pair:

- `backups/kboard-slot0-before-white-key-scales-1.2.9-2026-10-02.*`
- `backups/kboard-slot0-after-white-key-scales-1.2.9-2026-10-02.*`

Both preset images have SHA-256
`17e23e9b877e0f9ce6544c2540ada63725e397dd6e36072e48f884611e7ad723`.
Preserved settings include MPE with 15 member channels, glide range 2,
tilt amount 5%, tilt reference 2, pressure sensitivity 88, and velocity
sensitivity 60. Flash, restore, and identity logs use prefix
`backups/kboard-white-key-scales-1.2.9-` and date `2026-10-02`.
Physical Tilt and Pressure buttons need re-enabling after the flash.
On 2026-10-02, the user confirmed the corrected scale mapping works on the
keyboard.

## Firmware hooks and state

- The keyboard has 25 key LEDs in chromatic order at LED indices 0–24; the Cyclone patch already
  demonstrates taking ownership of these LEDs.
- The key-on hook at `0x4C4F` follows the existing Velocity/menu hooks and maps white-key presses to
  the 15 scale choices while the selector owns input.
- The common sender at `0x7C67` receives status in R7 and data bytes in R5/R3. Quantization is
  applied there to note-on and note-off, preserving MPE channel assignment.
- On scale-menu entry, send All Notes Off on active channels before allowing scale/transposition
  changes. This prevents notes started under the previous mapping from hanging or being released
  at a different pitch after the player exits.
- The selection is intentionally session-only and stays in the menu/slider runtime XRAM area. It
  does not add preset fields or touch the relative-bend mode bytes at `0x04EF`/`0x04F8`.
- The one-second Tilt + Pressure chord runs through the existing menu dispatcher and cooperates
  with the Velocity slider and Cyclone's three-button chord.

## Implementation status

The full image builder now layers the scale selector after Cyclone and produces a 1.2.3 image and
SysEx file. Selector state uses `0x0F40` for the shared menu state and private bytes `0x0F65–0x0F8C`;
this avoids aliasing the menu's mode/session state and Cyclone's `0x0F52–0x0F64` allocation. The
compact-table candidate stores one packed LED/quantizer record for each scale and pitch class at
`0xCF00–0xCFB3`, plus the transpose lookup at `0xD200–0xD218`. Its scale code ends at `0xD25F`, so
`0xD260–0xEDFF` is one 7,072-byte erased run. The records save 7,200 flash bytes versus the prior
pattern, quantizer, and pointer tables. The first image was flashed on
2026-09-30; the updater accepted all 352 chunks and confirmed firmware 1.2.3, and the identity
request confirmed application mode. Slot 0 was restored and read back byte-identically. On hardware,
completing the entry chord froze the device until power cycle. Emulator tracing found the cause: the
stock MIDI sender clobbers R4, which the 16-channel All Notes Off loop had used as its counter. The
loop now saves and restores that counter around each send. The corrected image was flashed on
2026-09-30; the updater accepted all 352 chunks, confirmed 1.2.3, and the identity request confirmed
application mode. Slot 0 was restored and verified byte-identically. That flashed image passes the
chord-entry/selector emulator regression test and the full suite (67 tests, one skipped). The user
reported two remaining display problems: selector exit left key LEDs lit, and a fresh boot displayed
the all-white Ionian pattern instead of Chromatic with C blinking. Follow-up tracing found XRAM
aliases with the menu mode/session bytes, and a forced-redraw branch error. The new build remaps the
state, clears all key LEDs on exit, and initializes the blink phase on entry. The focused test and
full suite pass (67 tests, one skipped). The user authorized the flash on 2026-09-30. KMI SendSysEx
v0.15.0 accepted all 351 chunks and confirmed application version 1.2.3. Slot 0 was restored and
read back byte-identically; both backup images hash to
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. Hands-on confirmation of the
LED behavior remains. Image SHA-256 is
`0c882a7dcd47ca67e3383842dd191f5ef6990c833ea8ce79dfaf582172f03391`; SysEx SHA-256 is
`7f3ccae668625f1932ea4f35883186cc75e936d7a6373c1a9a7dd8ba2b1ad966`.

The user then reported that Bitwig received no keyboard input. With Bitwig closed, a raw capture
while keys were pressed contained only repeated `0xFF` MIDI System Reset bytes and no notes. The
stock key routine at `0x4C4F` continues after its initial `MOV DPTR,#0x0889`; the custom normal-mode
path had returned instead, skipping the stock note processing. The path now replays that instruction
and resumes at `0x4C52`; selector presses still return without sounding. An emulator check confirms a
normal key-on reaches the stock sender as status `0x90`, and the full suite passes (68 tests, one
skipped). The user explicitly authorized flashing this fix on 2026-09-30. KMI SendSysEx v0.15.0
accepted all 351 chunks and confirmed application version 1.2.3. Slot 0 was restored from
`../backups/kboard-slot0-before-midi-path-fix-2026-09-30.restore.syx` and read back byte-identically
in `../backups/kboard-slot0-after-midi-path-fix-2026-09-30.*`; both preset image hashes are
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`. Image SHA-256 is
`f01ae1c7dc45befc07d82e7d1b5d18fce722a801a54fd85ff25650f7b11d1650`; SysEx SHA-256 is
`8b59d69223ff2ceff422c6b69c134fa5faf98da734a46eda563fc06ad91e82a7`. The user later confirmed
that normal MIDI and quantization work on hardware. The emulator suite passed 68 tests with one
skipped. Firmware updates reset the physical Tilt and Pressure buttons to off.

## Sensor dispatch and bend fix flashed (2026-09-30)

The user confirmed normal MIDI and quantization work, then reported that the sensitivity settings
view no longer opens and that tilt pitch bend stops after entering the selector, including when
Chromatic is selected, until a restart. The image preserved R7 around the scale chord helper, but
that did not restore settings access: the menu body reloads its reading from XRAM `0x0977`. Scale
entry's direct All Notes Off sequence bypasses the relative
tilt note-off hook, so the image clears all 16 per-note active flags afterward. It also keeps the
pre-selector Tilt/Pressure button snapshot and restores it on selector exit, so using either button
to exit cannot toggle that control off. The full suite passed (68 tests, one skipped). After the
user's explicit go-ahead, the image was flashed with KMI SendSysEx v0.15.0; the updater process
finished, the device returned to application mode, and an identity request confirmed 1.2.3. Image
SHA-256: `39e0636729adfd6ba3ecf4432970db51f3748d394d38e616a2a68f0c227b74b1`; SysEx SHA-256:
`7d3583fd64ee5f5ebe2ca5e6468cb0371268fddbd92fe51c93d4c2e0d6bd7eaa`. Slot 0 was restored and
verified byte-identically; both before/after image hashes are
`2ecae9a803c30b0802404d48552354026a9b570d2b2a2bd03b743f0998f9ca0b`. The user has not yet
validated settings-menu access or tilt bend after this flash.

## Sensitivity menu dispatch fix flashed (2026-09-30)

The user confirmed that all three sensitivity settings still fail to open. In the full firmware,
the menu and scale selector share XRAM state byte `0x0F40`. The scale dispatcher returned whenever
that byte was nonzero. A first Tilt, Pressure, or Velocity press starts menu state 1, so the
dispatcher then blocked every later sample from reaching the menu's one-second hold timer. The
real-byte 8051 emulator reproduced state 1 staying stuck for all three buttons; the menu-only and
Cyclone images reached state 2. The dispatcher now returns only for selector states 6–8. An
integration regression covers each single-button hold and release and a Velocity hold after
entering and leaving a nonchromatic scale. The full suite passes 70 tests with one skipped. After
the user's explicit go-ahead, KMI SendSysEx v0.15.0 accepted all 352 chunks and confirmed
application version 1.2.3. Image SHA-256:
`416f9a4034a60ce35ec2bf9c6d16827925e996776b523bcdf61da8f6a147f3fe`; SysEx SHA-256:
`994113404c2ad4dec7fe84a6f15fb6055ca77cdd18dd332475e6c3b7279f1c2b`. Slot 0 was restored
and read back byte-identically. The before/after preset image hashes are
`2ecae9a803c30b0802404d48552354026a9b570d2b2a2bd03b743f0998f9ca0b`. Hardware validation of
sensitivity-menu access and tilt pitch bend after this flash is pending.

## Menu value and MPE voice-count fix flashed (2026-09-30)

On hardware, the latest flashed image opens the Tilt, Pressure, and Velocity sensitivity menus,
but a white-key press does not change their values. The scale key-on wrapper at `0xC800` resumed
the stock note routine for every state except scale editing. It skipped the sensitivity menu key
handler at `0x8BA0` for shared states 2, 3, and 5. The new source routes those states to the menu
handler; a real-byte emulator regression checks that each menu's first white key changes its
corresponding value.

The user also reported that tilt becomes full-range after selecting a scale and that two opposite
tilts interfere. An authorized hardware MIDI capture showed two notes initially on MPE member
channels 2 and 3 (`0x91`, `0x92`) with member-channel bends limited to 6912–9600 and 7680–8192.
After scale selection sent 16 All Notes Off messages, the next two notes were on shared MIDI
channel 1 (`0x90`), and 2,507 subsequent pitch bends on that channel spanned 0–16383. A read-only
slot 0 dump afterward was byte-identical to the pre-flash backup: MPE mode byte `0x0351` remained 1,
relative-bend flags `0x04EF` remained `0x7F`, and bend reduction `0x04F1` remained 52.

The failure is MPE allocator bookkeeping. The selector consumes a scale-choice key press at
`0x4C4F`, so stock note-on does not allocate a member channel. Its release still ran stock key-off
at `0x62D7`, which decrements the active voice counter at XRAM `0x00AD` for the key's nonzero stale
channel marker. The emulator reproduced a count of 0 wrapping to 255 and the next stock allocator
call at `0x6FE5` returning channel 0. This explains why the relative member-channel bend helper
no longer controls those notes, even after selecting Chromatic again.

The flashed image marks all selector key presses in XRAM `0x0F74–0x0F8C` and hooks `0x62D7` to consume
the matching release and clear its mark, including a release after leaving the menu. Ordinary
releases resume at stock `0x62DA`. It also preserves the quantizer's caller registers and fixes the
ninth-bit pointer calculation for later scale rows, whose LED and quantizer tables are more than
256 bytes long. Regression tests cover actual front-button selection, a selector key release,
stock MPE allocation, two simultaneous notes bending in opposite directions, all 180 scale and
transpose table rows, and the three sensitivity values. The full suite passes 74 tests with one
skipped. Candidate image SHA-256:
`f339062155e18dff0dcbbbaf560da751677415847376b5d95dc82deca60b14cd`; candidate SysEx SHA-256:
`1962b7141d143698070618d6a5e319f0d7d7d3670d9cf6126d2adbc92d09a372`. After the user's
explicit go-ahead, KMI SendSysEx v0.15.0 accepted all 353 chunks and confirmed application version
1.2.3. Slot 0 was restored from `../backups/kboard-slot0-before-selector-keyoff-fix-2026-09-30.restore.syx`
and read back byte-identically at `../backups/kboard-slot0-after-selector-keyoff-fix-2026-09-30.*`.
Both images hash to `2ecae9a803c30b0802404d48552354026a9b570d2b2a2bd03b743f0998f9ca0b`.
The user later confirmed menu value editing and MPE tilt after scale selection. They also found
the separate sensitivity-menu MPE regression described below.

## Sensitivity-menu release fix candidate (2026-09-30)

The user confirmed the flashed selector release fix restored MPE after scale selection, and that
Tilt, Pressure, and Velocity sensitivity values can be changed. They then reported the same MPE
failure after visiting any sensitivity view. The installed image routes menu key presses to the
value editor at `0x8BA0`, which returns without allocating an MPE channel. It marks only scale
selector keys as consumed. In the installed image, the emulator reproduced a Tilt value key press
followed by stock release at `0x62D7`: active voice count XRAM `0x00AD` wrapped from 0 to 255, and
stock allocator `0x6FE5` returned channel 0. Pressure and Velocity use the same key handler.

The new candidate calls a common key marker from menu states 2, 3, and 5, covering white value
keys and black keys; the existing key-off hook then consumes each matching release, including one
after leaving the view. A no-key visit and exit leaves the voice count at zero in the emulator.
The full suite passes 76 tests with one skipped. Candidate image SHA-256:
`6fe6e76a37de15aa421b4824b8093155a58bce4ce9ded80cfd4fd1596bb13d21`; SysEx SHA-256:
`79d441c0b2e8bf79c876e78d4b7024bdad0e0e14ad3abe22988890b723d481cb`. After the user's
explicit go-ahead, KMI SendSysEx v0.15.0 accepted all 354 chunks and confirmed application version
1.2.3. Slot 0 was restored from `../backups/kboard-slot0-before-menu-keyoff-fix-2026-09-30.restore.syx`
and read back byte-identically at `../backups/kboard-slot0-after-menu-keyoff-fix-2026-09-30.*`.
Both images hash to `41f363050398529cc3a91915da6db8bffbbe7a82aa0b724ff6c626c19e2d5b95`.
The user subsequently reported that the scale selector, sensitivity menus, and MPE behavior work
together on the device. The RAM-compression image now packs these 25 marks into four bytes at
`0x0F74–0x0F77`. Its build hashes and flash verification are recorded in `resource_audit.md`.

## Compact table candidate (2026-09-30)

The scale LED and quantizer tables now share 180 packed records (one per scale and pitch class),
plus the existing 25-byte transpose lookup. This removes 7,200 table bytes and reduces the scale
layer from 7,938 allocated bytes to 1,565. The real-byte emulator checks every scale, transpose,
LED, and quantizer result. The full suite passes 77 tests with one skipped. The user authorized
flashing this image on 2026-09-30; the updater accepted all 242 chunks and confirmed application
1.2.3. Slot 0 was restored and read back byte-identically (SHA-256
`01615d521800aa7865565ecbcd8fa7f6d3d26bb7f2b002745e0afbce04680a6b`). Hands-on behavior checks
for the compact LED and quantizer lookups are pending. Image SHA-256:
`803c686a470adab0d68325fd44a0b40c9505a33bbcb3da1c739bb61451237330`; SysEx SHA-256:
`2d6414575fea3a4d270c26fb4132c26fb6172fb780909069079d2827184e80f4`.

## RAM compression flash (2026-09-30)

The user approved flashing the candidate that merges per-channel relative-tilt state with its
baseline and packs the 25 consumed-key marks into a four-byte bitmap. KMI SendSysEx v0.15.0
accepted all 242 chunks and confirmed application version 1.2.3. The candidate saves 37 named
XRAM bytes and is recorded in `resource_audit.md`. Slot 0 was backed up before flashing, restored,
and read back byte-identically afterward (preset image SHA-256
`01615d521800aa7865565ecbcd8fa7f6d3d26bb7f2b002745e0afbce04680a6b`). The full suite passed 80
tests with one skipped before flashing. Physical Tilt and Pressure buttons may need re-enabling.
