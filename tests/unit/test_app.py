"""UI smoke tests using textual's Pilot (headless)."""

from __future__ import annotations

import time

import pytest

from pycom.app import PyComApp
from pycom.keys import hex_bytes_per_line


async def test_app_starts_and_renders_device_output():
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        # simulate device output
        app.model.feed_bytes(b"\x1b[32mhello device\r\nworld")
        app._view().mark_dirty()
        await pilot.pause(0.3)
        rendered = str(app._view().render())
        assert "hello device" in rendered
        assert "world" in rendered


async def test_ctrl_a_prefix_opens_menu():
    from pycom.screens.menu_popup import MainMenuScreen

    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1

        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause()
        assert len(app.screen_stack) == 2, "menu is a pushed screen"
        assert isinstance(app.screen, MainMenuScreen), "menu screen should be on top"
        assert len(app.screen.query("#menu-popup")) == 1, "main-menu popup should be shown"

        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1, "escape should pop the menu screen"
        assert len(app.screen.query("#menu-popup")) == 0, "escape should close the popup"


async def test_prefix_cancel_with_escape():
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app._prefix is False


async def test_quit_confirm_can_be_cancelled():
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("x")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        # Esc is bound to "否" — the app must keep running
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert app._running is True


async def test_tab_in_modal_steps_one_widget_at_a_time():
    """Regression: Tab used to move focus by TWO widgets inside modals."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr.query_one("#echo").focus()
        await pilot.pause()

        expected = ["echo", "wrap", "rx_cr", "rx_lf", "vt", "enter"]

        visited = []
        for _ in range(len(expected) + 1):
            visited.append(app.focused.id)
            await pilot.press("tab")
            await pilot.pause(0.02)
        # every checkbox must be reachable in strict order, no skips
        assert visited[: len(expected)] == expected


async def test_arrows_navigate_between_fields_in_modal():
    """Arrow keys move the focus through the fields of a dialog.

    Previously the arrow keys did nothing inside modals (unless a widget like
    an Input or DataTable happened to own them).
    """
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr.query_one("#echo").focus()
        await pilot.pause()

        visited = []
        for _ in range(8):
            visited.append(app.focused.id)
            await pilot.press("down")
            await pilot.pause(0.02)
        # terminal group checkboxes, then its selects, in DOM order
        assert visited == ["echo", "wrap", "rx_cr", "rx_lf", "vt", "enter", "back", "decode"]


async def test_checkbox_toggles_with_left_right_arrows():
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        cb = scr.query_one("#echo")
        cb.focus()
        await pilot.pause()

        before = cb.value
        await pilot.press("right")
        await pilot.pause(0.02)
        assert cb.value is not before, "right arrow should toggle a focused checkbox"
        await pilot.press("left")
        await pilot.pause(0.02)
        assert cb.value is before, "left arrow should toggle it back"


async def test_arrows_inside_input_do_not_move_focus():
    """While typing in an Input the arrows edit the text, not the focus."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        field = scr.query_one("#timeout")
        field.focus()
        field.value = "abcd"
        await pilot.pause()

        await pilot.press("left")
        await pilot.pause(0.02)
        assert app.focused is field, "left arrow must stay inside the input"
        # up/down still navigate (they have no text-editing meaning)
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused is not field


async def test_dropdown_arrows_navigate_and_enter_selects_value():
    """选项页下拉框：折叠时方向键用于移动字段焦点（不打开菜单），
    Enter 打开菜单、方向键高亮、Enter 确认并自动关闭。"""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        sel = scr.query_one("#enter")
        sel.focus()
        await pilot.pause()
        assert sel.value == "cr"

        # ←/→：同一行两个下拉框之间移动焦点
        await pilot.press("right")
        await pilot.pause(0.02)
        assert app.focused.id == "back"
        await pilot.press("left")
        await pilot.pause(0.02)
        assert app.focused.id == "enter"

        # ↓/↑：折叠时只移动焦点，不打开菜单
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "back"
        assert sel.has_class("-expanded") is False
        await pilot.press("up")
        await pilot.pause(0.02)
        assert app.focused.id == "enter"

        # Enter：打开菜单；Escape：原样关闭、焦点回到下拉框
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert sel.has_class("-expanded") is True
        assert type(app.focused).__name__ == "SelectOverlay"
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert app.focused is sel
        assert sel.has_class("-expanded") is False

        # 再次打开，方向键高亮 “CR+LF”，Enter 确认选中
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert sel.value == "crlf"
        assert app.focused is sel


async def test_help_menu_arrows_select_and_enter_runs():
    """Main menu rows are selectable: arrows move, Enter runs the item."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause(0.2)
        assert app.focused.id == "menu-p", "first menu item should be focused"

        # navigate down to the "设置" row and activate it with Enter
        while app.focused.id != "menu-o":
            await pilot.press("down")
            await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert len(app.screen_stack) == 2
        assert isinstance(app.screen_stack[-1], OptionsScreen)


async def test_help_menu_letter_key_still_runs():
    """Pressing the function letter on the main menu runs it immediately."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause(0.2)
        await pilot.press("o")
        await pilot.pause(0.3)
        assert len(app.screen_stack) == 2
        assert isinstance(app.screen_stack[-1], OptionsScreen)


async def test_confirm_dialog_arrows_move_between_buttons():
    from pycom.screens.base import ConfirmDialog

    app = PyComApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmDialog("退出", "确定要退出 PyCom 吗？"))
        await pilot.pause(0.2)
        assert app.focused.id == "yes"
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "no"


async def test_ctrl_a_s_chooses_protocol_then_sends():
    """Ctrl+A S 先打开协议选择（YMODEM/ZMODEM），选定后再进入发送界面。"""
    from pycom.screens.transfer import ProtocolPicker, SendScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        await pilot.press("ctrl+a")
        await pilot.press("s")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], ProtocolPicker)
        await pilot.click("#zmodem")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], SendScreen)
        assert app.screen_stack[-1].protocol == "zmodem"


async def test_ctrl_a_r_chooses_protocol_then_receives():
    """Ctrl+A R 先选择协议（含 ZMODEM 接收），选定后再进入接收界面。"""
    from pycom.screens.transfer import ProtocolPicker, RecvScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        await pilot.press("ctrl+a")
        await pilot.press("r")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], ProtocolPicker)
        await pilot.click("#ymodem")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], RecvScreen)
        assert app.screen_stack[-1].protocol == "ymodem"


async def test_ctrl_a_s_cancel_opens_nothing():
    """协议选择页取消后不进入任何发送/接收界面。"""
    from pycom.screens.transfer import ProtocolPicker

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        await pilot.press("ctrl+a")
        await pilot.press("s")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], ProtocolPicker)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert len(app.screen_stack) == 1


async def test_path_picker_up_entry_and_navigation():
    """文件选择器：列表顶部有 '..' 可返回上一级，无“上一级”按钮。"""
    import os
    import tempfile

    from pycom.screens.filepicker import PathPicker

    root = tempfile.mkdtemp()
    sub = os.path.join(root, "subdir")
    os.makedirs(sub)
    with open(os.path.join(sub, "data.bin"), "wb") as fh:
        fh.write(b"x")

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(sub, pick_files=True))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        assert len(scr.query("#up")) == 0  # 上一级按钮已移除
        table = scr.query_one("#picker-table")
        keys = {str(k.value) for k in table.rows}
        assert "__up__" in keys
        # 聚焦在第一行（'..'），回车返回上一级
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert app.screen_stack[-1]._cur == os.path.abspath(root)


async def test_path_picker_hides_up_at_root():
    """文件选择器：文件系统根目录不显示 '..'（Windows 为“我的电脑”盘符视图）。"""
    import os

    from pycom.screens.filepicker import PathPicker

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(os.path.abspath(os.sep), pick_files=True))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        if os.name == "nt":
            # Windows 盘符根目录：“..”回到“我的电脑”，根视图无 “..”
            await pilot.click("#root")
            await pilot.pause(0.3)
            table = scr.query_one("#picker-table")
            keys = {str(k.value) for k in table.rows}
            assert "__up__" not in keys
        else:
            table = scr.query_one("#picker-table")
            keys = {str(k.value) for k in table.rows}
            assert "__up__" not in keys


