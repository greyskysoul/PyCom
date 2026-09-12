"""Colour themes loaded from JSON theme files (``pycom/theme.py``).

The palettes used to live inline in ``app.py``; these tests pin down the
file-driven behaviour: one theme file provides *both* the dark and the light
variant, every bundled theme defines every CSS variable the stylesheet
references, the built-in compatibility theme is force-enabled (and never
offered as a choice), and non-truecolour (ANSI) themes get *exact* terminal
colours instead of an approximation.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from rich.color import ColorType

from pycom import theme as thememod
from pycom.app import PyComApp
from pycom.config import AppConfig

CSS_PATH = Path(__file__).resolve().parents[2] / "src" / "pycom" / "resources" / "app.tcss"

# Every app-specific variable the stylesheet is allowed to reference.
APP_VARIABLES = frozenset(
    {
        "term-bg",
        "term-fg",
        "muted",
        "faint",
        "label",
        "placeholder",
        "accent",
        "error",
        "border",
        "control-bg",
        "control-focus-bg",
        "control-fg",
        "button-bg",
        "hover-bg",
        "checkbox-fg",
        "toggle-off",
        "highlight-bg",
        "highlight-fg",
        "accent-btn-bg",
        "accent-btn-fg",
        "accent-btn-hover-bg",
        "table-header-bg",
        "table-header-fg",
        "compact-bg",
        "hex-bg",
        "hex-input-bg",
        "hex-input-focus-bg",
        "hex-input-fg",
        "hex-bar-border",
        "hex-caret-fg",
        "hex-ascii-dot-fg",
        "cursor-block-bg",
        "cursor-block-fg",
        "cursor-inactive-fg",
    }
)
# Retired names: the “重点色” button group replaced them (see app.tcss).
RETIRED_VARIABLES = (
    "menu-btn-bg",
    "menu-btn-fg",
    "menu-btn-hover-bg",
    "primary-btn-bg",
    "primary-btn-fg",
    "primary-btn-hover-bg",
)
# Textual 自己生成的变量（由 Theme 的颜色推出），主题文件不需要也不应该定义它们。
TEXTUAL_VARIABLES = frozenset(
    {"background", "foreground", "panel", "surface", "text", "text-success"}
)
# Every theme must resolve every variable in *both* variants.
ALL_REGISTERED = [
    thememod.registered_name(thememod.DEFAULT_THEME, variant) for variant in thememod.VARIANTS
] + [thememod.registered_name(thememod.COMPAT_FAMILY, variant) for variant in thememod.VARIANTS]


@pytest.fixture(autouse=True)
def _fresh_theme_cache():
    """Theme files are cached; re-read them for each test that changes them."""
    thememod.reload()
    yield
    thememod.reload()


def _css_variables() -> set[str]:
    return set(re.findall(r"\$([a-z0-9-]+)", CSS_PATH.read_text(encoding="utf-8")))


def _write_theme(config_dir: Path, filename: str, data: dict) -> None:
    directory = config_dir / "themes"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(data), encoding="utf-8")


def _use_config_dir(monkeypatch, path: Path) -> None:
    import pycom.config as cfgmod

    monkeypatch.setattr(cfgmod, "_config_dir", lambda: str(path))
    thememod.reload()


# --------------------------------------------------------------------- bundled files


def test_bundled_theme_provides_dark_and_light_variants():
    entries = {entry.name: entry for entry in thememod.load_entries()}
    bundled = entries[thememod.DEFAULT_THEME]
    assert bundled.theme(thememod.VARIANT_DARK).dark is True
    assert bundled.theme(thememod.VARIANT_LIGHT).dark is False
    # one theme file = one design, both appearances
    assert (
        bundled.theme(thememod.VARIANT_DARK).primary
        != bundled.theme(thememod.VARIANT_LIGHT).primary
    )


def test_compat_theme_is_built_in_and_hidden_from_the_picker():
    entries = {entry.name: entry for entry in thememod.load_entries()}
    assert thememod.COMPAT_FAMILY in entries
    compat = entries[thememod.COMPAT_FAMILY]
    assert compat.ansi is True
    # 不在“设置 → 外观”的主题列表里：兼容模式由侦测/--compat 强制启用
    assert thememod.COMPAT_FAMILY not in thememod.theme_names()
    assert thememod.COMPAT_FAMILY not in thememod.theme_labels()
    assert thememod.COMPAT_THEME in thememod.load_themes()


def test_no_compat_theme_file_is_shipped():
    """兼容主题是内置的：主题文件目录里不应再有 pycom-compat.json。"""
    directory = Path(__file__).resolve().parents[2] / "src" / "pycom" / "resources" / "themes"
    assert sorted(p.name for p in directory.glob("*.json")) == ["one-half.json"]


def test_css_uses_only_declared_app_variables():
    """app.tcss may only use app variables plus Textual's generated ones."""
    used = _css_variables() - TEXTUAL_VARIABLES
    assert used <= APP_VARIABLES, f"undeclared variables: {sorted(used - APP_VARIABLES)}"
    assert not used & set(RETIRED_VARIABLES), "app.tcss still uses a retired variable"


