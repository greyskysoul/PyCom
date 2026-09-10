"""Generate SVG screenshots of PyCom's key screens for the README.

Run from the repository root:
    python tools/screenshots.py

Each screenshot drives the real app with Textual's headless pilot, feeds some
demo data, then writes an SVG (GitHub renders SVGs inline) into
``docs/screenshots/``.  Two sets are produced: the English UI (``main.svg``,
``hex.svg``, ``menu.svg``, ``options.svg``) for README.md and the Chinese UI
(``*-zh.svg``) for README.zh-CN.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pycom.app import PyComApp
from pycom.config import AppConfig

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

MAIN_DEMO = (
    b"\x1b[1;36mSTM32 Bootloader v2.1\x1b[0m\r\n"
    b"\x1b[32m[ OK ]\x1b[0m flash detected\r\n"
    b"\x1b[33m[WARN]\x1b[0m app area empty, waiting for firmware\r\n"
    b"\x1b[32m[ OK ]\x1b[0m ready for YMODEM transfer\r\n"
    b"> "
)

HEX_DEMO = (
    b"HELLO PyCom! 0123456789"
    + bytes([0x01, 0x02, 0x03, 0x0D, 0x0A, 0x7F, 0x80, 0xFE])
    + b"YMODEM ready"
    + b"  booter flash @ 0x0800C000\r\n"
)


def _save(svg: str, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(svg, encoding="utf-8")
    print(f"wrote {path} ({len(svg)} bytes)")


async def _shot(app: PyComApp, pilot, name: str, title: str) -> None:
    await pilot.pause(0.4)  # let the layout settle
    _save(app.export_screenshot(title=title, simplify=True), name)


async def _generate(lang: str, suffix: str) -> None:
    """Generate all four screenshots in the given UI language."""
    # -- 1. main terminal (loopback + demo device output) -----------------------
    app = PyComApp(cfg=AppConfig(language=lang, hex_mode=False))
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(0.3)
        app.open_loopback()
        app.send_bytes(b"AT\r")
        await pilot.pause(0.3)
        app.model.feed_bytes(MAIN_DEMO)
        app._view().mark_dirty()
        await _shot(app, pilot, f"main{suffix}.svg", f"PyCom - main terminal")

    # -- 2. main menu popup ------------------------------------------------------
    app = PyComApp(cfg=AppConfig(language=lang, hex_mode=False))
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(0.3)
        app.model.feed_bytes(MAIN_DEMO)
        app._view().mark_dirty()
        await pilot.press("ctrl+a")
        await pilot.press("z")
        await _shot(app, pilot, f"menu{suffix}.svg", f"PyCom - main menu")

    # -- 3. HEX receive with ASCII pane -----------------------------------------
    app = PyComApp(cfg=AppConfig(language=lang, hex_mode=True))
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(0.3)
        app.open_loopback()
        await pilot.pause(0.3)
        app._rx_to_terminal(HEX_DEMO)
        await pilot.pause(0.3)
        await _shot(app, pilot, f"hex{suffix}.svg", f"PyCom - HEX mode with ASCII pane")

    # -- 4. options screen -------------------------------------------------------
    app = PyComApp(cfg=AppConfig(language=lang, hex_mode=False))
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("ctrl+a")
        await pilot.press("o")
        await _shot(app, pilot, f"options{suffix}.svg", f"PyCom - options")


async def main() -> None:
    await _generate("en", "")      # English UI -> main.svg / hex.svg / ...
    await _generate("zh", "-zh")   # Chinese UI -> main-zh.svg / hex-zh.svg / ...


if __name__ == "__main__":
    asyncio.run(main())