async def test_path_picker_drives_only_in_my_computer():
    """Windows：盘符根目录正常列出文件（无盘符），只有“我的电脑”视图才只显示盘符。"""
    import os

    from pycom.screens.filepicker import PathPicker

    if os.name != "nt":
        return  # Windows 专属
    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(os.path.abspath(os.sep), pick_files=True))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        # C:\ 盘符根目录：不显示盘符，显示 “..” 与正常内容
        table = scr.query_one("#picker-table")
        keys = {str(k.value) for k in table.rows}
        assert not any(k.startswith("drive:") for k in keys)
        assert "__up__" in keys
        # “..” 回到“我的电脑”，只显示盘符
        await pilot.press("enter")
        await pilot.pause(0.3)
        table = scr.query_one("#picker-table")
        keys = [str(k.value) for k in table.rows]
        assert keys and all(k.startswith("drive:") for k in keys)
        # 选择第一个盘符进入该盘（表格按 A-Z 顺序列出）
        first = keys[0][len("drive:") :]
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert scr._cur == os.path.abspath(first)


async def test_path_picker_compact_on_small_window():
    """文件选择器在小窗口下切换为紧凑整屏布局。"""
    import tempfile

    from pycom.screens.filepicker import PathPicker

    app = PyComApp()
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(tempfile.mkdtemp(), pick_files=True))
        await pilot.pause(0.3)
        assert app.screen_stack[-1].query_one("#picker-box").has_class("compact")


async def test_path_picker_address_input_jumps():
    """地址栏输入路径并回车，跳转到该目录。"""
    import os
    import tempfile

    from pycom.screens.filepicker import PathPicker

    root = tempfile.mkdtemp()
    sub = os.path.join(root, "subdir")
    os.makedirs(sub)

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(root, pick_files=True))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        inp = scr.query_one("#picker-path")
        inp.focus()
        inp.value = sub
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert scr._cur == os.path.abspath(sub)
        # 地址栏同步为当前目录
        assert scr.query_one("#picker-path").value == os.path.abspath(sub)


async def test_path_picker_home_and_root_buttons():
    """home 与 / 按钮快速跳转到用户主目录与根目录。"""
    import os
    import tempfile

    from pycom.screens.filepicker import PathPicker

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(tempfile.mkdtemp(), pick_files=True))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        await pilot.click("#root")
        await pilot.pause(0.3)
        if os.name == "nt":
            assert scr._cur == ""  # Windows 根 = “我的电脑”（盘符视图）
        else:
            assert scr._cur == os.path.abspath(os.sep)
        await pilot.click("#home")
        await pilot.pause(0.3)
        assert scr._cur == os.path.abspath(os.path.expanduser("~"))


async def test_path_picker_back_forward_history():
    """后退/前进按钮按文件管理器习惯在浏览历史中移动。"""
    import os
    import tempfile

    from pycom.screens.filepicker import PathPicker

    root = tempfile.mkdtemp()
    sub = os.path.join(root, "sub")
    os.makedirs(sub)

    app = PyComApp()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(root, pick_files=True))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        # 初始：无历史，后退/前进均不可用
        assert scr.query_one("#back").disabled is True
        assert scr.query_one("#forward").disabled is True
        # 进入子目录（表格第 1 行 = 目录，先向下再回车）
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert scr._cur == os.path.abspath(sub)
        assert scr.query_one("#back").disabled is False
        # 后退回根目录，前进重新可用
        await pilot.click("#back")
        await pilot.pause(0.3)
        assert scr._cur == os.path.abspath(root)
        assert scr.query_one("#forward").disabled is False
        # 前进回到子目录
        await pilot.click("#forward")
        await pilot.pause(0.3)
        assert scr._cur == os.path.abspath(sub)


async def test_path_picker_nav_buttons_short_and_uniform():
    """后退/前进/home// 四个导航按钮：宽度一致且尽量短。"""
    import tempfile

    from pycom.screens.filepicker import PathPicker

    app = PyComApp()
    async with app.run_test(size=(110, 40)) as pilot:
        await pilot.pause()
        app.push_screen(PathPicker(tempfile.mkdtemp(), pick_files=True))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        widths = {
            bid: scr.query_one(f"#{bid}").region.width
            for bid in ("back", "forward", "home", "root")
        }
        assert len(set(widths.values())) == 1, f"导航按钮宽度不一致: {widths}"
        assert next(iter(widths.values())) < 16, f"导航按钮过宽: {widths}"


async def test_send_screen_path_input_is_single_line_and_wide():
    """发送界面路径输入框：单行（高度 1）且占满整行宽度。"""
    from pycom.screens.transfer import SendScreen

    app = PyComApp()
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause()
        app.push_screen(SendScreen("ymodem"))
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        inp = scr.query_one("#file")
        assert inp.region.height == 1  # 单行
        assert inp.region.width >= 55  # 足够宽
        # 与浏览按钮同一行、未换行
        assert inp.region.y == scr.query_one("#browse").region.y


async def test_datatable_arrows_step_one_row_at_a_time():
    """Regression: arrow keys used to skip every other DataTable row."""
    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        table = scr.query_one("#ports")
        for i in range(4):
            table.add_row(f"COM{i}", "fake device")
        table.focus()
        await pilot.pause()

        rows = []
        for _ in range(4):
            rows.append(table.cursor_row)
            await pilot.press("down")
            await pilot.pause(0.02)
        assert rows == [0, 1, 2, 3]
        assert len(table.rows) >= 4


async def test_connect_success_refreshes_status_bar():
    """Regression: successful connect called the non-existent
    ``app.refresh_status`` and crashed with AttributeError."""
    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()

        # fake a successful port open
        def _fake_open(settings) -> None:
            return None

        app.open_serial = _fake_open  # type: ignore[method-assign]
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr._devices = [("COM9", "fake device")]  # type: ignore[attr-defined]
        scr._selected = "COM9"  # type: ignore[attr-defined]

        # exercises the same code path as pressing Enter on a DataTable row
        scr._connect()
        await pilot.pause(0.2)

        assert len(app.screen_stack) == 1, "dialog should have dismissed after connect"


async def test_options_autofocuses_first_checkbox():
    """Entering the options dialog must focus the first item so the arrow
    keys work immediately (no need to Tab first)."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.3)
        assert app.focused.id == "echo"

        # and arrows work right away
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "wrap"


async def test_options_checkboxes_use_circle_markers():
    """Options checkboxes render a hollow circle when off and a solid
    circle when on instead of the default X marker."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.push_screen(OptionsScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]

        echo = scr.query_one("#echo")  # default: off
        assert "○" in str(echo.render())
        assert "●" not in str(echo.render())

        vt = scr.query_one("#vt")  # default send_vt_sequences=True -> on
        assert "●" in str(vt.render())

        await pilot.press("right")  # toggle echo
        await pilot.pause(0.05)
        assert "●" in str(echo.render())
        assert "○" not in str(echo.render())


async def test_connection_page_compact_and_left_aligned_buttons():
    """Connection inputs are single-line; buttons sit on their own row at the
    left; the old 断开 button was replaced by 返回."""
    from textual.widgets import Collapsible

    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]

        # 高级参数默认折叠；展开后 5 个字段都是单行
        adv = scr.query_one("#adv-params", Collapsible)
        assert adv.collapsed is True
        adv.collapsed = False
        await pilot.pause(0.2)

        for field_id in ("baud", "bytesize", "parity", "stopbits", "flow"):
            assert scr.query_one(f"#{field_id}").region.height == 1

        assert len(scr.query("#disconnect")) == 0
        ids = [b.id for b in scr.query("Button")]
        assert ids == ["refresh", "connect", "cancel"]

        row = scr.query_one("#conn-buttons")
        first = scr.query_one("#refresh")
        # buttons are flush against the left edge of their row
        assert first.region.x == row.region.x

        # 返回 closes the dialog
        cancel = scr.query_one("#cancel")
        cancel.focus()
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 1


