"""Language selection screen."""

from __future__ import annotations

from pycom import i18n
from pycom.i18n import get_language, tr
from pycom.screens.menus import KeyedMenu


class LanguageScreen(KeyedMenu):
    """Pick the UI language; the choice is persisted and applied immediately."""

    def __init__(self) -> None:
        current = get_language()
        rows = []
        for code, name in i18n.LANGUAGES:
            marker = "\u25cf" if code == current else "\u25cb"  # ● / ○
            rows.append((code, f"{marker}  {name}"))
        super().__init__(tr("语言"), rows, self._dispatch, show_keys=False)

    def _dispatch(self, code: str) -> None:
        self.app.set_language(code)  # type: ignore[attr-defined]
