"""Ponto de entrada do Media Downloader."""

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from media_downloader.config import APP_TITLE
from media_downloader.theme import FONT_FAMILIES, stylesheet
from media_downloader.window import MediaDownloaderApp


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setDesktopFileName("mediadownloader")
    # Fusion desenha igual em todo sistema; o estilo nativo ignora parte do QSS.
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(10)
    app.setFont(font)
    app.setStyleSheet(stylesheet())
    window = MediaDownloaderApp()
    window.show()
    window.url_entry.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