async def test_connection_advanced_params_collapsed_by_default():
    """高级参数（数据位/校验/停止位/流控）默认折叠，隐藏字段不可见。"""
    from textual.widgets import Collapsible

    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]

        adv = scr.query_one("#adv-params", Collapsible)
        assert adv.collapsed is True
        # 折叠时隐藏字段不参与布局（region 为零），方向键导航会跳过它们
        for field_id in ("bytesize", "parity", "stopbits", "flow"):
            assert scr.query_one(f"#{field_id}").region.height == 0
        # 波特率始终可见
        assert scr.query_one("#baud").region.height == 1


async def test_connection_advanced_params_toggle_shows_fields():
    """展开高级参数后字段可见，方向键可聚焦标题、Enter 可再次折叠。"""
    from textual.widgets import Collapsible

    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]

        adv = scr.query_one("#adv-params", Collapsible)
        adv.collapsed = False
        await pilot.pause(0.2)
        for field_id in ("bytesize", "parity", "stopbits", "flow"):
            assert scr.query_one(f"#{field_id}").region.height == 1

        # 方向键可聚焦到折叠标题，Enter 可再次折叠
        title = scr.query_one("#adv-params CollapsibleTitle")
        title.focus()
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert adv.collapsed is True


async def test_connection_advanced_params_values_survive_collapse():
    """折叠/展开不丢失已填写的参数值。"""
    from textual.widgets import Collapsible, Input

    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]

        adv = scr.query_one("#adv-params", Collapsible)
        adv.collapsed = False
        await pilot.pause(0.2)
        scr.query_one("#bytesize", Input).value = "7"
        scr.query_one("#parity", Input).value = "E"
        scr.query_one("#stopbits", Input).value = "2"
        scr.query_one("#flow", Input).value = "rtscts"
        adv.collapsed = True
        await pilot.pause(0.2)
        adv.collapsed = False
        await pilot.pause(0.2)
        assert scr.query_one("#bytesize", Input).value == "7"
        assert scr.query_one("#parity", Input).value == "E"
        assert scr.query_one("#stopbits", Input).value == "2"
        assert scr.query_one("#flow", Input).value == "rtscts"


async def test_connection_advanced_params_state_survives_compact_swap():
    """富↔简洁切换保留高级参数的折叠状态。"""
    from textual.widgets import Collapsible

    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.3)
        scr = app.screen_stack[-1]
        adv = scr.query_one("#adv-params", Collapsible)
        adv.collapsed = False
        await pilot.pause(0.2)

        # 缩到小窗口切简洁模式，再放大切回富布局
        await pilot.resize_terminal(58, 15)
        await pilot.pause(0.4)
        assert scr.query_one("#conn-box").has_class("compact") is True
        await pilot.resize_terminal(100, 34)
        await pilot.pause(0.4)
        assert scr.query_one("#conn-box").has_class("compact") is False
        assert scr.query_one("#adv-params", Collapsible).collapsed is False


# --------------------------------------------------------------------------- new behaviour


async def test_enter_thrice_without_port_shows_reminder():
    """Pressing Enter 3 times with no port connected pops a reminder."""
    app = PyComApp()
    notes: list[str] = []

    async with app.run_test(size=(100, 28)) as pilot:
        app.notify = lambda message, *a, **k: notes.append(str(message))  # type: ignore[method-assign]
        await pilot.pause()
        assert app.is_connected() is False

        await pilot.press("enter")
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.05)

        assert any("未连接端口" in n for n in notes)


async def test_hex_menu_toggle_mounts_and_removes_bar():
    """HEX off -> no hex widgets in the DOM at all (so combos keep working);
    HEX on -> the bottom bar is mounted; toggling off removes it again."""
    app = PyComApp()
    mapper_calls: list[str] = []

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert len(app.query("#hex-bar")) == 0  # not mounted in normal mode

        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.2)
        assert app.cfg.hex_mode is True
        assert len(app.query("#hex-bar")) == 1
        assert app.query_one("#hex-input").can_focus is True
        assert app.query_one("#hex-send").can_focus is True
        # enabling via the shortcut auto-focuses the hex editor
        assert app.focused.id == "hex-input"

        # typing now goes into the hex editor, never through the byte mapper
        orig_map = app.mapper.map
        app.mapper.map = lambda key, char: (mapper_calls.append(key), orig_map(key, char))[1]  # type: ignore[method-assign]
        await pilot.press("a")
        await pilot.press("b")
        await pilot.pause(0.1)
        assert mapper_calls == []
        assert app.query_one("#hex-input").text == "AB"

        # Ctrl+A prefix still works while the hex editor exists
        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause(0.2)
        assert len(app.screen.query("#menu-popup")) == 1
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert len(app.screen.query("#menu-popup")) == 0

        # toggling off removes the bar again -> normal DOM restored
        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.2)
        assert app.cfg.hex_mode is False
        assert len(app.query("#hex-bar")) == 0


async def test_hex_editor_autoformats_and_rejects_invalid():
    """The hex editor adds a space after every byte and strips non-hex input."""
    app = PyComApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.2)
        field = app.query_one("#hex-input")
        assert field.text == ""

        # mixed/illegal input is filtered, uppercase, spaced per byte
        field.text = "aabbGG 0d"
        await pilot.pause(0.1)
        assert field.text == "AA BB 0D"

        # 超出宽度的长内容会按“每行 N 字节”分组换行（N 由输入框宽度决定）
        per_line = hex_bytes_per_line(max(1, field.size.width), max_bytes=32)
        assert per_line > 16  # 宽窗口下不再是固定 16 字节/行
        field.text = "AA" * (per_line + 3)
        await pilot.pause(0.1)
        lines = field.text.split("\n")
        assert [len(line.split()) for line in lines] == [per_line, 3]


async def test_hex_send_box_reflows_with_width():
    """发送框的换行随窗口宽度变化（32/16/8/4），变窄后再变宽恢复原分组。

    回归：发送框曾固定在 16 字节/行封顶，窗口再宽也不换行。
    """
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause(0.2)
        field = app.query_one("#hex-input")
        field.text = "AA" * 40  # 40 字节
        await pilot.pause(0.1)

        def per_line() -> int:
            return hex_bytes_per_line(max(1, field.size.width), max_bytes=32)

        def groups() -> list[int]:
            return [len(line.split()) for line in field.text.split("\n")]

        def widest() -> int:
            return max(len(line) for line in field.text.split("\n"))

        assert per_line() == 32
        assert groups() == [32, 8]

        await pilot.resize_terminal(60, 30)
        await pilot.pause(0.3)
        assert per_line() == 16
        assert groups() == [16, 16, 8]

        await pilot.resize_terminal(40, 30)
        await pilot.pause(0.3)
        assert per_line() == 8
        assert groups() == [8] * 5

        await pilot.resize_terminal(140, 30)
        await pilot.pause(0.3)
        assert per_line() == 32
        assert groups() == [32, 8]  # 变宽后恢复
        assert widest() <= field.size.width  # 内容始终不超出输入框


async def test_hex_receive_displays_hex_text():
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app.cfg.hex_mode = True
        app._rx_to_terminal(b"\x41\x42\x0d")
        await pilot.pause(0.3)
        assert "41 42 0D" in str(app._view().render())


async def test_hex_receive_separates_rx_chunks():
    """Two separately received chunks must not merge their boundary bytes
    (last byte of chunk 1 and first byte of chunk 2 keep a space)."""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app.cfg.hex_mode = True
        app._rx_to_terminal(b"\x41\x42")  # chunk 1 ends with 42
        await pilot.pause(0.05)
        app._rx_to_terminal(b"\x0d\x0a")  # chunk 2 starts with 0D
        await pilot.pause(0.3)
        text = str(app._view().render())
        assert "41 42 0D 0A" in text
        assert "420D" not in text


