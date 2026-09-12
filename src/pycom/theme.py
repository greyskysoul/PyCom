"""Colour themes loaded from JSON theme files.

The palettes live outside the code so a theme can be tweaked — or a brand new
one added — without touching Python:

* **bundled** themes ship inside the package: ``pycom/resources/themes/*.json``
* **user** themes are read from ``<user config folder>/themes/*.json`` and may
  add a new theme or override a bundled one (same ``name`` wins).

A theme is a *single* design that **always provides both a dark and a light
variant**; the app picks one from ``cfg.theme_mode`` (``"auto"`` follows the
terminal's background colour).  Theme file format::

    {
      "name": "one-half",          // theme name — also the cfg.theme value
      "label": "One Half",         // shown in 设置 → 外观 (translated via tr())
      "ansi": false,               // true = 8/16-colour theme (see below)

      "variants": {
        "dark":  { …palette that applies on a dark terminal… },
        "light": { …palette that applies on a light terminal… }
      }
    }

Each of the two variants carries the Textual colours plus the app's own CSS
variables::

    {
      "primary": "#61afef",       // --- textual.theme.Theme colours ---
      "secondary": "#56b6c2",
      "accent": "#e5c07b",
      "warning": "#e5c07b",
      "error": "#e06c75",
      "success": "#98c379",
      "foreground": "#dcdfe4",
      "background": "#282c34",
      "surface": "#313640",
      "panel": "#313640",

      "variables": {              // --- app CSS variables (see below) ---
        "control-bg": "#3a4048",
        …
      }
    }

``primary`` is the only required key — Textual builds its generated palette
from it; everything else has a sensible default.  If a variant is missing the
other one is used for both modes, so a half-written theme still works, but
shipping both palettes is the expected shape.

Colour values accept anything the Textual CSS dialect understands: ``#rrggbb``,
``rgb(...)``, CSS colour names, and the ANSI base-16 names ``ansi_black`` …
``ansi_white`` / ``ansi_default``.  Use the ``ansi_*`` names — together with
``"ansi": true`` — for a theme meant for a bare Linux console or any other
8/16-colour terminal, whose palette entries are *exact* rather than an
approximation of a truecolour value.

Registered theme names are ``"<name>-dark"`` / ``"<name>-light"``, e.g. the
bundled theme ``one-half`` registers ``one-half-dark`` and ``one-half-light``.

Besides the theme files, ``pycom-compat`` (8/16 colours for a bare Linux
console) is **built into this module** rather than shipped as a theme file,
because it is not a user choice: compatibility mode pins it, and the picker
never lists it.

Recognised ``variables`` keys (unknown keys are simply ignored, so themes may
also override Textual's own variables such as ``block-cursor-background``)::

    terminal     term-bg, term-fg
    text         muted, faint, label, placeholder, accent, error, border
    fields       control-bg, control-focus-bg, control-fg, button-bg,
                 hover-bg, checkbox-fg, toggle-off
    lists        highlight-bg, highlight-fg, table-header-bg, table-header-fg
    emphasis     accent-btn-bg, accent-btn-fg, accent-btn-hover-bg
                 (重点色按钮：左下角“菜单”与各对话框的主按钮)
    layout       compact-bg
    hex mode     hex-bg, hex-input-bg, hex-input-focus-bg, hex-input-fg,
                 hex-bar-border, hex-caret-fg, hex-ascii-dot-fg
    cursor       cursor-block-bg, cursor-block-fg, cursor-inactive-fg
    ansi only    ansi-background, ansi-foreground
                 (Textual's own ``:ansi`` CSS references these two, so an
                 ``"ansi": true`` theme must define them)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources as _resources
from typing import Any

from rich.color import Color
from textual.color import Color as TextualColor
from textual.theme import Theme

from pycom.config import themes_dir

# Names of the bundled theme files, in picker order.
BUNDLED_FILES: tuple[str, ...] = ("one-half.json",)
_RESOURCE_DIR = "themes"

# Variant keys inside a theme file (= the two selectable appearance modes).
VARIANT_DARK = "dark"
VARIANT_LIGHT = "light"
VARIANTS: tuple[str, ...] = (VARIANT_DARK, VARIANT_LIGHT)
# "auto" = follow the terminal background (see pycom.app.detect_terminal_dark).
MODE_AUTO = "auto"
MODES: tuple[str, ...] = (MODE_AUTO, VARIANT_DARK, VARIANT_LIGHT)

# The bundled theme used when nothing else is selected (and as the fallback for
# unknown theme names).
DEFAULT_THEME = "one-half"
# 兼容模式（Linux 控制台 / 16 色）专用主题：**内置在本模块**而不是主题文件里，
# 因为兼容模式是自动侦测/命令行开关，不是用户可选项；控制台底色无法可靠侦测，
# 因此固定用 dark 变体。
COMPAT_FAMILY = "pycom-compat"
COMPAT_THEME = f"{COMPAT_FAMILY}-{VARIANT_DARK}"
# 内置/隐藏主题：注册进 Textual，但不出现在“设置 → 外观”的主题列表里。
HIDDEN_THEMES: frozenset[str] = frozenset({COMPAT_FAMILY})
# 旧版本把深浅色当作两套独立主题（pycom-dark / pycom-light）；读到这些历史配置
# 值时当成默认主题 + 对应的深浅色，避免用户升级后主题被重置为 auto。
RETIRED_THEMES: frozenset[str] = frozenset({"pycom", "pycom-dark", "pycom-light"})

# Keys copied verbatim into textual.theme.Theme.
_COLOUR_KEYS = (
    "primary",
    "secondary",
    "accent",
    "warning",
    "error",
    "success",
    "foreground",
    "background",
    "surface",
    "panel",
    "boost",
)

# Last-resort palette so the CSS variables still resolve when the bundled theme
# files are missing/unreadable (a broken install should degrade, not crash).
_FALLBACK_VARIABLES: dict[str, str] = {
    "term-bg": "#282c34",
    "term-fg": "#dcdfe4",
    "control-bg": "#3a4048",
    "control-focus-bg": "#474e5d",
    "control-fg": "#dcdfe4",
    "border": "#474e5d",
    "highlight-bg": "#474e5d",
    "highlight-fg": "#dcdfe4",
    "accent-btn-bg": "#3b82f6",
    "accent-btn-fg": "#ffffff",
    "accent-btn-hover-bg": "#4a90e0",
}

# 兼容模式内置主题：只用基础 ANSI 色（0-7）——Linux 控制台只实现 8 种背景色，
# 亮色背景的 SGR 会被静默忽略（字段没有底色）。
_COMPAT_DARK: dict[str, Any] = {
    "primary": "ansi_cyan",
    "secondary": "ansi_blue",
    "accent": "ansi_cyan",
    "warning": "ansi_yellow",
    "error": "ansi_red",
    "success": "ansi_green",
    "foreground": "ansi_white",
    "background": "ansi_black",
    "surface": "ansi_black",
    "panel": "ansi_black",
    "variables": {
        "ansi-background": "ansi_black",
        "ansi-foreground": "ansi_white",
        "term-bg": "ansi_black",
        "term-fg": "ansi_default",
        "muted": "ansi_white",
        "faint": "ansi_cyan",
        "label": "ansi_white",
        "placeholder": "ansi_white",
        "accent": "ansi_cyan",
        "error": "ansi_red",
        "border": "ansi_cyan",
        "control-bg": "ansi_blue",
        "control-focus-bg": "ansi_cyan",
        "control-fg": "ansi_white",
        "button-bg": "ansi_blue",
        "hover-bg": "ansi_cyan",
        "checkbox-fg": "ansi_white",
        "toggle-off": "ansi_white",
        "highlight-bg": "ansi_cyan",
        "highlight-fg": "ansi_black",
        "accent-btn-bg": "ansi_blue",
        "accent-btn-fg": "ansi_white",
        "accent-btn-hover-bg": "ansi_cyan",
        "table-header-bg": "ansi_blue",
        "table-header-fg": "ansi_white",
        "compact-bg": "ansi_black",
        "hex-bg": "ansi_black",
        "hex-input-bg": "ansi_black",
        "hex-input-focus-bg": "ansi_black",
        "hex-input-fg": "ansi_default",
        "hex-bar-border": "ansi_cyan",
        "hex-caret-fg": "ansi_white",
        "hex-ascii-dot-fg": "ansi_white",
        "cursor-block-bg": "ansi_white",
        "cursor-block-fg": "ansi_black",
        "cursor-inactive-fg": "ansi_white",
    },
}

_COMPAT_LIGHT: dict[str, Any] = {
    "primary": "ansi_cyan",
    "secondary": "ansi_blue",
    "accent": "ansi_cyan",
    "warning": "ansi_yellow",
    "error": "ansi_red",
    "success": "ansi_green",
    "foreground": "ansi_black",
    "background": "ansi_white",
    "surface": "ansi_white",
    "panel": "ansi_white",
    "variables": {
        "ansi-background": "ansi_white",
        "ansi-foreground": "ansi_black",
        "term-bg": "ansi_white",
        "term-fg": "ansi_default",
        "muted": "ansi_black",
        "faint": "ansi_blue",
        "label": "ansi_black",
        "placeholder": "ansi_black",
        "accent": "ansi_blue",
        "error": "ansi_red",
        "border": "ansi_blue",
        "control-bg": "ansi_blue",
        "control-focus-bg": "ansi_cyan",
        "control-fg": "ansi_white",
        "button-bg": "ansi_blue",
        "hover-bg": "ansi_cyan",
        "checkbox-fg": "ansi_black",
        "toggle-off": "ansi_black",
        "highlight-bg": "ansi_cyan",
        "highlight-fg": "ansi_black",
        "accent-btn-bg": "ansi_blue",
        "accent-btn-fg": "ansi_white",
        "accent-btn-hover-bg": "ansi_cyan",
        "table-header-bg": "ansi_blue",
        "table-header-fg": "ansi_white",
        "compact-bg": "ansi_white",
        "hex-bg": "ansi_white",
        "hex-input-bg": "ansi_white",
        "hex-input-focus-bg": "ansi_white",
        "hex-input-fg": "ansi_default",
        "hex-bar-border": "ansi_blue",
        "hex-caret-fg": "ansi_black",
        "hex-ascii-dot-fg": "ansi_black",
        "cursor-block-bg": "ansi_black",
        "cursor-block-fg": "ansi_white",
        "cursor-inactive-fg": "ansi_black",
    },
}

_COMPAT_DATA: dict[str, Any] = {
    "name": COMPAT_FAMILY,
    "label": "兼容",
    "ansi": True,
    "variants": {VARIANT_DARK: _COMPAT_DARK, VARIANT_LIGHT: _COMPAT_LIGHT},
}


def registered_name(theme: str, variant: str) -> str:
    """Name a ``(theme, variant)`` pair is registered under in Textual."""
    return f"{theme}-{variant}"


@dataclass(frozen=True)
class ThemeEntry:
    """One theme file: a name/label plus its dark and light variants."""

    name: str
    label: str
    variants: dict[str, Theme]

    @property
    def ansi(self) -> bool:
        return any(theme.ansi for theme in self.variants.values())

    def theme(self, variant: str) -> Theme:
        """The requested variant (falling back to the other one)."""
        theme = self.variants.get(variant)
        if theme is not None:
            return theme
        return next(iter(self.variants.values()))

    def registered(self) -> Iterator[tuple[str, Theme]]:
        """``(registered name, theme)`` for every variant of this theme."""
        for variant, theme in self.variants.items():
            yield registered_name(self.name, variant), theme


def _bundled_dir() -> Any:
    return _resources.files("pycom.resources").joinpath(_RESOURCE_DIR)


def _read_bundled() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    directory = _bundled_dir()
    for filename in BUNDLED_FILES:
        try:
            out.append(json.loads(directory.joinpath(filename).read_text(encoding="utf-8")))
        except Exception:  # missing/unreadable/malformed: fall back to the rest
            continue
    return out


def _read_user() -> list[dict[str, Any]]:
    """Theme files dropped into the user config folder.

    Invalid files are skipped rather than raising: a typo in a user theme must
    not keep the terminal from starting.
    """
    directory = themes_dir()
    out: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for filename in names:
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, filename), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _parse_variant(name: str, variant: str, spec: dict[str, Any], ansi: bool) -> Theme | None:
    """Build one Textual theme from a variant block, or None if unusable."""
    colours = {key: spec[key] for key in _COLOUR_KEYS if isinstance(spec.get(key), str)}
    if "primary" not in colours:
        return None  # Textual needs a primary colour to generate its palette
    raw_variables = spec.get("variables")
    variables = (
        {k: v for k, v in raw_variables.items() if isinstance(k, str) and isinstance(v, str)}
        if isinstance(raw_variables, dict)
        else {}
    )
    return Theme(
        name=registered_name(name, variant),
        dark=variant != VARIANT_LIGHT,
        ansi=ansi,
        variables=variables,
        **colours,
    )


def _parse(data: dict[str, Any]) -> ThemeEntry | None:
    """Build a :class:`ThemeEntry` from one theme file, or None if unusable."""
    name = data.get("name")
    if not isinstance(name, str) or not name:
        return None
    raw_variants = data.get("variants")
    if not isinstance(raw_variants, dict):
        return None
    ansi = bool(data.get("ansi", False))
    variants: dict[str, Theme] = {}
    for variant in VARIANTS:
        spec = raw_variants.get(variant)
        if isinstance(spec, dict):
            theme = _parse_variant(name, variant, spec, ansi)
            if theme is not None:
                variants[variant] = theme
    if not variants:
        return None
    # 一套主题必须同时提供深浅色：缺哪一档就用已有的那档顶上（并换成它自己的
    # 注册名），这样只写了一半的用户主题也能用，而不是整个主题消失。
    for variant in VARIANTS:
        if variant not in variants:
            source = variants[VARIANTS[0] if variant == VARIANTS[1] else VARIANTS[1]]
            variants[variant] = Theme(
                name=registered_name(name, variant),
                dark=variant != VARIANT_LIGHT,
                ansi=source.ansi,
                variables=dict(source.variables),
                **{key: getattr(source, key) for key in _COLOUR_KEYS},
            )
    raw_label = data.get("label")
    label = raw_label if isinstance(raw_label, str) and raw_label else name
    return ThemeEntry(name=name, label=label, variants=variants)


def _parse_all() -> list[ThemeEntry]:
    """Every theme: bundled files, then the built-in compat theme, then user
    files (a later file may override an earlier theme of the same name)."""
    entries: dict[str, ThemeEntry] = {}
    for data in (*_read_bundled(), _COMPAT_DATA, *_read_user()):
        entry = _parse(data)
        if entry is not None:
            entries[entry.name] = entry
    return list(entries.values())


@lru_cache(maxsize=1)
def _cached_entries() -> tuple[ThemeEntry, ...]:
    return tuple(_parse_all())


def load_entries() -> list[ThemeEntry]:
    """All themes (cached; call :func:`reload` after editing theme files)."""
    return list(_cached_entries())


def reload() -> None:
    """Drop the cache so theme files are re-read (used by the tests)."""
    _cached_entries.cache_clear()


def load_themes() -> dict[str, Theme]:
    """Registered-theme mapping for :meth:`~textual.app.App.register_theme`."""
    return {reg: theme for entry in load_entries() for reg, theme in entry.registered()}


def theme_labels() -> dict[str, str]:
    """Selectable theme name -> picker label (translated via :func:`tr`).

    内置主题（如兼容主题）会注册进 Textual 但不出现在选择列表里：它们由
    自动侦测 / 命令行开关决定，不是用户的选项。
    """
    from pycom.i18n import tr

    return {entry.name: tr(entry.label) for entry in picker_entries()}


def picker_entries() -> list[ThemeEntry]:
    """Themes offered in “设置 → 外观”（不含隐藏的内置主题）。"""
    return [entry for entry in load_entries() if entry.name not in HIDDEN_THEMES]


def theme_names() -> list[str]:
    """Selectable theme names (families) in file order."""
    return [entry.name for entry in picker_entries()]


def _bundled_entries() -> list[ThemeEntry]:
    """Bundled themes only (no user files) — used for CSS-variable defaults."""
    entries: dict[str, ThemeEntry] = {}
    for data in _read_bundled():
        entry = _parse(data)
        if entry is not None:
            entries[entry.name] = entry
    return list(entries.values())


def default_variables() -> dict[str, str]:
    """Fallback CSS variables (dark palette of the bundled default theme)."""
    for entry in _bundled_entries():
        if entry.name == DEFAULT_THEME:
            return dict(entry.theme(VARIANT_DARK).variables)
    return dict(_FALLBACK_VARIABLES)


def split_selection(
    theme: str, mode: str = MODE_AUTO, names: Iterable[str] | None = None
) -> tuple[str, str]:
    """Normalise a stored selection into ``(theme name, mode)``.

    ``cfg.theme`` + ``cfg.theme_mode`` are the canonical pair, but older config
    files stored the appearance mode *in* ``cfg.theme`` (``"auto"``/``"light"``/
    ``"dark"``) or a fully qualified registered name (``"<theme>-dark"``); both
    are still accepted so an existing config keeps working.
    """
    known = set(names) if names is not None else set(load_themes())
    value = (theme or "").strip()
    wanted = (mode or "").strip().lower()
    if wanted not in MODES:
        wanted = MODE_AUTO
    lowered = value.lower()
    if lowered in MODES:  # legacy: the mode was stored in cfg.theme
        return DEFAULT_THEME, lowered
    if value in known:  # legacy: a fully qualified "<theme>-<variant>" name
        family, _sep, variant = value.rpartition("-")
        if family and variant in VARIANTS:
            return family, variant
    if not value:
        return DEFAULT_THEME, wanted
    if value in RETIRED_THEMES:  # 旧版本内置主题名 → 默认主题
        return DEFAULT_THEME, wanted
    return value, wanted


def resolve(
    theme: str,
    mode: str = MODE_AUTO,
    detected_dark: bool | None = None,
    names: Iterable[str] | None = None,
) -> str:
    """Registered theme name for a stored selection (never fails).

    ``detected_dark`` drives ``"auto"``: ``False`` (a light terminal) selects
    the light variant, anything else the dark one.  Unknown theme names fall
    back to the bundled default theme.
    """
    known = set(names) if names is not None else set(load_themes())
    family, wanted = split_selection(theme, mode, known)
    variant = wanted
    if variant == MODE_AUTO:
        variant = VARIANT_LIGHT if detected_dark is False else VARIANT_DARK
    for candidate in (registered_name(family, variant), registered_name(DEFAULT_THEME, variant)):
        if candidate in known:
            return candidate
    # last resort: the default theme's dark variant, else any registered theme
    for candidate in (registered_name(DEFAULT_THEME, VARIANT_DARK), *sorted(known)):
        if candidate in known:
            return candidate
    return candidate


@lru_cache(maxsize=256)
def colour(value: str) -> Color:
    """Resolve a theme colour value into a rich :class:`~rich.color.Color`.

    Accepts both truecolour values (``#rrggbb``) and the Textual ANSI names
    (``ansi_white`` / ``ansi_default``, which rich itself does not know).
    """
    try:
        return TextualColor.parse(value).rich_color
    except Exception:
        try:
            return Color.parse(value)
        except Exception:
            return Color.parse("#ffffff")


def widget_colour(widget: Any, name: str, fallback: str) -> Color:
    """Theme colour ``name`` (a CSS variable) as seen by ``widget``.

    Widget code that draws its own :class:`rich.text.Text` (terminal cursor,
    HEX caret, …) uses this instead of hard-coding a colour, so the active
    theme — including an 8/16-colour one — decides how that text looks.
    Falls back to ``fallback`` before the widget is mounted.
    """
    try:
        variables = widget.app.theme_variables
    except Exception:
        variables = None
    value = variables.get(name) if isinstance(variables, dict) else None
    return colour(value if isinstance(value, str) else fallback)
