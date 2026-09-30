#!/usr/bin/env python3
"""Build the complete K-Board custom firmware 1.2.3 in one command.

The script verifies the supplied official 1.2.2 input through the component
builders, assembles all patches in memory, and writes one final .bin/.syx pair.
It never opens a MIDI port or flashes a device.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_tools import build_sensor_config_patch as menus
from firmware_tools import build_cyclone_game_patch as cyclone
from firmware_tools import build_scale_quantizer_patch as scales
from firmware_tools import build_velocity_slider_patch as velocity
from firmware_tools.build_relative_tilt_patch import ROOT, STOCK_SYX
from firmware_tools.extract_kmi_firmware import extract
from firmware_tools.repack_kmi_firmware import pack, records_from_image

VERSION_BYTE_ADDRESS = 0x5CF8
STOCK_VERSION_BYTE = 0x02
CUSTOM_VERSION_BYTE = 0x03
DEFAULT_OUTPUT = ROOT / "firmware_analysis/kboard-custom-firmware-1.2.3"


def build_image() -> tuple[bytes, bytes]:
    # The builders construct each stage in memory. Only the final image is
    # written by this entry point.
    slider_image, _ = velocity.build_image()
    menu_image, _ = menus.build_image(slider_image)
    game_image, _ = cyclone.build_image(menu_image)
    final_image, _ = scales.build_image(game_image)
    patched = bytearray(final_image)
    if patched[VERSION_BYTE_ADDRESS] != STOCK_VERSION_BYTE:
        raise ValueError("stock application version byte differs; refusing to identify this as 1.2.3")
    patched[VERSION_BYTE_ADDRESS] = CUSTOM_VERSION_BYTE
    records, _ = extract(STOCK_SYX)
    return bytes(patched), pack(records_from_image(records, patched))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    image, sysex = build_image()
    image_path = Path(f"{args.output_prefix}.bin")
    sysex_path = Path(f"{args.output_prefix}.syx")
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(image)
    sysex_path.write_bytes(sysex)
    print(f"image: {image_path} ({len(image)} bytes, sha256 {hashlib.sha256(image).hexdigest()})")
    print(f"SysEx: {sysex_path} ({len(sysex)} bytes, sha256 {hashlib.sha256(sysex).hexdigest()})")


if __name__ == "__main__":
    main()
