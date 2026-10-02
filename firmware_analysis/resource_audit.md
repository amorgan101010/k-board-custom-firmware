# K-Board firmware resource audit

Audited 2026-09-30 against the local official 1.2.2 image and the compact-table
1.2.3 image now running on the K-Board. The resource figures come from the
builder output; hands-on validation of the compact lookups is pending.

## Summary

| Resource | Current allocation | Remaining | Confidence |
| --- | ---: | ---: | --- |
| Expansion flash `0x8E15–0xEDFF` | flashed 1.2.3: 4,530; 1.2.4 candidate: 4,862 / 24,555 non-`0xFF` bytes | candidate: 19,693 bytes erased | High for the listed window: it is erased in stock and builders check their destinations before writing. |
| XRAM patch state `0x0F00–0x0F8C` | flashed 1.2.3: 80 named; 1.2.4 candidate: 96 named | 109 tail bytes certified free (`0x0F8D–0x0FF9`), plus 45 gaps inside the candidate's reserved spans | High for stock 1.2.2 from reset under its firmware-managed queue invariants. Conservative computed-pointer ceiling: `0x0EFF`; the software cursor reaches `0x0FFA–0x0FFF`. |

The candidate's 19,693 erased flash bytes are fragmented. The largest single hole is
7,072 bytes (`0xD260–0xEDFF`), recovered largely by compressing the scale
tables. Space outside the documented expansion window,
including the `0xEE00` runtime log page and preset/boot regions, is excluded
from the safe-space figure.

## Flash occupancy

The known-safe expansion window is 24,555 bytes. It was entirely `0xFF` in the
retail image. The unflashed 1.2.4 candidate has 4,862 non-erased bytes there:

| Patch layer | Newly occupied bytes in the safe window |
| --- | ---: |
| Relative tilt and Bend Pad | 332 |
| Velocity slider | 0 |
| Sensitivity menus | 1,499 |
| Cyclone | 1,409 |
| Scale selector and quantizer | 1,622 |
| **Total occupied non-`0xFF` bytes** | **4,862** |

The figures count non-`0xFF` bytes in the final flat image. The 332-byte
relative tilt addition is its exact scaler and 14-bit output routine.

The established relative tilt and slider code use the earlier erased block
`0x8300–0x8932`; the new 332-byte high-resolution routines use
`0x8E15–0x8F6E` in the safe expansion window.

Current erased runs of at least 16 bytes in the safe window are:

| Address | Bytes | Address | Bytes |
| --- | ---: | --- | ---: |
| `0x8F6F–0x92FF` | 913 | `0x930F–0x931F` | 17 |
| `0x932F–0x933F` | 17 | `0x934F–0x97FF` | 1,201 |
| `0x981E–0x9FFF` | 2,018 | `0xA1C7–0xA2FF` | 313 |
| `0xA4C7–0xA5FF` | 313 | `0xA7C3–0xA8FF` | 317 |
| `0xA94A–0xAFFF` | 1,718 | `0xB00D–0xB04F` | 67 |
| `0xB06E–0xB0FF` | 146 | `0xB1DB–0xB2FF` | 293 |
| `0xB352–0xB3FF` | 174 | `0xB48B–0xB5FF` | 373 |
| `0xB64B–0xB67F` | 53 | `0xB6E5–0xB6FF` | 27 |
| `0xB73A–0xB7FF` | 198 | `0xB889–0xB8FF` | 119 |
| `0xB953–0xB9FF` | 173 | `0xBA45–0xBAFF` | 187 |
| `0xBB1C–0xBBFF` | 228 | `0xBC0F–0xBC1F` | 17 |
| `0xBC36–0xBCFF` | 202 | `0xBD40–0xBDFF` | 192 |
| `0xBE22–0xBEFF` | 222 | `0xBF16–0xBF2F` | 26 |
| `0xBFB0–0xBFFF` | 80 | `0xC040–0xC0FF` | 192 |
| `0xC146–0xC1FF` | 186 | `0xC255–0xC3FF` | 427 |
| `0xC505–0xC5FF` | 251 | `0xC756–0xC7FF` | 170 |
| `0xC87E–0xC8BF` | 66 | `0xC8D2–0xC8FF` | 46 |
| `0xC92C–0xC9FF` | 212 | `0xCA93–0xCBFF` | 365 |
| `0xCC9F–0xCCFF` | 97 | `0xCD27–0xCDFF` | 217 |
| `0xCE18–0xCE7F` | 104 | `0xCEE1–0xCEFF` | 31 |
| `0xCFB4–0xD1FF` | 588 | `0xD219–0xD23F` | 39 |
| `0xD260–0xEDFF` | 7,072 |  |  |

There are additional erased runs shorter than 16 bytes; the candidate's
19,693-byte total includes those. A builder must still validate every destination against the
complete combined image. An erased byte by itself is not proof that runtime
code cannot reference that location.

## XRAM use

The allocation below shows the 1.2.4 candidate RAM layout after the two
compression changes and relative tilt precision update. The flashed 1.2.3
image still has 80 named bytes; the candidate uses 16 more.

