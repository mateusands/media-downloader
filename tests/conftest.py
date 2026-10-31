"""The UI tests use real Qt widgets without opening a window on screen."""

import os

import pytest

# Before any Qt import: without a display (CI, SSH) the default plugin aborts.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """The window reads and writes settings and history: never the user's own."""
    monkeypatch.setattr("media_downloader.window.HISTORY_PATH", tmp_path / "historico.json")
    monkeypatch.setattr("media_downloader.window.SETTINGS_PATH", tmp_path / "config.json")
