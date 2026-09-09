"""File / directory picker with an inline parent ("..") entry."""

from __future__ import annotations

import contextlib
import os
import string

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from pycom.i18n import tr
from pycom.screens.base import ResponsiveCompact

_UP = "__up__"
_DRIVE_PREFIX = "drive:"
# Windows “我的电脑”视图：不属于任何真实路径，只列出盘符。
_MY_COMPUTER = ""


def _list_drives() -> list[str]:
    """Windows drive letters that currently exist (e.g. ``"C:\\"``)."""
    drives: list[str] = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        try:
            if os.path.exists(drive):
                drives.append(drive)
        except OSError:
            continue
    return drives


def _is_drive_root(path: str) -> bool:
    drive, tail = os.path.splitdrive(path)
    return bool(drive) and tail in ("\\", "/")


class PathPicker(ResponsiveCompact):
    """Navigate the filesystem; dismisses with the selected path (str).

    * ``pick_files=True``  -> Enter on a file selects it
    * ``pick_files=False`` -> "选择当前目录" button selects the shown folder

    The list shows ``..`` at the top when the current folder has a parent; at
    a filesystem root ``..`` is omitted.  On Windows the ``/`` button (and
    ``..`` on a drive root) opens the "My Computer" view which lists the drive
    letters only; inside a drive its root shows the normal folder/file listing
    (no drive entries).  The editable address bar (``#picker-path``) jumps to a
    typed path on Enter; ``home`` / ``/`` quick-jump to the user's home
    directory and the top level.  Directories sort before files.
    """

    ROOT_ID = "picker-box"
    MIN_WIDTH = 96
    MIN_HEIGHT = 36

    def __init__(self, start: str = "", pick_files: bool = True) -> None:
        super().__init__()
        self._start = start or os.getcwd()
        self._pick_files = pick_files
        self._cur: str = self._start
        # 浏览历史（浏览器风格）：_index 指向 _history 当前项，后退/前进移动它
        self._history: list[str] = []
        self._index = -1

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(tr("选择文件 / 目录"), id="picker-title")
            with Horizontal(id="picker-nav"):
                yield Button("←", id="back", compact=True)
                yield Button("→", id="forward", compact=True)
                yield Button("home", id="home", compact=True)
                yield Button("/", id="root", compact=True)
            yield Input("", id="picker-path")
            with Vertical(id="picker-tree"):
                yield DataTable(id="picker-table")
            with Horizontal(id="picker-buttons"):
                if not self._pick_files:
                    yield Button(tr("选择当前目录"), id="choose", compact=True)
                yield Button(tr("取消"), id="cancel", compact=True)

    def on_mount(self) -> None:
        super().on_mount()
        table = self.query_one("#picker-table", DataTable)
        table.cursor_type = "row"
        table.add_column("")
        self._load(self._cur)

    # -- helpers ----------------------------------------------------------------------
    def _load(self, path: str, record: bool = True) -> None:
        if path == _MY_COMPUTER:
            target = _MY_COMPUTER
        else:
            if not os.path.isdir(path):
                path = os.path.dirname(path) or path
            target = os.path.abspath(path)
        if record:
            # 新导航：截断前进历史，压入当前项
            del self._history[self._index + 1 :]
            self._history.append(target)
            self._index = len(self._history) - 1
        self._cur = target
        self._show()
        self._update_nav_buttons()

    def _show(self) -> None:
        if self._cur == _MY_COMPUTER:
            # Windows “我的电脑”：只显示盘符列表，无 “..”
            self.query_one("#picker-path", Input).value = ""
            table = self.query_one("#picker-table", DataTable)
            table.clear()
            for drive in _list_drives():
                table.add_row(drive, key=_DRIVE_PREFIX + drive)
            table.focus()
            return
        self.query_one("#picker-path", Input).value = self._cur
        table = self.query_one("#picker-table", DataTable)
        table.clear()
        parent = os.path.dirname(self._cur)
        # 普通目录显示 “..”；Windows 盘符根目录也显示 “..” 用于回到“我的电脑”
        if parent != self._cur or (os.name == "nt" and _is_drive_root(self._cur)):
            table.add_row("..", key=_UP)
        dirs: list[str] = []
        files: list[str] = []
        try:
            entries = os.listdir(self._cur)
        except OSError:
            entries = []
        for name in sorted(entries, key=str.lower):
            full = os.path.join(self._cur, name)
            try:
                (dirs if os.path.isdir(full) else files).append(name)
            except OSError:
                continue
        for name in dirs:
            table.add_row(f"{name}/", key=name)
        for name in files:
            table.add_row(name, key=name)
        table.focus()

    def _update_nav_buttons(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#back", Button).disabled = self._index <= 0
            self.query_one("#forward", Button).disabled = self._index >= len(self._history) - 1

    def _back(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._load(self._history[self._index], record=False)

    def _forward(self) -> None:
        if self._index < len(self._history) - 1:
            self._index += 1
            self._load(self._history[self._index], record=False)

    # -- events --------------------------------------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        if key == _UP:
            # 盘符根目录的 “..” 回到“我的电脑”；否则返回上一级目录
            if os.name == "nt" and _is_drive_root(self._cur):
                self._load(_MY_COMPUTER)
            else:
                self._load(os.path.dirname(self._cur))
            return
        if key.startswith(_DRIVE_PREFIX):
            self._load(key[len(_DRIVE_PREFIX) :])
            return
        path = os.path.join(self._cur, key)
        if os.path.isdir(path):
            self._load(path)
        elif self._pick_files:
            self.dismiss(path)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "back":
            self._back()
        elif bid == "forward":
            self._forward()
        elif bid == "home":
            self._load(os.path.expanduser("~"))
        elif bid == "root":
            # Windows 的“根”是“我的电脑”（盘符列表），其余平台是文件系统根
            self._load(_MY_COMPUTER if os.name == "nt" else os.path.abspath(os.sep))
        elif bid == "choose" and not self._pick_files:
            self.dismiss(self._cur)
        elif bid == "cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """地址栏输入路径并回车：跳转到该目录。"""
        path = event.value.strip()
        if path and os.path.isdir(path):
            self._load(path)
        else:
            self.query_one("#picker-path", Input).value = self._cur