async def test_hex_receive_multiline_wraps_to_line_start():
    """RX chunks longer than one hex line wrap cleanly: the bytes-per-line
    adapts to the display width and each wrapped line must start at column 0
    (format_hex's LF separator is sent as CR+LF, otherwise a bare LF makes every
    subsequent line drift right like a staircase)."""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        per_line = app._hex_rx_per_line()
        app._rx_to_terminal(bytes(range(per_line + 1)))  # 一整行 + 1 字节
        await pilot.pause(0.3)
        rows = ["".join(c.data for c in row).rstrip() for row in app.model.screen_rows()]
        nonempty = [r for r in rows if r]
        assert len(nonempty) >= 2
        assert nonempty[0] == " ".join(f"{b:02X}" for b in range(per_line))
        # 第二行必须从行首开始，前面不能有缩进（回归 \n -> \r\n 修复）
        assert nonempty[1].startswith(f"{per_line:02X}")
        assert nonempty[1] == nonempty[1].lstrip()


async def test_hex_receive_does_not_break_on_cr_lf_bytes():
    """HEX 接收时真实的 0A/0D 字节只是普通数据，显示为 "0A"/"0D"，
    不应像文本模式那样在换行字节处断行——只按字节数分组换行。"""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app._rx_to_terminal(b"AB\r\nCD")  # 含真实 CR/LF（0D 0A）
        await pilot.pause(0.3)
        rows = ["".join(c.data for c in row).rstrip() for row in app.model.screen_rows()]
        nonempty = [r for r in rows if r]
        # 全部留在同一显示行内顺序显示，0D/0A 处没有产生额外断行
        assert nonempty == ["41 42 0D 0A 43 44"]


async def test_hex_receive_wraps_across_small_chunks():
    """连续到达的多个小块也要严格按“每行 N 字节”换行（与发送区一致的连续
    自动换行），而不是每个块各自排版、长期堆在同一行不换行。"""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        per_line = app._hex_rx_per_line()
        # 每块只发 2 字节，但累计超过 per_line 字节后必须发生换行
        chunk = b"\xaa\xbb"
        for _ in range(per_line // 2 + 1):
            app._rx_to_terminal(chunk)
        await pilot.pause(0.3)
        rows = ["".join(c.data for c in row).rstrip() for row in app.model.screen_rows()]
        nonempty = [r for r in rows if r]
        assert nonempty[0] == " ".join("AA BB" for _ in range(per_line // 2))
        # 溢出部分从新一行第 0 列开始
        assert nonempty[1] == "AA BB"
        assert nonempty[1] == nonempty[1].lstrip()


async def test_hex_receive_ascii_pane():
    """The separate right-hand pane shows the printable ASCII for the visible
    hex rows: visible ASCII as the character, control/extended bytes as a grey
    dot."""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        per_line = app._hex_rx_per_line()
        row = b"A\x00B\x80" + bytes([0x63]) * (per_line - 4)
        app._rx_to_terminal(row)
        await pilot.pause(0.3)

        pane = app.query_one("#hex-ascii-pane")
        rt = pane.render()
        # 一满行：A . B . cccc...
        assert rt.plain.strip() == "A.B." + "c" * (per_line - 4)
        # 灰色圆点带样式，可打印字符保持默认
        gray_spans = [s for s in rt.spans if s.style and s.style.color]
        assert len(gray_spans) >= 2


async def test_hex_receive_ascii_updates_in_real_time():
    """未满一行的尾部行，其 ASCII 字符也会随字节到达在分栏里实时补全。"""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        pane = app.query_one("#hex-ascii-pane")

        # 只有 2 字节（未满一行）：分栏立即显示 "AB"
        app._rx_to_terminal(b"AB")
        await pilot.pause(0.3)
        pane.refresh()
        assert pane.render().plain.strip() == "AB"

        # 再补 2 字节：分栏实时补全为 "AB.C"
        app._rx_to_terminal(b"\x00C")
        await pilot.pause(0.3)
        pane.refresh()
        assert pane.render().plain.strip() == "AB.C"


async def test_hex_ascii_pane_width_follows_bytes_per_line():
    """ASCII 分栏宽度跟随“每行字节数”（32/16/8/4）：宽窗口 32 字节 -> 33 列
    （32 个字符 + 1 列分隔边框），窄窗口退到 16 字节 -> 17 列。

    分栏宽度由整个终端区宽度决定（``4n <= 总宽度``），因此不会出现
    “分栏变宽 -> hex 区变窄 -> 每行字节数又变”的来回抖动。
    """
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(140, 28)) as pilot:
        await pilot.pause(0.3)
        pane = app.query_one("#hex-ascii-pane")
        assert app._hex_rx_per_line() == 32
        assert pane.content_size.width == 32  # 内容区正好 32 个字符
        assert pane.region.width == 33

        # 变窄：每行 16 字节，分栏随之缩到 17 列（不再固定 33 列）
        await pilot.resize_terminal(95, 28)
        await pilot.pause(0.3)
        assert app._hex_rx_per_line() == 16
        assert pane.region.width == 17
        assert pane.content_size.width == 16

        # 更窄：8 字节/行 -> 9 列
        await pilot.resize_terminal(63, 28)
        await pilot.pause(0.3)
        assert app._hex_rx_per_line() == 8
        assert pane.region.width == 9

        # 变回宽窗口：恢复 32/33
        await pilot.resize_terminal(140, 28)
        await pilot.pause(0.3)
        assert app._hex_rx_per_line() == 32
        assert pane.region.width == 33
        assert pane.content_size.width == 32


async def test_hex_ascii_pane_fits_a_full_32_byte_row():
    """分栏宽度按最多 32 个字符设置：满行 32 字节时最后一个 ASCII 字符也要
    完整显示（分栏不能比它要显示的字符更宽/更窄）。"""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(140, 28)) as pilot:
        await pilot.pause()
        assert app._hex_rx_per_line() == 32  # 宽窗口：每行 32 字节
        app._rx_to_terminal(bytes(range(0x41, 0x61)))  # 32 个可打印字符
        await pilot.pause(0.3)
        pane = app.query_one("#hex-ascii-pane")
        line = pane.render().plain.split("\n")[0]
        assert len(line) == 32
        # 去掉左侧 1 列分隔边框后，内容区正好容纳 32 个字符
        assert pane.content_size.width == 32


async def test_hex_reflow_on_resize_keeps_every_byte():
    """HEX 模式下先变窄再变宽：被挤出旧行的字节必须按新宽度重新排版显示。

    pyte 变窄时会裁掉超出新宽度的字符且变宽后无法还原；应用保留 HEX 模式下
    收到的原始字节，每行字节数变化时整体重排，所以字节一个都不能少。"""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(110, 28)) as pilot:
        await pilot.pause()
        wide_per_line = app._hex_rx_per_line()
        data = bytes(range(64))
        app._rx_to_terminal(data)
        await pilot.pause(0.2)

        def tokens() -> list[str]:
            rows = app.model.history_rows() + app.model.screen_rows()
            out: list[str] = []
            for row in rows:
                out.extend("".join(c.data for c in row).split())
            return out

        expected = [f"{b:02X}" for b in data]
        assert tokens() == expected

        # 变窄：每行字节数变小，原来 16 字节/行的内容会被裁掉
        await pilot.resize_terminal(50, 28)
        await pilot.pause(0.3)
        narrow_per_line = app._hex_rx_per_line()
        assert narrow_per_line < wide_per_line
        assert tokens() == expected  # 重排后一个字节都不少
        rows = ["".join(c.data for c in row).rstrip() for row in app.model.screen_rows()]
        assert rows[0] == " ".join(f"{b:02X}" for b in data[:narrow_per_line])

        # 变宽：恢复原来的每行字节数，内容依然完整（不再永久丢失）
        await pilot.resize_terminal(110, 28)
        await pilot.pause(0.3)
        assert app._hex_rx_per_line() == wide_per_line
        assert tokens() == expected
        rows = ["".join(c.data for c in row).rstrip() for row in app.model.screen_rows()]
        assert rows[0] == " ".join(f"{b:02X}" for b in range(wide_per_line))


