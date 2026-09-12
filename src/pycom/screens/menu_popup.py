"""Floating main-menu popup.

A pushed Screen with a translucent background keeps the terminal and status
bar behind it visible (dimmed); the box is anchored at the bottom-left, next
to the 菜单 button.  Arrow keys move the highlight, Enter / Space or the
leading letter runs an item, Esc or clicking outside closes it.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from functools import partial

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.dom import DOMNode
from textual.events import Click, Key
from textual.screen import Screen
from textual.widgets import Button

from pycom.i18n import tr

# 主菜单条目（key, 中文标签）。key 为功能热键字母（也是左侧显示的快捷字母）。
MAIN_MENU = [
    ("p", "串口参数"),
    ("d", "数据传输"),
    ("c", "清屏"),
    ("h", "HEX模式 开/关"),
    ("l", "会话捕获 开/关"),
    ("o", "设置"),
    ("y", "语言"),
    ("a", "关于"),
    ("x", "退出"),
]


class MainMenuScreen(Screen):
    """Bottom-left floating menu listing the main functions.

    Push with :meth:`~textual.app.App.push_screen`; the screen pops itself
    when an item is picked or it is closed.  ``on_pick`` receives the
    single-letter action code (see ``menu_action``).
    """

    def __init__(self, on_pick: Callable[[str], None]) -> None:
        super().__init__()
        self._rows = [(key, tr(label)) for key, label in MAIN_MENU]
        self._on_pick = on_pick

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-popup"):
            for key, label in self._rows:
                yield Button(
                    f"  {key}   {label}",
                    id=f"menu-{key}",
                    classes="menu-item",
                    compact=True,
                )

    def on_mount(self) -> None:
        self.query_one(".menu-item", Button).focus()

    # -- helpers -----------------------------------------------------------------
    def _codes(self) -> set[str]:
        return {key for key, _ in self._rows}

    def _close(self) -> None:
        with contextlib.suppress(Exception):
            self.dismiss()

    def _pick(self, code: str) -> None:
        self._close()
        # Run the action after this popup is gone (avoid a 0.0 timer — Textual 8
        # crashes dividing by a zero delay).
        self.app.set_timer(0.05, partial(self._on_pick, code))  # type: ignore[attr-defined]

    def _move(self, down: bool) -> None:
        items = list(self.query(".menu-item"))
        if not items:
            return
        idx = next((i for i, w in enumerate(items) if w.has_focus), -1)
        if idx < 0:
            items[0].focus()
            return
        items[(idx + (1 if down else -1)) % len(items)].focus()

    # -- input -------------------------------------------------------------------
    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            event.stop()
            self._close()
            return
        if event.key in ("up", "down"):
            event.stop()
            self._move(event.key == "down")
            return
        if event.key == "enter":
            # 直接激活当前高亮项：不依赖 Button 绑定的 enter 键（某些终端/
            # 场景下 Button 的 enter 绑定可能未被触发），保证回车必定生效。
            event.stop()
            code = self._focused_code()
            if code in self._codes():
                self._pick(code)
            return
        char = (event.character or "").lower()
        if not char:
            return
        for code, _ in self._rows:
            if code.startswith(char):
                event.stop()
                self._pick(code)
                return
        # Consume everything else so it never leaks to the terminal while open.
        event.stop()

    def _focused_code(self) -> str:
        """The action code of the currently highlighted menu item ('' if none)."""
        for item in self.query(".menu-item"):
            if item.has_focus:
                return (item.id or "").removeprefix("menu-")
        return ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        code = (event.button.id or "").removeprefix("menu-")
        if code in self._codes():
            event.stop()
            self._pick(code)

    def on_click(self, event: Click) -> None:
        event.stop()
        # A click that lands on the menu box itself (or one of its children)
        # is handled above; only clicks on the surrounding overlay close it.
        box = self.query_one("#menu-popup")
        widget: DOMNode | None = event.widget
        while widget is not None and widget is not self:
            if widget is box:
                return
            widget = widget.parent
        self._close()