The custom state allocation is compact and contiguous:

| Address | Reserved span | Named bytes | Mod state |
| --- | ---: | ---: | --- |
| `0x0F00–0x0F3F` | 64 | 51 | 16 cached low bend bytes (candidate), 16 combined tilt state/baseline bytes, 3 Bend Pad values, 16 cached high bend bytes |
| `0x0F40–0x0F51` | 18 | 10 | Slider state/timer/step/saved bit (5), sensitivity menu mode/pending/session (3), save index (2) |
| `0x0F52–0x0F64` | 19 | 18 | Cyclone state, button/timing state, cursor/target, score, and display flags |
| `0x0F65–0x0F8C` | 40 | 17 | Scale selector state (13) and a four-byte consumed-key bitmap at `0x0F74–0x0F77` |
| `0x0F8D–0x0FFF` | 115 | 0 | Not used by custom patches |

The mods reserve 141 bytes of address space; 96 bytes have named state
variables in the candidate and 45 bytes are gaps inside those reserved spans.
The remaining 115 bytes are unallocated by custom patches. The trace below accounts for all stock
computed-DPTR families: six bytes at the end are used by stock, and
`0x0F8D–0x0FF9` (109 bytes) are outside every traced stock target.

### `MOVX @DPTR` audit

The stock listing has 322 `MOVX @DPTR` instructions without a statically
resolved XRAM reference, distributed across 143 function bodies. These are not
322 independent pointers: most are repeated loads/stores through shared helpers
and address-generation families. The trace below follows those families back
to their state writers and call-site arguments. Ghidra resolves the other
accesses to targets whose largest physical XRAM address is `0x0DBC` after alias
normalization.