async def test_hex_send_button_transmits_bytes():
    app = PyComApp()
    sent: list[bytes] = []
    notes: list[str] = []

    async with app.run_test(size=(100, 28)) as pilot:
        app.notify = lambda message, *a, **k: notes.append(str(message))  # type: ignore[method-assign]
        app.is_connected = lambda: True  # type: ignore[method-assign]
        app.serial.write = lambda data: sent.append(bytes(data))  # type: ignore[method-assign]
        await pilot.pause()

        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.1)
        assert app.cfg.hex_mode is True

        field = app.query_one("#hex-input")
        field.text = "AA 0D 7F"
        await pilot.pause(0.05)
        app._send_hex_box()
        assert sent == [b"\xaa\x0d\x7f"]
        # 发送后发送区内容被保留，可直接再次发送同一批数据
        assert field.text == "AA 0D 7F"
        app._send_hex_box()
        assert sent == [b"\xaa\x0d\x7f", b"\xaa\x0d\x7f"]

        # non-hex input is stripped by the editor -> empty warning, nothing sent
        field.text = "GG"
        await pilot.pause(0.05)
        app._send_hex_box()
        assert sent == [b"\xaa\x0d\x7f", b"\xaa\x0d\x7f"]
        assert any("输入字节" in n for n in notes)


async def test_hex_enter_in_input_sends_bytes():
    """HEX 输入框内按回车直接发送，而不是插入换行；发送后内容保留。"""
    app = PyComApp()
    sent: list[bytes] = []
    notes: list[str] = []

    async with app.run_test(size=(100, 28)) as pilot:
        app.notify = lambda message, *a, **k: notes.append(str(message))  # type: ignore[method-assign]
        app.is_connected = lambda: True  # type: ignore[method-assign]
        app.serial.write = lambda data: sent.append(bytes(data))  # type: ignore[method-assign]
        await pilot.pause()

        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.2)
        assert app.cfg.hex_mode is True
        assert app.focused.id == "hex-input"  # 快捷键开启后自动聚焦输入框

        # 逐键输入字节，回车即发送
        for ch in ("A", "A", " ", "0", "D"):
            await pilot.press(ch)
            await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert sent == [b"\xaa\x0d"]
        # 回车不会在输入框里插入换行
        assert "\n" not in app.query_one("#hex-input").text
        # 发送后焦点仍留在输入框、内容保留，可继续输入
        assert app.focused.id == "hex-input"

        # 输入框为空时回车：不发送、也不弹提示框（静默忽略）
        app.query_one("#hex-input").text = ""
        await pilot.pause(0.05)
        notes.clear()
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert sent == [b"\xaa\x0d"]
        assert notes == []


async def test_hex_toast_dismissed_by_enter_without_stacking():
    """HEX 模式启动时的 toast（如“已连接 COM3”）按回车应关闭，且不再弹出新的
    “请先输入字节”提示框。

    回归：焦点在 16 进制输入框（TextArea）时按键会被它 ``event.stop()``，不会
    冒泡到 ``App._on_key``，之前的实现里提示框因此永远关不掉；而回车又会走
    “空输入框 -> 弹提示”的分支，看起来就是提示框越堆越多。
    """
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.3)
        assert app.focused.id == "hex-input"  # 焦点在输入框：按键被 TextArea 消费
        app.notify("已连接 COM3", timeout=60)  # 模拟启动连接提示
        await pilot.pause(0.1)
        assert len(app._notifications) == 1

        await pilot.press("enter")
        await pilot.pause(0.2)
        assert len(app._notifications) == 0, "回车应关闭提示框"

        # 反复按回车：提示框不应重新弹出，也不应堆叠
        for _ in range(3):
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert len(app._notifications) == 0


async def test_hex_toast_closed_by_key_without_swallowing_it():
    """关闭提示框的那次按键仍要照常执行（这里把十六进制位输入到输入框）。"""
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(hex_mode=True))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.3)
        app.notify("HEX 模式已开启", timeout=60)
        await pilot.pause(0.1)
        await pilot.press("a")
        await pilot.pause(0.1)
        assert len(app._notifications) == 0
        assert app.query_one("#hex-input").text == "A", "按键不能被吞掉"


async def test_hex_toggle_keeps_its_own_toast():
    """Ctrl+A H 的按键先关掉旧提示，但不能把这次切换刚产生的提示一并清掉。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        app.notify("旧提示", timeout=60)
        await pilot.pause(0.1)
        await pilot.press("ctrl+a")
        await pilot.press("h")
        await pilot.pause(0.3)
        assert app.cfg.hex_mode is True
        messages = [n.message for n in app._notifications]
        assert len(messages) == 1
        assert "HEX 模式已开启" in messages[0]


async def test_idle_exit_when_no_bytes_received():
    app = PyComApp(exit_idle=0.2)
    exited: list = []

    async with app.run_test(size=(100, 28)) as pilot:
        app.exit = lambda *a, **k: exited.append(a)  # type: ignore[method-assign]
        await pilot.pause()
        app._last_rx = time.monotonic() - 5.0
        app._tick()
        assert exited, "idle watchdog should have triggered exit"

        # fresh data keeps the app alive
        app._last_rx = time.monotonic()
        app._tick()
        assert len(exited) == 1


# --------------------------------------------------------------------------- CLI arguments


def test_cli_short_params_removed_long_kept():
    from pycom.app import _parse_args

    args = _parse_args(
        [
            "--port",
            "COM1",
            "--data-bits",
            "7",
            "--parity",
            "E",
            "--stop-bits",
            "1.5",
            "--flow",
            "rtscts",
        ]
    )
    assert args.data_bits == 7
    assert args.parity == "E"
    assert args.stop_bits == 1.5
    assert args.flow == "rtscts"

    with pytest.raises(SystemExit):
        _parse_args(["-d", "8"])  # short alias removed
    with pytest.raises(SystemExit):
        _parse_args(["-f", "rtscts"])  # short alias removed (needs -p now)


def test_cli_send_and_script_require_port():
    from pycom.app import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["-s", "AT\r"])
    with pytest.raises(SystemExit):
        _parse_args(["-f", "boot.txt"])

    args = _parse_args(["-p", "COM3", "-s", "AT\r"])
    assert args.port == "COM3"
    assert args.send == "AT\r"

    args = _parse_args(["-p", "COM3", "-f", "boot.txt", "-b", "115200"])
    assert args.script == "boot.txt"


# --------------------------------------------------------------------------- virtual loopback


async def test_connection_page_lists_virtual_loopback():
    from pycom.screens.connection import ConnectionScreen

    app = PyComApp(enable_debug=True)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert any(dev == "LOOPBACK" for dev, _ in scr._devices)  # type: ignore[attr-defined]
        assert scr._devices[-1][0] == "LOOPBACK"  # type: ignore[attr-defined]


async def test_connection_page_hides_virtual_loopback_by_default():
    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert not any(dev == "LOOPBACK" for dev, _ in scr._devices)  # type: ignore[attr-defined]


async def test_connect_virtual_loopback_routes_to_open_loopback():
    from pycom.screens.connection import ConnectionScreen

    app = PyComApp(enable_debug=True)
    calls: list = []

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.open_loopback = lambda: (calls.append(1), None)[1]  # type: ignore[method-assign]
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        scr._selected = "LOOPBACK"  # type: ignore[attr-defined]
        scr._connect()  # type: ignore[attr-defined]
        await pilot.pause(0.2)
        assert calls == [1]
        assert len(app.screen_stack) == 1, "loopback connect should dismiss the dialog"


async def test_loopback_echoes_sent_bytes():
    """Virtual loopback: sent bytes come straight back as received text."""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        assert app.is_connected() is True
        assert "回环" in app._status_text()

        app.send_bytes(b"abc")
        await pilot.pause(0.3)
        assert "abc" in str(app._view().render())
        assert app._tx == 3
        assert app._rx == 3

        app.close_serial()
        await pilot.pause(0.05)
        assert app.is_connected() is False


async def test_clear_screen_resets_tx_rx_counters():
    """清屏 (Ctrl+A C) 同时复位状态栏的 TX/RX 字节计数器与显示内容。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None

        app.send_bytes(b"hello")
        await pilot.pause(0.3)
        assert app._tx == 5
        assert app._rx == 5
        assert "hello" in str(app._view().render())

        # Ctrl+A C 走清屏动作
        await pilot.press("ctrl+a")
        await pilot.press("c")
        await pilot.pause(0.3)

        assert app._tx == 0, "清屏后 TX 计数应归零"
        assert app._rx == 0, "清屏后 RX 计数应归零"
        assert not "".join(c.data for r in app.model.screen_rows() for c in r).strip()
        assert "TX 0" in app._status_text()
        assert "RX 0" in app._status_text()


