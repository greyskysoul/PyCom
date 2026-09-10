"""Runtime flag for the reduced-compatibility UI.

On a bare Linux virtual console (``TERM=linux`` / init3) many fonts lack the
geometric-shape glyphs used by the modern UI (e.g. ``○``/``●`` fall back to a
diamond).  When :func:`set_ascii_ui` is enabled, marker glyphs switch to plain
ASCII (``[ ]`` / ``[x]``) which every terminal font can render.
"""

from __future__ import annotations

_ascii_ui = False


def set_ascii_ui(enabled: bool) -> None:
    """Enable/disable the ASCII-only UI glyph fallbacks."""
    global _ascii_ui
    _ascii_ui = bool(enabled)


def ascii_ui() -> bool:
    return _ascii_ui


def marker(selected: bool) -> str:
    """Checkbox/radio marker for the current compatibility mode."""
    if _ascii_ui:
        return "[x]" if selected else "[ ]"
    return "\u25cf" if selected else "\u25cb"  # ● / ○


# Directional arrows used by the dropdown and the collapsible section header.
_ARROWS_ASCII = {"down": "v", "up": "^", "right": ">"}
_ARROWS_UNICODE = {"down": "\u25bc", "up": "\u25b2", "right": "\u25b6"}  # ▼ / ▲ / ▶


def arrow(direction: str) -> str:
    """Arrow glyph for ``direction`` (``"down"``/``"up"``/``"right"``)."""
    table = _ARROWS_ASCII if _ascii_ui else _ARROWS_UNICODE
    return table.get(direction, table["down"])
