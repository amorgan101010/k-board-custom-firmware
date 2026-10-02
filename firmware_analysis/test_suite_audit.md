# Test suite audit — 2026-10-02

The suite has strong arithmetic and historical regression coverage. Its largest
gap is lifecycle coverage across feature boundaries: a note can be allocated,
muted, silenced, paired, released, or reused by different layers. Tests need to
check that those layers agree on which voices the receiver actually heard.

## Scope and baseline

Reviewed all eight test modules, their firmware builders, the interpreter,
preset codec, editor profile migration, and relevant firmware notes. Ran:

```sh
PYTHONPATH=. python -m unittest \
  tests.test_protocol tests.test_firmware_extract \
  tests.test_relative_tilt_patch tests.test_velocity_slider_patch \
  tests.test_sensor_config_patch tests.test_cyclone_game_patch \
  tests.test_scale_quantizer_patch tests.test_pressure_glide_patch
```

Result: **116 tests run, 115 passed, one skipped**, in 40.491 seconds. The skip
is the extractor test's optional `/tmp/kmi-sendsysex` checkout, not a failing
firmware test. The complete in-memory build matches the documented flashed
1.2.9 image and SysEx hashes. No device I/O was performed. Firmware and tests
were not changed by this audit.

| Module | Tests | Image exercised / useful existing coverage |
| --- | ---: | --- |
| Protocol | 11 | Golden official packets, validation, dump parsing, preset round trips, receiver compensation |
| Firmware extraction | 3 | Seven-bit decoding, malformed input, optional official-image extraction |
| Relative tilt | 14 | Tilt component; exhaustive legacy input pairs and high-resolution scaler, pad mixing, mode changes |
| Velocity slider | 23 | Slider component versus stock; timing, jitter, register preservation, MIDI gating |
| Sensor menus | 6 | Menu component; all sensitivity steps, page switching, button restoration, full preset write |
| Cyclone | 15 | Complete current image; stock sensor-loop entry, game behavior, exit, MIDI and LED gating |
| Scale quantizer | 14 | Complete current image; scale tables, selector/menu dispatch, consumed releases, real allocator recovery |
| Pressure glide | 30 | Complete current image; force arithmetic, cutoff, pairing, real releases, LEDs, channel reuse, tilt compensation |

These are test-method counts, not code-coverage measurements. Many methods
exercise extensive input grids. Keep that coverage: executing emitted bytes
and real stock routines is especially valuable for this project.

## Highest-priority regression tests

The first three findings were reproduced with the complete current image in
`Machine`. They establish firmware-path behavior, not hardware validation or
an exhaustive description of how each receiving instrument responds.

### 1. Cyclone can leave an unheard voice eligible for glide