async def test_loopback_echoes_cr_as_crlf():
    """A lone \r sent into the loopback comes back as \r\n (like a real
    terminal), so Enter starts a new line instead of overwriting it."""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.3)

        # 程序一启动就打印菜单快捷键提示（本地打印，不计入 RX 计数）
        rows0 = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        assert "按 Ctrl+A Z 打开功能菜单" in rows0[0]
        assert app._tx == 0 and app._rx == 0

        assert app.open_loopback() is None
        await pilot.pause(0.2)
        # 连接成功后打印“已连接”提示（前后各留一行空行）
        rows1 = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        assert rows1[1] == ""  # 提示前空行
        assert "已连接" in rows1[2] and "虚拟回环" in rows1[2]
        assert rows1[3] == ""  # 提示后空行

        # "a\rb" echoes as "a\r\nb" -> a then b on consecutive lines (no blank row)
        app.send_bytes(b"a\rb")
        await pilot.pause(0.3)
        rows = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        assert rows[4] == "a"
        assert rows[5] == "b"
        assert app._tx == 3
        assert app._rx == 4  # the lone \r is echoed as two bytes (\r\n)

        # an already-formed \r\n must not double up into two line breaks
        app.send_bytes(b"\r\n")
        await pilot.pause(0.3)
        assert app._tx == 5
        assert app._rx == 6  # \r\n echoed unchanged (2 bytes)


class _FakeSerial:
    """SerialManager 替身：记录打开状态与写入内容（供“切换连接”测试用）。"""

    def __init__(self) -> None:
        self._open = False
        self.written: list[bytes] = []

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self, settings) -> str | None:
        self.settings = settings
        self._open = True
        return None

    def close(self) -> None:
        self._open = False

    def write(self, data: bytes) -> bool:
        if not self._open:
            return False
        self.written.append(data)
        return True

    def set_dtr(self, _value: bool) -> None:
        pass

    def set_rts(self, _value: bool) -> None:
        pass


async def test_switch_from_loopback_to_real_port(monkeypatch):
    """Regression: LOOPBACK → 真实串口 切换后必须退出回环模式。

    旧代码 open_serial() 不复位 _loopback：真实串口虽已打开，但发送仍被
    回环分支截走、状态栏仍显示“虚拟回环”，看起来就像“切换不成功”。
    """
    from pycom.config import ConnectionSettings

    monkeypatch.setattr("pycom.app.save_config", lambda cfg: None)

    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        fake = _FakeSerial()
        app.serial = fake  # type: ignore[assignment]

        assert app.open_loopback() is None
        assert app._loopback is True
        assert "虚拟回环" in app._status_text()

        # ConnectionScreen 对非 LOOPBACK 行调用的正是 open_serial
        settings = ConnectionSettings(port="COM42", baudrate=9600)
        assert app.open_serial(settings) is None

        assert app._loopback is False, "切到真实串口后应退出虚拟回环"
        assert fake.is_open
        assert "虚拟回环" not in app._status_text()
        assert "COM42" in app._status_text()

        # 发送必须到达真实串口，而不是被回环截走
        app.send_bytes(b"z")
        assert fake.written == [b"z"]


async def test_switch_real_to_real_keeps_sending_to_new_port(monkeypatch):
    """真实串口 → 另一真实串口：数据发往新端口。"""
    from pycom.config import ConnectionSettings

    monkeypatch.setattr("pycom.app.save_config", lambda cfg: None)

    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        fake = _FakeSerial()
        app.serial = fake  # type: ignore[assignment]

        assert app.open_serial(ConnectionSettings(port="COM1", baudrate=9600)) is None
        app.send_bytes(b"a")
        assert fake.written == [b"a"]

        assert app.open_serial(ConnectionSettings(port="COM2", baudrate=115200)) is None
        app.send_bytes(b"b")
        assert app._loopback is False
        assert fake.written == [b"a", b"b"]
        assert "COM2" in app._status_text()


def test_cli_exit_idle_accepts_float_and_rejects_nonpositive():
    from pycom.app import _parse_args

    assert _parse_args(["-e", "0.5"]).exit_idle == 0.5
    assert _parse_args(["--exit-idle", "3"]).exit_idle == 3.0
    with pytest.raises(SystemExit):
        _parse_args(["-e", "0"])
    with pytest.raises(SystemExit):
        _parse_args(["-e", "-2"])


def test_cli_hex_flag_parsed():
    from pycom.app import _parse_args

    assert _parse_args(["--hex"]).hex is True
    assert _parse_args([]).hex is False


def test_cli_no_mouse_flag_parsed():
    from pycom.app import _parse_args

    assert _parse_args(["--no-mouse"]).no_mouse is True
    assert _parse_args([]).no_mouse is False