def test_every_declared_variable_is_referenced_somewhere():
    """Guards against typos: each documented variable is used by app.tcss or by
    the widget code that paints its own text (terminal cursor, HEX caret…)."""
    widget_only = {
        "hex-input-fg",
        "hex-caret-fg",
        "hex-ascii-dot-fg",
        "cursor-block-bg",
        "cursor-block-fg",
        "cursor-inactive-fg",
    }
    assert (_css_variables() - TEXTUAL_VARIABLES) | widget_only == APP_VARIABLES


def test_retired_button_variables_are_gone_from_theme_files():
    for entry in thememod.load_entries():
        for theme in entry.variants.values():
            assert not set(theme.variables) & set(RETIRED_VARIABLES)


@pytest.mark.parametrize("name", ALL_REGISTERED)
def test_every_variant_defines_every_used_css_variable(name):
    """Each variant must resolve every app variable (bundled vars + Textual's own
    generated ones), otherwise the CSS fails to parse or a widget falls back to
    a hard-coded colour."""
    theme = thememod.load_themes()[name]
    available = set(theme.to_color_system().generate()) | set(theme.variables)
    assert available >= APP_VARIABLES, sorted(APP_VARIABLES - available)


def test_compat_theme_keeps_textual_ansi_variables():
    """Textual's own ``:ansi`` CSS references ``$ansi-background``."""
    for variant in thememod.VARIANTS:
        theme = thememod.load_themes()[thememod.registered_name(thememod.COMPAT_FAMILY, variant)]
        assert theme.variables["ansi-background"].startswith("ansi_")
        assert theme.variables["ansi-foreground"].startswith("ansi_")


def test_default_variables_come_from_the_dark_variant_of_the_default_theme():
    dark = thememod.load_themes()[
        thememod.registered_name(thememod.DEFAULT_THEME, thememod.VARIANT_DARK)
    ]
    assert thememod.default_variables() == dark.variables


# --------------------------------------------------------------------- the loader


def test_split_selection_normalises_stored_values():
    names = thememod.load_themes()
    default, dark, light = thememod.DEFAULT_THEME, thememod.VARIANT_DARK, thememod.VARIANT_LIGHT
    # canonical pair: theme + appearance mode
    assert thememod.split_selection("one-half", dark, names) == ("one-half", dark)
    assert thememod.split_selection("one-half", light, names) == ("one-half", light)
    assert thememod.split_selection("one-half", "auto", names) == ("one-half", "auto")
    # legacy: the mode used to live in cfg.theme
    assert thememod.split_selection("dark", "auto", names) == (default, dark)
    assert thememod.split_selection("light", "auto", names) == (default, light)
    assert thememod.split_selection("auto", "auto", names) == (default, "auto")
    # legacy: a fully qualified registered name
    assert thememod.split_selection("one-half-dark", "auto", names) == ("one-half", dark)
    assert thememod.split_selection("one-half-light", "auto", names) == ("one-half", light)
    # retired built-in theme names map onto the default theme
    assert thememod.split_selection("pycom", "auto", names) == (default, "auto")
    assert thememod.split_selection("pycom-dark", "auto", names) == (default, "auto")
    # empty / junk
    assert thememod.split_selection("", "auto", names) == (default, "auto")
    assert thememod.split_selection("", "nonsense", names) == (default, "auto")


def test_resolve_picks_the_variant_from_the_appearance_mode():
    names = thememod.load_themes()
    assert thememod.resolve("one-half", "dark", None, names) == "one-half-dark"
    assert thememod.resolve("one-half", "light", None, names) == "one-half-light"
    # auto = follow the terminal background (unknown -> dark)
    assert thememod.resolve("one-half", "auto", True, names) == "one-half-dark"
    assert thememod.resolve("one-half", "auto", False, names) == "one-half-light"
    assert thememod.resolve("one-half", "auto", None, names) == "one-half-dark"


