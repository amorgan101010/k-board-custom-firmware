"""K-Board preset codec and ALSA MIDI connection.

Protocol and preset data adapted from KMI Music's MPL-2.0 k-board-editor:
https://github.com/Muse-Kinetics/k-board-editor
Copyright (c) 2026 KMI Music, Inc.  See LICENSE-MPL-2.0.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
MODEL = json.loads((HERE / "preset_model.json").read_text())
PRODUCT_ID = (0x00, 0x01, 0x5F, 0x7A, 0x1A, 0x00)
IDENTITY_REQUEST = (0x7E, 0x7F, 0x06, 0x01)
PRESET_DUMP_HEADER = bytes((0xF0, 0x7E, 0x00, 0x20, 0x00, 0x00, 0x00, 0x00))
PRESET_DUMP_LENGTH = 553
CURVES = ("Linear", "Logarithmic", "Sine", "Cosine", "Exponential", "Invert")
ENUMS = {
    "Linear": 0, "Logarithmic": 1, "Sine": 2, "Cosine": 3,
    "Exponential": 4, "Invert": 5, "Custom 1": 6, "Custom 2": 7,
    "Latest": 0, "Earliest": 1, "Highest": 2, "Lowest": 3,
    "USB 1": 0, "Expander": 1, "USB 1 + Expander": 2,
    "None": 0, "Velocity": 1, "Key Pitch": 2, "Key Number": 3,
    "Note On": 4, "Pressure": 5, "Tilt": 6, "Bend Pad": 7,
    "Expression Pedal": 8, "USB 3": 2, "Keyboard": 0,
    "Keyboard + Expander": 3, "Keyboard + USB 3": 4,
    "USB 3 + Expander": 5, "All": 6,
    "Note CV Out": 128, "Velocity CV Out": 129,
    "Channel Pressure CV Out": 130, "Pitch Bend CV Out": 131,
    "1 Volt/Octave": 0, "1.2 Volts/Octave": 1, "Hz/Volt": 2,
    "Normal": 0, "Control Only": 2, "All Off": 1,
    "Off": 0, "On": 1, "Legato": 2,
}
ENUMS.update({f"CC#{n:03d}": n for n in range(128)})

DEFAULT_PROFILE = {
    "midi_channel": 0,
    "mpe_active": False,
    "mpe_member_channels": 1,
    "pressure_cc": 1,
    "tilt_cc": -1,
    "bend_range_pad": 12,
    "bend_range_tilt": 1,
    "relative_tilt_amount": 64,
    "relative_tilt_deadzone": 0,
    "relative_pad_amount": 64,
    "relative_pad_deadzone": 0,
    "relative_tilt_enabled": True,
    "relative_pad_enabled": True,
    "combine_pad_tilt": True,
    "velocity_sensitivity": 60,
    "pressure_sensitivity": 60,
    "tilt_sensitivity": 70,
    "velocity_curve": "Logarithmic",
    "pressure_disabled_return": -1,
    "tilt_disabled_return": -1,
    "on_thresh": 30,
}
BOUNDS = {
    "midi_channel": (0, 15), "mpe_member_channels": (1, 15),
    "pressure_cc": (-1, 127), "tilt_cc": (-1, 127),
    "bend_range_pad": (1, 12), "bend_range_tilt": (1, 12),
    "relative_tilt_amount": (0, 64), "relative_tilt_deadzone": (0, 12),
    "relative_pad_amount": (0, 64), "relative_pad_deadzone": (0, 12),
    "velocity_sensitivity": (60, 254), "pressure_sensitivity": (60, 254),
    "tilt_sensitivity": (0, 70), "pressure_disabled_return": (-1, 127),
    "tilt_disabled_return": (-1, 127), "on_thresh": (1, 127),
}
BEND_MIN = (64, 58, 53, 48, 43, 37, 32, 27, 22, 16, 11, 6, 0)
BEND_MAX = (64, 69, 74, 79, 85, 90, 95, 100, 106, 111, 116, 121, 127)
FIELD_MAP = {
    "midi_channel": "Keyboard_Global_USB_1_Channel",
    "mpe_active": "Keyboard_Global_Channel_Rotation_Active",
    "mpe_member_channels": "Keyboard_Global_Polyphony_Number",
    "pressure_cc": "Keyboard_CC_00_Control_Number",
    "tilt_cc": "Keyboard_CC_01_Control_Number",
    "pressure_sensitivity": "Keyboard_CC_00_Gain",
    "tilt_sensitivity": "Globals_Tilt_Sensitivity",
    "velocity_sensitivity": "Keyboard_Velocity_Gain",
    "velocity_curve": "Keyboard_Velocity_Curve",
    "pressure_disabled_return": "Keyboard_Global_Program_Change_A",
    "tilt_disabled_return": "Keyboard_Global_Program_Change_B",
    "on_thresh": "Globals_On_Thresh",
}
OPTIONAL_PROFILE_FIELDS = {
    "relative_tilt_amount", "relative_tilt_deadzone",
    "relative_pad_amount", "relative_pad_deadzone", "relative_tilt_enabled",
    "relative_pad_enabled", "combine_pad_tilt",
}


def validate_profile(profile: dict) -> dict:
    # Profiles saved before the custom firmware controls were added retain
    # their exact behavior when opened with this editor.
    if not isinstance(profile, dict):
        raise ValueError("profile must be a settings object")
    profile = dict(profile)
    if "stock_bend_behavior" in profile:
        stock = profile.pop("stock_bend_behavior")
        if type(stock) is not bool:
            raise ValueError("stock_bend_behavior must be true or false")
        profile.setdefault("relative_tilt_enabled", not stock)
        profile.setdefault("relative_pad_enabled", not stock)
        profile.setdefault("combine_pad_tilt", not stock)
    keys = set(profile)
    if keys - set(DEFAULT_PROFILE) or (set(DEFAULT_PROFILE) - OPTIONAL_PROFILE_FIELDS) - keys:
        raise ValueError("profile is missing required settings or has unknown settings")
    checked = {}
    for key, value in (DEFAULT_PROFILE | profile).items():
        if key in ("mpe_active", "relative_tilt_enabled", "relative_pad_enabled", "combine_pad_tilt"):
            if type(value) is not bool:
                raise ValueError(f"{key} must be true or false")
        elif key == "velocity_curve":
            if value not in CURVES:
                raise ValueError(f"invalid velocity curve: {value}")
        else:
            lo, hi = BOUNDS[key]
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
        checked[key] = value
    if checked["mpe_active"]:
        checked["pressure_cc"] = -1
        if checked["tilt_cc"] not in (-1, 74):
            raise ValueError("MPE tilt must be pitch bend or CC 74")
    return checked


def full_preset(profile: dict) -> dict:
    profile = validate_profile(profile)
    preset = MODEL.copy()
    for field, key in FIELD_MAP.items():
        value = profile[field]
        if field == "tilt_sensitivity":
            value = 70 - value
        preset[key] = value
    preset["CV_In_CV_1_Offset"] = 64 - profile["relative_tilt_amount"]
    preset["CV_In_CV_2_Offset"] = profile["relative_tilt_deadzone"]
    preset["CV_In_CV_1_Min"] = 64 - profile["relative_pad_amount"]
    preset["CV_In_CV_2_Min"] = profile["relative_pad_deadzone"]
    # The otherwise-unused CV limit stores three independent custom behavior flags.
    flags = (int(profile["relative_tilt_enabled"]) << 2) | (int(profile["relative_pad_enabled"]) << 1)
    flags |= int(profile["combine_pad_tilt"])
    preset["CV_In_CV_1_Max"] = 0x70 | flags
    for field, keys in {
        "bend_range_pad": ("Keyboard_Global_USB_2_Channel", "Keyboard_Global_Key_Selection_Criteria"),
        "bend_range_tilt": ("Keyboard_Pitch_Bend_Max", "Keyboard_Pitch_Bend_Min"),
    }.items():
        bend = profile[field]
        preset[keys[0]] = BEND_MAX[bend]
        preset[keys[1]] = BEND_MIN[bend]
    return preset


def _crc_byte(crc: int, byte: int) -> int:
    temp = (crc >> 8) ^ (byte & 255)
    crc = (crc << 8) & 0xFFFF
    quick = temp ^ (temp >> 4)
    crc ^= quick
    crc ^= (quick << 5) & 0xFFFF
    crc ^= (quick << 12) & 0xFFFF
    return crc & 0xFFFF


class _Writer:
    def __init__(self):
        self.data = bytearray()
        self.crc = 0xFFFF
        self.sum_byte = 0
        self.high_bits = 0
        self.high_count = 0

    def encoded(self, value: int, crc: bool = True, sum_byte: bool = True,
                sum_value: int | None = None):
        if crc:
            self.crc = _crc_byte(self.crc, value)
        if sum_byte:
            self.sum_byte = (self.sum_byte + (value if sum_value is None else sum_value)) & 255
        self.high_bits = (self.high_bits | (value & 0x80)) >> 1
        self.data.append(value & 0x7F)
        self.high_count += 1
        if self.high_count == 7:
            self.data.append(self.high_bits)
            self.high_bits = self.high_count = 0

    def flush(self):
        while self.high_count:
            self.encoded(0, sum_byte=False)


def build_sysex(profile: dict) -> bytes:
    """Encode one complete 470-byte device preset, as the official editor does."""
    preset = full_preset(profile)
    w = _Writer()
    w.data.extend((0xF0, *PRODUCT_ID, 0x01))
    for byte in (0, 2, 0x22, 0x20):
        w.encoded(byte, sum_byte=False)
    crc = w.crc
    w.encoded(crc >> 8, sum_byte=False)
    w.encoded(crc & 255, sum_byte=False)
    w.flush()
    for byte in (0xA1, 470 >> 8, 470 & 255):
        w.encoded(byte, crc=False, sum_byte=False)
    w.crc = 0xFFFF
    w.sum_byte = 0
    w.encoded(0)
    for key, value in preset.items():
        if key == "Preset_Name":
            continue
        number = ENUMS[value] if isinstance(value, str) else value
        if "Gain" in key and key != "Globals_Gain":
            w.encoded(number >> 8, sum_value=number)
            w.encoded(number & 255, sum_byte=False)
        else:
            w.encoded(number)
    w.encoded(w.sum_byte, sum_byte=False)
    w.flush()
    w.data.append(0xF7)
    if len(w.data) != 561 or any(b & 0x80 for b in w.data[1:-1]):
        raise ValueError("invalid encoded preset length or MIDI data byte")
    return bytes(w.data)


def build_settings_request(slot: int = 0) -> bytes:
    """Build stock firmware's preset dump request (category 0x50, type 0x02).
    The published editor never sends it, but firmware 1.2.2 answers with the
    470-byte flash image of the requested slot.  The editor writes slot 0.
    """
    if slot not in range(4):
        raise ValueError("K-Board preset slot must be 0-3")
    w = _Writer()
    w.data.extend((0xF0, *PRODUCT_ID, 0x01))
    for byte in (0, 2, 0x50, 0x02):
        w.encoded(byte, sum_byte=False)
    crc = w.crc
    w.encoded(crc >> 8, sum_byte=False)
    w.encoded(crc & 255, sum_byte=False)
    w.flush()
    w.encoded(slot, crc=False, sum_byte=False)
    w.flush()
    w.data.append(0xF7)
    return bytes(w.data)
def _decode_7bit(encoded: bytes) -> bytes:
    if len(encoded) % 8:
        raise ValueError("settings dump is not aligned to 8-byte MIDI groups")
    decoded = bytearray()
    for offset in range(0, len(encoded), 8):
        group = encoded[offset:offset + 8]
        if any(byte & 0x80 for byte in group):
            raise ValueError("settings dump contains an invalid MIDI data byte")
        decoded.extend(
            byte | (((group[7] >> bit) & 1) << 7)
            for bit, byte in enumerate(group[:7])
        )
    return bytes(decoded)


def decode_preset_image(image: bytes) -> dict:
    """Decode one 470-byte on-device preset into the editor's settings."""
    if len(image) != 470:
        raise ValueError(f"expected a 470-byte preset image, received {len(image)} bytes")

    values = {}
    cursor = 1  # byte zero is the preset slot
    for key in MODEL:
        if key == "Preset_Name":
            continue
        if "Gain" in key and key != "Globals_Gain":
            value = int.from_bytes(image[cursor:cursor + 2], "big")
            cursor += 2
        else:
            value = image[cursor]
            cursor += 1
        values[key] = value
    if cursor != 470:
        raise ValueError(f"preset model consumed {cursor} bytes instead of 470")

    result = {}
    for field, key in FIELD_MAP.items():
        value = values[key]
        if field in ("pressure_cc", "tilt_cc", "pressure_disabled_return", "tilt_disabled_return"):
            value = -1 if value == 0xFF else value
        elif field == "mpe_active":
            value = bool(value)
        elif field == "velocity_curve":
            try:
                value = CURVES[value]
            except IndexError as exc:
                raise ValueError(f"unknown velocity curve value {value}") from exc
        elif field == "tilt_sensitivity":
            value = 70 - value
        result[field] = value

    bend_max = values["Keyboard_Global_USB_2_Channel"]
    bend_min = values["Keyboard_Global_Key_Selection_Criteria"]
    try:
        pad_range = next(
            index for index, pair in enumerate(zip(BEND_MIN, BEND_MAX))
            if pair == (bend_min, bend_max)
        )
    except StopIteration as exc:
        raise ValueError(f"unknown bend-pad range bytes {bend_min}/{bend_max}") from exc
    tilt_max = values["Keyboard_Pitch_Bend_Max"]
    tilt_min = values["Keyboard_Pitch_Bend_Min"]
    try:
        tilt_range = next(
            index for index, pair in enumerate(zip(BEND_MIN, BEND_MAX))
            if pair == (tilt_min, tilt_max)
        )
    except StopIteration as exc:
        raise ValueError(f"unknown tilt range bytes {tilt_min}/{tilt_max}") from exc
    result["bend_range_pad"] = pad_range
    result["bend_range_tilt"] = tilt_range
    result["relative_tilt_amount"] = 64 - values["CV_In_CV_1_Offset"]
    result["relative_tilt_deadzone"] = values["CV_In_CV_2_Offset"]
    result["relative_pad_amount"] = 64 - values["CV_In_CV_1_Min"]
    result["relative_pad_deadzone"] = values["CV_In_CV_2_Min"]
    mode = values["CV_In_CV_1_Max"]
    flags = (0x00 if mode == 0x7E else mode & 0x07) if mode & 0xF8 == 0x70 else 0x07
    result["relative_tilt_enabled"] = bool(flags & 0x04)
    result["relative_pad_enabled"] = bool(flags & 0x02)
    result["combine_pad_tilt"] = bool(flags & 0x01)
    return validate_profile(result)


