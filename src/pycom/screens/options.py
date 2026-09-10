"""Options / preferences dialog."""

from __future__ import annotations

from collections.abc import Generator

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Label, Static, TabbedContent, TabPane

from pycom.compat import marker
from pycom.config import save_config
from pycom.i18n import tr
from pycom.screens.base import AdaptiveModal, FieldSelect


class _CircleCheckbox(Checkbox):
    """Checkbox whose marker is a hollow circle (off) / solid circle (on).

    On a bare Linux console the circle glyphs may be missing from the font, so
    compatibility mode swaps them for ASCII ``[ ]`` / ``[x]`` (see pycom.compat).
    """

    @property
    def _button(self) -> Content:
        style = self.get_visual_style("toggle--button")
        return Content.assemble((marker(self.value), style))


# 下拉可选项 (显示文本, 存储值)，顺序即下拉菜单中的展示顺序。
# 显示文本在构建时按当前语言翻译；存储值不翻译。
_ENTER_VALUES = frozenset(("cr", "crlf", "lf", "none"))
_BACK_VALUES = frozenset(("del", "bs"))
_DECODE_VALUES = frozenset(("utf-8", "gbk", "latin-1"))


def _enter_options() -> list[tuple[str, str]]:
    return [
        (tr("CR (回车)"), "cr"),
        ("CR+LF", "crlf"),
        (tr("LF (换行)"), "lf"),
        (tr("不发送"), "none"),
    ]


def _back_options() -> list[tuple[str, str]]:
    return [
        ("DEL (0x7F)", "del"),
        ("BS (0x08)", "bs"),
    ]


def _decode_options() -> list[tuple[str, str]]:
    return [
        ("UTF-8", "utf-8"),
        ("GBK", "gbk"),
        ("Latin-1", "latin-1"),
    ]


_THEME_VALUES = frozenset(("auto", "light", "dark"))


def _theme_options() -> list[tuple[str, str]]:
    return [
        (tr("\u81ea\u52a8"), "auto"),
        (tr("\u6d45\u8272"), "light"),
        (tr("\u6df1\u8272"), "dark"),
    ]


def _field_row(label: str, control: Widget) -> Generator[Widget, None, None]:
    """One full-width labelled row used by the compact layout."""
    with Horizontal(classes="c-row"):
        yield Label(label, classes="c-label")
        yield control


class _OptionsRich(Vertical):
    """Full layout: dense multi-row form inside a centred box."""

    def compose(self) -> ComposeResult:
        yield Static(tr("设置"), id="options-title")
        with TabbedContent(id="options-body"):
            with TabPane(tr("\u7ec8\u7aef")):
                yield _CircleCheckbox(tr("本地回显"), id="echo", compact=True)
                yield _CircleCheckbox(tr("自动回绕"), id="wrap", compact=True)
                yield _CircleCheckbox(tr("接收 LF -> CR+LF"), id="rx_cr", compact=True)
                yield _CircleCheckbox(tr("接收 CR -> CR+LF"), id="rx_lf", compact=True)
                yield _CircleCheckbox(tr("发送方向键/功能键 VT 序列"), id="vt", compact=True)
                with Horizontal(classes="form-row"):
                    yield Label(tr("回车发送"), classes="form-label")
                    yield FieldSelect(_enter_options(), id="enter", allow_blank=False, compact=True)
                    yield Label(tr("退格发送"), classes="form-label")
                    yield FieldSelect(_back_options(), id="back", allow_blank=False, compact=True)
                with Horizontal(classes="form-row"):
                    yield Label(tr("解码字符集"), classes="form-label")
                    yield FieldSelect(
                        _decode_options(), id="decode", allow_blank=False, compact=True
                    )
            with TabPane(tr("\u5916\u89c2")), Horizontal(classes="form-row"):
                yield Label(tr("\u4e3b\u9898"), classes="form-label")
                yield FieldSelect(_theme_options(), id="theme", allow_blank=False, compact=True)
            with TabPane(tr("\u6587\u4ef6\u4f20\u8f93")):
                with Horizontal(classes="form-row"):
                    yield Label(tr("传输超时(s)"), classes="form-label")
                    yield Input("", id="timeout", placeholder="10", compact=True)
                    yield Label(tr("重试次数"), classes="form-label")
                    yield Input("", id="retries", placeholder="10", compact=True)
                with Horizontal(classes="form-row"):
                    yield Label(tr("数据块"), classes="form-label")
                    yield Input("", id="blocksize", placeholder="1024 / 128", compact=True)
            with TabPane(tr("\u6355\u83b7")):
                yield _CircleCheckbox(tr("捕获时加时间戳"), id="ts", compact=True)
        with Horizontal(id="options-buttons"):
            yield Button(tr("保存"), id="save", variant="primary", compact=True)
            yield Button(tr("取消"), id="cancel", compact=True)


