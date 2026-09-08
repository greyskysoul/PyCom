"""Unit tests for the pyte-backed terminal model."""

from __future__ import annotations

from pycom.termdisplay.vt import TerminalModel


def _text(rows) -> list[str]:
    return ["".join(c.data for c in row).rstrip() for row in rows]


def test_feed_basic_lines():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"hello\r\nworld\r\n")
    lines = _text(m.screen_rows())
    assert lines[0] == "hello"
    assert lines[1] == "world"


def test_ansi_colors_parsed():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"\x1b[31mred\x1b[0m ok")
    row = m.screen_rows()[0]
    chars = [c for c in row if c.data]
    assert any(c.fg == "red" for c in chars)


def test_carriage_return_overwrites():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"12345\rABCD")
    line = _text(m.screen_rows())[0]
    assert line == "ABCD5"


def test_scroll_captures_history():
    m = TerminalModel(20, 3)  # tiny viewport -> scrolls quickly
    for i in range(6):
        m.feed_bytes(f"line{i}\r\n".encode())
    hist = _text(m.history_rows())
    scr = _text(m.screen_rows())
    assert hist, "history should not be empty"
    assert hist[0] == "line0"
    assert "line1" in hist
    # the newest visible lines are on screen
    assert "line5" in "".join(scr)


def test_rx_newline_conversion():
    m = TerminalModel(20, 6)
    m.rx_add_cr = True  # LF-only device output should also move to column 0
    m.feed_bytes(b"a\nb")
    lines = _text(m.screen_rows())
    assert lines[0] == "a"
    assert lines[1] == "b"


def test_clear():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"some text")
    m.clear()
    assert not m.history_rows()
    assert not "".join(_text(m.screen_rows())).strip()


def _text_of(rows) -> list[str]:
    return ["".join(c.data for c in row).rstrip() for row in rows]


def test_resize_keeps_visible_content_and_history():
    """Resizing must NOT blank the display or wipe the scrollback.

    Regression: the old resize rebuilt the screen from scratch, so any
    content that was visible (the most recent output) was lost and the
    terminal looked like its history had been cleared.
    """
    m = TerminalModel(20, 4)
    for i in range(10):
        m.feed_bytes(f"line{i:03d}\r\n".encode())

    visible_before = _text_of(m.screen_rows())
    assert any(v for v in visible_before), "screen should hold content"

    # shrink the viewport: the most recent lines stay visible, the ones that
    # scroll off the top are captured into the scrollback instead of dropped
    m.resize(12, 3)
    hist = _text_of(m.history_rows())
    vis = _text_of(m.screen_rows())
    assert any(v for v in vis), "visible content must survive a resize"
    # the newest line is still on screen
    assert "line009" in "".join(vis)
    # nothing before it was lost: it moved into the scrollback
    assert "line006" in "".join(hist) + "".join(vis)

    # grow back — the same content is still reachable
    m.resize(20, 4)
    vis2 = _text_of(m.screen_rows())
    assert any(v for v in vis2), "growing back must not blank the screen"
    assert "line009" in "".join(vis2)


def test_resize_shrink_width_clips_history_rows():
    """Narrowing the terminal clips captured scrollback rows so they never
    overflow the new, narrower window."""
    m = TerminalModel(20, 3)
    for i in range(6):
        m.feed_bytes(f"{i:04d}".encode() + b"\r\n")
    assert m.history_rows(), "history should exist before the resize"

    m.resize(4, 3)
    # every history row is now at most 4 cells wide
    for row in m.history_rows():
        assert len(row) <= 4


def test_resize_shrink_never_duplicates_history():
    """Regression: shrinking the terminal must not duplicate lines.

    pyte's own ``Screen.resize`` only shifts rows that already exist in its
    (sparse) buffer, so on a screen with blank rows the top row never actually
    scrolls off — it used to land in the scrollback AND stay on screen, then get
    captured again on the next shrink (duplicated history lines)."""
    m = TerminalModel(20, 6)
    for i in range(10):
        m.feed_bytes(f"L{i:02d}\r\n".encode())

    def non_blank(rows) -> list[str]:
        return [t for t in _text(rows) if t]

    # shrink repeatedly down to a single line, then bounce around
    for h in (5, 4, 3, 2, 1):
        m.resize(20, h)
    for _ in range(3):
        m.resize(20, 2)
        m.resize(20, 4)
        m.resize(20, 2)

    hist = non_blank(m.history_rows())
    screen = non_blank(m.screen_rows())
    all_lines = hist + screen
    dupes = {t for t in set(all_lines) if all_lines.count(t) > 1}
    assert not dupes, f"resize duplicated lines: {sorted(dupes)}"


def test_resize_sparse_screen_shrink_keeps_content_once():
    """Shrinking a screen whose bottom rows are blank must move the top content
    into the scrollback exactly once (not leave it visible and re-capture it)."""
    m = TerminalModel(20, 6)
    m.feed_bytes(b"HELLO\r\n")
    m.resize(20, 5)
    m.resize(20, 4)
    hist = _text(m.history_rows())
    screen = _text(m.screen_rows())
    assert "HELLO" in hist
    assert "HELLO" not in screen, "content must leave the screen, not stay behind"
    assert hist.count("HELLO") == 1, "content must not be duplicated in history"


def test_resize_grow_then_shrink_cursor_follows_content():
    """Regression: growing the window and shrinking it back must not leave the
    cursor stranded below the content.

    When the screen is shortened the surviving rows shift up, so a cursor that
    pointed at surviving content must move up with it — otherwise it ends up on a
    blank row below the text (displayed ``dropped`` rows too low while its stored
    row is unchanged)."""
    m = TerminalModel(20, 5)
    for i in range(5):
        m.feed_bytes(f"L{i}\r\n".encode())
    assert m.cursor_position()[0] == 4  # bottom of a full screen

    # grow, keep typing so the screen fills again, then shrink:
    m.resize(20, 10)
    for i in range(5):
        m.feed_bytes(f"N{i}\r\n".encode())
    assert m.cursor_position()[0] == 9  # bottom of the 10-row screen

    # shrink back: the last 5 rows survive and shift to the top, cursor follows
    m.resize(20, 5)
    assert m.cursor_position()[0] == 4, "cursor must stay on the last content row"

    # a full screen shrunk from the bottom keeps the cursor at the bottom
    for i in range(5):
        m.feed_bytes(f"X{i}\r\n".encode())
    assert m.cursor_position()[0] == 4
    m.resize(20, 3)
    assert m.cursor_position()[0] == 2, "cursor must stay at the bottom on shrink"

    # cursor that pointed into the captured (top) region goes to the top
    m2 = TerminalModel(20, 6)
    m2.feed_bytes(b"AAA\r\n")  # content at row 0, cursor on row 1
    m2.resize(20, 2)  # shrink by 4: row 0 (AAA) is captured, cursor pointed at row 1
    assert m2.cursor_position()[0] == 0
