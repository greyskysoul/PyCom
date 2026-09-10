"""PyCom application — minicom-style serial terminal with YMODEM transfer."""

from __future__ import annotations

import argparse
import codecs
import contextlib
import os
import queue
import shutil
import sys
import threading
import time
from copy import deepcopy
from importlib import resources as _resources
from typing import Any, ClassVar

from rich.style import Style
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical
from textual.events import Key, Paste
from textual.theme import Theme
from textual.widgets import Button, TextArea

from pycom import APP_NAME, __version__
from pycom.config import AppConfig, ConnectionSettings, load_config, save_config
from pycom.i18n import detect_system_language, get_language, set_language, tr
from pycom.keys import (
    KeyMapper,
    decode_escapes,
    format_hex_lines,
    hex_bytes_per_line,
    parse_hex_line,
)
from pycom.screens.about import AboutScreen
from pycom.screens.base import ConfirmDialog
from pycom.screens.connection import ConnectionScreen
from pycom.screens.language import LanguageScreen
from pycom.screens.menu_popup import MainMenuScreen
from pycom.screens.options import OptionsScreen
from pycom.screens.transfer import VALID_PROTOCOLS, ProtocolPicker, RecvScreen, SendScreen
from pycom.screens.transfermenu import TransferMenuScreen
from pycom.serialio import SerialManager
from pycom.termdisplay.view import HexAsciiPane, StatusBar, TerminalView
from pycom.termdisplay.vt import TerminalModel
from pycom.xfer.ymodem import YModemEngine
from pycom.xfer.zmodem import ZModemEngine

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

# Vertical-bar cursor shown in the 16-hex editor while it is not focused.
_HEX_BAR = "\u2502"
_HEX_BAR_STYLE = Style(color="#9aa7b8")

# 主窗口最小可用终端尺寸：低于该值时界面“完全无法使用”，启动/运行时会打印
# 提示并直接退出（而不是渲染一个残破、无法操作的界面）。
MIN_TERMINAL_COLS = 20
MIN_TERMINAL_ROWS = 5
# app.run() 因窗口过小退出时的返回值，供 main() 识别并打印提示。
_EXIT_TOO_SMALL = "too-small"


def _too_small_message(cols: int, rows: int) -> str:
    return tr(
        "PyCom: 终端窗口太小（{cols} 列 × {rows} 行），界面无法正常使用。\n"
        "请将窗口放大到至少 {mincols} 列 × {minrows} 行后重新运行。\n",
        cols=cols,
        rows=rows,
        mincols=MIN_TERMINAL_COLS,
        minrows=MIN_TERMINAL_ROWS,
    )


def _linear_to_location(text: str, idx: int) -> tuple[int, int]:
    """Convert a linear character index into a (row, column) location."""
    lines = text.split("\n")
    for row, line in enumerate(lines):
        if idx <= len(line):
            return (row, idx)
        idx -= len(line) + 1
    last = len(lines) - 1
    return (last, len(lines[last]))


def _location_after_hex(text: str, hex_count: int) -> tuple[int, int]:
    """Location just after the ``hex_count``-th hex digit of ``text``."""
    if hex_count <= 0:
        return (0, 0)
    seen = 0
    for i, ch in enumerate(text):
        if ch in _HEX_DIGITS:
            seen += 1
            if seen == hex_count:
                return _linear_to_location(text, i + 1)
    return _linear_to_location(text, len(text))


class _HexArea(TextArea):
    """Multi-line 16-hex editor used by the HEX mode input bar.

    * only hex digits are kept (anything else is stripped while typing),
    * a single space is inserted after every byte,
    * each line holds 4/8/16 bytes depending on the current width.
    """

    BINDINGS: ClassVar[list] = [
        b
        for b in TextArea.BINDINGS
        if not (isinstance(b, Binding) and "ctrl+a" in b.key.split(","))
    ] + [
        # 在 16 进制输入框内按回车直接发送（替代插入换行）
        Binding("enter", "send_hex", show=False, priority=True),
    ]

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(**kwargs)
        self._reformatting = False
        self.cursor_blink = False  # 常亮的块状光标，不闪烁

    def action_send_hex(self) -> None:
        """回车直接发送：解析输入框内容并发送到串口。"""
        self.app._send_hex_box()  # type: ignore[attr-defined]

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._reflow(keep_cursor=True)

    def on_resize(self) -> None:
        if self.text:
            self._reflow(keep_cursor=False)

    def _watch_has_focus(self, focus: bool) -> None:
        # 聚焦/失焦时丢弃行缓存并重绘：失焦要画出“竖线”光标，聚焦要恢复实心块。
        super()._watch_has_focus(focus)
        self._line_cache.clear()
        self.refresh()

    def get_line(self, line_index: int) -> RichText:
        """Render one document line; while not focused, draw the cursor as a
        vertical bar (the focused cursor stays TextArea's own block)."""
        line = super().get_line(line_index)
        if self.has_focus:
            return line
        row, col = self.cursor_location
        if row != line_index:
            return line
        bar = RichText(_HEX_BAR, style=_HEX_BAR_STYLE)
        if col >= len(line):
            return RichText.assemble(line, bar)
        return RichText.assemble(line[:col], bar, line[col + 1 :])

    def _reflow(self, keep_cursor: bool) -> None:
        """Rebuild the document in canonical form: only hex, spaced per byte."""
        if self._reformatting:
            return
        raw = self.text or ""
        digits = "".join(c for c in raw if c in _HEX_DIGITS).upper()
        target = self._hex_digits_before_cursor(raw)
        formatted = format_hex_lines(digits, hex_bytes_per_line(max(1, self.size.width)))
        if formatted == raw:
            return
        self._reformatting = True
        try:
            self.text = formatted
        finally:
            self._reformatting = False
        location = (
            _location_after_hex(formatted, target)
            if keep_cursor
            else _linear_to_location(formatted, len(formatted))
        )
        self.cursor_location = location  # type: ignore[assignment]

    def _hex_digits_before_cursor(self, raw: str) -> int:
        row, col = self.cursor_location
        lines = raw.split("\n")
        prefix = sum(len(line) + 1 for line in lines[:row]) + col
        prefix = min(prefix, len(raw))
        return sum(1 for ch in raw[:prefix] if ch in _HEX_DIGITS)


class _HexBar(Vertical):
    """Bottom 16-hex input bar; mounted only while HEX mode is on.

    Keeping it out of the DOM when HEX is off prevents hidden focusable widgets
    from stealing focus (which used to break Ctrl+A and other combos).
    """

    def compose(self) -> ComposeResult:
        yield _HexArea(id="hex-input")
        yield Button(tr("发送"), id="hex-send", compact=True)


class _StatusMenuButton(Button, can_focus=False):
    """Bottom-right "菜单" button on the main window.

    Equivalent to Ctrl+A Z.  ``can_focus`` is disabled so it never steals the
    keyboard focus: typed keys keep going straight to the serial port and the
    Ctrl+A prefix handling is unaffected — the button is mouse-click only.
    """


_PREFIX_FUNCS = {
    "z": "主菜单",
    "p": "串口参数",
    "s": "选择发送协议",
    "r": "选择接收协议",
    "c": "清屏",
    "h": "16进制 开/关",
    "l": "捕获开/关",
    "o": "选项",
    "y": "语言",
    "a": "关于",
    "x": "退出",
}


def _load_css() -> str:
    """Read app.tcss from package resources (works frozen & editable)."""
    try:
        return _resources.files("pycom.resources").joinpath("app.tcss").read_text(encoding="utf-8")
    except Exception:
        return ""


CSS_CONTENT = _load_css()


