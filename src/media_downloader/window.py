"""Main window: consumes the backend queue by polling (a 100 ms QTimer).

No thread touches a widget. The backend publishes to the queue; `_poll` drains it
and `_handle` dispatches — the same pattern as before the switch to Qt.
"""

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .clip import PRECISE_CUT_SECONDS, clip_error, format_time, parse_time
from .config import APP_ICON_PATH, APP_TITLE, SUPPORTED_SITES_URL
from .downloader import DownloadManager, folder_summary_lines, summary_lines, transfer_text
from .history import HISTORY_PATH, HistoryEntry, load_history
from .icons import icon, pixmap
from .models import DownloadSummary, MediaOptions, MediaPreview, MusicMetadataCandidate
from .review import MetadataReview
from .selection import PlaylistSelection
from .settings import SETTINGS_PATH, Settings, load_settings, save_settings
from .theme import CLR_ACCENT_LIGHT, CLR_ACCENT_SOFT, CLR_MUTED, CLR_ON_ACCENT, CLR_TEXT
from .widgets import (
    FormatCard,
    OptionRow,
    PreviewCard,
    RangeSlider,
    ToggleSwitch,
    icon_label,
    keyboard_focus,
    label,
    set_prop,
    tabular,
)

_QUALITY_CHIPS = (("best", "Melhor"), ("1080", "1080p"), ("720", "720p"),
                  ("480", "480p"), ("360", "360p"))
# Waits for the user to stop typing before querying the link.
_PROBE_DELAY_MS = 600