class _OptionsCompact(Vertical):
    """Simple layout for small windows: each control on its own full-width
    row, inside a scroll area so very short windows stay usable."""

    def compose(self) -> ComposeResult:
        yield Static(tr("设置"), id="options-title")
        with TabbedContent(id="options-body"):
            with TabPane(tr("\u7ec8\u7aef")):
                yield _CircleCheckbox(tr("本地回显"), id="echo", compact=True)
                yield _CircleCheckbox(tr("自动回绕"), id="wrap", compact=True)
                yield _CircleCheckbox(tr("接收 LF -> CR+LF"), id="rx_cr", compact=True)
                yield _CircleCheckbox(tr("接收 CR -> CR+LF"), id="rx_lf", compact=True)
                yield _CircleCheckbox(tr("发送方向键/功能键 VT 序列"), id="vt", compact=True)
                yield from _field_row(
                    tr("回车发送"),
                    FieldSelect(_enter_options(), id="enter", allow_blank=False, compact=True),
                )
                yield from _field_row(
                    tr("退格发送"),
                    FieldSelect(_back_options(), id="back", allow_blank=False, compact=True),
                )
                yield from _field_row(
                    tr("解码字符集"),
                    FieldSelect(_decode_options(), id="decode", allow_blank=False, compact=True),
                )
            with TabPane(tr("\u5916\u89c2")):
                yield from _field_row(
                    tr("\u4e3b\u9898"),
                    FieldSelect(_theme_options(), id="theme", allow_blank=False, compact=True),
                )
            with TabPane(tr("\u6587\u4ef6\u4f20\u8f93")):
                yield from _field_row(
                    tr("传输超时(s)"), Input("", id="timeout", placeholder="10", compact=True)
                )
                yield from _field_row(
                    tr("重试次数"), Input("", id="retries", placeholder="10", compact=True)
                )
                yield from _field_row(
                    tr("数据块"), Input("", id="blocksize", placeholder="1024 / 128", compact=True)
                )
            with TabPane(tr("\u6355\u83b7")):
                yield _CircleCheckbox(tr("捕获时加时间戳"), id="ts", compact=True)
        with Horizontal(id="options-buttons"):
            yield Button(tr("保存"), id="save", variant="primary", compact=True)
            yield Button(tr("取消"), id="cancel", compact=True)


class OptionsScreen(AdaptiveModal):
    """Edit persisted options; on save they are applied immediately."""

    ROOT_ID = "options-box"
    # 富布局在高度 <27 时已放不下、无法使用，因此低于该高度（或宽度 <84）
    # 就自动切到可滚动的简洁模式。
    MIN_WIDTH = 84
    MIN_HEIGHT = 27

    def build_rich(self) -> Vertical:
        return _OptionsRich(id=self.ROOT_ID)

    def build_compact(self) -> Vertical:
        return _OptionsCompact(id=self.ROOT_ID)

    # -- value plumbing (both layouts use the same widget ids) -----------------
    def after_build(self) -> None:
        cfg = self.app.cfg  # type: ignore[attr-defined]
        self.query_one("#echo", Checkbox).value = cfg.local_echo
        self.query_one("#wrap", Checkbox).value = cfg.wrap
        self.query_one("#rx_cr", Checkbox).value = cfg.rx_add_cr
        self.query_one("#rx_lf", Checkbox).value = cfg.rx_add_lf
        self.query_one("#ts", Checkbox).value = cfg.capture_timestamps
        self.query_one("#vt", Checkbox).value = cfg.send_vt_sequences
        # 下拉框：仅当配置值合法时才选中它，否则回退到第一个选项
        enter = self.query_one("#enter", FieldSelect)
        enter.value = cfg.enter_sends if cfg.enter_sends in _ENTER_VALUES else "cr"
        back = self.query_one("#back", FieldSelect)
        back.value = cfg.backspace_sends if cfg.backspace_sends in _BACK_VALUES else "del"
        decode = self.query_one("#decode", FieldSelect)
        decode.value = cfg.decode if cfg.decode in _DECODE_VALUES else "utf-8"
        self.query_one("#timeout", Input).value = str(cfg.xfer_timeout)
        self.query_one("#retries", Input).value = str(cfg.xfer_retries)
        self.query_one("#blocksize", Input).value = str(cfg.xfer_block_size)
        theme = self.query_one("#theme", FieldSelect)
        theme.value = cfg.theme if cfg.theme in _THEME_VALUES else "auto"
        # 进入即选中第一项，方向键才能直接上下移动
        self.query_one("#echo", Checkbox).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def _save(self) -> None:
        cfg = self.app.cfg  # type: ignore[attr-defined]
        cfg.local_echo = self.query_one("#echo", Checkbox).value
        cfg.wrap = self.query_one("#wrap", Checkbox).value
        cfg.rx_add_cr = self.query_one("#rx_cr", Checkbox).value
        cfg.rx_add_lf = self.query_one("#rx_lf", Checkbox).value
        cfg.capture_timestamps = self.query_one("#ts", Checkbox).value
        cfg.send_vt_sequences = self.query_one("#vt", Checkbox).value
        # 下拉框值必然合法，无需再校验
        cfg.enter_sends = str(self.query_one("#enter", FieldSelect).value)
        cfg.backspace_sends = str(self.query_one("#back", FieldSelect).value)
        cfg.decode = str(self.query_one("#decode", FieldSelect).value)
        cfg.theme = str(self.query_one("#theme", FieldSelect).value)
        try:
            cfg.xfer_timeout = float(self.query_one("#timeout", Input).value)
            cfg.xfer_retries = int(self.query_one("#retries", Input).value)
            cfg.xfer_block_size = int(self.query_one("#blocksize", Input).value)
        except ValueError:
            pass
        if cfg.xfer_block_size not in (128, 1024):
            cfg.xfer_block_size = 1024
        save_config(cfg)
        self.app.apply_config()  # type: ignore[attr-defined]
        self.app.set_theme_mode(cfg.theme)  # type: ignore[attr-defined]
        self.dismiss(None)
