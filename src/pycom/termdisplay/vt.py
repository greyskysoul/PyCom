"""Pure-data terminal emulation model built on :mod:`pyte`.

A :class:`TerminalModel` owns a pyte :class:`~pyte.screens.Screen` plus its own
scrollback history.  pyte only models the *visible* screen and discards rows
that scroll off the top; we subclass its ``Screen`` and override ``index()`` to
capture the about-to-be-lost top row into a ``deque`` scrollback.

Everything in this module is UI-agnostic: it never imports textual, so it can
be unit-tested and reused from non-TUI code.
"""

from __future__ import annotations

import codecs
from collections import deque
from collections.abc import Callable, Sequence

import pyte
from pyte.screens import Char, Margins, StaticDefaultDict

DEFAULT_SCROLLBACK = 4000
_DEFAULT_CODECS = ("utf-8", "gbk", "latin-1")


def _row_is_blank(chars: Sequence[Char]) -> bool:
    return not any(c.data not in (" ", "") for c in chars)


class _CaptureScreen(pyte.screens.Screen):
    """pyte Screen that reports rows scrolling off the top of a full-screen scroll."""

    def __init__(
        self,
        columns: int,
        lines: int,
        on_scroll_out: Callable[[list[Char]], None] | None,
    ) -> None:
        super().__init__(columns, lines)
        self._scroll_cb = on_scroll_out  # callable(list[Char]) | None

    def snapshot_row(self, y: int) -> list[Char]:
        row = self.buffer.get(y)
        if row is None:
            return []
        out: list[Char] = []
        for x in range(self.columns):
            out.append(row[x])
        return out

    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:
            # A true full-screen scroll: the top row is about to be lost.
            top_row = self.snapshot_row(0)
            if self._scroll_cb is not None and top_row and not _row_is_blank(top_row):
                self._scroll_cb(top_row)
        super().index()


class TerminalModel:
    """In-memory terminal: feed it bytes, read styled rows for display."""

    def __init__(
        self,
        columns: int = 80,
        lines: int = 24,
        scrollback: int = DEFAULT_SCROLLBACK,
        decode: str = "utf-8",
    ) -> None:
        self.columns = columns
        self.lines = lines
        self.decode = decode
        self.rx_add_cr = False
        self.rx_add_lf = False

        self._history: deque[list[Char]] = deque(maxlen=max(0, scrollback))
        self._decoder = self._make_decoder(decode)
        self._screen = _CaptureScreen(columns, lines, self._on_scroll_out)
        self._stream = pyte.Stream(self._screen)

    # -- construction helpers ---------------------------------------------------------
    def _make_decoder(self, codec: str):
        name = codec if codec in _DEFAULT_CODECS else "utf-8"
        return codecs.getincrementaldecoder(name)(errors="replace")

    def set_decode(self, codec: str) -> None:
        self.decode = codec if codec in _DEFAULT_CODECS else "utf-8"
        self._decoder = self._make_decoder(self.decode)

    # -- event hooks -------------------------------------------------------------------
    def _on_scroll_out(self, row: list[Char]) -> None:
        self._history.append(row)

    # -- sizing -------------------------------------------------------------------------
    def resize(self, columns: int, lines: int) -> None:
        """Resize the terminal, preserving visible content and scrollback.

        Height shrink is done by hand instead of pyte's ``Screen.resize``:
        pyte's ``delete_lines`` only moves rows already in its (sparse) buffer,
        so blank rows below leave the top row visible and it gets captured
        again on the next shrink (duplicated history).  The top rows move into
        the scrollback first, then the survivors shift up.  Width changes just
        clip the history and the live rows; the stream keeps referencing the
        same screen object.
        """
        columns = max(1, columns)
        lines = max(1, lines)
        old_columns, old_lines = self.columns, self.lines
        screen = self._screen

        if lines < old_lines:
            # Rows that would be clipped off the top of a shorter screen move
            # into the scrollback instead of being dropped.
            dropped = old_lines - lines
            for y in range(dropped):
                top_row = screen.snapshot_row(y)
                if top_row and not _row_is_blank(top_row):
                    self._history.append(top_row)
            # Shift the surviving rows up by ``dropped`` and drop the rest.
            buffer = screen.buffer
            survivors: dict[int, StaticDefaultDict[int, Char]] = {
                y: buffer[y] for y in range(dropped, old_lines) if y in buffer
            }
            buffer.clear()
            for y, saved_row in survivors.items():
                buffer[y - dropped] = saved_row

        self.columns = columns
        self.lines = lines

        if columns < old_columns:
            # Never let captured rows overflow a narrower window.
            for hist_row in self._history:
                del hist_row[columns:]
            # Clip the live screen rows to the new width as well.
            for live_row in screen.buffer.values():
                for x in range(columns, old_columns):
                    live_row.pop(x, None)

        # Synchronise the pyte screen object with the new size (the buffer was
        # already handled above, so this only updates bookkeeping).
        screen.lines = lines
        screen.columns = columns
        screen.set_margins()
        if columns < old_columns and screen.cursor.x >= columns:
            # 变窄：把光标夹到新宽度内。
            screen.cursor.x = columns - 1
        if lines < old_lines:
            # 变矮时内容整体上移了 ``dropped`` 行，光标也要跟着上移，
            # 否则光标会停留在内容下方的空白处（相对内容看起来“下移”了）。
            if screen.cursor.y >= dropped:
                screen.cursor.y -= dropped
            else:
                # 光标原本指向的行已被滚入历史：放到新屏幕顶部。
                screen.cursor.y = 0
        elif screen.cursor.y >= lines:
            screen.cursor.y = lines - 1
        screen.dirty.update(range(lines))

    # -- input --------------------------------------------------------------------------
    def feed_bytes(self, data: bytes) -> None:
        if not data:
            return
        text = data.decode("latin-1") if self.decode == "latin-1" else self._decoder.decode(data)
        self.feed_text(text)

    def feed_text(self, text: str) -> None:
        if not text:
            return
        if self.rx_add_cr:
            text = text.replace("\n", "\r\n")
        if self.rx_add_lf:
            text = text.replace("\r\n", "\r").replace("\r", "\r\n")
        self._stream.feed(text)

    # -- cursor state -------------------------------------------------------------------
    def mid_line(self) -> bool:
        """True when the display cursor sits inside a line that already has
        content (0 < x < columns).  Used to insert a separator between two
        separately-received RX chunks rendered on the same line."""
        x = self._screen.cursor.x
        return 0 < x < self.columns

    def cursor_position(self) -> tuple[int, int]:
        """(row, column) of the display cursor, clamped to the screen.

        pyte leaves the column at ``columns`` when a wrap is pending (the
        character is still visually on the last cell), so the column is
        clamped back to the last column for display purposes."""
        row = min(max(self._screen.cursor.y, 0), self.lines - 1)
        col = min(max(self._screen.cursor.x, 0), self.columns - 1)
        return row, col

    # -- output ---------------------------------------------------------------------------
    def history_rows(self) -> list[list[Char]]:
        return list(self._history)

    def screen_rows(self) -> list[list[Char]]:
        return [self._screen.snapshot_row(y) for y in range(self.lines)]

    def total_rows(self) -> int:
        return len(self._history) + self.lines

    def clear(self) -> None:
        self._history.clear()
        self._screen.reset()

    # -- text helpers -----------------------------------------------------------------------
    def plain_lines(self) -> list[str]:
        """Current visible content as plain text lines (used by capture/copy)."""
        return ["".join(c.data for c in row) for row in self.screen_rows()]