def test_cli_enable_debug_parsed_and_hidden_from_help(capsys):
    from pycom.app import _parse_args

    assert _parse_args(["--enable-debug"]).enable_debug is True
    assert _parse_args([]).enable_debug is False

    # --enable-debug 是隐藏调试开关，不出现在 --help 文本中
    with pytest.raises(SystemExit) as exc:
        _parse_args(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--enable-debug" not in help_text


async def test_hex_mode_enabled_at_startup_shows_bar():
    """A config with hex_mode=True (set by the --hex flag) starts in HEX mode."""
    from pycom.config import AppConfig

    cfg = AppConfig(hex_mode=True)
    app = PyComApp(cfg=cfg)
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        assert app.cfg.hex_mode is True
        assert len(app.query("#hex-bar")) == 1
        assert app.query_one("#hex-input").can_focus is True

        # received bytes are rendered as hex right away
        app._rx_to_terminal(b"\x55\xaa")
        await pilot.pause(0.3)
        assert "55 AA" in str(app._view().render())


# --------------------------------------------------------------------------- small-window compact fallback


async def test_options_compact_on_small_window():
    """A too-small window switches to the simple full-width layout instead of
    the boxed multi-column form, and it stays keyboard-navigable."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(60, 16)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is True
        assert box.region.width == 60 and box.region.height == 16  # fills window

        # first field autofocused; arrows still move through the fields
        assert app.focused.id == "echo"
        await pilot.press("down")
        await pilot.pause(0.02)
        assert app.focused.id == "wrap"

        # every control of the dialog exists inside the simple layout
        for cid in (
            "echo",
            "wrap",
            "rx_cr",
            "rx_lf",
            "ts",
            "vt",
            "enter",
            "back",
            "decode",
            "timeout",
            "retries",
            "blocksize",
            "save",
            "cancel",
        ):
            assert len(scr.query(f"#{cid}")) == 1, f"missing #{cid} in compact mode"

        # 取消 closes the dialog
        scr.query_one("#cancel").focus()
        await pilot.pause(0.02)
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert len(app.screen_stack) == 1


async def test_options_switches_layout_when_resized_and_keeps_edits():
    """Resizing across the threshold swaps rich <-> compact in place while
    keeping the values the user already typed/toggled."""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is False  # rich on a big window

        # make some edits before shrinking
        scr.query_one("#echo").focus()
        await pilot.press("right")
        await pilot.pause(0.05)
        scr.query_one("#timeout").value = "99"
        await pilot.pause(0.05)

        # shrink -> the compact layout keeps the values
        await pilot.resize_terminal(58, 15)
        await pilot.pause(0.4)
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is True
        assert scr.query_one("#echo").value is True
        assert scr.query_one("#timeout").value == "99"

        # grow back -> the rich layout keeps the values
        await pilot.resize_terminal(100, 32)
        await pilot.pause(0.4)
        box = scr.query_one("#options-box")
        assert box.has_class("compact") is False
        assert scr.query_one("#echo").value is True
        assert scr.query_one("#timeout").value == "99"


async def test_connection_compact_on_small_window_and_connect():
    """Small-window connection page replaces the port table with a dropdown
    (LOOPBACK still offered when --enable-debug) and connecting to it still works."""
    from pycom.screens.connection import ConnectionScreen

    app = PyComApp(enable_debug=True)
    calls: list = []

    async with app.run_test(size=(58, 15)) as pilot:
        await pilot.pause(0.2)
        app.open_loopback = lambda: (calls.append(1), None)[1]  # type: ignore[method-assign]
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        box = scr.query_one("#conn-box")
        assert box.has_class("compact") is True
        assert len(scr.query("#ports")) == 0  # no DataTable in simple mode
        select = scr.query_one("#port-sel")
        assert app.focused is select
        assert any(dev == "LOOPBACK" for dev, _ in scr._devices)  # type: ignore[attr-defined]

        # the dropdown is filled with the detected ports (preselected)
        names = [dev for dev, _ in scr._devices]  # type: ignore[attr-defined]
        assert "LOOPBACK" in names
        assert str(select.value) in names

        # connect via the virtual loopback (same code path as rich mode)
        scr._selected = "LOOPBACK"  # type: ignore[attr-defined]
        scr._connect()  # type: ignore[attr-defined]
        await pilot.pause(0.2)
        assert calls == [1]
        assert len(app.screen_stack) == 1, "dialog dismissed after connect"


# --------------------------------------------------------------------------- 简洁模式自动切换阈值


async def test_connection_compact_threshold_is_30_rows():
    """连接页富布局在高度 <30 时自动切换为简洁模式（30 行及以上保持富布局）。"""
    from pycom.screens.connection import ConnectionScreen

    app = PyComApp()
    async with app.run_test(size=(100, 29)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(ConnectionScreen())
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#conn-box").has_class("compact") is True
        await pilot.resize_terminal(100, 30)
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#conn-box").has_class("compact") is False


async def test_options_compact_threshold_is_27_rows():
    """选项页富布局在高度 <27 时自动切换为简洁模式（27 行及以上保持富布局）。"""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 26)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#options-box").has_class("compact") is True
        await pilot.resize_terminal(100, 27)
        await pilot.pause(0.4)
        assert app.screen_stack[-1].query_one("#options-box").has_class("compact") is False


async def test_compact_controls_start_at_the_same_column():
    """简洁模式下输入框与下拉框从同一列开始且等宽，不再参差不齐。"""
    from pycom.screens.options import OptionsScreen

    app = PyComApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause(0.2)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.4)
        scr = app.screen_stack[-1]
        assert scr.query_one("#options-box").has_class("compact") is True
        tabs = scr.query_one("#options-body")

        def check(cids):
            xs = [scr.query_one(f"#{cid}").region.x for cid in cids]
            widths = [scr.query_one(f"#{cid}").region.width for cid in cids]
            assert len(set(xs)) == 1, f"控件左缘未对齐: {xs}"
            assert len(set(widths)) == 1, f"控件宽度不一致: {widths}"

        # 终端 tab（默认活动）内的控件对齐
        check(("enter", "back", "decode"))
        # 文件传输 tab
        tabs.active = "tab-3"
        await pilot.pause(0.1)
        check(("timeout", "retries", "blocksize"))


async def test_main_menu_popup_usable_on_small_window():
    """功能菜单改为弹出式后，在小窗口下弹层也应完整可用：所有条目都能用
    方向键到达，Esc 可关闭。"""
    app = PyComApp()
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause(0.3)
        assert len(app.screen.query("#menu-popup")) == 1
        assert app.focused.id == "menu-p"
        # 遍历到最后一项
        for _ in range(8):
            await pilot.press("down")
            await pilot.pause(0.02)
        assert app.focused.id == "menu-x"
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert len(app.screen.query("#menu-popup")) == 0


async def test_notifications_cleared_by_any_key():
    """未连接端口 / HEX 模式等 toast 提示放在左侧显示，按下任意键即关闭。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        app.notify("未连接端口：请按 Ctrl+A P 连接后再试", timeout=60)
        await pilot.pause(0.1)
        assert len(app._notifications) == 1
        await pilot.press("a")
        await pilot.pause(0.2)
        assert len(app._notifications) == 0, "任意键应关闭 toast 提示"


async def test_toast_renders_at_bottom_left():
    """toast（未连接端口 / HEX 模式等）在屏幕左下角显示（ToastHolder 左对齐）。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28), notifications=True) as pilot:
        await pilot.pause(0.3)
        app.notify("未连接端口：请按 Ctrl+A P 连接后再试", severity="warning", timeout=60)
        await pilot.pause(0.5)
        toast = next(w for w in app.screen.walk_children() if type(w).__name__ == "Toast")
        # 左对齐：toast 左缘靠近屏幕左缘；位于屏幕底部区域
        assert toast.region.x <= 2
        assert toast.region.y >= 20
        # 任意键关闭
        await pilot.press("a")
        await pilot.pause(0.2)
        assert len(app._notifications) == 0


# --------------------------------------------------------------------------- 左下角“菜单”按钮 + 启动/连接提示


async def test_main_screen_menu_button_bottom_left_opens_menu():
    """主界面左下角的“菜单”按钮；点击打开同一功能菜单，
    且按钮不抢占键盘焦点（can_focus=False）。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        btn = app.query_one("#menu-btn")
        assert btn.can_focus is False  # 只响应鼠标，不让按键焦点离开终端
        assert "菜单" in str(btn.render())
        bottom = app.query_one("#bottom")
        # 位于最底行、紧贴左缘
        assert bottom.region.y + bottom.region.height == app.size.height
        assert btn.region.y == bottom.region.y and btn.region.height == 1
        assert btn.region.x == bottom.region.x
        # 状态栏不再重复显示“Ctrl+A Z”文字提示（交给按钮 + 启动首行提示）
        assert "Ctrl+A Z" not in app._status_text()

        # 点击打开主菜单（与 Ctrl+A Z 等价，弹出式弹层）
        await pilot.click("#menu-btn")
        await pilot.pause(0.3)
        assert len(app.screen.query("#menu-popup")) == 1, "菜单按钮应打开主菜单弹层"


async def test_startup_hint_shown_without_connection():
    """程序一启动（无论是否连接端口）就在终端首行显示橙/粗体的菜单快捷键提示。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.2)
        assert app.is_connected() is False  # 没有连接端口
        row = app.model.screen_rows()[0]
        text = "".join(c.data for c in row).rstrip()
        assert "按 Ctrl+A Z 打开功能菜单" in text
        c = next(x for x in row if x.data.strip())
        assert c.bold is True and c.fg == "ffa500"  # 橙色加粗
        assert app._rx == 0 and app._tx == 0  # 本地提示不是串口收发


async def test_connect_hint_shown_in_orange_bold():
    """连接上端口后在屏幕上打印“已连接”提示（橙/粗体），每次连接都会打印。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.3)
        assert app.open_loopback() is None
        await pilot.pause(0.2)

        rows = ["".join(c.data for c in r).rstrip() for r in app.model.screen_rows()]
        # 第 0 行是启动菜单提示；第 1 行空行；第 2 行是“已连接”提示；第 3 行空行
        assert "按 Ctrl+A Z 打开功能菜单" in rows[0]
        assert rows[1] == ""
        assert rows[2].startswith("已连接 虚拟回环")
        assert rows[3] == ""
        line = app.model.screen_rows()[2]
        c = next(x for x in line if x.data.strip())
        assert c.bold is True and c.fg == "ffa500"
        assert app._rx == 0 and app._tx == 0

        # 清屏后再次连接仍会打印“已连接”提示
        app.model.clear()
        assert app.open_loopback() is None
        await pilot.pause(0.2)
        text = "".join(c.data for r in app.model.screen_rows() for c in r)
        assert "已连接" in text


# --------------------------------------------------------------------------- Ctrl+C / 复制 / 粘贴