# --- colour themes -----------------------------------------------------------
# Custom CSS variables referenced by app.tcss.  The dark values are the
# historical palette; the light theme overrides them per-theme (see
# _build_themes).  They are provided through get_theme_variable_defaults()
# (fallback) and each Theme.variables (per-theme override).
_DARK_VARIABLES: dict[str, str] = {
    "term-bg": "#282c34",
    "term-fg": "#dcdfe4",
    "hex-bg": "#313640",
    "hex-input-bg": "#282c34",
    "hex-input-focus-bg": "#313640",
    "hex-input-fg": "#dcdfe4",
    "muted": "#5c6370",
    "control-bg": "#3a4048",
    "control-focus-bg": "#474e5d",
    "control-fg": "#dcdfe4",
    "button-bg": "#3a4048",
    "hover-bg": "#4a5260",
    "placeholder": "#5c6370",
    "label": "#919baa",
    "faint": "#5c6370",
    "border": "#474e5d",
    "highlight-bg": "#474e5d",
    "highlight-fg": "#dcdfe4",
    "error": "#e06c75",
    "accent": "#61afef",
    "checkbox-fg": "#dcdfe4",
    "toggle-off": "#5c6370",
    "compact-bg": "#282c34",
    "menu-btn-bg": "#3b82f6",
    "menu-btn-fg": "#ffffff",
    "menu-btn-hover-bg": "#4a90e0",
    "primary-btn-bg": "#3b82f6",
    "primary-btn-fg": "#ffffff",
    "primary-btn-hover-bg": "#4a90e0",
    "table-header-bg": "#3a4048",
    "table-header-fg": "#dcdfe4",
}

_LIGHT_VARIABLES: dict[str, str] = {
    "term-bg": "#fafafa",
    "term-fg": "#383a42",
    "hex-bg": "#f0f0f0",
    "hex-input-bg": "#ffffff",
    "hex-input-focus-bg": "#f0f0f0",
    "hex-input-fg": "#383a42",
    "muted": "#a0a1a7",
    "control-bg": "#f0f0f0",
    "control-focus-bg": "#e5e5e5",
    "control-fg": "#383a42",
    "button-bg": "#f0f0f0",
    "hover-bg": "#fafafa",
    "placeholder": "#a0a1a7",
    "label": "#a0a1a7",
    "faint": "#a0a1a7",
    "border": "#d4d4d4",
    "highlight-bg": "#e5e5e5",
    "highlight-fg": "#383a42",
    "error": "#e45649",
    "accent": "#0184bc",
    "checkbox-fg": "#383a42",
    "toggle-off": "#a0a1a7",
    "compact-bg": "#f0f0f0",
    "menu-btn-bg": "#4a90d9",
    "menu-btn-fg": "#ffffff",
    "menu-btn-hover-bg": "#5ba0e0",
    "primary-btn-bg": "#4a90d9",
    "primary-btn-fg": "#ffffff",
    "primary-btn-hover-bg": "#5ba0e0",
    "table-header-bg": "#e5e5e5",
    "table-header-fg": "#383a42",
}


def _build_themes() -> dict[str, Theme]:
    """The two PyCom colour themes (dark = the historical palette)."""
    return {
        "pycom-dark": Theme(
            name="pycom-dark",
            primary="#61afef",
            secondary="#56b6c2",
            accent="#e5c07b",
            warning="#e5c07b",
            error="#e06c75",
            success="#98c379",
            foreground="#dcdfe4",
            background="#282c34",
            surface="#313640",
            panel="#313640",
            dark=True,
            variables=dict(_DARK_VARIABLES),
        ),
        "pycom-light": Theme(
            name="pycom-light",
            primary="#0184bc",
            secondary="#0997b3",
            accent="#c18401",
            warning="#c18401",
            error="#e45649",
            success="#50a14f",
            foreground="#383a42",
            background="#fafafa",
            surface="#ffffff",
            panel="#f0f0f0",
            dark=False,
            variables=dict(_LIGHT_VARIABLES),
        ),
    }


def _env_dark() -> bool | None:
    """Cheap hint from the COLORFGBG env var (``fg;bg``, 0-7 = dark bg)."""
    val = os.environ.get("COLORFGBG")
    if not val:
        return None
    parts = val.split(";")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]) < 8
    except ValueError:
        return None


def _system_dark() -> bool | None:
    """Windows: read the system light/dark theme from the registry.

    ``AppsUseLightTheme`` is 0 for dark and 1 for light.  Returns None on
    non-Windows or when the value cannot be read.
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        key = winreg.OpenKey(  # type: ignore[attr-defined]
            winreg.HKEY_CURRENT_USER,  # type: ignore[attr-defined]
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")  # type: ignore[attr-defined]
            return value == 0  # 0 = dark, 1 = light
        finally:
            winreg.CloseKey(key)  # type: ignore[attr-defined]
    except Exception:
        return None


def _parse_osc11(response: bytes) -> tuple[int, int, int] | None:
    """Parse an OSC 11 background-colour reply into (r, g, b).

    Accepts ``ESC ] 11 ; rgb:RRRR/GGGG/BBBB`` (ST or BEL terminator),
    with 3- or 4-digit hex per channel, plus ``#RRGGBB``.
    """
    if not response:
        return None
    text = response.decode("ascii", "replace")
    for marker in ("\x1b]11;", "]11;"):
        if marker in text:
            text = text.split(marker, 1)[1]
            break
    text = text.rstrip("\x07\x1b\\\x00").strip()
    if text.startswith("rgb:"):
        channels = text[4:].split("/")
        if len(channels) != 3:
            return None
        vals: list[int] = []
        for ch in channels:
            try:
                n = int(ch, 16)
            except ValueError:
                return None
            if len(ch) == 4:
                n >>= 8
            elif len(ch) == 3:
                n >>= 4
            elif len(ch) == 1:
                n <<= 4
            vals.append(n)
        return (vals[0], vals[1], vals[2])
    if text.startswith("#") and len(text) == 7:
        try:
            return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
        except ValueError:
            return None
    return None


def _read_osc_reply(timeout: float) -> bytes:
    """Read the terminal's reply to an OSC query from stdin (raw)."""
    buf = bytearray()
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        import msvcrt

        fd = sys.stdin.fileno()
        try:
            msvcrt.setmode(fd, os.O_BINARY)  # type: ignore[attr-defined]
        except Exception:
            return b""
        while time.monotonic() < deadline:
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                ch = os.read(fd, 1)
                if not ch:
                    break
                buf += ch
                if ch in (b"\x07", b"\\"):
                    break
            else:
                time.sleep(0.005)
    else:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
        except Exception:
            return b""
        try:
            tty.setraw(fd)  # type: ignore[attr-defined]
            while time.monotonic() < deadline:
                r, _, _ = select.select([fd], [], [], 0.01)
                if r:
                    ch = os.read(fd, 1)
                    if not ch:
                        break
                    buf += ch
                    if ch in (b"\x07", b"\\"):
                        break
        finally:
            with contextlib.suppress(Exception):
                termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]
    return bytes(buf)