The K-Board's Silicon Labs C8051F34x runs with the EMIF in Internal-Only mode:
16-bit `MOVX @DPTR` effective addresses beyond the installed 4 KiB wrap on a
4 KiB boundary. Thus an unknown 16-bit DPTR conservatively may touch any byte
from `0x0000` through `0x0FFF`. The data sheet documents this aliasing and
lists `EMI0CF` reset value `0x03` (Internal-Only, USB FIFO access disabled).
The stock image has no writes to `EMI0CF` or `EMI0CN`; startup clears IRAM and
all 4 KiB of XRAM. See Silicon Labs' [C8051F34x data sheet, Sections 13.1 and
13.6.1](https://www.silabs.com/documents/public/data-sheets/C8051F34x.pdf).

### Computed-pointer trace

The 322 sites reduce to these address sources. Bounds are for the effective
12-bit XRAM address after the controller's Internal-Only aliasing:

| Address source | Trace result | Bound / evidence |
| --- | --- | --- |
| Software cursor (`IRAM 0x4F:0x50`) | Top-page temporary accesses | Startup clears the cursor to zero. Its only mutator is `0x2DDF`; callers that mutate it are `0x313D` and `0x5EB3`, with fixed adjustments and matching restores. The computed dispatch in `0x313D` has 15 table entries; each path reaches the common restore. The net change per call is zero. The temporary window is exactly `0x0FFA–0x0FFF` (six bytes). |
| USB queue at `0x0728`, serviced by `0x4992` and `0x71D2` | Linked records and ring-buffer data | Queue cursors are initialized to `0x064A` at `0x7E8A`; queue processing is called from `0x8037`. This path uses the C8051F34x USB FIFO address window `0x0400–0x07FF`, so its records cannot reach the patch page. The device data sheet documents the FIFO window and endpoint partitioning. |
| Key scan and per-key tables (`0x2443`, `0x38FB`, related helpers) | Index-derived table reads/writes | The scan index wraps at 24; the other key indices are checked or bounded by the 25/32-key loops. Their generated pointers stay below `0x0400`. |
| `0x09C1:0x09C2` and configuration table pointers | Preset/configuration and MIDI state | Writers choose high bytes and indices from bounded scan/menu state; the largest effective target in this family is below `0x0A00`. The variable lookup at `0x5B52` is guarded by `XRAM[0x0983] == 3`; after reset, the only stock writes to `0x0983` store `0x5D` or `0x5E`, so that XDATA-capable lookup branch is unreachable in the stock image. |
| Shared XDATA access helpers (`0x2AB8`, `0x2AD1`, `0x2AFE`, `0x2BD9`, `0x2C04`, `0x2C3C`) | Caller-supplied pointers | Every XDATA-mode call site uses fixed firmware addresses, a bounded loop/index, or the configuration records above. The largest XRAM target is `0x0EFF`; no caller passes a target in `0x0F00–0x0FF9`. |
| Remaining computed accesses | Local table offsets and byte/word field walks | Backward tracing of DPTR setup and caller arguments places these in the stock state/configuration region; none exceeds `0x0EFF`. The largest statically resolved physical address remains `0x0DBC`. |

Thus the stock 1.2.2 image has no traced XRAM target in `0x0F00–0x0FF9`.
`0x0FFA–0x0FFF` is the only stock use in the top 256-byte page. The currently
unallocated `0x0F8D–0x0FF9` span is **109 bytes certified free for additional
patch state** under the listed firmware invariants. This is a static firmware
trace, not a claim that arbitrary RAM corruption or a modified stock image is
safe. Normal MIDI send, note-off, and expression traces also observed only the
six cursor-window addresses at the top of XRAM.

## Efficiency opportunities

### 1. Compact the scale tables (implemented in this candidate)

The old scale representation generated 7,405 bytes of table data:

- 4,500 bytes for 15 scales × 12 transpositions × 25 LED-key states.
- 2,160 bytes for 15 scales × 12 transpositions × 12 pitch-class quantizer
  entries.
- 720 bytes of two-byte pointers to those regular tables.
- 25 bytes for the transpose modulo lookup.

The builder now stores 180 one-byte records plus the 25-byte transpose lookup.
Each record combines LED state in its upper two bits and a signed quantizer
correction (`-6..+6`) in its lower nibble. Both runtime paths rotate the pitch
class for the current transpose and read the same record. The compact tables
use 205 bytes rather than 7,405, saving **7,200 table bytes**. The draw and
quantizer routines grew to decode the shared layout; the complete scale layer
now allocates 1,622 non-erased bytes, down from 7,938. Total patch
allocation in the safe window fell by 6,373 bytes. Emulator coverage checks all
180 scale/transposition combinations, all 4,500 LED positions, and all 2,160
quantizer corrections. The 77-test project suite passes (one skipped), and the
1.2.3 image built and flashed successfully; hardware behavior still needs validation.

### 2. Pack the consumed-key marks (implemented, 21-byte saving)

The scale patch stores a 25-bit bitmap at `0x0F74–0x0F77`, releasing 21 bytes
from the previous one-byte-per-key allocation. Key-on and key-off compute a
byte index and bit mask while preserving the scanner's scratch registers. The
mask loop runs only on menu key presses and releases; it does not affect the
MIDI bend path. Regression coverage exercises simultaneous held keys across
bitmap byte boundaries.

### 3. Merge per-channel tilt state into baselines (implemented, 16-byte saving)

The state array is folded into the 16 baseline bytes at `0x0F10–0x0F1F`,
releasing the separate 16-byte state array. Zero means inactive, `0x81` means
armed after note-on, `0x82` means an absolute-mode sample was seen, and values
1–128 encode relative baselines 0–127 with a +1 bias. The bend hook checks the
two sentinels and removes the bias for active baselines; it needs no indexed
bitfield operations or extra XRAM accesses. The member-channel Bend Pad scan
continues to use nonzero state as its active-note test. Real-byte hook coverage
checks note-on, note-off, absolute/relative transitions, and endpoint baselines.

Together, the bitmap and merged tilt state release 37 bytes of named RAM:
named state falls from 117 bytes in the flashed image to 80 bytes in this
candidate. The address reservation remains 141 bytes, and no additional XRAM
is certified safe from stock's computed DPTR accesses.

The flashed 1.2.3 RAM-savings image is
`kboard-custom-firmware-1.2.3-xram-savings-candidate.{bin,syx}`. Image
SHA-256: `af7603406088eb42423d675835f2502a297cda4954dd9e5f95715d8f55a1d087`;
SysEx SHA-256: `f33c46e0a862f77d9c7cd3e57e92dce1cc489a7271a6f6a9e1c15ff9572f51c5`.
The full project suite passed 80 tests with one skipped before flashing. On
2026-09-30 KMI SendSysEx accepted all 242 chunks and confirmed application
version 1.2.3. Slot 0 was restored and verified byte-identically; both preset
images have SHA-256
`01615d521800aa7865565ecbcd8fa7f6d3d26bb7f2b002745e0afbce04680a6b`.

### 4. Pack Cyclone flags only if RAM becomes tight

Several Cyclone state bytes are Boolean or small enums. Combining flags may
save a few bytes, but would make the already well-bounded 19-byte game state
harder to inspect and change. This is lower value than table compression.

### 5. Reduce alignment gaps only when a feature needs a larger block

Patch routines use fixed addresses, often aligned to `0x100` boundaries.
There are many holes between routines, including blocks up to 427 bytes.
The combined builder checks occupied destinations, so current fragmentation
does not prevent ordinary additions. For a future routine larger than the
largest hole, a linker-like allocator or repacking the scale routines and
tables would recover space without changing features. Keep routine addresses
stable for now because hooks and emulator fixtures refer to them directly.

## Excluded regions and limitations

- `0xEE00` is the stock button-state log area and is erased at runtime.
- `0xF000–0xF7FF` holds preset data. Firmware update erases it; the existing
  backup/restore procedure remains required.
- `0xF800–0xFFFF` is outside the audited expansion range and is not counted,
  even though much of the flat image is `0xFF` there.
- The audit compares retail 1.2.2 with the local custom builder output. It
  does not claim every non-`0xFF` byte is executable code, nor that erased
  space outside the known-safe window is unreferenced by all computed jumps
  and pointers.
