"""The Ctrl+A Z main menu overlay (minicom-style)."""

from __future__ import annotations

from pycom.i18n import tr
from pycom.screens.menus import KeyedMenu

# 主菜单条目（key, 中文标签）。key 为功能热键字母（也是左侧显示的快捷字母）。
# 顺序按使用频率排列：串口参数最常用在最前，退出在最后。
MAIN_MENU = [
    ("p", "串口参数"),
    ("d", "数据传输"),
    ("c", "清屏"),
    ("h", "16进制 开/关"),
    ("l", "会话捕获 开/关"),
    ("o", "选项设置"),
    ("y", "语言"),
    ("a", "关于"),
    ("x", "退出"),
]


class MainMenuScreen(KeyedMenu):
    """Overlay listing the Ctrl+A functions; a single letter key runs one.

    Small terminals toggle the ``compact`` class on ``#help-box`` (see
    ResponsiveCompact), turning the boxed list into a full-screen scrollable
    one so every item stays reachable.
    """

    def __init__(self) -> None:
        rows = [(key, tr(label)) for key, label in MAIN_MENU]
        super().__init__(
            tr("PyCom - Ctrl+A 功能菜单"),
            rows,
            self._dispatch,
            footer=tr("方向键选择；Enter 或功能字母执行；Esc 关闭"),
        )

    def _dispatch(self, code: str) -> None:
        self.app.menu_action(code)  # type: ignore[attr-defined]