def _query_osc11(timeout: float) -> bytes | None:
    """Send an OSC 11 query and read the reply (best-effort).

    On Windows the console is switched to raw mode first so the reply is
    delivered immediately (cooked mode buffers it until Enter, which would
    otherwise leak the reply into the UI).  Returns None when the query
    cannot be sent.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        fd = sys.stdin.fileno()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        try:
            msvcrt.setmode(fd, os.O_BINARY)  # type: ignore[attr-defined]
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return None
            # clear ENABLE_LINE_INPUT (0x0002) + ENABLE_ECHO_INPUT (0x0004)
            kernel32.SetConsoleMode(handle, mode.value & ~0x0006)
        except Exception:
            return None
        try:
            sys.stdout.buffer.write(b"\x1b]11;?\x07")
            sys.stdout.buffer.flush()
            return _read_osc_reply(timeout)
        finally:
            with contextlib.suppress(Exception):
                kernel32.SetConsoleMode(handle, mode.value)
    sys.stdout.buffer.write(b"\x1b]11;?\x07")
    sys.stdout.buffer.flush()
    return _read_osc_reply(timeout)


def detect_terminal_dark(timeout: float = 0.5) -> bool | None:
    """Best-effort detection of the terminal's light/dark background.

    Priority:
      1. ``COLORFGBG`` environment variable (set by many terminals).
      2. OSC 11 query (the terminal's actual background colour).
      3. Windows system theme (registry ``AppsUseLightTheme``).

    Returns True for a dark background, False for a light one, or None when
    unknown (the caller falls back to the default theme).
    """
    env = _env_dark()
    if env is not None:
        return env
    raw = _query_osc11(timeout)
    rgb = _parse_osc11(raw) if raw else None
    if rgb is not None:
        r, g, b = rgb
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 128.0
    system = _system_dark()
    if system is not None:
        return system
    return None


class _QueueIO:
    """Adapt a ``queue.Queue`` + SerialManager to the YModemEngine read/write API."""

    def __init__(
        self, q: queue.Queue[bytes], serial: SerialManager, cancel: threading.Event
    ) -> None:
        self.q = q
        self.serial = serial
        self.cancel = cancel

    def read(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                return None
            try:
                data = self.q.get(timeout=min(0.2, deadline - time.monotonic()))
                return data
            except queue.Empty:
                continue
        return None

    def write(self, data: bytes) -> None:
        self.serial.write(data)


class PyComApp(App):
    """The terminal main window.  Handles the Ctrl+A prefix key model."""

    CSS = CSS_CONTENT
    TITLE = f"PyCom v{__version__}"
    SUB_TITLE = "串口终端 - YMODEM"

    # Ctrl+C 由 _on_key 直接发送 ^C 到串口；这里用一个空动作覆盖 Textual 默认的
    # ctrl+c → help_quit（“按 ctrl+q 退出”提示），避免误触弹出退出提示。
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "noop", show=False, system=True),
    ]

    # 应用内选中（Textual 自带）+ Ctrl+Shift+C/V 快捷键复制粘贴。
    # 注意：本应用必须向终端上报鼠标（滚动/按钮），而终端一旦处于鼠标上报
    # 状态就会关闭自身的原生选中；因此普通拖拽产生的是 Textual 的“应用内”
    # 选中，复制用 Ctrl+Shift+C。在 Windows Terminal 中要原生选中需按住
    # Shift 拖拽，再用终端自己的 Ctrl+Shift+C / 右键复制。
    ALLOW_SELECT: ClassVar[bool] = True

    def __init__(
        self,
        cfg: AppConfig | None = None,
        cli_conn: ConnectionSettings | None = None,
        exit_idle: float | None = None,
        startup_text: str | None = None,
        startup_script: str | None = None,
        enable_debug: bool = False,
        detected_dark: bool | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg or AppConfig()
        # register the two colour themes and apply the configured one
        for _name, _theme in _build_themes().items():
            self.register_theme(_theme)
        self._detected_dark = detected_dark
        self.theme = self._resolve_theme(detected_dark)
        # 解析界面语言：优先已保存的选择，否则自动侦测系统语言（失败退回英文）
        if self.cfg.language not in ("zh", "en"):
            self.cfg.language = set_language(detect_system_language())
        else:
            set_language(self.cfg.language)
        self.sub_title = tr("串口终端 - YMODEM")
        self.cli_conn = cli_conn
        self.exit_idle = max(0.0, float(exit_idle)) if exit_idle is not None else None
        self.startup_text = startup_text
        self.startup_script = startup_script
        # --enable-debug：开启调试选项（如虚拟回环端口），默认关闭。
        self.enable_debug = enable_debug

        self.model = TerminalModel(80, 24, scrollback=self.cfg.scrollback, decode=self.cfg.decode)
        self.model.rx_add_cr = self.cfg.rx_add_cr
        self.model.rx_add_lf = self.cfg.rx_add_lf

        self.serial = SerialManager(on_data=self._on_rx, on_error=self._on_serial_error)
        self.mapper = KeyMapper(self.cfg)

        self._prefix = False
        self._tx = 0
        self._rx = 0
        self._enter_presses = 0
        self._last_rx = time.monotonic()
        self._startup_thread: threading.Thread | None = None
        self._loopback = False  # virtual echo device (no real port)
        # HEX 接收显示：当前显示行已排的字节数（跨接收块持续计数，用于按
        # 窗口宽度在 4/8/16 字节处连续换行）
        self._hex_row_bytes = 0

        # 启动/连接时打印到终端的本地提示（橙/粗体），布局稳定后统一刷出
        self._pending_hints: list[str] = []
        self._hint_flush_pending = False

        # capture file
        self._capture_fh: Any = None
        self._cap_decoder: Any = None
        self._at_line_start = True

        # transfer state (owned by worker thread)
        self._xfer_queue: queue.Queue[bytes] | None = None
        self._xfer_cancel = threading.Event()
        self._xfer_ui: Any = None  # screen that shows progress
        self._xfer_thread: threading.Thread | None = None

        # too-small terminal guard (see _guard_minimum_size)
        self._too_small = False
        self._too_small_size = (0, 0)

    # ====================================================================== compose
    def compose(self):
        with Container(id="term-root"):
            with Horizontal(id="term-area"):
                yield TerminalView(self.model, id="term")
            with Horizontal(id="bottom"):
                # “菜单”按钮在左下角；状态文字占满其余宽度
                yield _StatusMenuButton(tr("菜单"), id="menu-btn")
                yield StatusBar("", id="status")

    # ------------------------------------------------------ minimum-size guard
    def _terminal_too_small(self) -> bool:
        """True when the terminal is smaller than the usable floor."""
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return False  # layout not done yet
        return w < MIN_TERMINAL_COLS or h < MIN_TERMINAL_ROWS

    def _guard_minimum_size(self) -> None:
        """Terminal far too small to be usable: stop the app; the CLI prints a
        hint afterwards (see main()).  Safe to call repeatedly — only the first
        detection triggers the exit."""
        if self._too_small or not self._terminal_too_small():
            return
        self._too_small = True
        self._too_small_size = (self.size.width, self.size.height)
        self.exit(_EXIT_TOO_SMALL)

    def on_mount(self) -> None:
        self._guard_minimum_size()
        if self._too_small:
            return  # exiting — do not start timers/bootstrap
        self.set_interval(0.4, self._tick)
        # compose children are not mounted yet — retry until the DOM is ready
        self._bootstrap_attempt()

    def on_resize(self, _event=None) -> None:
        self._guard_minimum_size()

    def _bootstrap_attempt(self) -> None:
        try:
            self._view()
            self._status()
        except Exception:
            self.set_timer(0.05, self._bootstrap_attempt)
            return
        self._bootstrap()

    def _bootstrap(self) -> None:
        self._view().focus()
        # 终端滚动时同步刷新右侧 ASCII 分栏
        self._view().on_scroll = self._refresh_hex_pane
        self.apply_config()
        # 程序一启动就打印菜单快捷键提示（无论是否连接端口）
        self._print_startup_hint()
        if self.cli_conn is not None:
            err = self.open_serial(self.cli_conn)
            if err:
                self.notify(tr("连接失败: {err}", err=err), severity="error")
            else:
                self.notify(tr("已连接 {name}", name=self.cli_conn.short()))
                self._start_startup_send()
        self._refresh_status()

    # ------------------------------------------------------------- CLI startup sends
    def _start_startup_send(self) -> None:
        """(CLI) send the -s string / -f script once the port is open."""
        if not (self.startup_text or self.startup_script):
            return
        self._startup_thread = threading.Thread(
            target=self._run_startup_send, name="pycom-startup-send", daemon=True
        )
        self._startup_thread.start()

    def _startup_busy(self) -> bool:
        return self._startup_thread is not None and self._startup_thread.is_alive()

    def _run_startup_send(self) -> None:
        """Run on a worker thread: send the -s string, then the -f script lines."""
        try:
            if self.startup_text:
                self._startup_write(decode_escapes(self.startup_text))
            if self.startup_script:
                with open(self.startup_script, encoding=self.cfg.decode) as fh:
                    lines = fh.read().splitlines()
                for raw in lines:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    self._startup_write(decode_escapes(line))
                    time.sleep(0.2)
        except OSError as exc:
            self.call_from_thread(self.notify, tr("启动发送失败: {err}", err=exc), severity="error")
        finally:
            self._startup_thread = None

    def _startup_write(self, data: bytes) -> None:
        if data and self.serial.is_open:
            self.serial.write(data)

    # ======================================================================= helpers
    def _view(self) -> TerminalView:
        return self.query_one("#term", TerminalView)

    def _refresh_hex_pane(self) -> None:
        """Repaint the right-hand ASCII pane (present only in HEX mode)."""
        with contextlib.suppress(Exception):
            self.query_one("#hex-ascii-pane", HexAsciiPane).refresh()

    def _status(self) -> StatusBar:
        return self.query_one("#status", StatusBar)

    # =================================================================== serial io
    def is_connected(self) -> bool:
        return self._loopback or self.serial.is_open

    def open_serial(self, settings: ConnectionSettings) -> str | None:
        if self._xfer_thread is not None and self._xfer_thread.is_alive():
            return tr("请先完成/取消进行中的文件传输")
        # 离开虚拟回环模式：一旦要打开真实串口，发送必须走该端口而不是回环。
        # 与 open_loopback()（关闭真实串口并把 _loopback 置 True）保持对称，
        # 否则 LOOPBACK → 真实串口 切换后 _loopback 仍为 True，发送会被回环
        # 截走、状态栏也一直显示“虚拟回环”，看起来就像“切换不成功”。
        self._loopback = False
        err = self.serial.open(settings)
        if err is None:
            self.cfg.last = settings
            save_config(self.cfg)
            self._last_rx = time.monotonic()
            self._enter_presses = 0
            self._print_local_hint(self._connected_hint())
            self._refresh_status()
        return err

    def open_loopback(self) -> str | None:
        """Connect the virtual loopback device: every byte sent is echoed back."""
        if self._xfer_thread is not None and self._xfer_thread.is_alive():
            return tr("请先完成/取消进行中的文件传输")
        if self.serial.is_open:
            self.serial.close()
        self._loopback = True
        self._last_rx = time.monotonic()
        self._enter_presses = 0
        self._print_local_hint(self._connected_hint())
        self._refresh_status()
        return None

    # --------------------------------------------- 屏幕上的本地提示（橙/粗体）
    def _connected_hint(self) -> str:
        name = tr("虚拟回环") if self._loopback else self.cfg.last.short()
        # 前后各留一行空行，让“已连接”提示在屏幕上更醒目
        return f"\r\n{tr('已连接 {name}', name=name)}\r\n"

    def _print_local_hint(self, text: str) -> None:
        """排队一条本地提示（橙/粗体），等布局稳定后统一打印。

        本地提示不计入 TX/RX、不写入捕获文件；HEX 模式下不打印。延迟一小
        帧再刷出，避免启动阶段的首次布局/缩放把刚写入的提示清掉。
        """
        if self.cfg.hex_mode:
            return
        self._pending_hints.append(text)
        self._schedule_hint_flush()

    def _schedule_hint_flush(self) -> None:
        if self._hint_flush_pending:
            return
        self._hint_flush_pending = True
        try:
            self.set_timer(0.05, self._flush_hints)
        except Exception:
            self._hint_flush_pending = False

    def _flush_hints(self) -> None:
        self._hint_flush_pending = False
        if not self._pending_hints:
            return
        pending = self._pending_hints
        self._pending_hints = []
        data = b"".join(
            f"\x1b[1m\x1b[38;2;255;165;0m{t}\x1b[0m\r\n".encode(self.model.decode, "replace")
            for t in pending
        )
        self.model.feed_bytes(data)
        with contextlib.suppress(Exception):
            self._view().mark_dirty()

    def _flush_hints_now(self) -> None:
        """在发送/显示真实串口内容前立即刷出尚未打印的本地提示，保证提示
        总是先于设备回显/接收数据出现。"""
        self._flush_hints()

    def _print_startup_hint(self) -> None:
        """程序启动即显示菜单快捷键提示（无论有没有连接端口）。"""
        self._print_local_hint(tr("按 Ctrl+A Z 打开功能菜单"))

    def _no_port_msg(self) -> str:
        return tr("未连接端口：请按 Ctrl+A P 连接后再试")

    def _remind_connect(self) -> None:
        """Toast shown when a send is attempted with no port connected."""
        self.notify(self._no_port_msg(), severity="warning", timeout=6)

    # ------------------------------------------------------------- HEX send/recv bar
    def _hex_bar(self) -> Vertical:
        return self.query_one("#hex-bar", Vertical)

    def _sync_hex_ui(self) -> None:
        """Mount the hex input row + ASCII pane only while HEX mode is on;
        remove them when off.
        (A permanently hidden focusable row used to steal focus and break
        Ctrl+A / other combos in the normal mode.)"""
        present = len(self.query("#hex-bar")) > 0
        if self.cfg.hex_mode:
            if not present:
                self._hex_row_bytes = 0  # 重新进入 HEX 模式：从头开始计行
                self.query_one("#term-root").mount(_HexBar(id="hex-bar"), before="#bottom")
                # 右侧 ASCII 分栏：随 HEX 模式挂载/卸载
                self.query_one("#term-area").mount(HexAsciiPane(self._view(), id="hex-ascii-pane"))
        else:
            if present:
                with contextlib.suppress(Exception):
                    self.query_one("#hex-bar", Vertical).remove()
                with contextlib.suppress(Exception):
                    self.query_one("#hex-ascii-pane").remove()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "menu-btn":
            event.stop()
            self._open_menu()
            return
        if button_id != "hex-send":
            return
        event.stop()
        self._send_hex_box()

    def _send_hex_box(self) -> None:
        """Parse the 16-hex input and transmit it."""
        field = self.query_one("#hex-input", TextArea)
        raw = field.text.strip()
        if not raw:
            self.notify(tr("请先在 16 进制输入框输入字节"), severity="warning")
            return
        try:
            data = parse_hex_line(raw)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        if not self.is_connected():
            self._remind_connect()
            return
        self.send_bytes(data)  # handles TX counter / local echo / loopback echo
        # 发送后保留输入内容（便于重复发送/追加），仅把焦点还给输入框
        field.focus()

    def close_serial(self) -> None:
        self.cancel_transfer()
        self._loopback = False
        self.serial.close()
        self._stop_capture()
        self._refresh_status()

    def send_bytes(self, data: bytes) -> bool:
        """Transmit bytes.  On the virtual loopback the data is echoed back as
        RX (a pure loopback device), otherwise it goes to the real port."""
        self._flush_hints_now()  # 先打印待刷出的本地提示
        if not data:
            return False
        if self._loopback:
            # 像真实终端那样回显：单独的 \r 回显为 \r\n（回车换行），
            # 已带 \n 的 \r\n 保持原样，避免按回车后仍停在原行覆盖内容。
            echoed = data.replace(b"\r\n", b"\r").replace(b"\r", b"\r\n")
            self._tx += len(data)
            self._rx += len(echoed)
            self._rx_to_terminal(echoed)
            return True
        ok = self.serial.write(data)
        if ok:
            self._tx += len(data)
            if self.cfg.local_echo:
                self._rx_to_terminal(data)
        return ok

    def _on_serial_error(self, message: str) -> None:
        self.call_from_thread(self._handle_serial_error, message)

    def _handle_serial_error(self, message: str) -> None:
        self.close_serial()
        self._refresh_status()
        self.notify(message, severity="error", timeout=8)

    # -- receive path -------------------------------------------------------------------
    def _on_rx(self, data: bytes) -> None:  # runs on the reader thread
        q = self._xfer_queue
        if q is not None:
            q.put(data)
        else:
            self._rx += len(data)
            self.call_from_thread(self._rx_to_terminal, data)

    def _rx_to_terminal(self, data: bytes) -> None:
        self._flush_hints_now()  # 接收数据显示前先打印待刷出的本地提示
        self._write_capture(data)
        self._last_rx = time.monotonic()
        if self.cfg.hex_mode:
            # 16 进制接收：真实 0A/0D 只是普通数据，显示为 "0A"/"0D"，不当作
            # 换行断行；行只在累计满 N 字节（按窗口宽度取 32/16/8/4）时换行。
            text = self._format_hex_rx(data)
            if text:
                self.model.feed_bytes(text.encode("ascii", "replace"))
        else:
            self.model.feed_bytes(data)
        self._view().mark_dirty()
        self._refresh_hex_pane()

    def _hex_rx_per_line(self) -> int:
        """每行多少字节随终端显示宽度自适应（32/16/8/4）。

        接收区只显示纯十六进制文本（右侧的 ASCII 列由独立的分栏显示），
        每行最多 32 字节，以匹配分栏的固定宽度。
        """
        return hex_bytes_per_line(max(1, self.model.columns), max_bytes=32)

    def _format_hex_rx(self, data: bytes) -> str:
        """把一段接收数据排版为“每行 N 字节”的纯十六进制文本。

        发送区(输入框)之所以能连续按宽度换行，是因为它每次把整篇文本重排；
        而接收数据是分块到达的，若只在单个块内分组，小于 N 字节的小块会一直
        堆在同一行、长时间不换行。这里用 ``self._hex_row_bytes`` 跨数据块持续
        计数，无论块多大都严格在每满 N 字节处换行，从而与发送区一致地连续
        自动换行。文本用 ``\\r\\n`` 换行，保证每行回到列首。
        """
        if not data:
            return ""
        per_line = self._hex_rx_per_line()
        used = self._hex_row_bytes
        if used >= per_line:  # 行宽变化后旧计数失效，重新从行首排
            used = 0
        out: list[str] = []
        # 若本行已有内容（含切到 HEX 前文本模式留下的内容），先补一个空格，
        # 避免上一数据块末尾字节与下一数据块首字节粘在一起（如 "42"+"0D"）。
        if self.model.mid_line():
            out.append(" ")
        i, n = 0, len(data)
        while i < n:
            seg = data[i : i + (per_line - used)]
            i += len(seg)
            out.append(" ".join(f"{b:02X}" for b in seg))
            used += len(seg)
            if used >= per_line:
                out.append("\r\n")  # 该行已满：换到下一行行首
                used = 0
        self._hex_row_bytes = used
        return "".join(out)

    # ==================================================================== key handling
    def action_noop(self) -> None:
        """Ctrl+C 由 _on_key 直接发送到串口（^C），此动作仅吞掉 Textual 默认的
        “按 ctrl+q 退出”提示，不执行任何操作。"""

    async def _on_key(self, event: Key) -> None:
        # 任意键按下即关闭左侧的 toast 提示（未连接端口 / HEX 模式等）
        if self._notifications:
            self.clear_notifications()
        if len(self.screen_stack) > 1:
            # A modal is on top: its widgets / screen bindings have already
            # processed this key.  Do NOT fall through to app-level bindings —
            # that would re-run e.g. Tab/shift+Tab focus navigation a second
            # time and make focus jump two widgets per key press.
            return
        if self._prefix:
            self._prefix_second(event)
            event.stop()
            return
        if event.key == "ctrl+a":
            event.stop()
            self._enter_prefix()
            return
        # 复制 / 粘贴（Ctrl+Shift+C / Ctrl+Shift+V）：复制的是 Textual 的“应用内”
        # 选中；在 Windows Terminal 中这两个键会被终端自身截走，需按住 Shift
        # 拖拽做原生选中后用终端的复制。
        if event.key == "ctrl+shift+c":
            event.stop()
            self._copy_selection()
            return
        if event.key == "ctrl+shift+v":
            event.stop()
            self._paste_clipboard()
            return
        if self.cfg.hex_mode:
            # 16 进制模式：主界面按键不再直接发送，只能经底部 16 进制框发送
            event.stop()
            return
        data = self.mapper.map(event.key, event.character)
        if data is not None:
            event.stop()
            ok = self.send_bytes(data)
            if event.key in ("enter", "return"):
                if not ok:
                    self._enter_presses += 1
                    if self._enter_presses >= 3:
                        self._enter_presses = 0
                        self._remind_connect()
                else:
                    self._enter_presses = 0
            else:
                self._enter_presses = 0
        else:
            self._enter_presses = 0

    # -- 复制 / 粘贴 ----------------------------------------------------------------
    def _copy_selection(self) -> None:
        """复制终端中选中的文本到剪贴板（Ctrl+Shift+C）。

        选中的是 Textual 的“应用内”选中：宿主终端在鼠标上报期间关闭了原生
        选中，普通拖拽只能生成应用内高亮；复制统一走这里的快捷键。在 Windows
        Terminal 中 Ctrl+Shift+C 被终端截走，需按住 Shift 拖拽原生选中后用
        终端的复制。
        """
        text = self.screen.get_selected_text()
        if text:
            self.copy_to_clipboard(text)
            self.notify(tr("已复制 {n} 字符", n=len(text)))
        else:
            self.notify(tr("没有选中的文本"), severity="warning")

    def _paste_clipboard(self) -> None:
        """把剪贴板内容发送到串口（Ctrl+Shift+V）。"""
        text = self.clipboard
        if not text:
            self.notify(tr("剪贴板为空"), severity="warning")
            return
        if not self.is_connected():
            self._remind_connect()
            return
        self.send_bytes(text.encode(self.cfg.decode, "replace"))

    def on_paste(self, event: Paste) -> None:
        """终端粘贴（bracketed paste）：把粘贴内容发送到串口。"""
        if len(self.screen_stack) > 1 or self.cfg.hex_mode:
            return
        if not event.text:
            return
        if not self.is_connected():
            self._remind_connect()
            return
        self.send_bytes(event.text.encode(self.cfg.decode, "replace"))

    # -- Ctrl+A prefix model --------------------------------------------------------------
    def _enter_prefix(self) -> None:
        self._prefix = True
        # 焦点若在 HEX 输入条上，先清空焦点，否则前缀后的字母会被输入框吞掉
        # （TerminalView 不可聚焦，focus() 不生效，只能 set_focus(None)）
        if self.cfg.hex_mode:
            with contextlib.suppress(Exception):
                focused = self.focused
                if focused is not None and focused.id in ("hex-input", "hex-send"):
                    self.screen.set_focus(None)
        self._status().update(self._prefix_hint())

    def _prefix_hint(self) -> str:
        items = " / ".join(f"{key.upper()} {tr(label)}" for key, label in _PREFIX_FUNCS.items())
        return tr("前缀模式: {items} / Esc 取消", items=items)

    def _cancel_prefix(self) -> None:
        self._prefix = False
        self._refresh_status()

    def _prefix_second(self, event: Key) -> None:
        self._prefix = False
        if event.key in ("escape", "ctrl+a"):
            # escape or a second Ctrl+A cancels the prefix (no byte is sent)
            self._refresh_status()
            return
        char = (event.character or "").lower()
        if char in _PREFIX_FUNCS:
            self.menu_action(char)
            return
        # unknown keys fall through to the device like a plain key press
        data = self.mapper.map(event.key, event.character)
        if data is not None and self.serial.is_open:
            self.send_bytes(data)
        self._refresh_status()

    def _open_menu(self) -> None:
        """Open the floating main-menu popup anchored at the bottom-left."""
        if isinstance(self.screen, MainMenuScreen):
            return  # already open
        self.push_screen(MainMenuScreen(self.menu_action))

    # ======================================================================== actions
    def menu_action(self, code: str) -> None:
        """Dispatch a single-letter menu action (also from the main-menu popup)."""
        if code == "z":
            self._open_menu()
        elif code == "p":
            self.push_screen(ConnectionScreen())
        elif code == "d":
            self.push_screen(TransferMenuScreen())
        elif code == "s":
            # 先选择协议（YMODEM / ZMODEM），再进入发送界面
            self.push_screen(ProtocolPicker(tr("选择发送协议")), callback=self._send_protocol)
        elif code == "r":
            # 先选择协议（YMODEM / ZMODEM），再进入接收界面
            self.push_screen(ProtocolPicker(tr("选择接收协议")), callback=self._recv_protocol)
        elif code == "x":
            self.push_screen(
                ConfirmDialog(tr("退出"), tr("确定要退出 PyCom 吗？")),
                callback=lambda yes: self.exit() if yes else None,
            )
        elif code == "c":
            self.clear_terminal()
        elif code == "l":
            self.toggle_capture()
        elif code == "h":
            self.cfg.hex_mode = not self.cfg.hex_mode
            self.apply_config()
            if self.cfg.hex_mode:
                # 快捷键开启后自动聚焦 16 进制输入框，可直接输入
                self.set_timer(0.05, self._focus_hex_field)
            self.notify(
                tr("HEX 模式已开启：在底部输入框输入，点“发送”")
                if self.cfg.hex_mode
                else tr("HEX 模式已关闭")
            )
        elif code == "o":
            self.push_screen(OptionsScreen())
        elif code == "y":
            self.push_screen(LanguageScreen())
        elif code == "a":
            self.push_screen(AboutScreen())

    def _send_protocol(self, protocol: str | None) -> None:
        """ProtocolPicker result -> open the send dialog for that protocol."""
        if protocol in VALID_PROTOCOLS:
            self.open_send(protocol)

    def _recv_protocol(self, protocol: str | None) -> None:
        """ProtocolPicker result -> open the receive dialog for that protocol."""
        if protocol in VALID_PROTOCOLS:
            self.open_recv(protocol)

    def open_send(self, protocol: str = "ymodem") -> None:
        """Open the send dialog for ``protocol`` (ymodem | zmodem)."""
        if not self.is_connected():
            self._remind_connect()
        else:
            self.push_screen(SendScreen(protocol))

    def open_recv(self, protocol: str = "ymodem") -> None:
        """Open the receive dialog for ``protocol`` (ymodem | zmodem)."""
        if not self.is_connected():
            self._remind_connect()
        else:
            self.push_screen(RecvScreen(protocol))

    def set_language(self, code: str) -> None:
        """Switch the UI language now and persist the choice."""
        if code not in ("zh", "en") or code == get_language():
            return
        self.cfg.language = code
        set_language(code)
        save_config(self.cfg)
        self._apply_language()

    def _apply_language(self) -> None:
        """Refresh already-visible UI text after a language change."""
        self.sub_title = tr("串口终端 - YMODEM")
        with contextlib.suppress(Exception):
            self.query_one("#menu-btn", Button).label = tr("菜单")
        with contextlib.suppress(Exception):
            self.query_one("#hex-send", Button).label = tr("发送")
        self._refresh_status()

    def _focus_hex_field(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#hex-input", TextArea).focus()

    def clear_terminal(self) -> None:
        """清屏：同时复位 TX/RX 字节计数器与 HEX 行内计数，并刷新状态栏。

        清屏代表显示区域重新开始，状态栏里的收发计数也随之从 0 累计，
        便于按“屏/页”衡量一次会话的数据量。
        """
        self.model.clear()
        self._tx = 0
        self._rx = 0
        self._hex_row_bytes = 0  # 清屏后 HEX 行计数从头开始
        self._view().mark_dirty()
        self._refresh_hex_pane()
        self._refresh_status()

    # =========================================================================== config
    def apply_config(self) -> None:
        self.mapper.refresh(self.cfg)
        self.model.set_decode(self.cfg.decode)
        self.model.rx_add_cr = self.cfg.rx_add_cr
        self.model.rx_add_lf = self.cfg.rx_add_lf
        self._view().mark_dirty()
        self._sync_hex_ui()
        self._refresh_hex_pane()
        self._refresh_status()

    # ============================================================================ theme
    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Fallback values for the custom CSS variables (dark palette)."""
        return dict(_DARK_VARIABLES)

    def set_theme_mode(self, mode: str) -> None:
        """Switch the colour theme mode now and persist it.

        ``mode`` is one of "auto" | "light" | "dark"; "auto" follows the
        terminal's detected background colour.
        """
        if mode not in ("auto", "light", "dark"):
            return
        self.cfg.theme = mode
        save_config(self.cfg)
        self._apply_theme()

    def _apply_theme(self) -> None:
        self.theme = self._resolve_theme(self._detected_dark)

    def _resolve_theme(self, detected_dark: bool | None) -> str:
        """Map the configured theme mode to a registered theme name."""
        mode = self.cfg.theme
        if mode == "light":
            return "pycom-light"
        if mode == "dark":
            return "pycom-dark"
        # auto: follow the terminal (unknown -> dark, the historical default)
        return "pycom-dark" if detected_dark is not False else "pycom-light"

    # ============================================================================ status
    def _status_text(self) -> str:
        conn = tr("虚拟回环") if self._loopback else self.cfg.last.short()
        state = tr("已连接") if self.is_connected() else tr("未连接")
        flags = []
        if self._loopback:
            flags.append(tr("回环"))
        if self.is_connected():
            flags.append(f"TX {self._tx:,}")
            flags.append(f"RX {self._rx:,}")
        if self._capture_fh is not None:
            flags.append(tr("捕获"))
        if self.cfg.local_echo:
            flags.append(tr("回显"))
        if self.cfg.wrap:
            flags.append(tr("回绕"))
        if self.cfg.hex_mode:
            flags.append("HEX")
        if self._prefix:
            right = ""
        elif self.cfg.hex_mode:
            right = tr("HEX：底部输入，点发送")
        else:
            # Ctrl+A Z 提示已改为右下角的“菜单”按钮，状态栏不再重复显示
            right = ""
        mid = "  ".join(flags)
        return f" {conn} | {state} | {mid}".rstrip(" |") + (f"    {right}" if right else "")

    def _refresh_status(self) -> None:
        with contextlib.suppress(Exception):  # DOM not ready / already torn down
            self._status().update(self._status_text())
        self._sync_view_active()

    def _sync_view_active(self) -> None:
        """Tell the terminal view whether it is the active typing target: only
        on the main screen with HEX mode off (keys then reach the terminal), a
        filled-block cursor is shown; otherwise the cursor turns hollow."""
        with contextlib.suppress(Exception):
            self._view().active = len(self.screen_stack) == 1 and not self.cfg.hex_mode

    def refresh_status(self) -> None:
        """Public entry point used by screens/dialogs to refresh the status bar."""
        self._refresh_status()

    def _tick(self) -> None:
        self._check_idle_exit()
        if self._prefix:
            return
        self._refresh_status()

    def _check_idle_exit(self) -> None:
        """(-e) exit when no byte has been received for ``exit_idle`` seconds."""
        limit = self.exit_idle
        if limit is None or self._transfer_busy() or self._startup_busy() or self._prefix:
            return
        if time.monotonic() - self._last_rx >= limit:
            self.exit()

    # ============================================================================ capture
    def toggle_capture(self) -> None:
        if self._capture_fh is not None:
            self._stop_capture()
            return
        path = self.cfg.capture_path
        if not path:
            path = f"pycom-capture-{time.strftime('%Y%m%d-%H%M%S')}.log"
        try:
            fh = open(path, "a", encoding="utf-8", newline="")  # noqa: SIM115 persistent handle
        except OSError as exc:
            self.notify(tr("无法创建捕获文件: {err}", err=exc), severity="error")
            return
        self._capture_fh = fh
        self.cfg.capture_path = path
        save_config(self.cfg)
        self._cap_decoder = codecs.getincrementaldecoder(self.cfg.decode)(errors="replace")
        self._at_line_start = True
        self.notify(tr("开始捕获到 {path}", path=path))
        self._refresh_status()

    def _stop_capture(self, refresh: bool = True) -> None:
        if self._capture_fh is not None:
            with contextlib.suppress(Exception):
                self._capture_fh.close()  # type: ignore[union-attr]
        self._capture_fh = None
        if refresh:
            self._refresh_status()

    def _write_capture(self, data: bytes) -> None:
        if self._capture_fh is None:
            return
        try:
            text = self._cap_decoder.decode(data)  # type: ignore[union-attr]
        except Exception:
            return
        if not text:
            return
        if self.cfg.capture_timestamps:
            ts = time.strftime("%H:%M:%S")
            parts = text.split("\n")
            if self._at_line_start and parts and parts[0]:
                parts[0] = f"[{ts}] {parts[0]}"
            for i in range(1, len(parts)):
                if parts[i]:
                    parts[i] = f"[{ts}] {parts[i]}"
            text = "\n".join(parts)
            self._at_line_start = text.endswith("\n")
        self._capture_fh.write(text)  # type: ignore[union-attr]
        self._capture_fh.flush()  # type: ignore[union-attr]

    # ====================================================================== transfers
    def _transfer_busy(self) -> bool:
        return self._xfer_thread is not None and self._xfer_thread.is_alive()

    def _guard_transfer(self) -> str | None:
        if not self.is_connected():
            return tr("未连接串口")
        if self._transfer_busy():
            return tr("已有文件传输正在进行")
        return None

    def start_transfer_send(self, path: str, protocol: str = "ymodem") -> str | None:
        err = self._guard_transfer()
        if err:
            return err
        if not os.path.isfile(path):
            return tr("文件不存在: {path}", path=path)
        self.cfg._last_send_file = path  # type: ignore[attr-defined]

        self._xfer_queue = queue.Queue()
        self._xfer_cancel.clear()
        self._xfer_ui = self.screen_stack[-1] if len(self.screen_stack) > 1 else None
        self._xfer_thread = threading.Thread(
            target=self._run_send,
            args=(path, protocol),
            name=f"pycom-{protocol}-send",
            daemon=True,
        )
        self._xfer_thread.start()
        return None

    def start_transfer_recv(
        self, directory: str, name_override: str = "", protocol: str = "ymodem"
    ) -> str | None:
        err = self._guard_transfer()
        if err:
            return err
        self._xfer_queue = queue.Queue()
        self._xfer_cancel.clear()
        self._xfer_ui = self.screen_stack[-1] if len(self.screen_stack) > 1 else None
        self._xfer_thread = threading.Thread(
            target=self._run_recv,
            args=(directory, name_override, protocol),
            name=f"pycom-{protocol}-recv",
            daemon=True,
        )
        self._xfer_thread.start()
        return None

    def cancel_transfer(self) -> None:
        self._xfer_cancel.set()

    def _xfer_emit(self, method: str, *args) -> None:
        ui = self._xfer_ui
        if ui is None:
            return
        with contextlib.suppress(Exception):
            self.call_from_thread(getattr(ui, method), *args)

    def _xfer_done(self) -> None:
        self._xfer_queue = None
        self._xfer_thread = None
        self._xfer_ui = None
        self._xfer_cancel.clear()
        self._refresh_status()

    def _engine(self, cb, protocol: str = "ymodem"):
        assert self._xfer_queue is not None
        io = _QueueIO(self._xfer_queue, self.serial, self._xfer_cancel)
        if protocol == "zmodem":
            return ZModemEngine(
                io.read,
                io.write,
                timeout=self.cfg.xfer_timeout,
                retries=self.cfg.xfer_retries,
                cancel=self._xfer_cancel,
                cb=cb,
            )
        return YModemEngine(
            io.read,
            io.write,
            timeout=self.cfg.xfer_timeout,
            retries=self.cfg.xfer_retries,
            block_size=self.cfg.xfer_block_size,
            cancel=self._xfer_cancel,
            cb=cb,
        )

    def _run_send(self, path: str, protocol: str) -> None:
        name = os.path.basename(path)

        def cb(phase: str, fname: str, sent: int, total) -> None:
            self._xfer_emit("show_progress", phase, fname, sent, total)

        try:
            with open(path, "rb") as fh:
                ok, msg = self._engine(cb, protocol).send(fh, filename=name)
        except Exception as exc:
            ok, msg = False, str(exc)
        self._xfer_emit("show_result", ok, msg)
        self._xfer_done()

    def _run_recv(self, directory: str, name_override: str, protocol: str) -> None:
        def cb(phase: str, fname: str, sent: int, total) -> None:
            self._xfer_emit("show_progress", phase, fname, sent, total)

        def open_file(filename: str, size) -> object | None:
            from pycom.screens.transfer import sanitize_filename

            fname = sanitize_filename(name_override or filename or "download.bin")
            path = os.path.join(directory, fname)
            try:
                return open(path, "wb")
            except OSError as exc:
                self._xfer_emit(
                    "show_result", False, tr("无法写入 {path}: {err}", path=path, err=exc)
                )
                return None

        try:
            ok, msg, fname = self._engine(cb, protocol).recv(open_file)
        except Exception as exc:
            ok, msg = False, str(exc)
        self._xfer_emit("show_result", ok, msg)
        self._xfer_done()

    # ============================================================================== meta
    def on_unmount(self) -> None:
        self.cancel_transfer()
        self.serial.close()
        self._stop_capture(refresh=False)


# =============================================================================== CLI
def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=tr(
            "minicom 风格的跨平台串口终端：VT/ANSI 渲染、YMODEM/ZMODEM 收发、"
            "16 进制接收/发送。\n"
            "不带参数启动即进入交互界面（Ctrl+A 打开功能菜单）。"
        ),
        epilog=tr(
            "示例：\n"
            "  pycom -p COM3 -b 115200\n"
            '  pycom -p COM3 -s "AT\\r"\n'
            "  pycom -p COM3 -f boot.txt -e 5\n"
            "  pycom -p COM3 --hex\n"
            "  pycom -p COM3 --no-mouse\n"
            "  pycom --bare -p COM3 -b 115200   # 无界面纯直通：stdin→串口，串口→stdout\n"
            "\n"
            "-s/-f 内容支持 \\n \\r \\t \\xHH 等转义；-e 支持小数秒；--no-mouse 关闭鼠标捕获，"
            "把滚轮/点击交还宿主终端。\n"
            "--bare 隐藏全部界面，仅供外部进程（如 AI agent）通过标准输入输出驱动。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help=tr("显示本帮助并退出"))
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help=tr("显示版本号并退出"),
    )
    # 调试开关：默认隐藏，不在 --help 中展示。
    parser.add_argument("--enable-debug", action="store_true", help=argparse.SUPPRESS)

    conn = parser.add_argument_group(tr("连接参数"))
    conn.add_argument(
        "-p", "--port", metavar="PORT", default=None, help=tr("串口，如 COM3 或 /dev/ttyUSB0")
    )
    conn.add_argument("-b", "--baud", type=int, metavar="BAUD", default=None, help=tr("波特率"))
    conn.add_argument("--data-bits", type=int, metavar="5-8", default=None, help=tr("数据位"))
    conn.add_argument("--parity", metavar="N/E/O", default=None, help=tr("校验位"))
    conn.add_argument("--stop-bits", type=float, metavar="1|1.5|2", default=None, help=tr("停止位"))
    conn.add_argument(
        "--flow", metavar="MODE", default=None, help=tr("流控：none / rtscts / xonxoff")
    )

    startup = parser.add_argument_group(tr("启动动作"))
    startup.add_argument(
        "-s", "--send", metavar="TEXT", default=None, help=tr("连接后发送字符串命令（支持转义）")
    )
    startup.add_argument(
        "-f",
        "--script",
        metavar="FILE",
        default=None,
        help=tr("连接后逐行发送脚本文件（# 开头为注释行）"),
    )
    startup.add_argument(
        "-e",
        "--exit-idle",
        type=float,
        metavar="SECS",
        default=None,
        help=tr("空闲自动退出：连续 SECS 秒未收到任何字节"),
    )
    startup.add_argument("--hex", action="store_true", help=tr("启动即开启 16 进制接收/发送"))

    ui = parser.add_argument_group(tr("界面选项"))
    ui.add_argument(
        "--no-mouse",
        action="store_true",
        help=tr("禁用鼠标捕获：终端不再上报鼠标，滚轮/点击由宿主终端自身处理"),
    )

    bridge = parser.add_argument_group(tr("直通模式（--bare，无界面）"))
    bridge.add_argument(
        "--bare",
        action="store_true",
        help=tr(
            "隐藏全部界面：把 stdin 接到串口（发送），串口 RX 原样打到 stdout。"
            "需通过 -p/--port 指定串口，适合把终端交给 AI agent 等外部进程驱动"
        ),
    )

    args = parser.parse_args(argv)
    if (args.send is not None or args.script is not None) and not args.port:
        parser.error(tr("-s/--send、-f/--script 需要先通过 -p/--port 指定端口"))
    if args.exit_idle is not None and args.exit_idle <= 0:
        parser.error(tr("-e/--exit-idle 必须为正数"))
    if args.bare and not args.port:
        parser.error(tr("--bare 直通模式必须通过 -p/--port 指定串口"))
    if args.bare and (args.send or args.script or args.hex or args.exit_idle is not None):
        parser.error(tr("--bare 直通模式不能与 -s/-f/-e/--hex 等交互启动选项同时使用"))
    return args


def _make_conn(cfg: AppConfig, args) -> ConnectionSettings | None:
    """Build the CLI connection settings from -p/-b/--parity/...; returns None
    when no connection option was given (interactive mode then starts idle)."""
    if not (args.port or args.baud):
        return None
    conn = deepcopy(cfg.last)
    if args.port:
        conn.port = args.port
    if args.baud:
        conn.baudrate = args.baud
    if args.data_bits:
        conn.bytesize = args.data_bits
    if args.parity:
        conn.parity = args.parity.upper()
    if args.stop_bits:
        conn.stopbits = args.stop_bits
    if args.flow:
        conn.flow = args.flow.lower()
    return conn


def _binary_stdio() -> None:
    """Make stdin/stdout byte-transparent on Windows (no CRLF translation)."""
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import msvcrt

            # os.O_BINARY / msvcrt.setmode only exist on Windows; mypy on
            # POSIX has no stub for them, hence the ignores.
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)  # type: ignore[attr-defined]
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)  # type: ignore[attr-defined]


