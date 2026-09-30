#!/usr/bin/env python3
"""Capture K-Board MPE note and pitch-bend ordering without changing MIDI."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import time

import rtmidi


CENTER = 8192


@dataclass
class Gesture:
    channel: int
    note: int
    velocity: int
    started: float
    bend_before_note: int | None = None
    first_bend: int | None = None
    minimum: int = 16383
    maximum: int = 0

    def add_bend(self, value: int) -> None:
        if self.first_bend is None:
            self.first_bend = value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)


def bend_value(message: list[int]) -> int:
    return message[1] | (message[2] << 7)


def open_kboard(wait_seconds: float) -> tuple[rtmidi.MidiIn, str]:
    deadline = time.monotonic() + wait_seconds
    announced_wait = False
    while time.monotonic() < deadline:
        midi_in = rtmidi.MidiIn()
        ports = midi_in.get_ports()
        match = next(((index, name) for index, name in enumerate(ports) if "K-Board" in name), None)
        if match is None:
            if not announced_wait:
                print("Waiting for a K-Board MIDI input...", flush=True)
                announced_wait = True
            time.sleep(0.5)
            continue
        index, name = match
        try:
            midi_in.open_port(index)
        except rtmidi.SystemError:
            if not announced_wait:
                print("Waiting for Bitwig to release the K-Board MIDI port...", flush=True)
                announced_wait = True
            time.sleep(0.5)
            continue
        midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        return midi_in, name
    raise TimeoutError("K-Board stayed busy; disable its Bitwig controller entry and try again")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20.0, help="capture duration after the port opens")
    parser.add_argument("--wait", type=float, default=120.0, help="how long to wait for the MIDI port")
    args = parser.parse_args()

    midi_in, port_name = open_kboard(args.wait)
    print(f"CAPTURE READY on {port_name}", flush=True)
    print("Hold a chord; press and tilt individual notes.\n", flush=True)

    began = time.monotonic()
    deadline = began + args.seconds
    last_bend: dict[int, tuple[int, float]] = {}
    active: dict[int, Gesture] = {}
    finished: list[Gesture] = []
    counts: Counter[tuple[int, str]] = Counter()
    last_printed_bend: dict[int, int] = {}

    while time.monotonic() < deadline:
        event = midi_in.get_message()
        if event is None:
            time.sleep(0.001)
            continue
        message, _delta = event
        status = message[0]
        if not 0x80 <= status < 0xF0:
            continue
        kind = status & 0xF0
        channel = (status & 0x0F) + 1
        elapsed = time.monotonic() - began

        if kind == 0x90 and message[2] > 0:
            prior = last_bend.get(channel)
            prior_value = prior[0] if prior and elapsed - prior[1] <= 0.100 else None
            gesture = Gesture(channel, message[1], message[2], elapsed, prior_value)
            active[channel] = gesture
            counts[channel, "note_on"] += 1
            print(
                f"{elapsed:7.3f}  ch {channel:2} NOTE ON  {message[1]:3} vel {message[2]:3}"
                + (f"  recent bend {prior_value:5} ({prior_value - CENTER:+5})" if prior_value is not None else ""),
                flush=True,
            )
        elif kind == 0x80 or (kind == 0x90 and message[2] == 0):
            counts[channel, "note_off"] += 1
            gesture = active.pop(channel, None)
            if gesture:
                finished.append(gesture)
            print(f"{elapsed:7.3f}  ch {channel:2} NOTE OFF {message[1]:3}", flush=True)
        elif kind == 0xE0:
            value = bend_value(message)
            last_bend[channel] = (value, elapsed)
            counts[channel, "pitch_bend"] += 1
            gesture = active.get(channel)
            first = gesture is not None and gesture.first_bend is None
            if gesture:
                gesture.add_bend(value)
            previous = last_printed_bend.get(channel)
            if first or previous is None or abs(value - previous) >= 128:
                marker = " LANDING" if first else ""
                print(f"{elapsed:7.3f}  ch {channel:2} BEND     {value:5} ({value - CENTER:+5}){marker}", flush=True)
                last_printed_bend[channel] = value
        elif kind == 0xD0:
            counts[channel, "pressure"] += 1
        elif kind == 0xB0 and message[1] == 74:
            counts[channel, "cc74"] += 1

    finished.extend(active.values())
    print("\nGESTURE SUMMARY")
    if not finished:
        print("No notes captured.")
    for gesture in finished:
        bend_range = "none"
        if gesture.first_bend is not None:
            bend_range = f"first={gesture.first_bend} ({gesture.first_bend - CENTER:+}), range={gesture.minimum}..{gesture.maximum}"
        print(
            f"ch {gesture.channel:2} note {gesture.note:3}: before={gesture.bend_before_note}, {bend_range}"
        )
    print("\nMESSAGE COUNTS")
    for (channel, kind), count in sorted(counts.items()):
        print(f"ch {channel:2} {kind:10}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