async def test_ctrl_c_sends_break_no_quit_prompt():
    """Ctrl+C 直接发送 ^C 到串口，不再弹出 Textual 的“退出？”提示。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        await pilot.pause(0.2)
        app._tx = 0
        app._rx = 0
        await pilot.press("ctrl+c")
        await pilot.pause(0.3)
        assert app._tx == 1, "Ctrl+C 应发送一个字节 (^C)"
        assert app._running is True, "Ctrl+C 不应退出程序"
        assert len(app._notifications) == 0, "不应弹出“退出？”提示"


async def test_ctrl_shift_c_copies_selection():
    """Ctrl+Shift+C 把终端中选中的文本复制到剪贴板。"""
    from textual.geometry import Offset
    from textual.selection import Selection

    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        app.model.clear()
        app.model.feed_bytes(b"hello world\r\nsecond line")
        app._view().mark_dirty()
        await pilot.pause(0.2)
        view = app._view()
        app.screen.selections[view] = Selection(Offset(0, 0), Offset(5, 0))
        await pilot.press("ctrl+shift+c")
        await pilot.pause(0.2)
        assert app.clipboard == "hello"


async def test_ctrl_shift_v_pastes_clipboard():
    """Ctrl+Shift+V 把剪贴板内容发送到串口。"""
    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        await pilot.pause(0.2)
        app.copy_to_clipboard("AT\r")
        app._tx = 0
        app._rx = 0
        await pilot.press("ctrl+shift+v")
        await pilot.pause(0.3)
        assert app._tx == 3, "粘贴内容应发送到串口"


async def test_paste_event_sends_to_port():
    """终端粘贴（bracketed paste）事件把内容发送到串口。"""
    from textual.events import Paste

    app = PyComApp()
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        await pilot.pause(0.2)
        app._tx = 0
        app._rx = 0
        app.post_message(Paste("hello"))
        await pilot.pause(0.3)
        assert app._tx == 5, "粘贴事件内容应发送到串口"


# --------------------------------------------------------------------------- 功能菜单底部 about / 文案


async def test_main_menu_shows_about_and_clean_copy():
    """功能菜单条目去掉冗余括号说明；关于信息移入“关于”页面。"""
    from pycom import PROJECT_AUTHOR, PROJECT_URL, __version__
    from pycom.screens.about import AboutScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+a")
        await pilot.press("z")
        await pilot.pause(0.2)
        popup = app.screen.query_one("#menu-popup")
        # 关于信息不再停留在主菜单底部（已移入关于页）
        assert len(popup.query("#help-about")) == 0
        assert len(popup.query("#help-repo")) == 0
        # 主菜单不再包含“主菜单”自引用项（z 已移除）
        assert len(popup.query("#menu-z")) == 0
        # 所有主菜单条目不含冗余括号说明
        for key in ("p", "d", "c", "h", "l", "o", "y", "a", "x"):
            label = str(popup.query_one(f"#menu-{key}").render())
            assert "（" not in label and "(" not in label and ")" not in label

        # 关于页面承载项目信息（版本/作者/主页/协议）
        app.push_screen(AboutScreen())
        await pilot.pause(0.2)
        about = str(app.screen_stack[-1].query_one("#help-body Static").render())
        assert __version__ in about and PROJECT_AUTHOR in about
        assert PROJECT_URL in about and "MIT" in about


async def test_main_menu_back_button_bottom_left_closes():
    """关于页面左下角有“返回”按钮，点击后回到主界面。"""
    from pycom.screens.about import AboutScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(AboutScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        back = scr.query_one("#menu-back")
        assert "返回" in str(back.render())

        await pilot.click("#menu-back")
        await pilot.pause(0.3)
        assert len(app.screen_stack) == 1, "返回应关闭关于页面"


# --------------------------------------------------------------------------- --bare CLI


def test_cli_bare_requires_port():
    """--bare must specify a port: it is a headless bridge, not a UI."""
    from pycom.app import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["--bare"])
    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-b", "115200"])


def test_cli_bare_rejects_interactive_startup_options():
    """--bare has no UI, so -s/-f/-e/--hex (interactive startup) are refused."""
    from pycom.app import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-p", "COM3", "--hex"])
    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-p", "COM3", "-s", "AT\r"])
    with pytest.raises(SystemExit):
        _parse_args(["--bare", "-p", "COM3", "-e", "5"])


def test_cli_bare_accepts_port_and_baud():
    from pycom.app import _parse_args

    args = _parse_args(["--bare", "-p", "COM3", "-b", "115200"])
    assert args.bare is True
    assert args.port == "COM3"
    assert args.baud == 115200


# --------------------------------------------------------------------------- main menu / exit dialog small-window


async def test_confirm_dialog_stays_boxed_on_large_window():
    """The exit confirmation keeps its boxed layout when the terminal is big."""
    from pycom.screens.base import ConfirmDialog

    app = PyComApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmDialog("退出", "确定要退出 PyCom 吗？"))
        await pilot.pause(0.2)
        assert not app.screen_stack[-1].query_one("#confirm").has_class("compact")


async def test_confirm_dialog_compact_on_small_window():
    """Regression: the exit dialog (fixed width 54) overflowed narrow windows.
    On a small terminal it toggles `compact`, stays inside the screen, and both
    buttons remain usable."""
    from pycom.screens.base import ConfirmDialog

    app = PyComApp()
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmDialog("退出", "确定要退出 PyCom 吗？"))
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        root = scr.query_one("#confirm")
        assert root.has_class("compact")
        assert root.region.x >= 0 and root.region.right <= scr.size.width
        assert root.region.y >= 0 and root.region.bottom <= scr.size.height
        assert app.focused.id == "yes"
        await pilot.press("right")
        await pilot.pause(0.02)
        assert app.focused.id == "no"


# --------------------------------------------------------------------------- minimum terminal size


async def test_app_does_not_exit_on_usable_window():
    """A normal-sized terminal never trips the too-small guard."""
    app = PyComApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        assert app._too_small is False
        assert app._running is True


async def test_app_exits_when_terminal_too_small():
    """When the terminal is far too small to be usable, the app stops instead
    of rendering a broken interface."""
    from pycom.app import MIN_TERMINAL_COLS, MIN_TERMINAL_ROWS

    app = PyComApp()
    async with app.run_test(size=(MIN_TERMINAL_COLS - 5, MIN_TERMINAL_ROWS - 1)) as pilot:
        await pilot.pause(0.3)
        assert app._too_small is True
        assert app._running is False


def test_too_small_message_lists_required_size():
    from pycom.app import MIN_TERMINAL_COLS, MIN_TERMINAL_ROWS, _too_small_message

    msg = _too_small_message(10, 3)
    assert "10" in msg and "3" in msg
    assert str(MIN_TERMINAL_COLS) in msg
    assert str(MIN_TERMINAL_ROWS) in msg
    assert "太小" in msg


def test_cli_prints_hint_and_returns_1_when_terminal_too_small(monkeypatch, capsys):
    """main() checks the terminal before launching the TUI: too small -> a hint
    on stderr and exit code 1, without entering the (unusable) interface."""
    import os

    import pycom.app as appmod

    monkeypatch.setattr(
        appmod.shutil, "get_terminal_size", lambda *a, **k: os.terminal_size((10, 3))
    )
    rc = appmod.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "太小" in err


def test_parse_osc11_rgb_and_hex():
    from pycom.app import _parse_osc11

    assert _parse_osc11(b"\x1b]11;rgb:0f0f/1111/1a1a\x1b\\") == (15, 17, 26)
    assert _parse_osc11(b"\x1b]11;rgb:fff/fff/fff\x07") == (255, 255, 255)
    assert _parse_osc11(b"\x1b]11;#ffffff\x07") == (255, 255, 255)
    assert _parse_osc11(b"noise") is None
    assert _parse_osc11(b"") is None


def test_resolve_theme_modes():
    from pycom.app import PyComApp
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(theme="light"))
    assert app._resolve_theme(None) == "pycom-light"
    app.cfg.theme = "dark"
    assert app._resolve_theme(None) == "pycom-dark"
    app.cfg.theme = "auto"
    assert app._resolve_theme(True) == "pycom-dark"
    assert app._resolve_theme(False) == "pycom-light"
    assert app._resolve_theme(None) == "pycom-dark"


async def test_app_registers_themes_and_applies_light():
    from pycom.app import PyComApp
    from pycom.config import AppConfig

    app = PyComApp(cfg=AppConfig(theme="light"))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert app.theme == "pycom-light"
        assert "pycom-dark" in app.available_themes
        assert "pycom-light" in app.available_themes
