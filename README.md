# K-Board native Linux editor

A PySide6 editor for the 25-key KMI K-Board (`1f38:001a`), using the preset protocol from KMI's [published editor source](https://github.com/Muse-Kinetics/k-board-editor). It runs without Wine and sends presets through the controller's ALSA MIDI port.

## Start

Connect the K-Board and run:

```sh
./launch-editor.sh
```

The launcher creates a local Python environment and installs `requirements.txt` on first use. The window shows the connected firmware version. The development K-Board was updated with KMI's official updater and independently verified as firmware 1.2.2 on September 29, 2026.

## Editing

The window provides the settings in the official editor: MPE and member channel count, normal MIDI channel, pressure and tilt assignments, pad and tilt bend ranges, velocity/pressure/tilt sensitivity, velocity curve, note-on threshold, and return values for the Press and Tilt buttons. It also has custom amount and landing deadzone controls for tilt and the Bend Pad.

For MPE, enable MPE, set member channels to match the receiving instrument (up to 15), and choose whether tilt sends pitch bend or CC 74. The K-Board's **Key tilt bend range** setting has no effect in MPE mode, as [KMI's manual](https://files.keithmcmillen.com/products/k-board/documentation/K-Board-Manual.pdf) confirms. Set the per-note bend range in the receiving instrument. The custom **Relative tilt amount** scales tilt inside that range.

With the custom firmware, **Relative tilt amount** scales each note's bend around where your finger first lands. The shown percent is rounded from an exact 0–64 setting. At a receiver range of ±1 semitone, 25% allows up to about ±25 cents of bend. **Landing deadzone** keeps pitch centered while the tilt sensor stays within 0–12 steps of the landing position; movement beyond it ramps from center. Both controls need the custom firmware to affect MIDI output. The velocity curve applies to velocity; tilt sensitivity is still available for broader touch response changes.

In stock MPE mode, the Bend Pad sends global pitch bend on channel 1 and tilt sends per-note bend on member channels. The custom v6 firmware instead adds the Bend Pad's relative movement to each active note's tilt bend on its member channel, while keeping the master bend centered. The first pad position is neutral; **Relative pad amount** sets its maximum share of the receiving instrument's per-note bend range, and **Pad landing deadzone** keeps small movements centered. The Bend Pad input span should be 12 for full sensor resolution in this custom mode. At a receiver range of ±1 semitone, a 25% pad amount means up to about ±25 cents from the pad, summed with tilt and clipped at the receiver range.

After a firmware update, turn the K-Board's physical Tilt and Press buttons back on if you want those expressions; the update reset them to off on the development board.

**Send to K-Board** writes the complete preset, then reads it back and reports whether the board stored it. Changing a control only edits the local profile. Use **File → Save profile** to keep it as JSON, or **File → Export SysEx** for a standard `.syx` preset file. Opening or saving a profile never sends it to the device.

On connect the editor loads the settings stored on the K-Board. **Read from K-Board** reloads them at any time.

### Preset readback

KMI's published editor never reads settings, but firmware 1.2.2 has a native preset dump. Send KMI category `0x50`, type `0x02`, with a slot byte (0–3) in the payload; see `build_settings_request()`. The board answers with a 553-byte reply: 8 raw header bytes (`F0 7E 00 20 00 00 00 00`), then the slot's 470-byte flash image and an XOR checksum in KMI's 7-bit encoding. The checksum covers `0x7E`, `0x20`, and the image. The editor writes and reads slot 0. The dump handler is at `0x560A` in the 1.2.2 image, reached from `0x7B8F`. Type `0x06` is a separate 189-byte factory/calibration dump, which is all zeros on this board.

A dump of the development board's settings from September 29, 2026 is in `backups/`. Flashing firmware rewrites the preset pages (`0xF000–0xF7FF`), so read and save your settings before any firmware update.

`firmware_tools/ApplySettingsDumpPatch.java` and `firmware_analysis/kboard_1.2.2-settings-dump.*` are a superseded firmware patch that added a custom dump before the native one was found. They were never flashed.

### Experimental relative tilt firmware

The firmware patchers work from a local copy of the official K-Board 1.2.2 firmware. Firmware images, generated outputs, and device preset backups are excluded from Git. Place your own `K-Board Firmware v1.2.2_cs512.syx` at `firmware_stock/`; the builder checks its hash and hook bytes before writing anything. The tools only write files and never communicate with the K-Board.

`firmware_tools/build_relative_tilt_patch.py` builds a relative tilt and Bend Pad image for MPE. For each note, the first tilt sample sets that channel's zero point; later movement bends relative to it. The v6 output combines relative Bend Pad movement with each note's tilt bend. It writes `firmware_analysis/kboard_1.2.2-relative-tilt-v6.bin` and `.syx` locally.

```sh
python firmware_tools/build_relative_tilt_patch.py
python -m unittest tests/test_relative_tilt_patch.py
```

The emitted 8051 hooks and the SysEx round trip are checked in software. Earlier live captures confirmed centered first bends and the v3 hard amount limit. V4's first pad hook missed the active send path; V5 made the pad override tilt; both are archived. V6 was flashed and its intended preset read back byte for byte. A live capture showed the master bend staying centered while the pad bent active member channels within the selected 25% range. See [`firmware_analysis/relative_tilt_findings.md`](firmware_analysis/relative_tilt_findings.md) for hook locations, capture data, and recovery.

### Experimental on-board sensitivity menus

The v10 firmware provides Velocity, Pressure, and Tilt sensitivity menus. Hold any settings button for 2.5 seconds to enter; use the 15 white keys to choose a value. V11 reduces the hold to 1 second. In v11, a short press of any settings button exits, and a 1 second hold on another settings button switches pages. The edited button is the only one lit during a menu; exiting restores the settings-button states from before entry.

Velocity and Pressure use the same 15 values, gain 60–254 (100 = unchanged). Tilt uses 15 sensitivity values from 0–70. V10 targets the preset fields at XRAM `0x0396:0x0397` (velocity), `0x0364:0x0365` (pressure), and `0x034F` (tilt). On menu exit it erases and rewrites the editor's slot 0 with the full 470-byte live preset image; power loss during that rewrite can leave the preset incomplete.

`firmware_tools/build_sensor_config_patch.py` builds v11 on top of the exact v8 artifact and refuses an unexpected v8 image hash. Build the relative tilt v6 image first, then the velocity slider v8 image, before building the sensor menus. V9 had an off-by-one Pressure/Tilt address bug; v10 fixes it. V11 with the usability changes is now flashed on the development board. The slot 0 preset was backed up before the update, restored after, and verified byte-identical. A firmware update resets the physical Tilt and Press buttons to off; re-enable them on the device. See the findings file for the v9 issue and flash records.

Before any flash, use `firmware_tools/preset_backup.py` (`backup`, `restore-file`, `send`, `identity`) to save the preset and build a byte-exact restore file. A flash resets the preset to factory settings, so send the restore file afterwards and read it back to confirm.

## Bitwig MPE input

[`bitwig/K-Board_MPE.control.js`](bitwig/K-Board_MPE.control.js) marks the original K-Board's channel-per-note messages as an expressive MIDI input in Bitwig. This is required because Bitwig's generic MIDI keyboard controller accepts every channel but does not convert member-channel pressure and pitch bend into per-note expressions.

Install the script in `~/Bitwig Studio/Controller Scripts`, then add **Keith McMillen → K-Board MPE** in Bitwig's controller settings and assign `K-Board MIDI 1`. Remove or disable the generic keyboard entry that was using the same port. Set the script's **Per-note pitch bend range** preference to the bend range you want at 100% relative tilt amount; the editor scales from there.


To run all the checks (`tests/` is not a package, so `unittest discover` does not work): `PYTHONPATH=. .venv/bin/python -m unittest tests.test_protocol tests.test_firmware_extract tests.test_relative_tilt_patch tests.test_velocity_slider_patch tests.test_sensor_config_patch`.

## Protocol and licensing

`preset_model.json` and the codec in `kboard_protocol.py` are adapted from KMI Music's [MPL-2.0 source](https://github.com/Muse-Kinetics/k-board-editor/tree/main/packages/k-board-js-api/src/presetCodec), copyright (c) 2026 KMI Music, Inc. Their license is in `LICENSE-MPL-2.0`. The Python encoder's 561-byte output was compared byte for byte against the official JavaScript encoder for the default editor profile.