def test_resolve_never_fails_on_an_unknown_theme():
    names = thememod.load_themes()
    assert thememod.resolve("does-not-exist", "light", None, names) == "one-half-light"
    assert thememod.resolve("does-not-exist", "dark", None, names) == "one-half-dark"
    assert thememod.resolve("", "auto", False, names) == "one-half-light"


def test_colour_resolves_truecolour_and_ansi_values():
    assert thememod.colour("#3b82f6").triplet is not None
    assert thememod.colour("#3b82f6").type == ColorType.TRUECOLOR
    # rich itself does not know ``ansi_white``; the loader must
    ansi = thememod.colour("ansi_white")
    assert ansi.type == ColorType.STANDARD
    assert ansi.number == 7


def test_user_theme_file_is_loaded_and_overrides_by_name(tmp_path, monkeypatch):
    _use_config_dir(monkeypatch, tmp_path)
    _write_theme(
        tmp_path,
        "solarized.json",
        {
            "name": "pycom-solarized",
            "label": "Solarized",
            "variants": {
                "dark": {"primary": "#268bd2"},
                "light": {"primary": "#268bd2", "background": "#fdf6e3"},
            },
        },
    )
    # a user file may also replace a bundled theme by reusing its name
    _write_theme(
        tmp_path,
        "custom-dark.json",
        {
            "name": thememod.DEFAULT_THEME,
            "label": "Custom One Half",
            "variants": {
                "dark": {"primary": "#ff0000", "variables": thememod.default_variables()},
                "light": {"primary": "#00ff00"},
            },
        },
    )
    themes = thememod.load_themes()
    assert "pycom-solarized-dark" in themes
    assert "pycom-solarized-light" in themes
    assert themes["one-half-dark"].primary == "#ff0000"
    assert thememod.theme_labels()["pycom-solarized"] == "Solarized"


def test_half_written_user_theme_reuses_its_only_variant(tmp_path, monkeypatch):
    """只写了一个深浅色的主题仍然可用（两个模式都用那一档）。"""
    _use_config_dir(monkeypatch, tmp_path)
    _write_theme(tmp_path, "solo.json", {"name": "solo", "variants": {"dark": {"primary": "#123"}}})
    themes = thememod.load_themes()
    assert "solo-dark" in themes and "solo-light" in themes
    assert themes["solo-light"].primary == themes["solo-dark"].primary


def test_broken_user_theme_files_are_ignored(tmp_path, monkeypatch):
    _use_config_dir(monkeypatch, tmp_path)
    directory = tmp_path / "themes"
    directory.mkdir(parents=True)
    (directory / "broken.json").write_text("{ not json", encoding="utf-8")
    (directory / "no-primary.json").write_text(
        json.dumps({"name": "bare", "variants": {"dark": {}}}), encoding="utf-8"
    )
    (directory / "no-variants.json").write_text(
        json.dumps({"name": "flat", "primary": "#123456"}), encoding="utf-8"
    )
    (directory / "notes.txt").write_text("ignore me", encoding="utf-8")
    _write_theme(tmp_path, "good.json", {"name": "good", "variants": {"dark": {"primary": "#123"}}})

    names = {entry.name for entry in thememod.load_entries()}
    assert "good" in names
    assert "bare" not in names
    assert "flat" not in names
    assert names >= {thememod.DEFAULT_THEME, thememod.COMPAT_FAMILY}


# --------------------------------------------------------------------- in the app


def test_app_registers_theme_files_and_rejects_unknown_names():
    app = PyComApp(cfg=AppConfig(theme="nope"))
    assert thememod.registered_name(thememod.DEFAULT_THEME, "dark") in app.available_themes
    assert app.cfg.theme == "nope"  # untouched config value falls back to the default
    assert app.theme == "one-half-dark"


async def test_user_theme_can_be_selected(tmp_path, monkeypatch):
    _use_config_dir(monkeypatch, tmp_path)
    _write_theme(
        tmp_path,
        "solarized.json",
        {
            "name": "pycom-solarized",
            "label": "Solarized",
            "variants": {
                "dark": {"primary": "#268bd2", "variables": {"accent-btn-bg": "#b58900"}},
                "light": {"primary": "#268bd2"},
            },
        },
    )
    app = PyComApp(cfg=AppConfig(theme="pycom-solarized", theme_mode="dark"))
    assert app.theme == "pycom-solarized-dark"
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause(0.3)  # 主题变量在 CSS 刷新后才生效
        assert app.theme_variables["accent-btn-bg"] == "#b58900"


