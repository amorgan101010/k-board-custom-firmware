#!/usr/bin/env python3
"""Native Linux K-Board editor."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from kboard_protocol import CURVES, DEFAULT_PROFILE, Device, build_sysex, validate_profile


STYLE = """
QWidget { color: #e9eef7; font: 13px "Noto Sans"; }
QMainWindow, QWidget#root { background: #0b1019; }
QScrollArea { background: #0b1019; border: none; }
QLabel, QCheckBox { background: transparent; }
QFrame#card { background: #121a27; border: 1px solid #253247; border-radius: 14px; }
QFrame#deviceCard, QFrame#actionBar { background: #111923; border: 1px solid #253247; border-radius: 11px; }
QFrame#rule { background: #263346; max-height: 1px; }
QLabel#eyebrow { color: #64d8bb; font-size: 10px; font-weight: 750; letter-spacing: 2px; }
QLabel#title { color: #f7f9fd; font-size: 29px; font-weight: 750; }
QLabel#profile, QLabel#cardHint, QLabel#modeCopy { color: #8f9db1; font-size: 12px; }
QLabel#cardTitle { color: #f5f7fb; font-size: 17px; font-weight: 700; }
QLabel#modeCopy { padding: 1px 0 5px 0; }
QLabel#status { font-weight: 650; }
QLabel#status[state="checking"] { color: #f0c56e; }
QLabel#status[state="connected"] { color: #72ddbf; }
QLabel#status[state="error"] { color: #ff8f9c; }
QLabel#activity { color: #9aa9bd; font-size: 12px; }
QSpinBox, QComboBox { background: #1a2637; color: #f1f5fb; border: 1px solid #34465f;
                     border-radius: 7px; min-height: 30px; padding: 2px 9px; }
QSpinBox:hover, QComboBox:hover { border-color: #55718f; }
QSpinBox:focus, QComboBox:focus { border-color: #64d8bb; }
QSpinBox:disabled, QComboBox:disabled { background: #131c29; color: #58667a; border-color: #222e40; }
QComboBox QAbstractItemView { background: #1a2637; color: #f1f5fb; selection-background-color: #274c50; }
QSlider::groove:horizontal { height: 4px; background: #27364b; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #64d8bb; border-radius: 2px; }
QSlider::handle:horizontal { background: #eafbf6; border: 2px solid #64d8bb; width: 14px;
                             margin: -6px 0; border-radius: 8px; }
QCheckBox { spacing: 10px; color: #f2f6fb; font-weight: 650; }
QCheckBox::indicator { width: 36px; height: 20px; border: 1px solid #42536a;
                       border-radius: 10px; background: #202c3d; }
QCheckBox::indicator:checked { background: #64d8bb; border-color: #64d8bb; }
QPushButton { background: #1b293b; color: #dce5f1; border: 1px solid #354961; border-radius: 8px;
              padding: 9px 15px; font-weight: 650; }
QPushButton:hover { background: #24364c; border-color: #526b87; }
QPushButton:pressed { background: #152131; }
QPushButton:disabled { background: #121a26; color: #566377; border-color: #222d3d; }
QPushButton#quiet { background: transparent; }
QPushButton#send { background: #64d8bb; border-color: #64d8bb; color: #0a2924; padding: 11px 20px; }
QPushButton#send:hover { background: #82e5ca; border-color: #82e5ca; }
QMenuBar { background: #0b1019; color: #aab6c7; padding: 3px 9px; }
QMenu { background: #172233; color: #e8eef9; border: 1px solid #33445c; padding: 5px; }
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 5px; }
QMenu::item:selected, QMenuBar::item:selected { background: #25364c; color: white; }
"""


def spin(lo: int, hi: int, special: str | None = None) -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(lo, hi)
    if special:
        widget.setSpecialValueText(special)
    return widget


class ChannelSpin(QSpinBox):
    """Show MIDI channels 1–16 while storing the zero-based protocol value."""

    def textFromValue(self, value):
        return str(value + 1)

    def valueFromText(self, text):
        return int(text) - 1


class TiltAmountSpin(QSpinBox):
    """Display a percentage while storing an exact 0–64 firmware width."""

    def textFromValue(self, value):
        return str(round(value * 100 / 64))

    def valueFromText(self, text):
        return round(int(text.strip().rstrip("%")) * 64 / 100)


class ValueCombo(QComboBox):
    """A combo box whose labels are separate from its protocol values."""

    def set_options(self, options):
        current = self.value() if self.count() else None
        self.blockSignals(True)
        self.clear()
        for label, value in options:
            self.addItem(label, value)
        index = self.findData(current)
        self.setCurrentIndex(max(index, 0))
        self.blockSignals(False)

    def value(self):
        return self.currentData()

    def set_value(self, value):
        index = self.findData(value)
        if index < 0:
            raise ValueError(f"value {value} is unavailable")
        self.setCurrentIndex(index)


class SliderField(QWidget):
    """A quick slider with an exact numeric value alongside it."""

    def __init__(self, lo: int, hi: int, percent_of_64: bool = False):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.spin = TiltAmountSpin() if percent_of_64 else spin(lo, hi)
        self.spin.setRange(lo, hi)
        if percent_of_64:
            self.spin.setSuffix("%")
        self.spin.setFixedWidth(72)
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin)


class Editor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("K-Board Linux Editor")
        self.setMinimumSize(860, 700)
        self.resize(930, 1030)
        self.path: Path | None = None
        self.dirty = False
        self.loading = False
        self.controls: dict[str, QWidget] = {}

        self._menus()
        root = QWidget()
        root.setObjectName("root")
        root.setMinimumHeight(960)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 21, 28, 24)
        outer.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(18)
        brand = QVBoxLayout()
        brand.setSpacing(1)
        eyebrow = QLabel("NATIVE LINUX EDITOR")
        eyebrow.setObjectName("eyebrow")
        brand.addWidget(eyebrow)
        title = QLabel("K-Board")
        title.setObjectName("title")
        brand.addWidget(title)
        self.profile_name = QLabel("Untitled profile · local settings")
        self.profile_name.setObjectName("profile")
        brand.addWidget(self.profile_name)
        header.addLayout(brand, 1)

        device_card = QFrame()
        device_card.setObjectName("deviceCard")
        device_layout = QHBoxLayout(device_card)
        device_layout.setContentsMargins(14, 10, 11, 10)
        device_layout.setSpacing(10)
        self.status = QLabel("●  Checking for K-Board")
        self.status.setObjectName("status")
        self.status.setProperty("state", "checking")
        device_layout.addWidget(self.status)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("quiet")
        refresh.clicked.connect(self.probe)
        device_layout.addWidget(refresh)
        header.addWidget(device_card, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)

        performance, performance_stack = self._card_shell(
            "Performance", "Choose how notes and per-note expression leave the keyboard."
        )
        grid.addWidget(performance, 0, 0, 1, 2)
        performance_columns = QHBoxLayout()
        performance_columns.setContentsMargins(0, 0, 0, 0)
        performance_columns.setSpacing(28)
        mode_form = self._form()
        expression_form = self._form()
        performance_columns.addLayout(mode_form, 1)
        performance_columns.addLayout(expression_form, 1)
        performance_stack.addLayout(performance_columns)

        self._add_check(mode_form, "mpe_active", "Enable MPE")
        self.mode_copy = QLabel()
        self.mode_copy.setObjectName("modeCopy")
        self.mode_copy.setWordWrap(True)
        mode_form.addRow(self.mode_copy)
        self._add_spin(mode_form, "mpe_member_channels", "MPE member channels", 1, 15)
        channel = ChannelSpin()
        channel.setRange(0, 15)
        mode_form.addRow("MIDI channel", channel)
        self.controls["midi_channel"] = channel
        channel.valueChanged.connect(self._changed)

        pressure = ValueCombo()
        pressure.set_options([("Channel pressure", -1), *[(f"CC {n}", n) for n in range(128)]])
        expression_form.addRow("Pressure output", pressure)
        self.controls["pressure_cc"] = pressure
        pressure.currentIndexChanged.connect(self._changed)

        self.tilt_output = ValueCombo()
        self.tilt_output.set_options([("Pitch bend", -1), *[(f"CC {n}", n) for n in range(128)]])
        self._tilt_mpe_mode = False
        expression_form.addRow("Key tilt output", self.tilt_output)
        self.controls["tilt_cc"] = self.tilt_output
        self.tilt_output.currentIndexChanged.connect(self._changed)

        pad_heading = QLabel("Bend Pad")
        pad_heading.setObjectName("cardTitle")
        mode_form.addRow(pad_heading)
        self._add_spin(mode_form, "bend_range_pad", "Pad input span", 1, 12)
        self.controls["bend_range_pad"].setSuffix(" semitones")
        self.controls["bend_range_pad"].setToolTip(
            "In custom MPE mode, use 12 for full pad sensor resolution; Relative pad amount sets the audible bend range."
        )
        self._add_spin(expression_form, "bend_range_tilt", "Key tilt bend range", 1, 12)
        self.controls["bend_range_tilt"].setSuffix(" semitones")
        self.controls["bend_range_tilt"].setToolTip(
            "Stock K-Board setting. KMI says this has no effect in MPE mode; set the per-note range in the receiving instrument."
        )
        self._add_slider(expression_form, "relative_tilt_amount", "Relative tilt amount", 0, 64,
                         percent_of_64=True)
        self.controls["relative_tilt_amount"].setToolTip(
            "Custom relative tilt firmware: 25% uses one quarter of the receiver's configured pitch-bend range."
        )
        self._add_spin(expression_form, "relative_tilt_deadzone", "Landing deadzone", 0, 12)
        self.controls["relative_tilt_deadzone"].setSuffix(" steps")
        self.controls["relative_tilt_deadzone"].setToolTip(
            "Custom relative tilt firmware: changes this many tilt sensor steps from the landing point stay centered."
        )
        self._add_slider(mode_form, "relative_pad_amount", "Relative pad amount", 0, 64,
                         percent_of_64=True)
        self.controls["relative_pad_amount"].setToolTip(
            "Custom firmware: scales Bend Pad movement from its first touch, within the receiver's per-note bend range."
        )
        self._add_spin(mode_form, "relative_pad_deadzone", "Pad landing deadzone", 0, 12)
        self.controls["relative_pad_deadzone"].setSuffix(" steps")
        self.controls["relative_pad_deadzone"].setToolTip(
            "Custom firmware: small Bend Pad movements after first touch stay centered."
        )

        response, response_form = self._card(
            "Playing feel", "Tune how quickly the keys react to touch and movement."
        )
        grid.addWidget(response, 1, 0)
        self._add_slider(response_form, "velocity_sensitivity", "Velocity sensitivity", 60, 254)
        self._add_slider(response_form, "pressure_sensitivity", "Pressure sensitivity", 60, 254)
        self._add_slider(response_form, "tilt_sensitivity", "Tilt sensitivity", 0, 70)
        curve = QComboBox()
        curve.addItems(CURVES)
        response_form.addRow("Velocity curve", curve)
        self.controls["velocity_curve"] = curve
        curve.currentIndexChanged.connect(self._changed)
        self._add_spin(response_form, "on_thresh", "Note-on threshold", 1, 127)

        extras, extras_form = self._card(
            "Hardware button behavior",
            "Optional values sent when the keyboard's Press or Tilt button disables that dimension.",
        )
        grid.addWidget(extras, 1, 1)
        self._add_spin(extras_form, "pressure_disabled_return", "Pressure disabled", -1, 127, "Do not send a value")
        self._add_spin(extras_form, "tilt_disabled_return", "Tilt disabled", -1, 127, "Do not send a value")

        outer.addStretch()
        action_bar = QFrame()
        action_bar.setObjectName("actionBar")
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(16, 12, 12, 12)
        actions.setSpacing(11)
        self.activity = QLabel("Changes stay local until you send them.")
        self.activity.setObjectName("activity")
        actions.addWidget(self.activity, 1)
        save = QPushButton("Save profile")
        save.clicked.connect(self.save)
        actions.addWidget(save)
        self.read_button = QPushButton("Read from K-Board")
        self.read_button.setEnabled(False)
        self.read_button.clicked.connect(self.read_from_device)
        actions.addWidget(self.read_button)
        self.send_button = QPushButton("Send to K-Board")
        self.send_button.setObjectName("send")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self.send)
        actions.addWidget(self.send_button)
        outer.addWidget(action_bar)

        self._load_profile(DEFAULT_PROFILE, None)
        QTimer.singleShot(0, self.probe)

    @staticmethod
    def _card_shell(title: str, hint: str):
        card = QFrame()
        card.setObjectName("card")
        stack = QVBoxLayout(card)
        stack.setContentsMargins(18, 16, 18, 17)
        stack.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        stack.addWidget(heading)
        copy = QLabel(hint)
        copy.setObjectName("cardHint")
        copy.setWordWrap(True)
        stack.addWidget(copy)
        stack.addSpacing(7)
        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFixedHeight(1)
        stack.addWidget(rule)
        stack.addSpacing(6)
        return card, stack

    @staticmethod
    def _form():
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        return form

    @classmethod
    def _card(cls, title: str, hint: str):
        card, stack = cls._card_shell(title, hint)
        form = cls._form()
        stack.addLayout(form)
        return card, form

    def _add_spin(self, form, key, label, lo, hi, special=None):
        widget = spin(lo, hi, special)
        form.addRow(label, widget)
        self.controls[key] = widget
        widget.valueChanged.connect(self._changed)

    def _add_slider(self, form, key, label, lo, hi, percent_of_64=False):
        field = SliderField(lo, hi, percent_of_64)
        form.addRow(label, field)
        self.controls[key] = field.spin
        field.spin.valueChanged.connect(self._changed)

    def _add_check(self, form, key, label):
        widget = QCheckBox(label)
        widget.setMinimumWidth(150)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addRow(widget)
        self.controls[key] = widget
        widget.toggled.connect(self._changed)

    def _menus(self):
        menu = self.menuBar().addMenu("File")
        for name, shortcut, handler in (
            ("New default profile", QKeySequence.StandardKey.New, self.new),
            ("Open profile…", QKeySequence.StandardKey.Open, self.open),
            ("Save profile", QKeySequence.StandardKey.Save, self.save),
            ("Save profile as…", QKeySequence.StandardKey.SaveAs, self.save_as),
            ("Export SysEx…", None, self.export_sysex),
        ):
            action = QAction(name, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(handler)
            menu.addAction(action)

    @staticmethod
    def _set_state(widget, state):
        widget.setProperty("state", state)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _changed(self, *_):
        if self.loading:
            return
        mpe = self.controls["mpe_active"].isChecked()
        self.controls["mpe_member_channels"].setEnabled(mpe)
        self.controls["midi_channel"].setEnabled(not mpe)
        self.controls["pressure_cc"].setEnabled(not mpe)
        self.controls["bend_range_tilt"].setEnabled(not mpe)
        self.controls["relative_tilt_amount"].setEnabled(mpe)
        self.controls["relative_tilt_deadzone"].setEnabled(mpe)
        self.controls["relative_pad_amount"].setEnabled(mpe)
        self.controls["relative_pad_deadzone"].setEnabled(mpe)
        self.mode_copy.setText(
            "Each held note gets its own MIDI channel, pressure, and tilt."
            if mpe else
            "All notes share one MIDI channel; pressure and tilt use the assignments below."
        )
        if mpe != self._tilt_mpe_mode:
            tilt_value = self.tilt_output.value()
            tilt_options = ([('Pitch bend', -1), ('CC 74', 74)] if mpe else
                            [('Pitch bend', -1), *[(f'CC {n}', n) for n in range(128)]])
            self.tilt_output.set_options(tilt_options)
            if any(value == tilt_value for _, value in tilt_options):
                self.tilt_output.set_value(tilt_value)
            self._tilt_mpe_mode = mpe
        if mpe:
            self.controls["pressure_cc"].set_value(-1)
        self.dirty = True
        self.activity.setText("Local profile has unsaved changes. Nothing has been sent.")
        self._title()

    def _title(self):
        name = self.path.name if self.path else "Untitled profile"
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} — K-Board Linux Editor")
        self.profile_name.setText(f"{name}{' · modified' if self.dirty else ' · local settings'}")

    def profile(self) -> dict:
        result = {}
        for key, widget in self.controls.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, ValueCombo):
                value = widget.value()
            elif isinstance(widget, QComboBox):
                value = widget.currentText()
            else:
                value = widget.value()
            result[key] = value
        return validate_profile(result)

    def _load_profile(self, profile: dict, path: Path | None):
        profile = validate_profile(profile)
        self.loading = True
        mpe = profile["mpe_active"]
        self.tilt_output.set_options(
            [("Pitch bend", -1), ("CC 74", 74)] if mpe else
            [("Pitch bend", -1), *[(f"CC {n}", n) for n in range(128)]]
        )
        self._tilt_mpe_mode = mpe
        for key, value in profile.items():
            widget = self.controls[key]
            if isinstance(widget, QCheckBox):
                widget.setChecked(value)
            elif isinstance(widget, ValueCombo):
                widget.set_value(value)
            elif isinstance(widget, QComboBox):
                widget.setCurrentText(value)
            else:
                widget.setValue(value)
        self.loading = False
        self.path = path
        self.dirty = False
        self._changed()
        self.dirty = False
        self.activity.setText("Changes stay local until you send them.")
        self._title()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved profile", "Discard unsaved changes to this local profile?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def new(self):
        if self._confirm_discard():
            self._load_profile(DEFAULT_PROFILE, None)

    def open(self):
        if not self._confirm_discard():
            return
        name, _ = QFileDialog.getOpenFileName(
            self, "Open K-Board profile", str(Path.home()), "K-Board profiles (*.json)",
        )
        if not name:
            return
        try:
            data = json.loads(Path(name).read_text())
            if not isinstance(data, dict) or data.get("format") != "kboard-linux-profile-v1":
                raise ValueError("unrecognized profile format")
            self._load_profile(data["settings"], Path(name))
            self.activity.setText(f"Opened {Path(name).name}. Nothing has been sent.")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Could not open profile", str(exc))

    def save(self):
        if not self.path:
            return self.save_as()
        try:
            data = {"format": "kboard-linux-profile-v1", "settings": self.profile()}
            self.path.write_text(json.dumps(data, indent=2) + "\n")
            self.dirty = False
            self._title()
            self.activity.setText(f"Saved {self.path.name}. Nothing has been sent.")
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save profile", str(exc))
            return False

    def save_as(self):
        name, _ = QFileDialog.getSaveFileName(
            self, "Save K-Board profile", str(self.path or Path.home() / "kboard-profile.json"),
            "K-Board profiles (*.json)",
        )
        if not name:
            return False
        previous = self.path
        self.path = Path(name)
        if self.save():
            return True
        self.path = previous
        self._title()
        return False

    def export_sysex(self):
        name, _ = QFileDialog.getSaveFileName(
            self, "Export preset SysEx", str(Path.home() / "kboard-preset.syx"), "SysEx files (*.syx)",
        )
        if not name:
            return
        try:
            Path(name).write_bytes(build_sysex(self.profile()))
            self.activity.setText(f"Exported {Path(name).name}.")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not export SysEx", str(exc))

    def probe(self):
        self.status.setText("●  Checking for K-Board")
        self._set_state(self.status, "checking")
        self.send_button.setEnabled(False)
        self.read_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            with Device() as device:
                version = device.identity()
                profile = None if self.dirty or self.path else device.read_settings()
            if profile is not None:
                self._load_profile(profile, None)
                self.activity.setText("Loaded the current settings from K-Board.")
            self.status.setText(f"●  Connected · Firmware {version}")
            self.status.setToolTip("")
            self._set_state(self.status, "connected")
            self.send_button.setEnabled(True)
            self.read_button.setEnabled(True)
        except Exception as exc:
            self.status.setText("●  K-Board unavailable")
            self.status.setToolTip(str(exc))
            self._set_state(self.status, "error")
            self.send_button.setEnabled(False)
            self.read_button.setEnabled(False)
        finally:
            QApplication.restoreOverrideCursor()

    def send(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            profile = self.profile()
            with Device() as device:
                version = device.identity()
                device.send_preset(profile)
                time.sleep(1.0)  # the board rewrites a flash page before it can answer
                stored = device.read_settings()
            if stored != profile:
                raise RuntimeError("K-Board did not store the preset; reading it back returned different settings")
            self.status.setText(f"●  Connected · Firmware {version}")
            self._set_state(self.status, "connected")
            self.activity.setText("Profile sent to K-Board and verified by reading it back.")
        except Exception as exc:
            self.send_button.setEnabled(False)
            QMessageBox.critical(self, "Could not send preset", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def read_from_device(self):
        if not self._confirm_discard():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            with Device() as device:
                version = device.identity()
                profile = device.read_settings()
            self._load_profile(profile, None)
            self.status.setText(f"●  Connected · Firmware {version}")
            self._set_state(self.status, "connected")
            self.activity.setText("Loaded the current settings from K-Board.")
        except Exception as exc:
            QMessageBox.critical(self, "Could not read K-Board settings", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def closeEvent(self, event):
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("K-Board Linux Editor")
    app.setStyleSheet(STYLE)
    window = Editor()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
