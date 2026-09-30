# K-Board custom firmware

This project builds experimental custom firmware for the retail Keith McMillen K-Board. The current build is identified by the device as firmware **1.2.3** and is made from a locally supplied official 1.2.2 firmware image. The patcher writes one complete firmware image and one SysEx update file; it does not connect to or flash the keyboard.

## Features

- **Per-note MPE tilt:** each note starts centered at its landing position. Set the tilt amount below 100% to scale pitch bends below the instrument's configured range, including less than a semitone. A landing deadzone is available.
- **Bend Pad control:** choose relative movement from the first touch or the stock absolute pad position. Set its amount and deadzone when relative mode is enabled.
- **Independent bend paths:** choose whether tilt is relative, whether the Bend Pad is relative, and whether pad movement is combined with per-note tilt. Turning all three off restores the stock tilt and pad paths.
- **On-board sensitivity menus:** hold Velocity, Pressure, or Tilt for one second, then choose one of 15 settings with the white keys. A short press exits and saves; holding another settings button switches pages.

Firmware generation and editor support are experimental. The 1.2.3 image has been flashed to a retail K-Board and confirmed by the updater and device identity response. The new independent bend combinations still need hands-on verification. See [firmware findings](firmware_analysis/relative_tilt_findings.md) for patch details, analysis, test coverage, and hardware observations.

## Build the firmware

Use Python 3.10 or later. Put your own official K-Board 1.2.2 SysEx file here:

```text
firmware_stock/K-Board Firmware v1.2.2_cs512.syx
```

Run the single builder:

```sh
python firmware_tools/build_custom_firmware.py
```

It validates the stock input and hook bytes, applies all firmware patches in memory, and writes:

```text
firmware_analysis/kboard-custom-firmware-1.2.3.bin
firmware_analysis/kboard-custom-firmware-1.2.3.syx
```

These generated files are ignored by Git. The builder never flashes a device.

## Install and recover

Flashing erases the K-Board's preset pages. Back up the preset first with `firmware_tools/preset_backup.py`, install the generated SysEx with KMI's official updater, then restore the preset and read it back to verify it. Firmware updates also reset the physical Tilt and Press buttons to off; re-enable them on the keyboard if needed.

The sensitivity menus save changes to preset slot 0. A power interruption during that flash write can leave the preset incomplete. Keep a recoverable backup before using the menus. To return to stock firmware, use your local official 1.2.2 image, then restore the saved preset.

## Tools

### Preset editor

Launch the native Linux editor with:

```sh
./launch-editor.sh
```

The launcher creates a local virtual environment and installs `requirements.txt` on first use. The editor can read and write presets, verify sent settings, and save profiles as JSON. The custom bend settings are hidden unless a connected K-Board identifies itself as firmware 1.2.3.

### Bitwig MPE controller script

[`bitwig/K-Board_MPE.control.js`](bitwig/K-Board_MPE.control.js) configures Bitwig to interpret member-channel pressure and pitch bend as per-note MPE expression. Install it in Bitwig's Controller Scripts folder, then add **Keith McMillen → K-Board MPE** in controller settings. Set its per-note pitch-bend range to match the receiving instrument.

## Validation

Run the project test suite with:

```sh
PYTHONPATH=. python -m unittest \
  tests.test_protocol \
  tests.test_firmware_extract \
  tests.test_relative_tilt_patch \
  tests.test_velocity_slider_patch \
  tests.test_sensor_config_patch
```

The patcher uses the official firmware only as local input. This repository does not include KMI firmware, generated firmware images, MIDI captures, or device preset backups. KMI's published preset model and codec are licensed under MPL-2.0; see [`LICENSE-MPL-2.0`](LICENSE-MPL-2.0).

## Acknowledgements and disclaimer

Thank you to Keith McMillen Instruments / Muse Kinetic and [insolace](https://github.com/insolace) for making the original K-Board 1.2.2 firmware available.

This is an independent project. I am not affiliated with, endorsed by, or sponsored by Keith McMillen Instruments, Muse Kinetic, or insolace.