def run_bare(args) -> int:
    """--bare entry point: a transparent, UI-less serial bridge.

    Every byte read from stdin is written to the port and every byte received
    on the port is written raw to stdout.  Nothing else is rendered, so an
    external process (e.g. an AI agent) can own this process's stdin/stdout
    pipes and talk straight to the device.
    """
    cfg = load_config()
    if cfg.language in ("zh", "en"):
        set_language(cfg.language)
    conn = _make_conn(cfg, args)
    assert conn is not None, "--bare requires -p/--port (enforced by argparse)"

    _binary_stdio()
    stop = threading.Event()

    def on_rx(data: bytes) -> None:  # serial reader thread
        try:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except (OSError, ValueError):
            stop.set()

    def on_error(message: str) -> None:
        sys.stderr.write(f"pycom: {message}\n")
        sys.stderr.flush()
        stop.set()

    mgr = SerialManager(on_data=on_rx, on_error=on_error)
    err = mgr.open(conn)
    if err:
        sys.stderr.write(f"pycom: {tr('连接失败: {err}', err=err)}\n")
        sys.stderr.flush()
        return 1
    sys.stderr.write(
        f"pycom: {tr('bare 直通已连接 {short}（stdin→串口，串口 RX→stdout）', short=conn.short())}\n"
    )
    sys.stderr.flush()

    def pump_stdin() -> None:
        fd = sys.stdin.fileno()
        try:
            while not stop.is_set():
                data = os.read(fd, 4096)
                if not data:  # stdin EOF -> agent closed the pipe
                    break
                mgr.write(data)
        except (OSError, ValueError):
            pass  # console closed / interrupted

    stdin_thread = threading.Thread(target=pump_stdin, name="pycom-bare-stdin", daemon=True)
    stdin_thread.start()
    try:
        while stdin_thread.is_alive() and not stop.is_set():
            stdin_thread.join(timeout=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        mgr.close()
    return 0


def main(argv=None) -> int:
    # CLI 帮助文本按侦测到的系统语言显示（失败退回英文）
    set_language(detect_system_language())
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.bare:
        return run_bare(args)

    cfg = load_config()
    if args.hex:
        cfg.hex_mode = True  # 本次启动自动开启 16 进制接收/发送

    cli_conn = _make_conn(cfg, args)

    # 启动前检查终端尺寸：窗口小到完全无法使用时直接提示并退出，不进入 TUI。
    cols, rows = shutil.get_terminal_size()
    if cols < MIN_TERMINAL_COLS or rows < MIN_TERMINAL_ROWS:
        sys.stderr.write(_too_small_message(cols, rows))
        return 1

    # auto theme: query the terminal background (fall back to dark)
    detected_dark = detect_terminal_dark() if cfg.theme == "auto" else None

    app = PyComApp(
        cfg=cfg,
        cli_conn=cli_conn,
        exit_idle=args.exit_idle,
        startup_text=args.send,
        startup_script=args.script,
        enable_debug=args.enable_debug,
        detected_dark=detected_dark,
    )
    result = app.run(mouse=not args.no_mouse)
    if result == _EXIT_TOO_SMALL:  # 运行中窗口被缩到过小
        w, h = app._too_small_size
        sys.stderr.write(_too_small_message(w, h))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
