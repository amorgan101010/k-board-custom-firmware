# Cyclone game implementation notes

## Agreed behavior

- Hold Tilt, Pressure, and Velocity together for one second to enter.
- The cursor moves across the 15 natural-note key LEDs and always bounces at
  each end. Its starting direction is randomized at each level.
- A pair of black-note LEDs marks a natural-note target only when exactly one
  white-key LED lies between them. D#/F# and A#/C# are excluded.
- Touching the pitch-bend pad stops the cursor. A stop on the target advances
  the level and speeds up the cursor. A stop elsewhere ends the game
  immediately. Passing the target without stopping is not a miss.
- Pressing Tilt, Pressure, or Velocity exits the game.
- Pressing Sustain after game over starts a new game. The Toggle button has no
  game-over action.
- Speed starts at 240 ms per key and drops by 12 ms per hit to a 48 ms floor.
- Correct stops continue to score after the 15-LED score bar is full; the bar only shows up to 15. The game does not end at score 11.
- Six targets are used across the 25-key range: D, G, and A in each available
  octave, each shown by the black-note pair that brackets exactly one white key.
- Targets do not repeat in consecutive rounds. Selection uses an eight-bit
  pseudorandom state mixed with the millisecond tick; a repeated target is
  advanced to the next valid target.
- After a miss, a dim white-key bar shows the score, the failed cursor blinks
  brightly over the bar, and the target pair remains lit. During the hidden
  phase, the cursor LED is fully off even when it overlaps the score bar, so
  that bar LED temporarily disappears too. An exit button returns to normal
  operation.

## Firmware map

The sensor dispatcher at `0x56E8` scans channels 0 through 7, skipping 5.
Channels 0, 1, and 4 are Tilt, Pressure, and Velocity. Channel 2 runs the
button handler at `0x5435` and is the Sustain input; channel 3 runs `0x4AFD`
and is Toggle. The pitch-bend pad touch state is XRAM `0x0999`; the game uses
that active state as its stop press and waits for release before accepting
another stop. The current menu dispatcher is at `0x8B60`; it is reached by
the sensor hook at `0x575C`. Key LEDs use `0x7AFB` with the LED index in R7
and brightness in R5. Natural-note key LED indices are obtained from the
existing `WHITE_TABLE` at `0x8B00`.

The unified builder layers the slider, sensitivity menus, custom bend patches,
and game. `build_cyclone_game_patch.py` preserves the menu dispatcher and sender
in relocated copies, then adds game-aware wrappers at the menu dispatch and
MIDI sender entries. It also gates stock key LED writes at `0x7B26` while the
game owns the LEDs; game drawing sets an XRAM flag to allow its own calls to
`0x7AFB`. Game state uses XRAM `0x0F52–0x0F64`, after the existing tilt,
slider, and menu state at `0x0F00–0x0F51`.

The entry chord has priority over a pending sensitivity hold. If a sensitivity
menu or Velocity slider is already active, game entry is deferred so an
in-progress setting is not discarded. The game restores the Tilt, Pressure,
and Velocity indicator bits to the values captured when the full chord starts;
the settings buttons are then swallowed until their release after exit.

The initial game image was flashed to the retail K-Board on September 30, 2026
and the user reports that gameplay worked well. The revised image with the
marker, random-target, game-over display, and control changes described above
was also flashed on September 30, 2026. The user reports those changes work
well. A reported run ending at 11 prompted a source review: successful stops
have no cap at 11, although the LED bar saturates at 15. The cursor overlap
issue was confirmed in the game-over blink and fixed in the later flashed build
described below. After that flash, the user confirmed a score of 13 and that
the cursor blink works correctly.

The currently generated 1.2.3 image has large erased regions beginning at
`0xA94A`; game routines and tables can be allocated there with checked
non-overlapping spans. The flashed revised image has SHA-256
`98f34894f26dce60c4f8289b9a979fb76b74ca02b69f02f8e12cdec801c69e82` and its
SysEx file has SHA-256
`da3feabbec5b43eff8e802e4e8ddfc4d75e138ba94dd6681041a163013bb973f`. The updater
accepted 223 chunks and confirmed application version 1.2.3. The pre-flash slot
0 image was restored and verified byte-identically; see the backup pair in
`backups/kboard-slot0-{before,after}-cyclone-refinements-2026-09-30.image.bin`.

The follow-up cursor-blink build sets the hidden-phase LED brightness to zero
unconditionally, fixing the steady cursor when it overlaps a lit score LED.
It was flashed on September 30, 2026. The updater accepted all 223 chunks and
confirmed application version 1.2.3. Its image SHA-256 is
`95e18f7d851e90680e7f73d29e38c798598980a373327f95f3535add0acf54b2` and SysEx
SHA-256 `ebe0f6a231641f941b0ef74230a3fef9c4c1945c7baa48b415eac39c36ca9e1f`.
Slot 0 was backed up before flashing, restored, and verified byte-identically
afterward. Both preset images have SHA-256
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`; see
`backups/kboard-slot0-{before,after}-cyclone-blink-fix-2026-09-30.*`.

## Always-bouncing cursor build

The latest source removes wraparound movement. At either end of the 15-key
row, the cursor reverses direction and moves to the adjacent key. Starting
direction, initial cursor position, targets, and speed progression remain
randomized or unchanged as before. The user requested this after trying both
path styles. The rebuilt image has SHA-256
`cb2a8fd64ce9332d01168621532f7e7eec5bde61d429bb492346b257f37f60ac` and SysEx
SHA-256 `f18ab113ee09262f5d69623cf732d9d3eec40a20420032fea60c2224d18d93c5`.
This build was flashed on September 30, 2026. The updater accepted all 223
chunks and confirmed application version 1.2.3. Slot 0 was backed up before
flashing, restored, and verified byte-identically afterward. Both preset images
have SHA-256
`6a22b43cca981cb489140805e7229953841629160f5bc5a2cd75d07120e51088`; see
`backups/kboard-slot0-{before,after}-cyclone-bounce-2026-09-30.*`. The user may
need to re-enable the physical Tilt and Press buttons after the update.