Evidence: [Cyclone suppression test](../tests/test_cyclone_game_patch.py#L241),
[scale MIDI dispatch](../firmware_tools/build_scale_quantizer_patch.py#L793),
and [glide note registration](../firmware_tools/build_pressure_glide_patch.py#L293).

Reproduction used an encoded MPE preset with 15 members, glide range 24,
physical Pressure on and Tilt/Velocity off, initialized stock channel tables,
and the real stock button scan and allocator:

1. Enter Cyclone with the three-button hold and release.
2. Press key 0 through `0x4C4F`, keeping it held.
3. Exit with Tilt and release the exit button through the stock sensor loop.
4. Press key 12 through `0x4C4F`.

The first key's positive-velocity note-on is suppressed, but its glide role
becomes `FLAG_NORMAL`. On exit it remains eligible. The second key then forms
a primary/secondary pair and its note-on is consumed. Neither note-on reaches
the USB writer. The first key also emits two-byte channel pressure during the
game, as described below.

Add a regression requiring the first ordinary key after game exit to produce
an audible note when any retained candidate never reached the receiver.
Test key release during and after the game, multiple held keys, and repeated
entry/exit. Define whether game keys should allocate at all; whatever policy
is chosen, glide eligibility must follow audible voice ownership.

### 2. Selector All Notes Off leaves glide candidates behind

Evidence: [selector entry test](../tests/test_scale_quantizer_patch.py#L84)
seeds tilt markers without allocating actual held voices;
[All Notes Off helper](../firmware_tools/build_scale_quantizer_patch.py#L136)
clears tilt state but leaves glide roles and stock allocations intact.

Reproduction used the same initialized MPE preset:

1. Play key 0 normally: member note-on `(0x91, 24, 127)` reaches the writer.
2. Keep it held while entering the selector through the actual button scan.
3. Select with key 5, release that selection key, and exit normally.
4. Press key 12 before releasing key 0.

Selector entry emits CC 123 while the old glide role stays `FLAG_NORMAL` and
the stock active count stays 1. After exit, the new key pairs with the old
candidate; its note-on is consumed. A receiver honoring CC 123 has no sounding
voice for that pair. This is distinct from the existing consumed-selection-key
release regressions, which start with no held performance voice.

Add cases for an ordinary held voice, an active pair, and a deferred primary
release. Verify that selector entry makes silenced voices ineligible for new
pairing, that later physical releases free allocations exactly once, and that
new notes resume. Do not simply require every stock allocation to become free
on entry: retained physical keys still need a consistent release policy.

### 3. MIDI suppression misses the two-byte pressure path

Evidence: [game MIDI suppression test](../tests/test_cyclone_game_patch.py#L241)
calls `0x7C67` only; the separate
[pressure sender](../firmware_tools/build_pressure_glide_patch.py#L761)
continues into stock `0x7DF7` without passing the game/menu gates.

Calling `0x7DF4` with `R7=0xD1, R5=76` produced USB bytes `[209, 76]` in
ordinary mode, active Cyclone, and active sensitivity-menu state 3. Thus the
actual pressure route does not have the suppression behavior demonstrated for
the three-byte route. The real game-key reproduction above also emitted
`[209, 0]` during the game.

Add wire-level checks for primary and companion channel pressure through
`0x8247`/`0x7DF4`, in every relevant UI state. Preserve companion suppression
and verify ordinary pressure resumes afterward. For the scale selector,
document its intended expression policy first: its sender explicitly allows
expression messages, so silence should not be assumed for that mode.

### 4. Repair the legacy-preset fixture before relying on its coverage

Evidence: [legacy round-trip test](../tests/test_protocol.py#L58) computes
three offsets as `1 + list(MODEL).index(name)`. That ignores two-byte Gain
fields and writes different fields:

| Field | Offset used by test | Correct image offset | Correct XRAM address |
| --- | ---: | ---: | ---: |
| CV1 Max | 416 | 424 | `0x04EF` |
| CV1 Offset | 418 | 426 | `0x04F1` |
| CV2 Max | 424 | 433 | `0x04F8` |

The attempted CV2 Max write accidentally changes CV1 Max to `0x7F`; the coarse
width was already 48 in the original fixture. The expected legacy 25% still
passes, hiding the fixture error. This does not prove a codec defect; it weakens
the claimed legacy compatibility check.

Build fixture offsets with the width-aware cursor already used later in the
same test. Assert the intended raw fields before decoding. Use values whose
legacy and current interpretations differ, and cover `0x78` flags, the `0x7E`
escape, current percent mode, and absent glide sidecar. JSON v1 migration is
implemented in the editor's `open()` method and currently has no dedicated
test; extract that conversion into a pure function and test it separately.

## Next interaction coverage to add

| Priority | Scenario | Assertions that would add useful confidence |
| --- | --- | --- |
| High | Glide + tilt + pad, with pressure-first, tilt-first, and pad-first updates | Every final member bend includes each contribution once; cache resends agree, low bytes survive, saturation is correct, hidden companion MIDI stays hidden |
| High | Toggle physical Tilt/Pressure during a held pair; change glide settings while held | Defined transition behavior, no stale cache, no orphan partner, valid cleanup in both release orders |
| High | Quantizer + glide with duplicate snapped pitches, negative/positive transpose, and range boundaries | Pairing uses emitted pitches; exact window includes/excludes correctly; duplicate pitches and tie choices are deterministic; note-offs match heard note-ons |
| High | Pairing with 1, 2, 8, and 15 MPE members, exhaustion and reuse | No mapping collisions or unpaired partner references; predictable allocation failure/stealing; releases restore the count and next single-note pitch |
| Medium | Staggered chord presses near one second; partial release; tick wrap | Exactly one UI claims the gesture; page/game/selector priority and button restoration work through the full dispatcher |
| Medium | Edit sensitivity, switch page, exit, then glide with the saved preset | Only intended fields change; tilt/glide sidecars and bend flags survive; actual pressure mapper changes aftertouch without changing force weighting |
| Medium | Sustain + pair release/repress + UI entry | Specify whether MIDI note-off deferral belongs to the receiver or device; assert coherent note lifetimes under that policy |
| Medium | Sensor force around configurable release/recontact thresholds and nonunity Globals Gain | Weight origin agrees with the real stock gate; run both sensor maxima, fractional boundaries, recontact, and actual scaling rather than setting only the final coarse reading |

The existing glide/pad resend test starts with zero force weights, so it proves
pad retention without demonstrating all three nonzero contributions together.
The sensitivity-independent force sweep changes the pressure Gain field but
enters the raw helper directly; keep it, and supplement it with the real mapper
and scan path to check the full interaction.

## Harness changes that make those tests reliable

**Create a shared complete-image board fixture.** Load a real encoded preset,
initialize the stock key/channel pools and curve pointers, set the startup
stack pointer, and provide operations for buttons, key scans, note-on/off, tilt,
pad, and captured MIDI. Cache the immutable firmware image; use a fresh machine
per scenario. Keep component tests for isolated arithmetic and historical
behavior, but run current UI lifecycle and gating checks through
`custom.build_image()`.

The detailed sensitivity-menu tests currently run `config.build_image()`.
Several of their behaviors are covered partially by the final-image scale
tests, but complete page switching, restoration, and preset saving should also
run against the full hook chain. The slider's longer hold timing is a component
behavior; do not blindly transplant its expectations to the current one-second
sensitivity menu.

**Observe at the USB writer, below both MIDI senders.** Stub `0x313D` as drained
and capture bytes. Parse complete two- and three-byte MIDI messages, handling
running status if encountered. A stub at `0x7C67`, `0x7C6A`, or the game gate is
useful for a unit test but cannot establish behavior for every output route.
Leave the real allocator, dispatcher, and release routines running in lifecycle
tests. Stub hardware acquisition and flash operations at their boundaries.

**Require positive note-on velocity for lifecycle fixtures.** The
[tilt-before/after-selector test](../tests/test_scale_quantizer_patch.py#L414)
expects zero-velocity `0x9n` messages. Glide treats these as note-off, so that
test cannot establish active glide voice registration. Keep its tilt assertions,
but make new interaction fixtures assert velocity 1–127 and track the receiver's
audible notes explicitly.

**Check state invariants after each action.** Distinguish stock allocated
channels from audible voices: a glide pair legitimately allocates two channels
for one audible note. Check partner symmetry, key/channel ownership, consumed
release bits, matching audible note lifetimes, valid bend data, and eventual
cleanup. Across UI transitions, assert state belongs to one UI and only its LEDs
are drawn. Add register/stack checks around nested sends and releases.

**Add deterministic generated sequences after focused regressions.** Reuse the
suite's fixed-seed approach to generate physically valid press/release,
force/tilt/pad, menu, and game sequences on the complete image. Report the seed
and shortest failing action sequence. Randomize scan cadence and wrap the tick
counter. This supplements the deliberate transition cases; it cannot replace
them or emulate real interrupt timing.

**Strengthen memory and transport checks modestly.** Monitor stack high-water
marks and XRAM writes for selected long scenarios, allowing documented scratch
and stock queue state. `Machine` defaults to 64 KiB XRAM despite the board's
4 KiB internal XRAM; add an explicit target address/alias policy or diagnostic
for suspicious accesses rather than treating all addresses as safe. Pin emitted
patch region ownership and preserve the entire stock tail from `0xEE00`, not
only preset pages. Retain the existing full-image SysEx round trip; add message
size and flash-page-boundary assertions. Replace the extractor's optional
`/tmp` path with the configured local stock path, while keeping firmware-free
codec tests runnable without distributing proprietary input.

## Suggested implementation order

1. Fix the legacy fixture and introduce the complete-image/wire-capture fixture.
2. Add the three reproduced lifecycle/gating regressions and define their
   intended behavior before changing firmware.
3. Add held-pair UI transitions, nonzero three-source bends, and channel
   exhaustion/reuse cases.
4. Extend with the smaller interaction matrix and deterministic action sequences.

Keep a small hardware follow-up for resulting changes: held notes across UI
entry/exit, pressure silence in Cyclone/settings, and glide/pad/tilt gestures.
Interpreter results cannot establish physical playing feel, sensor timing, USB
interrupt behavior, or receiving-instrument sustain and All Notes Off behavior.