def parse_settings_dump(packet: bytes) -> dict:
    """Validate a stock preset dump reply and return its editor profile."""
    return decode_preset_image(preset_image_from_dump(packet))
def preset_image_from_dump(packet: bytes) -> bytes:
    """Return the 470-byte image from a preset dump reply.
    Reply layout: 8 raw header bytes, then the image and an XOR checksum in
    the firmware's 7-bit encoding, zero padded, then F7.  The checksum
    covers the header's 0x7E and 0x20 bytes plus the image.
    """
    if (len(packet) != PRESET_DUMP_LENGTH or not packet.startswith(PRESET_DUMP_HEADER)
            or packet[-1] != 0xF7):
        raise ValueError("K-Board reply is not a preset dump")
    decoded = _decode_7bit(packet[len(PRESET_DUMP_HEADER):-1])
    image, checksum, padding = decoded[:470], decoded[470], decoded[471:]
    expected = 0x7E ^ 0x20
    for byte in image:
        expected ^= byte
    if checksum != expected:
        raise ValueError(f"preset dump checksum is 0x{checksum:02x}; expected 0x{expected:02x}")
    if any(padding):
        raise ValueError("preset dump has non-zero padding")
    return image
def _port(names: list[str]) -> str:
    matches = [name for name in names if name.startswith("K-Board:")]
    if len(matches) != 1:
        raise RuntimeError(f"expected one K-Board MIDI port, found {len(matches)}")
    return matches[0]


