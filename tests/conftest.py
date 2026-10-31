"""Os testes de interface usam widgets Qt de verdade, sem abrir janela na tela."""

import os

import pytest

# Antes de qualquer import do Qt: sem tela (CI, SSH) o plugin padrao aborta.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _config_isolada(tmp_path, monkeypatch):
    """A janela le e grava configuracao e historico: nunca os da pessoa."""
    monkeypatch.setattr("media_downloader.window.HISTORY_PATH", tmp_path / "historico.json")
    monkeypatch.setattr("media_downloader.window.SETTINGS_PATH", tmp_path / "config.json")
