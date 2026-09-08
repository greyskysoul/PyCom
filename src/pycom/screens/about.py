"""About dialog — project information."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, Static

from pycom import PROJECT_AUTHOR, PROJECT_URL, __version__
from pycom.i18n import tr
from pycom.screens.base import ResponsiveCompact


class AboutScreen(ResponsiveCompact):
    """Show project name/version/author/repo/licence."""

    ROOT_ID = "help-box"
    MIN_WIDTH = 50
    MIN_HEIGHT = 14

    def _body(self) -> str:
        lines = [
            f"PyCom {__version__}",
            tr("跨平台串口终端，支持 YMODEM / ZMODEM 文件传输、16 进制模式、会话捕获与 VT/ANSI 渲染。"),
            "",
            tr("作者: {author}", author=PROJECT_AUTHOR),
            tr("项目主页: {url}", url=PROJECT_URL),
            tr("开源协议: MIT"),
        ]
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(tr("关于"), id="help-title")
            with VerticalScroll(id="help-body"):
                yield Static(self._body())
            yield Button(tr("返回"), id="menu-back", compact=True)

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#menu-back", Button).focus()
