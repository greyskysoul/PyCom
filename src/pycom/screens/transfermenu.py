"""Data-transfer submenu (YMODEM / ZMODEM send & receive)."""

from __future__ import annotations

from pycom.i18n import tr
from pycom.screens.menus import KeyedMenu

# (热键, 中文标签)：s/r 为 YMODEM，u/d 为 ZMODEM
TRANSFER_MENU = [
    ("s", "发送文件 - YMODEM"),
    ("r", "接收文件 - YMODEM"),
    ("u", "发送文件 - ZMODEM"),
    ("d", "接收文件 - ZMODEM"),
]


class TransferMenuScreen(KeyedMenu):
    """Choose a protocol and direction for a file transfer."""

    def __init__(self) -> None:
        rows = [(key, tr(label)) for key, label in TRANSFER_MENU]
        super().__init__(
            tr("数据传输"),
            rows,
            self._dispatch,
            footer=tr("方向键选择；Enter 或功能字母执行；Esc 返回"),
        )

    def _dispatch(self, code: str) -> None:
        app = self.app  # type: ignore[attr-defined]
        if code == "s":
            app.open_send("ymodem")  # type: ignore[attr-defined]
        elif code == "r":
            app.open_recv("ymodem")  # type: ignore[attr-defined]
        elif code == "u":
            app.open_send("zmodem")  # type: ignore[attr-defined]
        elif code == "d":
            app.open_recv("zmodem")  # type: ignore[attr-defined]