def _section(title: str, icon_name: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    header = QHBoxLayout()
    header.setSpacing(10)
    header.addWidget(icon_label(icon_name, CLR_ACCENT_LIGHT, 18))
    header.addWidget(label(title, "h2"))
    header.addStretch(1)
    if subtitle:
        header.addWidget(label(subtitle, "small"))
    layout.addLayout(header)
    return card, layout


class MediaDownloaderApp(QMainWindow):
    _W, _H = 1120, 820

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setMinimumSize(980, 700)
        self.resize(self._W, self._H)
        self.setAcceptDrops(True)

        self._q: queue.Queue = queue.Queue()
        self._manager = DownloadManager(self._q)
        self._downloads_dir = load_settings(SETTINGS_PATH).downloads_dir
        self._manager.set_downloads_dir(self._downloads_dir)
        self._manager.set_history_path(HISTORY_PATH)
        self._thread: threading.Thread | None = None
        self._review = MetadataReview(self, self._manager)
        self._selection = PlaylistSelection(
            self, self._download_selected, self._selection_cancelled)
        self._preview: MediaPreview | None = None
        self._last_clipboard = ""
        self._syncing_clip = False

        self._build_ui()
        self._update_format()

        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._probe_now)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(100)

        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_url)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.start_download)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("page")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_download_page())
        self._pages.addWidget(self._build_history_page())
        main.addWidget(self._pages, 1)
        main.addWidget(self._build_action_bar())
        outer.addLayout(main, 1)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(236)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(16, 22, 16, 18)
        layout.setSpacing(4)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        image = QPixmap(str(APP_ICON_PATH))
        if not image.isNull():
            logo.setPixmap(image.scaled(
                36, 36, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label(APP_TITLE, "brand"))
        names.addWidget(label("yt-dlp · FFmpeg", "brandSub"))
        brand.addLayout(names, 1)
        layout.addLayout(brand)
        layout.addSpacing(22)

        self._nav = QButtonGroup(self)
        self._nav.setExclusive(True)
        for page, (text, icon_name) in enumerate((("Baixar", "download"), ("Histórico", "history"))):
            button = QPushButton(f"  {text}")
            button.setObjectName("navButton")
            button.setIcon(icon(icon_name, CLR_TEXT))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, page=page: self._show_page(page))
            self._nav.addButton(button, page)
            layout.addWidget(button)
        self._nav.button(0).setChecked(True)

        self.fix_folder_btn = QPushButton("  Corrigir pasta MP3")
        self.fix_folder_btn.setObjectName("navButton")
        self.fix_folder_btn.setIcon(icon("wand", CLR_TEXT))
        self.fix_folder_btn.setIconSize(QSize(18, 18))
        self.fix_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fix_folder_btn.setToolTip("Capa e metadata para MP3 que já estão no disco")
        self.fix_folder_btn.clicked.connect(self.fix_music_folder)
        layout.addWidget(self.fix_folder_btn)
        layout.addStretch(1)

        layout.addWidget(label("SALVAR EM", "sideLabel"))
        layout.addSpacing(4)
        self._dest_label = label(self._short_path(self._downloads_dir, 30), "destPath", wrap=True)
        self._dest_label.setToolTip(str(self._downloads_dir))
        layout.addWidget(self._dest_label)
        layout.addSpacing(8)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.dest_btn = QPushButton("Alterar")
        keyboard_focus(self.dest_btn)
        self.dest_btn.setObjectName("ghost")
        self.dest_btn.clicked.connect(self.choose_downloads_dir)
        self.folder_btn = QPushButton("Abrir")
        keyboard_focus(self.folder_btn)
        self.folder_btn.setObjectName("ghost")
        self.folder_btn.setIcon(icon("folder", CLR_TEXT, 16))
        self.folder_btn.clicked.connect(self.open_downloads_folder)
        buttons.addWidget(self.dest_btn)
        buttons.addWidget(self.folder_btn)
        layout.addLayout(buttons)
        layout.addSpacing(14)
        sites = label('<a href="#" style="color:%s; text-decoration:none;">'
                      'Sites compatíveis ↗</a>' % CLR_ACCENT_LIGHT, "link")
        sites.linkActivated.connect(lambda _: self._open_supported_sites())
        layout.addWidget(sites)
        return side

    def _scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(40, 34, 40, 34)
        content = QWidget()
        # Comfortable line length: on a wide screen the content does not spread out.
        content.setMaximumWidth(820)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        row.addStretch(1)
        row.addWidget(content, 100)
        row.addStretch(1)
        scroll.setWidget(holder)
        return scroll, layout

    def _build_download_page(self) -> QWidget:
        scroll, layout = self._scroll_page()
        layout.addWidget(label("O que vamos baixar?", "h1"))
        layout.addWidget(label(
            "Cole um link do YouTube, Vimeo, TikTok, Instagram, SoundCloud ou de outro site "
            "compatível com o yt-dlp. Arrastar o link para a janela também funciona.",
            "muted", wrap=True))
        layout.addSpacing(4)
        self._build_url_bar(layout)

        self._preview_card = PreviewCard()
        self._preview_card.hide()
        layout.addWidget(self._preview_card)

        self._build_format_section(layout)
        self._build_quality_section(layout)
        self._build_clip_section(layout)
        self._build_options_section(layout)
        layout.addStretch(1)
        return scroll

    def _build_url_bar(self, layout: QVBoxLayout) -> None:
        bar = QFrame()
        bar.setObjectName("urlBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 8, 8, 8)
        row.setSpacing(10)
        row.addWidget(icon_label("link", CLR_MUTED, 20))
        self.url_entry = QLineEdit()
        self.url_entry.setObjectName("urlInput")
        self.url_entry.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_entry.setClearButtonEnabled(True)
        self.url_entry.textChanged.connect(self._on_url_changed)
        self.url_entry.returnPressed.connect(self.start_download)
        row.addWidget(self.url_entry, 1)
        self.paste_btn = QPushButton("Colar")
        keyboard_focus(self.paste_btn)
        self.paste_btn.setObjectName("pasteButton")
        self.paste_btn.setIcon(icon("paste", CLR_TEXT, 16))
        self.paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paste_btn.clicked.connect(self._paste_url)
        row.addWidget(self.paste_btn)
        self._url_bar = bar
        # The border follows the field's focus, which is what the user sees.
        self.url_entry.installEventFilter(self)
        layout.addWidget(bar)
        self._url_hint = label("", "small")
        self._url_hint.hide()
        layout.addWidget(self._url_hint)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 — Qt API
        if watched is self.url_entry and event.type() in (event.Type.FocusIn, event.Type.FocusOut):
            set_prop(self._url_bar, "focused", event.type() == event.Type.FocusIn)
        return super().eventFilter(watched, event)

    def _build_format_section(self, layout: QVBoxLayout) -> None:
        card, body = _section("Formato", "sparkle")
        row = QHBoxLayout()
        row.setSpacing(12)
        self._mp3_card = FormatCard("music", "Áudio MP3", "192 kbps — só o som")
        self._mp4_card = FormatCard("video", "Vídeo MP4", "Imagem e som, na qualidade escolhida")
        self._mp3_card.clicked.connect(lambda: self._set_format("mp3"))
        self._mp4_card.clicked.connect(lambda: self._set_format("mp4"))
        row.addWidget(self._mp3_card)
        row.addWidget(self._mp4_card)
        body.addLayout(row)
        self._format = "mp3"
        layout.addWidget(card)

    def _build_quality_section(self, layout: QVBoxLayout) -> None:
        card, body = _section("Qualidade", "video", "teto — se não houver, baixa a mais próxima abaixo")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._quality_group = QButtonGroup(self)
        self._quality_group.setExclusive(True)
        self._quality_buttons: dict[str, QPushButton] = {}
        for value, text in _QUALITY_CHIPS:
            chip = QPushButton(text)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            keyboard_focus(chip)
            self._quality_group.addButton(chip)
            self._quality_buttons[value] = chip
            row.addWidget(chip)
        row.addStretch(1)
        self._quality_buttons["best"].setChecked(True)
        body.addLayout(row)
        self._quality_card = card
        layout.addWidget(card)

    def _build_clip_section(self, layout: QVBoxLayout) -> None:
        card = QFrame()
        card.setObjectName("card")
        body = QVBoxLayout(card)
        body.setContentsMargins(20, 18, 20, 18)
        body.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(icon_label("scissors", CLR_ACCENT_LIGHT, 18))
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Baixar só um trecho", "h2"))
        self._clip_subtitle = label(
            "De tal minuto a tal minuto. Desligado, baixa o arquivo inteiro.", "small", wrap=True)
        titles.addWidget(self._clip_subtitle)
        header.addLayout(titles, 1)
        self.clip_switch = ToggleSwitch()
        self.clip_switch.toggled.connect(self._update_clip)
        header.addWidget(self.clip_switch)
        body.addLayout(header)

        self._clip_body = QWidget()
        clip = QVBoxLayout(self._clip_body)
        clip.setContentsMargins(0, 6, 0, 0)
        clip.setSpacing(12)
        self.clip_slider = RangeSlider()
        self.clip_slider.rangeChanged.connect(self._slider_moved)
        clip.addWidget(self.clip_slider)

        times = QGridLayout()
        times.setHorizontalSpacing(12)
        times.setVerticalSpacing(4)
        times.addWidget(label("Início", "small"), 0, 0)
        times.addWidget(label("Fim", "small"), 0, 1)
        self.clip_start = QLineEdit()
        self.clip_end = QLineEdit()
        for column, field in enumerate((self.clip_start, self.clip_end)):
            field.setObjectName("timeInput")
            field.setFixedWidth(130)
            tabular(field)
            field.textEdited.connect(self._times_edited)
            times.addWidget(field, 1, column)
        self.clip_start.setPlaceholderText("0:00")
        self.clip_end.setPlaceholderText("fim")
        self._clip_summary = label("", "muted", wrap=True)
        times.addWidget(self._clip_summary, 1, 2)
        times.setColumnStretch(2, 1)
        clip.addLayout(times)
        self._clip_error = label("", "error")
        self._clip_error.hide()
        clip.addWidget(self._clip_error)
        body.addWidget(self._clip_body)
        self._clip_body.hide()
        self._clip_card = card
        layout.addWidget(card)

    def _build_options_section(self, layout: QVBoxLayout) -> None:
        card, body = _section("Extras", "tag")
        body.setSpacing(2)
        self.metadata_row = OptionRow(
            "tag", "Capa e metadados",
            "Título, artista e capa da plataforma. O que ficar incompleto vai para a revisão.")
        self.auto_metadata_row = OptionRow(
            "sparkle", "Aplicar sozinho do catálogo",
            "Quando artista, título e duração batem com o iTunes. O que ficar em dúvida "
            "vai para a revisão.")
        self.auto_metadata_row.switch.setChecked(True)
        self.subtitles_row = OptionRow(
            "subtitles", "Legendas embutidas",
            "Português e inglês, inclusive as automáticas, dentro do MP4.")
        self.chapters_row = OptionRow(
            "chapters", "Capítulos",
            "Grava os capítulos do vídeo no MP4 — o player mostra as marcações.")
        self.sponsors_row = OptionRow(
            "shield", "Pular patrocínios (SponsorBlock)",
            "Remove patrocínio, autopromoção, pedido de inscrição e o trecho sem música "
            "de clipes, marcados pela comunidade.")
        for row in (self.metadata_row, self.auto_metadata_row, self.subtitles_row,
                    self.chapters_row, self.sponsors_row):
            body.addWidget(row)
        self.metadata_row.switch.toggled.connect(lambda _: self._update_format())
        layout.addWidget(card)

    def _build_action_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("actionBar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(40, 14, 40, 16)
        layout.setSpacing(12)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        row = QHBoxLayout()
        row.setSpacing(14)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self._status_label = label("Pronto para baixar.", "status")
        self._status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._info_label = tabular(label("Os arquivos são organizados por formato e tipo.", "small"))
        self._info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        texts.addWidget(self._status_label)
        texts.addWidget(self._info_label)
        row.addLayout(texts, 1)
        self._pct_label = tabular(label("", "pct"))
        row.addWidget(self._pct_label)
        self.cancel_btn = QPushButton("Cancelar")
        keyboard_focus(self.cancel_btn)
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setIcon(icon("close", CLR_MUTED, 16))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_download)
        row.addWidget(self.cancel_btn)
        self.download_btn = QPushButton("Baixar")
        keyboard_focus(self.download_btn)
        self.download_btn.setObjectName("primary")
        self.download_btn.setIcon(icon("download", CLR_ON_ACCENT, 18))
        self.download_btn.setMinimumWidth(170)
        self.download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_btn.clicked.connect(self.start_download)
        row.addWidget(self.download_btn)
        layout.addLayout(row)
        return bar

    def _build_history_page(self) -> QWidget:
        scroll, layout = self._scroll_page()
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("Histórico", "h1"))
        titles.addWidget(label("Os últimos arquivos baixados neste computador.", "muted"))
        header.addLayout(titles, 1)
        layout.addLayout(header)
        self._history_list = QVBoxLayout()
        self._history_list.setSpacing(8)
        layout.addLayout(self._history_list)
        layout.addStretch(1)
        return scroll

    # ── Navigation ────────────────────────────────────────────────────────────

    def _show_page(self, page: int) -> None:
        if page == 1:
            self._refresh_history()
        self._pages.setCurrentIndex(page)
        self._nav.button(page).setChecked(True)
        # The history page has no link: progress stays visible, the "Baixar" button does not.
        self.download_btn.setVisible(page == 0)

    def _refresh_history(self) -> None:
        while self._history_list.count():
            item = self._history_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = load_history(HISTORY_PATH)
        if not entries:
            empty = QFrame()
            empty.setObjectName("card")
            box = QVBoxLayout(empty)
            box.setContentsMargins(24, 40, 24, 40)
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(icon_label("history", CLR_MUTED, 34), 0, Qt.AlignmentFlag.AlignHCenter)
            box.addWidget(label("Nada baixado ainda", "h2"), 0, Qt.AlignmentFlag.AlignHCenter)
            box.addWidget(label("O que você baixar aparece aqui, com atalho para abrir.",
                                "muted"), 0, Qt.AlignmentFlag.AlignHCenter)
            box.addSpacing(10)
            self._history_empty_action = QPushButton("Baixar algo")
            self._history_empty_action.setObjectName("primary")
            self._history_empty_action.setIcon(icon("download", CLR_ON_ACCENT, 18))
            self._history_empty_action.clicked.connect(self._focus_url)
            box.addWidget(self._history_empty_action, 0, Qt.AlignmentFlag.AlignHCenter)
            self._history_list.addWidget(empty)
            return
        for entry in entries:
            self._history_list.addWidget(self._history_row(entry))

    def _history_row(self, entry: HistoryEntry) -> QWidget:
        row = QFrame()
        row.setObjectName("historyRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.setSpacing(14)
        badge = QLabel()
        badge.setFixedSize(40, 40)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(pixmap("music" if entry.kind == "mp3" else "video", CLR_ACCENT_LIGHT, 20))
        badge.setStyleSheet(f"background: {CLR_ACCENT_SOFT}; border-radius: 10px;")
        layout.addWidget(badge)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = label(entry.title, "optionTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        texts.addWidget(title)
        exists = Path(entry.path).exists()
        when = entry.finished_at.replace("T", " ")[:16]
        detail = f"{entry.kind.upper()} · {when}" + ("" if exists else " · arquivo movido ou apagado")
        texts.addWidget(label(detail, "small"))
        layout.addLayout(texts, 1)
        open_btn = QPushButton("Abrir")
        open_btn.setObjectName("ghost")
        open_btn.setIcon(icon("open", CLR_TEXT, 15))
        open_btn.setEnabled(exists)
        open_btn.clicked.connect(lambda: self._open_path(Path(entry.path)))
        folder_btn = QPushButton("Pasta")
        folder_btn.setObjectName("ghost")
        folder_btn.setIcon(icon("folder", CLR_TEXT, 15))
        folder_btn.clicked.connect(lambda: self._open_path(Path(entry.path).parent))
        layout.addWidget(open_btn)
        layout.addWidget(folder_btn)
        return row

    # ── Link and preview ──────────────────────────────────────────────────────

    def _focus_url(self) -> None:
        self._show_page(0)
        self.url_entry.setFocus()
        self.url_entry.selectAll()

    def _paste_url(self) -> None:
        """'Colar' replaces the whole field: it is a shortcut for 'put the link here'."""
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.url_entry.setText(text)

    def _on_app_state(self, state) -> None:
        """When returning to the window with a copied link and an empty field, fill it in."""
        if state != Qt.ApplicationState.ApplicationActive or self.url_entry.text().strip():
            return
        if self._thread and self._thread.is_alive():
            return
        text = QGuiApplication.clipboard().text().strip()
        if text and text != self._last_clipboard and self._valid_url(text):
            self._last_clipboard = text
            self.url_entry.setText(text)
            self._set_hint("Link colado da área de transferência.")

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt API
        data = event.mimeData()
        text = data.urls()[0].toString() if data.hasUrls() else data.text()
        if text.strip() and not (self._thread and self._thread.is_alive()):
            self._show_page(0)
            self.url_entry.setText(text.strip())

    def _set_hint(self, text: str, error: bool = False) -> None:
        self._url_hint.setText(text)
        self._url_hint.setObjectName("error" if error else "small")
        self._url_hint.setStyle(self._url_hint.style())
        self._url_hint.setVisible(bool(text))
        set_prop(self._url_bar, "invalid", error)

    def _on_url_changed(self, text: str) -> None:
        self._set_hint("")
        self._preview = None
        self._update_clip_availability()
        if self._valid_url(text.strip()):
            self._probe_timer.start(_PROBE_DELAY_MS)
        else:
            self._probe_timer.stop()
            self._preview_card.hide()

    def _probe_now(self) -> None:
        url = self.url_entry.text().strip()
        if not self._valid_url(url):
            return
        self._preview_card.show_loading()
        # A preview is not a download: it runs alongside and is outside the "one at a time" guard.
        threading.Thread(target=self._manager.probe, args=(url,), daemon=True).start()

    def _apply_preview(self, preview: MediaPreview) -> None:
        self._preview = preview
        self._preview_card.show_preview(preview)
        if preview.duration:
            self.clip_slider.setMaximum(preview.duration)
            start = preview.start_hint if preview.start_hint and \
                preview.start_hint < preview.duration else 0.0
            self.clip_slider.setValues(start, preview.duration, emit=False)
            self._sync_times_from_slider()
            if preview.start_hint:
                self.clip_switch.setChecked(True)
        self._update_quality_availability()
        self._update_clip_availability()

    # ── Format, quality, and options ──────────────────────────────────────────

    def _set_format(self, value: str) -> None:
        self._format = value
        self._update_format()

    def _update_format(self) -> None:
        mp3 = self._format == "mp3"
        self._mp3_card.setSelected(mp3)
        self._mp4_card.setSelected(not mp3)
        self._quality_card.setVisible(not mp3)
        self.metadata_row.setAvailable(mp3, "Capa e metadados são do MP3.")
        # Automatic application only exists inside the metadata option.
        self.auto_metadata_row.setAvailable(
            mp3 and self.metadata_row.isChecked(), "Ligue \"Capa e metadados\" primeiro.")
        self.subtitles_row.setAvailable(not mp3, "Legendas só existem no vídeo.")
        self.chapters_row.setAvailable(not mp3, "Capítulos só existem no vídeo.")

    def _update_quality_availability(self) -> None:
        """Does not offer 1080p for a video that only exists in 480p."""
        top = self._preview.heights[0] if self._preview and self._preview.heights else None
        for value, chip in self._quality_buttons.items():
            available = value == "best" or top is None or int(value) <= top
            chip.setEnabled(available)
            chip.setToolTip("" if available else f"Este vídeo vai até {top}p.")
            if not available and chip.isChecked():
                self._quality_buttons["best"].setChecked(True)

    def _quality(self) -> str:
        for value, chip in self._quality_buttons.items():
            if chip.isChecked():
                return value
        return "best"

    # ── Section ───────────────────────────────────────────────────────────────

    def _update_clip_availability(self) -> None:
        playlist = bool(self._preview and self._preview.is_playlist)
        self.clip_switch.setEnabled(not playlist)
        if playlist:
            self.clip_switch.setChecked(False)
            self._clip_subtitle.setText(
                "Não vale para playlist: cada item tem duração própria.")
        else:
            self._clip_subtitle.setText(
                "De tal minuto a tal minuto. Desligado, baixa o arquivo inteiro.")
        self._update_clip()

    def _update_clip(self, *_args) -> None:
        on = self.clip_switch.isChecked()
        self._clip_body.setVisible(on)
        known = bool(self.clip_slider.maximum())
        self.clip_slider.setVisible(known)
        self._validate_clip()

    def _slider_moved(self, start: float, end: float) -> None:
        self._sync_times_from_slider()
        self._validate_clip()

    def _sync_times_from_slider(self) -> None:
        start, end = self.clip_slider.values()
        self._syncing_clip = True
        self.clip_start.setText(format_time(start))
        self.clip_end.setText(format_time(end))
        self._syncing_clip = False

    def _times_edited(self, _text: str) -> None:
        if self._syncing_clip:
            return
        start, end, error = self._read_clip()
        if error is None and self.clip_slider.maximum():
            self.clip_slider.setValues(start or 0.0, self.clip_slider.maximum() if end is None else end,
                                       emit=False)
        self._validate_clip()

    def _read_clip(self) -> tuple[float | None, float | None, str | None]:
        try:
            start = parse_time(self.clip_start.text())
        except ValueError:
            return None, None, "Início não é um tempo — use 1:30, 90 ou 1:02:03."
        try:
            end = parse_time(self.clip_end.text())
        except ValueError:
            return None, None, "Fim não é um tempo — use 1:30, 90 ou 1:02:03."
        duration = self._preview.duration if self._preview else None
        # An end equal to the duration means "to the end": there is no cut there.
        if end is not None and duration and abs(end - duration) < 0.05:
            end = None
        return start, end, clip_error(start, end, duration)

    def _validate_clip(self) -> bool:
        if not self.clip_switch.isChecked():
            self._clip_error.hide()
            self.download_btn.setEnabled(not self._busy())
            return True
        start, end, error = self._read_clip()
        set_prop(self.clip_start, "invalid", bool(error) and "Início" in (error or ""))
        set_prop(self.clip_end, "invalid", bool(error) and "Início" not in (error or ""))
        self._clip_error.setText(error or "")
        self._clip_error.setVisible(bool(error))
        if error:
            self._clip_summary.setText("")
        else:
            duration = self._preview.duration if self._preview else None
            stop = end if end is not None else duration
            if stop is not None:
                length = stop - (start or 0)
                precise = end is not None and length <= PRECISE_CUT_SECONDS
                self._clip_summary.setText(
                    f"Trecho de {format_time(length)} · "
                    + ("corte exato no quadro" if precise
                       else "corte no keyframe mais próximo (rápido)"))
            else:
                self._clip_summary.setText("Até o final do vídeo.")
        self.download_btn.setEnabled(not error and not self._busy())
        return not error

    def _media_options(self) -> MediaOptions:
        start = end = None
        if self.clip_switch.isChecked() and self.clip_switch.isEnabled():
            start, end, _ = self._read_clip()
        return MediaOptions(
            quality=self._quality(),
            clip_start=start or None,
            clip_end=end,
            subtitles=self.subtitles_row.isChecked(),
            chapters=self.chapters_row.isChecked(),
            skip_sponsors=self.sponsors_row.isChecked(),
        )

    # ── Actions ───────────────────────────────────────────────────────────────

    def _busy(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start_download(self) -> None:
        url = self.url_entry.text().strip()
        if not self._valid_url(url):
            self._set_hint("Link inválido — informe um endereço HTTP ou HTTPS.", error=True)
            self.url_entry.setFocus()
            return
        if self._busy():
            QMessageBox.warning(self, "Download em andamento",
                                "Aguarde o download atual terminar antes de iniciar outro.")
            return
        if not self._validate_clip():
            return

        self._set_busy(True)
        self._reset_progress("Iniciando...", "Analisando link...")
        self._start_worker(
            self._manager.download,
            url, self._format, self.metadata_row.isChecked(),
            self.auto_metadata_row.isChecked(), self._media_options(),
        )

    def fix_music_folder(self) -> None:
        """Fixes cover art and metadata of MP3 files already on disk."""
        if self._busy():
            QMessageBox.warning(self, "Operação em andamento",
                                "Aguarde o download atual terminar antes de corrigir uma pasta.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Pasta com os MP3 a corrigir")
        if not folder:
            return
        auto = QMessageBox.question(
            self, "Corrigir pasta MP3",
            "Aplicar sozinho quando artista, título e duração baterem com o catálogo?\n\n"
            "Isso grava as tags e a capa e renomeia o arquivo para o nome da música. "
            "O que ficar em dúvida vai para a revisão.\n\n"
            "Com \"Não\", tudo vai para a revisão e nada é gravado sem você confirmar.",
        ) == QMessageBox.StandardButton.Yes
        artist, ok = QInputDialog.getText(
            self, "Artista dos MP3 sem artista",
            "Opcional: artista para os MP3 que não têm artista gravado\n"
            "(ex.: uma pasta só de um artista). Deixe em branco para pular.")
        if not ok:
            return

        self._set_busy(True, busy_text="Corrigindo...")
        self._reset_progress("Lendo os MP3 da pasta...", f"Pasta: {folder}")
        self._start_worker(self._manager.fix_folder, folder, auto, artist)

    def _start_worker(self, target, *args) -> None:
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self._thread.start()

    def cancel_download(self) -> None:
        if self._selection.is_open():
            self._selection.cancel()
            return
        self._manager.cancel()
        self.cancel_btn.setEnabled(False)
        self._set_status("Cancelando — termina o item atual e para...")

    def _download_selected(self, indices: list[int]) -> None:
        self._set_status("Iniciando...")
        self._start_worker(self._manager.download_selected, indices)

    def _selection_cancelled(self) -> None:
        self._manager.discard_listing()
        self._set_busy(False)
        self._reset_progress("Download cancelado antes de começar.", "Nenhum item foi baixado.")

    def choose_downloads_dir(self) -> None:
        if self._busy():
            QMessageBox.warning(self, "Download em andamento",
                                "Aguarde o download atual terminar antes de trocar a pasta.")
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Onde salvar os downloads", str(self._downloads_dir))
        if not folder:
            return
        self._downloads_dir = Path(folder)
        self._manager.set_downloads_dir(self._downloads_dir)
        self._dest_label.setText(self._short_path(self._downloads_dir, 30))
        self._dest_label.setToolTip(str(self._downloads_dir))
        try:
            save_settings(SETTINGS_PATH, Settings(downloads_dir=self._downloads_dir))
        except OSError as exc:
            QMessageBox.warning(
                self, "Pasta de destino",
                f"A pasta vale para esta sessão, mas não deu para lembrar dela.\n\n{exc}")

    def open_downloads_folder(self) -> None:
        folder = self._downloads_dir
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(folder)

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao abrir", str(exc))

    @staticmethod
    def _open_supported_sites() -> None:
        webbrowser.open_new_tab(SUPPORTED_SITES_URL)

    # ── Event loop ────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        while True:
            try:
                event = self._q.get_nowait()
            except queue.Empty:
                break
            self._handle(event)

    def _handle(self, event: dict[str, Any]) -> None:
        etype = event.get("type")

        if etype == "status":
            self._set_status(event.get("message", ""))

        elif etype == "preview":
            # The user may have replaced the link while the preview was loading.
            if event["url"] == self.url_entry.text().strip():
                self._apply_preview(event["preview"])

        elif etype == "preview_failed":
            if event["url"] == self.url_entry.text().strip():
                self._preview_card.show_failed(event.get("message", ""))

        elif etype == "meta":
            total = event.get("total_items", 0)
            tipo = "Playlist" if event.get("playlist_mode") else "Mídia"
            self._info_label.setText(f"{tipo} · {total} item(s) · {event.get('target_dir', '')}")

        elif etype == "progress":
            pct = event.get("progress", 0.0)
            self.progress_bar.setValue(int(pct * 10))
            self._pct_label.setText(f"{pct:.0f}%")
            self._set_status(event.get("message", "Baixando..."))
            speed = transfer_text(event.get("speed"), event.get("eta"))
            if speed:
                self._info_label.setText(speed)

        elif etype == "done":
            summary: DownloadSummary = event["summary"]
            self._set_busy(False)
            ok = bool(summary.downloaded_count)
            self.progress_bar.setValue(1000 if ok else 0)
            self._pct_label.setText("100%" if ok else "0%")
            self._set_tone("ok" if ok else "")
            self._show_summary(summary)

        elif etype == "folder_checked":
            summary = event["summary"]
            self._set_busy(False)
            self.progress_bar.setValue(1000 if summary.total_items else 0)
            self._pct_label.setText("")
            pending = len(summary.metadata_pending_items)
            self._set_status(
                f"Pasta conferida — {len(summary.metadata_auto_applied)} corrigido(s) "
                f"sozinho(s), {pending} para revisar.")
            self._info_label.setText(f"Pasta: {summary.target_dir}")
            QMessageBox.information(self, "Correção da pasta",
                                    "\n".join(folder_summary_lines(summary)))
            if pending:
                self._review.open(summary.metadata_pending_items)

        elif etype == "playlist_listed":
            entries = event.get("entries", [])
            self._set_status(f"Playlist com {len(entries)} item(s) — escolha o que baixar.")
            self._selection.open(event.get("title", "Playlist"), entries)

        elif etype == "cancelled":
            summary = event["summary"]
            self._set_busy(False)
            self._set_tone("")
            if summary.total_items:
                self._set_status(
                    f"Cancelado — {summary.downloaded_count} de {summary.total_items} "
                    "item(s) baixado(s) antes de parar.")
                self._info_label.setText(f"Destino: {summary.target_dir}")
            else:
                self._set_status("Download cancelado antes de começar.")
                self._info_label.setText("Nenhum item foi baixado.")
            if summary.metadata_pending_items:
                self._review.open(summary.metadata_pending_items)

        elif etype == "error":
            self._set_busy(False)
            self.progress_bar.setValue(0)
            self._pct_label.setText("")
            self._set_tone("error")
            self._set_status("Falha no download.")
            self._info_label.setText("Verifique o link e tente de novo. Erro de extração "
                                     "costuma ser yt-dlp desatualizado: ./run.sh --atualizar")
            QMessageBox.critical(self, "Erro no download", event.get("message", "Erro desconhecido."))

        elif etype == "metadata_results":
            self._review.show_candidates(event["pending_item"], event.get("candidates", []))

        elif etype == "metadata_search_error":
            self._review.search_finished(event["pending_item"])
            QMessageBox.critical(
                self, "Busca de metadata",
                f"Não foi possível buscar metadata para {event['pending_item'].title}.\n\n"
                f"{event.get('message', 'Erro desconhecido.')}")

        elif etype == "metadata_applied":
            self._review.resolve(event["pending_item"])
            candidate: MusicMetadataCandidate = event["candidate"]
            cover = " com capa incorporada" if event.get("cover_embedded") else " sem capa disponível"
            self._set_status(f"Metadata de {candidate.artist} — {candidate.title} importada{cover}.")

        elif etype == "metadata_bulk_progress":
            self._review.bulk_progress(event["done"], event["total"])
            self._set_status(
                f"Aplicando sugestão do catálogo ({event['done'] + 1}/{event['total']})...")

        elif etype == "metadata_bulk_applied":
            self._review.resolve(event["pending_item"])

        elif etype == "metadata_bulk_done":
            applied, kept = event["applied"], event["kept"]
            self._review.bulk_finished(applied, kept, event.get("failed", 0))
            self._set_status(
                f"Sugestão aplicada em {applied} MP3"
                + (f"; {kept} continuam na revisão." if kept else "."))

        elif etype == "metadata_cover_cropped":
            self._review.resolve(event["pending_item"])

        elif etype == "metadata_crop_done":
            self._review.crop_finished(event["cropped"], event["failed"])
            self._set_status(f"{event['cropped']} capa(s) recortada(s) da miniatura do vídeo.")

        elif etype == "metadata_import_error":
            QMessageBox.critical(
                self, "Importação de metadata",
                f"Não foi possível atualizar {event['pending_item'].title}.\n\n"
                f"{event.get('message', 'Erro desconhecido.')}")

        elif etype == "metadata_embedded":
            self._review.show_embedded(event["pending_item"], event["embedded"])

        elif etype == "metadata_embedded_unavailable":
            self._review.show_embedded_unavailable(event["pending_item"])

        elif etype == "metadata_cover_preview":
            self._review.show_candidate_cover(event["candidate"], event["data"], event["mime"])

        elif etype == "metadata_cover_unavailable":
            self._review.show_candidate_cover_unavailable(event["candidate"])

    def _show_summary(self, s: DownloadSummary) -> None:
        failed = len(s.failed_items)
        pending = len(s.metadata_pending_items)
        self._set_status(
            f"Concluído — {s.downloaded_count} baixado(s)"
            + (f", {failed} com falha" if failed else "")
            + (f", {pending} com metadata a confirmar" if pending else ""))
        self._info_label.setText(f"Destino: {s.target_dir}")
        # Flash in the taskbar: the long download finished with the window in the background.
        QApplication.alert(self)
        if self._pages.currentIndex() == 1:
            self._refresh_history()
        QMessageBox.information(self, "Resumo do download", "\n".join(summary_lines(s)))
        if pending:
            self._review.open(s.metadata_pending_items)

    # ── State helpers ─────────────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setToolTip(text)

    def _set_tone(self, tone: str) -> None:
        for widget in (self._status_label, self._pct_label, self.progress_bar):
            set_prop(widget, "tone", tone)

    def _reset_progress(self, status: str, info: str) -> None:
        self.progress_bar.setValue(0)
        self._pct_label.setText("")
        self._set_tone("")
        self._set_status(status)
        self._info_label.setText(info)

    def _set_busy(self, busy: bool, busy_text: str = "Baixando...") -> None:
        self.download_btn.setEnabled(not busy)
        self.download_btn.setText(busy_text if busy else "Baixar")
        for widget in (self.fix_folder_btn, self.url_entry, self.paste_btn, self.dest_btn,
                       self._mp3_card, self._mp4_card, self._quality_card, self._clip_card,
                       self.metadata_row.parentWidget()):
            widget.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        if not busy:
            self._update_format()
            self._validate_clip()

    @staticmethod
    def _short_path(path: Path, limit: int = 70) -> str:
        # The end of the path is what tells where the file lands.
        text = str(path)
        return text if len(text) <= limit else "…" + text[-(limit - 1):]

    @staticmethod
    def _valid_url(url: str) -> bool:
        try:
            p = urlparse(url)
        except ValueError:
            return False
        if p.scheme not in {"http", "https"}:
            return False
        return bool(p.hostname)
