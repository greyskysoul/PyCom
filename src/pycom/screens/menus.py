"""Reusable keyed menu-list modal (arrow keys + letter hotkeys + Enter)."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import Button, Label, Static

from pycom.i18n import tr
from pycom.screens.base import ResponsiveCompact


class KeyedMenu(ResponsiveCompact):
    """A boxed list of labelled rows, each selectable by arrow+Enter or by
    pressing its leading letter.

    Args:
        title: heading text.
        rows: iterable of ``(key, label)`` — ``key`` is the hotkey letter.
        on_pick: callable ``on_pick(key)`` run after the modal dismisses.
        footer: optional hint line under the list.
        show_keys: render the leading ``key`` column (default True).
    """

    ROOT_ID = "help-box"
    MIN_WIDTH = 50
    MIN_HEIGHT = 30

    def __init__(
        self,
        title: str,
        rows,
        on_pick: Callable[[str], None],
        footer: str = "",
        show_keys: bool = True,
    ) -> None:
        super().__init__()
        self._title = title
        self._rows = list(rows)
        self._on_pick = on_pick
        self._footer = footer
        self._show_keys = show_keys

    def _codes(self) -> set[str]:
        return {key for key, _ in self._rows}

    def _match_key(self, char: str) -> str | None:
        """Map a pressed single character onto a row key (a row key may be a
        single letter like ``p`` or a short tag like ``en`` — first char wins)."""
        for key in self._codes():
            if key == char or key.startswith(char):
                return key
        return None

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(self._title, id="help-title")
            with VerticalScroll(id="help-body"):
                for key, label in self._rows:
                    text = f"  {key}   {label}" if self._show_keys else f"  {label}"
                    yield Button(text, id=f"menu-{key}", classes="menu-item", compact=True)
            yield Label(self._footer, id="help-footer")
            yield Button(tr("返回"), id="menu-back", compact=True)

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one(".menu-item", Button).focus()

    def _run(self, code: str) -> None:
        self.dismiss(None)
        # Run the action after this modal is gone (avoid 0.0 timer — Textual 8
        # crashes dividing by a zero delay).
        self.app.set_timer(0.05, partial(self._on_pick, code))  # type: ignore[attr-defined]

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            return  # handled by binding
        if event.key in ("up", "down", "left", "right"):
            super().on_key(event)
            return
        char = (event.character or "").lower()
        if not char:
            return
        key = self._match_key(char)
        if key is not None:
            event.stop()
            self._run(key)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "menu-back":
            event.stop()
            self.dismiss(None)
            return
        if not button_id.startswith("menu-"):
            return
        key = button_id[len("menu-") :]
        if key in self._codes():
            event.stop()
            self._run(key)