class Device:
    def __enter__(self):
        import mido
        self.output = mido.open_output(_port(mido.get_output_names()))
        try:
            self.input = mido.open_input(_port(mido.get_input_names()))
        except BaseException:
            self.output.close()
            raise
        return self

    def __exit__(self, *_):
        self.input.close()
        self.output.close()

    def identity(self, timeout: float = 2.0) -> str:
        import mido
        for _ in self.input.iter_pending():
            pass
        self.output.send(mido.Message("sysex", data=IDENTITY_REQUEST))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self.input.iter_pending():
                if msg.type != "sysex":
                    continue
                data = bytes(msg.data)
                if len(data) >= 17 and data[:8] == bytes((0x7E, 0, 6, 2, 0, 1, 0x5F, 0x1A)):
                    if data[8]:
                        raise RuntimeError("K-Board is in bootloader mode")
                    return ".".join(map(str, data[14:17]))
            time.sleep(0.01)
        raise TimeoutError("K-Board did not answer its identity request")

    def send_preset(self, profile: dict):
        import mido
        payload = build_sysex(profile)
        self.output.send(mido.Message("sysex", data=payload[1:-1]))

    def read_settings(self, slot: int = 0, timeout: float = 3.0) -> dict:
        import mido
        for _ in self.input.iter_pending():
            pass
        request = build_settings_request(slot)
        self.output.send(mido.Message("sysex", data=request[1:-1]))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in self.input.iter_pending():
                if msg.type != "sysex":
                    continue
                packet = bytes((0xF0, *msg.data, 0xF7))
                if packet.startswith(PRESET_DUMP_HEADER):
                    return parse_settings_dump(packet)
            time.sleep(0.01)
        raise TimeoutError("K-Board did not answer the preset dump request")
