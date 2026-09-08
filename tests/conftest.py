"""Shared pytest fixtures.

The app now resolves its UI language from the OS at startup (falling back to
English).  The original test-suite asserts Chinese UI text, so these fixtures
force the Chinese language for every test unless a test explicitly switches
via ``pycom.i18n.set_language``.
"""

from __future__ import annotations

import pytest

from pycom import i18n


@pytest.fixture(autouse=True)
def _force_chinese_language(monkeypatch):
    monkeypatch.setattr("pycom.app.detect_system_language", lambda: "zh")
    i18n.set_language("zh")
    yield
    i18n.set_language("zh")


@pytest.fixture(autouse=True)
def _isolated_config_dir(monkeypatch, tmp_path):
    """Keep every test away from the real user config (language, ports, ...)."""
    import pycom.config as cfgmod

    monkeypatch.setattr(cfgmod, "_config_dir", lambda: str(tmp_path))
