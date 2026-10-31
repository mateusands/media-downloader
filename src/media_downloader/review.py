"""Metadata review: the two dialogs and the cover preview.

Lives outside `window.py` because it is a flow of its own, with its own state (the
rows on screen) and its own lifecycle — the dialogs open and close without the
main window knowing. `window.py` only forwards queue events here.
"""

import threading
from collections import deque

import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .config import COVER_PREVIEW_SIZE
from .downloader import DownloadManager
from .metadata import metadata_review_detail
from .models import EmbeddedMetadata, MetadataPendingItem, MusicMetadataCandidate
from .theme import BG_HOVER, CLR_ERROR, CLR_MUTED
from .widgets import label, rounded_pixmap


def _has_provisional_cover(item: MetadataPendingItem) -> bool:
    return any(reason.startswith("capa provisoria") for reason in item.review_reasons)


def _alive(widget) -> bool:
    """The dialog may have been closed while the thread was working."""
    return widget is not None and shiboken6.isValid(widget)


def _cover_box(text: str) -> QLabel:
    box = QLabel(text)
    box.setFixedSize(COVER_PREVIEW_SIZE, COVER_PREVIEW_SIZE)
    box.setAlignment(Qt.AlignmentFlag.AlignCenter)
    box.setWordWrap(True)
    box.setStyleSheet(
        f"background: {BG_HOVER}; border-radius: 10px; color: {CLR_MUTED}; font-size: 10px;")
    return box


def _show_cover(box: QLabel, data: bytes) -> bool:
    image = rounded_pixmap(data, COVER_PREVIEW_SIZE, COVER_PREVIEW_SIZE, radius=10)
    if image is None:
        return False
    box.setStyleSheet("")
    box.setPixmap(image)
    return True


def _dialog(parent: QWidget, title: str, width: int, height: int) -> tuple[QDialog, QVBoxLayout]:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(width, height)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(10)
    return dialog, layout


def _scroll_list(layout: QVBoxLayout) -> QVBoxLayout:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    rows = QVBoxLayout(body)
    rows.setContentsMargins(0, 4, 6, 4)
    rows.setSpacing(8)
    rows.addStretch(1)
    scroll.setWidget(body)
    layout.addWidget(scroll, 1)
    return rows


