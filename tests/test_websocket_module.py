"""The panel's WebSocket commands live in websocket.py; panel.py only registers the panel."""

from __future__ import annotations

from pathlib import Path
import re

from custom_components.tariffy import websocket

FRONTEND = Path(__file__).parent.parent / "custom_components" / "tariffy" / "frontend"
PANEL_PY = (Path(__file__).parent.parent / "custom_components" / "tariffy" / "panel.py").read_text(encoding="utf-8")


def test_every_command_is_registered_once():
    names = [command._ws_command for command in websocket.COMMANDS]
    assert len(names) == len(set(names)), names
    assert all(name.startswith("tariffy/") for name in names)


def test_panel_py_defines_no_command():
    assert "websocket_command" not in PANEL_PY


def test_every_command_the_panel_sends_exists():
    sent = set()
    for path in FRONTEND.glob("*.js"):
        sent |= set(re.findall(r'type: "(tariffy/[a-z_]+)"', path.read_text(encoding="utf-8")))
    known = {command._ws_command for command in websocket.COMMANDS}
    assert sent, "the panel sends no command?"
    assert sent - known == set(), f"the panel sends unknown commands: {sent - known}"
