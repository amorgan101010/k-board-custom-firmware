#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$project_dir/.venv/bin/python" ]; then
    python3 -m venv "$project_dir/.venv"
fi
if ! "$project_dir/.venv/bin/python" -c 'import mido, rtmidi, PySide6' >/dev/null 2>&1; then
    "$project_dir/.venv/bin/python" -m pip install -r "$project_dir/requirements.txt"
fi
exec "$project_dir/.venv/bin/python" "$project_dir/kboard_editor.py"