async def test_accent_button_variables_drive_menu_and_primary_buttons():
    """“重点色”按钮：左下角菜单按钮与主按钮共用主题文件的 accent-btn-* 变量。"""
    app = PyComApp(cfg=AppConfig(language="zh", theme="one-half", theme_mode="dark"))
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause(0.3)
        menu_bg = app.query_one("#menu-btn").styles.background
        assert (menu_bg.r, menu_bg.g, menu_bg.b) == (0x3B, 0x82, 0xF6)
        variables = app.theme_variables
        assert variables["accent-btn-bg"] == "#3b82f6"
        for retired in RETIRED_VARIABLES:
            assert retired not in variables


async def test_light_variant_uses_its_own_accent_button_colour():
    app = PyComApp(cfg=AppConfig(language="zh", theme="one-half", theme_mode="light"))
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause(0.3)
        menu_bg = app.query_one("#menu-btn").styles.background
        assert (menu_bg.r, menu_bg.g, menu_bg.b) == (0x4A, 0x90, 0xD9)


async def test_compat_theme_is_forced_regardless_of_the_selection():
    """兼容模式强制启用内置 ANSI 主题，忽略配置里的主题/模式。"""
    for cfg in (
        AppConfig(theme="one-half", theme_mode="light"),
        AppConfig(theme="one-half", theme_mode="dark"),
        AppConfig(theme="auto"),
    ):
        app = PyComApp(cfg=cfg, compat=True)
        assert app.theme == thememod.COMPAT_THEME


async def test_compat_theme_draws_text_with_exact_ansi_colours():
    """非真彩色主题：自己绘制文本的控件（HEX 光标、终端光标、ASCII 点）也必须
    使用主题文件里的 ANSI 颜色，而不是近似真彩色。"""
    from pycom.theme import widget_colour

    app = PyComApp(cfg=AppConfig(language="en"), compat=True)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause(0.3)
        assert app.theme == thememod.COMPAT_THEME
        view = app._view()
        for name in ("cursor-block-bg", "cursor-block-fg", "cursor-inactive-fg", "hex-caret-fg"):
            resolved = widget_colour(view, name, "#000000")
            assert resolved.type == ColorType.STANDARD, name
            assert resolved.number is not None


async def test_theme_picker_offers_user_themes_but_not_the_built_ins(tmp_path, monkeypatch):
    from pycom.screens.options import _theme_options

    _use_config_dir(monkeypatch, tmp_path)
    _write_theme(
        tmp_path,
        "solarized.json",
        {"name": "pycom-solarized", "variants": {"dark": {"primary": "#268bd2"}}},
    )

    values = [value for _label, value in _theme_options()]
    assert values[0] == thememod.DEFAULT_THEME
    assert "pycom-solarized" in values
    assert thememod.COMPAT_FAMILY not in values  # 由 --compat 强制启用


async def test_options_screen_shows_the_active_theme_and_mode(tmp_path, monkeypatch):
    from pycom.screens.options import OptionsScreen

    _use_config_dir(monkeypatch, tmp_path)
    _write_theme(
        tmp_path,
        "solarized.json",
        {"name": "pycom-solarized", "variants": {"dark": {"primary": "#268bd2"}}},
    )

    app = PyComApp(cfg=AppConfig(language="zh", theme="pycom-solarized", theme_mode="light"))
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause(0.3)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.3)
        screen = app.screen_stack[-1]
        assert screen.query_one("#theme").value == "pycom-solarized"
        assert screen.query_one("#theme_mode").value == "light"


async def test_saving_the_options_applies_theme_and_mode():
    from pycom.screens.options import OptionsScreen

    app = PyComApp(cfg=AppConfig(language="zh"))
    async with app.run_test(size=(100, 34)) as pilot:
        await pilot.pause(0.3)
        app.push_screen(OptionsScreen())
        await pilot.pause(0.3)
        screen = app.screen_stack[-1]
        screen.query_one("#theme").value = thememod.DEFAULT_THEME
        screen.query_one("#theme_mode").value = "light"
        await pilot.pause(0.1)
        screen._save()
        await pilot.pause(0.2)
        assert app.cfg.theme == thememod.DEFAULT_THEME
        assert app.cfg.theme_mode == "light"
        assert app.theme == "one-half-light"


def test_themes_dir_lives_next_to_the_config_file(tmp_path, monkeypatch):
    import pycom.config as cfgmod

    monkeypatch.setattr(cfgmod, "_config_dir", lambda: str(tmp_path))
    assert cfgmod.themes_dir() == os.path.join(str(tmp_path), "themes")
