"""Tests for i18n: system-language detection, translation, persistence and
the language/transfer/ about menu flows."""

from __future__ import annotations

from pycom.app import PyComApp
from pycom.config import AppConfig
from pycom.i18n import detect_system_language, get_language, set_language, tr

# --------------------------------------------------------------------------- detection


def test_detect_falls_back_to_english_when_no_information(monkeypatch):
    import pycom.i18n as mod

    monkeypatch.setattr(mod.os, "name", "posix")
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(mod.locale, "getdefaultlocale", lambda: (None, None))
    assert detect_system_language() == "en"


def test_detect_chinese_via_env(monkeypatch):

    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert detect_system_language() == "zh"


def test_detect_non_chinese_via_env(monkeypatch):
    import pycom.i18n as mod

    monkeypatch.setattr(mod.os, "name", "posix")
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setattr(mod.locale, "getdefaultlocale", lambda: (None, None))
    assert detect_system_language() == "en"


# --------------------------------------------------------------------------- tr


def test_tr_identity_in_chinese():
    set_language("zh")
    assert tr("菜单") == "菜单"


def test_tr_english_translation():
    set_language("en")
    assert tr("菜单") == "Menu"


def test_tr_formats_params_in_english():
    set_language("en")
    assert tr("已连接 {name}", name="COM3") == "Connected to COM3"


def test_tr_unknown_key_falls_back_to_source():
    set_language("en")
    assert tr("不存在的中文字符串") == "不存在的中文字符串"


# --------------------------------------------------------------------------- persistence


def test_app_init_respects_saved_language():
    from pycom.app import PyComApp

    set_language("zh")
    PyComApp(cfg=AppConfig(language="en"))
    assert get_language() == "en"


def test_app_init_auto_detects_when_language_unset():
    from pycom.app import PyComApp

    PyComApp(cfg=AppConfig(language=""))
    assert get_language() in ("zh", "en")


# --------------------------------------------------------------------------- runtime switch


async def test_set_language_persists_and_refreshes_ui(monkeypatch):
    from pycom.app import PyComApp

    saved: dict = {}
    monkeypatch.setattr("pycom.app.save_config", lambda cfg: saved.update(language=cfg.language))

    app = PyComApp(cfg=AppConfig(language="zh"))
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert get_language() == "zh"

        app.set_language("en")
        await pilot.pause()

        assert get_language() == "en"
        assert app.cfg.language == "en"
        assert saved.get("language") == "en"
        assert app.sub_title == "Serial Terminal - YMODEM"
        assert "Menu" in str(app.query_one("#menu-btn").render())


# --------------------------------------------------------------------------- menu flows


async def test_main_menu_d_opens_transfer_submenu():
    from pycom.screens.help import MainMenuScreen
    from pycom.screens.transfermenu import TransferMenuScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        await pilot.press("d")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], TransferMenuScreen)


async def test_transfer_submenu_routes_zmodem_receive():
    from pycom.screens.transfer import RecvScreen
    from pycom.screens.transfermenu import TransferMenuScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        app.push_screen(TransferMenuScreen())
        await pilot.pause(0.2)
        await pilot.press("d")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], RecvScreen)
        assert app.screen_stack[-1].protocol == "zmodem"


async def test_transfer_submenu_routes_ymodem_send():
    from pycom.screens.transfer import SendScreen
    from pycom.screens.transfermenu import TransferMenuScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.open_loopback() is None
        app.push_screen(TransferMenuScreen())
        await pilot.pause(0.2)
        await pilot.press("s")
        await pilot.pause(0.3)
        assert isinstance(app.screen_stack[-1], SendScreen)
        assert app.screen_stack[-1].protocol == "ymodem"


async def test_language_screen_switches_to_english():
    from pycom.screens.language import LanguageScreen

    app = PyComApp(cfg=AppConfig(language="zh"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(LanguageScreen())
        await pilot.pause(0.2)
        await pilot.press("e")
        await pilot.pause(0.3)
        assert get_language() == "en"
        assert len(app.screen_stack) == 1  # 语言页已关闭，回到主界面


async def test_english_menu_renders_english_labels():
    from pycom.screens.help import MainMenuScreen

    app = PyComApp(cfg=AppConfig(language="en"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        scr = app.screen_stack[-1]
        assert "Serial port" in str(scr.query_one("#menu-p").render())
        assert "Quit" in str(scr.query_one("#menu-x").render())
        assert "Data transfer" in str(scr.query_one("#menu-d").render())


async def test_main_menu_has_no_self_referential_item():
    from pycom.screens.help import MainMenuScreen

    app = PyComApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(MainMenuScreen())
        await pilot.pause(0.2)
        assert len(app.screen_stack[-1].query("#menu-z")) == 0