class MetadataReview:
    """Owns the review dialogs. Knows nothing about the main window."""

    def __init__(self, parent: QWidget, manager: DownloadManager):
        self._parent = parent
        self._manager = manager
        self._import_thread: threading.Thread | None = None
        self._search_queue: deque[MetadataPendingItem] = deque()
        self._searching: MetadataPendingItem | None = None
        self._metadata_cover_labels: dict[str, QLabel] = {}
        self._metadata_review_rows: dict[
            MetadataPendingItem, tuple[QFrame, QLabel, QLabel, QPushButton],
        ] = {}
        self._metadata_review_window: QDialog | None = None
        self._bulk_thread: threading.Thread | None = None
        self._bulk_btn: QPushButton | None = None
        self._bulk_label: QLabel | None = None
        self._crop_btn: QPushButton | None = None

    def open(self, pending_items: list[MetadataPendingItem]) -> None:
        window, layout = _dialog(self._parent, "MP3 com metadata a confirmar", 820, 560)
        self._metadata_review_window = window
        self._metadata_review_rows = {}

        layout.addWidget(label("Confirmar metadata", "h1"))
        layout.addWidget(label(
            "A prévia mostra o que já está gravado no arquivo. Busque no catálogo para "
            "substituir; a seleção do catálogo, e não o título sugerido, é o que será importado.",
            "muted", wrap=True))

        bulk_row = QHBoxLayout()
        bulk_row.setSpacing(8)
        self._bulk_btn = QPushButton("Aplicar sugestão em todos")
        self._bulk_btn.setObjectName("primary")
        self._bulk_btn.clicked.connect(self._toggle_bulk)
        self._crop_btn = QPushButton("Recortar miniaturas")
        self._crop_btn.setObjectName("ghost")
        self._crop_btn.clicked.connect(self._crop_all)
        self._bulk_label = label(
            "Aplicar: resultado do catálogo com o mesmo artista e título. "
            "Recortar: usa o centro da miniatura do vídeo como capa quadrada.",
            "small", wrap=True)
        bulk_row.addWidget(self._bulk_btn)
        bulk_row.addWidget(self._crop_btn)
        bulk_row.addWidget(self._bulk_label, 1)
        layout.addLayout(bulk_row)

        rows = _scroll_list(layout)
        for pending_item in pending_items:
            row = QFrame()
            row.setObjectName("listRow")
            grid = QGridLayout(row)
            grid.setContentsMargins(10, 10, 12, 10)
            grid.setHorizontalSpacing(14)
            grid.setColumnStretch(1, 1)

            cover_label = _cover_box("Lendo\ncapa...")
            grid.addWidget(cover_label, 0, 0, 3, 1)
            title = label(pending_item.title, "optionTitle", wrap=True)
            grid.addWidget(title, 0, 1)
            embedded_label = label("Lendo o que já está gravado...", "small", wrap=True)
            grid.addWidget(embedded_label, 1, 1)
            grid.addWidget(label(metadata_review_detail(pending_item), "small", wrap=True), 2, 1)

            search_btn = QPushButton("Buscar metadata")
            search_btn.setObjectName("primary")
            search_btn.clicked.connect(lambda _=False, item=pending_item: self.start_search(item))
            grid.addWidget(search_btn, 0, 2, 2 if _has_provisional_cover(pending_item) else 3, 1)
            if _has_provisional_cover(pending_item):
                crop = QPushButton("Recortar capa")
                crop.setObjectName("ghost")
                crop.clicked.connect(
                    lambda _=False, item=pending_item: self._spawn(self._manager.crop_covers, [item]))
                grid.addWidget(crop, 2, 2)
            rows.insertWidget(rows.count() - 1, row)

            # Keyed by the item itself: two pending items may share the same
            # path (yt-dlp does not always report the file name), and then one
            # item's preview overwrote the other's row.
            self._metadata_review_rows[pending_item] = (
                row, cover_label, embedded_label, search_btn,
            )
            self._spawn(self._manager.load_embedded_metadata, pending_item)
        window.show()

    def show_embedded(
        self, pending_item: MetadataPendingItem, embedded: EmbeddedMetadata,
    ) -> None:
        row = self._metadata_review_rows.get(pending_item)
        if not row:
            return
        _, cover_label, embedded_label, _ = row
        if _alive(embedded_label):
            written = [f"Artista gravado: {embedded.artist or 'nenhum'}"]
            if embedded.album:
                written.append(f"álbum: {embedded.album}")
            embedded_label.setText(" · ".join(written))
        if not _alive(cover_label):
            return
        if not (embedded.cover and _show_cover(cover_label, embedded.cover)):
            cover_label.setText("Sem capa\nno arquivo")

    def show_embedded_unavailable(self, pending_item: MetadataPendingItem) -> None:
        """With no file to read, importing would only produce an error — the action is removed."""
        row = self._metadata_review_rows.get(pending_item)
        if not row:
            return
        _, cover_label, embedded_label, search_btn = row
        if _alive(cover_label):
            cover_label.setText("Arquivo\nnão lido")
        if _alive(embedded_label):
            embedded_label.setText("Arquivo não localizado — verifique se a conversão terminou.")
            embedded_label.setStyleSheet(f"color: {CLR_ERROR};")
        if _alive(search_btn):
            search_btn.setEnabled(False)

    def resolve(self, pending_item: MetadataPendingItem) -> None:
        """A confirmed item leaves the screen; with no pending items left, the review closes."""
        row = self._metadata_review_rows.pop(pending_item, None)
        if row and _alive(row[0]):
            row[0].hide()
            row[0].deleteLater()
        if self._metadata_review_rows:
            return
        window, self._metadata_review_window = self._metadata_review_window, None
        if _alive(window):
            window.close()

    def _toggle_bulk(self) -> None:
        """One click applies the suggestion to all; a second one stops between items."""
        if self._bulk_thread and self._bulk_thread.is_alive():
            self._manager.stop_bulk()
            self._set_bulk_text("Parando...", "Termina o item atual e para.", busy=True)
            return
        items = list(self._metadata_review_rows)
        if not items:
            return
        self._set_bulk_text("Parar", f"Conferindo 1/{len(items)} no catálogo...", busy=False)
        self._bulk_thread = self._spawn(self._manager.apply_suggested, items)

    def bulk_progress(self, done: int, total: int) -> None:
        if _alive(self._bulk_label):
            self._bulk_label.setText(f"Conferindo {done + 1}/{total} no catálogo...")

    def bulk_finished(self, applied: int, kept: int, failed: int = 0) -> None:
        parts = [f"{applied} aplicado(s)"]
        if failed:
            parts.append(f"{failed} sem resposta do catálogo — clique de novo em alguns minutos")
        if kept - failed:
            parts.append(f"{kept - failed} sem sugestão no catálogo")
        self._set_bulk_text("Aplicar sugestão em todos", "; ".join(parts) + ".", busy=False)

    def _crop_all(self) -> None:
        items = [item for item in self._metadata_review_rows if _has_provisional_cover(item)]
        if not items:
            if _alive(self._bulk_label):
                self._bulk_label.setText("Nenhum item com miniatura do vídeo para recortar.")
            return
        if _alive(self._crop_btn):
            self._crop_btn.setEnabled(False)
            self._crop_btn.setText("Recortando...")
        self._spawn(self._manager.crop_covers, items)

    def crop_finished(self, cropped: int, failed: int) -> None:
        if _alive(self._crop_btn):
            self._crop_btn.setEnabled(True)
            self._crop_btn.setText("Recortar miniaturas")
        text = f"{cropped} capa(s) recortada(s)"
        if failed:
            text += f"; {failed} não deu para recortar"
        if _alive(self._bulk_label):
            self._bulk_label.setText(text + ".")

    def _set_bulk_text(self, button: str, text: str, busy: bool) -> None:
        if _alive(self._bulk_btn):
            self._bulk_btn.setText(button)
            self._bulk_btn.setEnabled(not busy)
        if _alive(self._bulk_label):
            self._bulk_label.setText(text)

    def start_search(self, pending_item: MetadataPendingItem) -> None:
        """Queues instead of refusing.

        In a playlist the user clicks several items in a row. Answering
        "wait for the current search to finish" turned that into click, wait,
        click again — the queue does the work that used to be the user's. Still one
        at a time: the catalog has a request limit.
        """
        if pending_item == self._searching or pending_item in self._search_queue:
            return
        self._search_queue.append(pending_item)
        self._mark_search_button(pending_item, "Na fila...", busy=True)
        self._run_next_search()

    def _run_next_search(self) -> None:
        if self._searching is not None or not self._search_queue:
            return
        pending_item = self._search_queue.popleft()
        self._searching = pending_item
        self._mark_search_button(pending_item, "Buscando...", busy=True)
        self._spawn(self._manager.search_metadata, pending_item)

    def search_finished(self, pending_item: MetadataPendingItem) -> None:
        """Called on both result and error — the queue must not get stuck."""
        if self._searching == pending_item:
            self._searching = None
        self._mark_search_button(pending_item, "Buscar metadata", busy=False)
        self._run_next_search()

    def _mark_search_button(self, pending_item: MetadataPendingItem, text: str, busy: bool) -> None:
        row = self._metadata_review_rows.get(pending_item)
        if not row:
            return
        button = row[3]
        if _alive(button):
            button.setText(text)
            button.setEnabled(not busy)

    def _spawn(self, target, *args) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def show_candidates(
        self, pending_item: MetadataPendingItem, candidates: list[MusicMetadataCandidate],
    ) -> None:
        self.search_finished(pending_item)
        window, layout = _dialog(self._parent, "Resultados de metadata", 720, 520)
        self._metadata_cover_labels = {}
        layout.addWidget(label(f"Resultados para: {pending_item.title}", "h2", wrap=True))

        if not candidates:
            layout.addWidget(label(
                "Nenhum resultado encontrado. O catálogo do iTunes é de loja: música que não "
                "é vendida (gravação rara, bootleg) não aparece.", "muted", wrap=True))
            layout.addStretch(1)
            window.show()
            return

        rows = _scroll_list(layout)
        for candidate in candidates:
            row = QFrame()
            row.setObjectName("listRow")
            grid = QGridLayout(row)
            grid.setContentsMargins(10, 10, 12, 10)
            grid.setHorizontalSpacing(14)
            grid.setColumnStretch(1, 1)
            cover_label = _cover_box("Carregando\ncapa...")
            grid.addWidget(cover_label, 0, 0, 2, 1)
            self._metadata_cover_labels[candidate.track_id] = cover_label
            # iTunes returns long track names ("... Live At Queen Elizabeth
            # Theatre, Toronto, ON April 20, 2011"): wrap instead of clipping.
            grid.addWidget(label(f"{candidate.artist} — {candidate.title}", "optionTitle",
                                 wrap=True), 0, 1)
            details = " · ".join(part for part in [candidate.album, candidate.year] if part)
            grid.addWidget(label(details or "Álbum não informado", "small", wrap=True), 1, 1)
            import_btn = QPushButton("Importar")
            import_btn.setObjectName("primary")
            import_btn.clicked.connect(
                lambda _=False, item=pending_item, result=candidate, dialog=window:
                    self._start_import(item, result, dialog))
            grid.addWidget(import_btn, 0, 2, 2, 1)
            rows.insertWidget(rows.count() - 1, row)
            self._spawn(self._manager.load_metadata_cover_preview, candidate)
        window.show()

    def show_candidate_cover(
        self, candidate: MusicMetadataCandidate, data: bytes, _mime: str,
    ) -> None:
        box = self._metadata_cover_labels.get(candidate.track_id)
        if not _alive(box):
            return
        if not _show_cover(box, data):
            self.show_candidate_cover_unavailable(candidate)

    def show_candidate_cover_unavailable(self, candidate: MusicMetadataCandidate) -> None:
        box = self._metadata_cover_labels.get(candidate.track_id)
        if _alive(box):
            box.setText("Capa\nindisponível")

    def _start_import(
        self,
        pending_item: MetadataPendingItem,
        candidate: MusicMetadataCandidate,
        window: QDialog,
    ) -> None:
        if self._import_thread and self._import_thread.is_alive():
            QMessageBox.information(
                self._parent, "Importação de metadata", "Aguarde a operação atual terminar.")
            return
        window.close()
        self._import_thread = self._spawn(self._manager.apply_metadata, pending_item, candidate)
