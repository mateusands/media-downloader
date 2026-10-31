"""Choosing playlist items before downloading.

`PlaylistChoice` is the state — what is checked — and knows nothing about widgets,
so it can be tested without opening a window. `PlaylistSelection` is the dialog
that draws it. Like the review, it lives outside `window.py`: the main window only
gets back the chosen indices or the notice that the user backed out.
"""

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .clip import format_time
from .models import PlaylistEntry
from .theme import CLR_MUTED
from .widgets import label


class PlaylistChoice:
    def __init__(self, entries: list[PlaylistEntry]):
        self._entries = list(entries)
        self._chosen: set[int] = set()
        self.select_new()

    def select_new(self) -> None:
        self._chosen = {e.index for e in self._entries if not e.already_downloaded}

    def select_all(self) -> None:
        self._chosen = {e.index for e in self._entries}

    def select_none(self) -> None:
        self._chosen = set()

    def toggle(self, index: int) -> None:
        self._chosen ^= {index}

    def is_selected(self, index: int) -> bool:
        return index in self._chosen

    def selected(self) -> list[int]:
        return [e.index for e in self._entries if e.index in self._chosen]

    def can_confirm(self) -> bool:
        return bool(self._chosen)

    def confirm_label(self) -> str:
        if self._chosen:
            return f"Baixar {len(self._chosen)} de {len(self._entries)}"
        if all(e.already_downloaded for e in self._entries):
            return "Nada novo para baixar"
        return "Escolha ao menos um item"


class PlaylistSelection:
    """The selection dialog. Calls exactly one of the two callbacks when closing."""

    def __init__(
        self, parent: QWidget,
        on_confirm: Callable[[list[int]], None], on_cancel: Callable[[], None],
    ):
        self._parent = parent
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._dialog: QDialog | None = None

    def open(self, title: str, entries: list[PlaylistEntry]) -> None:
        choice = PlaylistChoice(entries)
        dialog = QDialog(self._parent)
        dialog.setWindowTitle("Escolher itens da playlist")
        dialog.resize(720, 600)
        dialog.setMinimumSize(560, 420)
        # Closing with the window's X means backing out, like "Cancelar".
        dialog.rejected.connect(self._rejected)
        self._dialog = dialog

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(10)
        already = sum(1 for e in entries if e.already_downloaded)
        layout.addWidget(label(title, "h1", wrap=True))
        layout.addWidget(label(
            f"{len(entries)} item(s) na playlist"
            + (f", {already} já baixado(s) antes nesta pasta." if already else ".")
            + " O que já foi baixado vem desmarcado; marque para baixar de novo.",
            "muted", wrap=True))

        actions = QHBoxLayout()
        actions.setSpacing(8)
        layout.addLayout(actions)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 4, 6, 4)
        rows.setSpacing(6)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(dialog.reject)
        confirm_btn = QPushButton(choice.confirm_label())
        confirm_btn.setObjectName("primary")
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(lambda: self._confirm(choice))
        footer.addWidget(cancel_btn)
        footer.addWidget(confirm_btn)
        layout.addLayout(footer)

        boxes: dict[int, QCheckBox] = {}

        def refresh() -> None:
            for index, box in boxes.items():
                box.blockSignals(True)
                box.setChecked(choice.is_selected(index))
                box.blockSignals(False)
            confirm_btn.setText(choice.confirm_label())
            confirm_btn.setEnabled(choice.can_confirm())

        for text, action in (
            ("Só os novos", choice.select_new),
            ("Marcar todos", choice.select_all),
            ("Desmarcar todos", choice.select_none),
        ):
            button = QPushButton(text)
            button.setObjectName("chip")
            button.clicked.connect(lambda _=False, action=action: (action(), refresh()))
            actions.addWidget(button)
        actions.addStretch(1)

        for entry in entries:
            row = QFrame()
            row.setObjectName("listRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(12, 8, 14, 8)
            box = QCheckBox(f"{entry.index:03d}   {entry.title}")
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            if entry.already_downloaded:
                box.setStyleSheet(f"color: {CLR_MUTED};")
            box.toggled.connect(lambda _c, index=entry.index: (choice.toggle(index), refresh()))
            boxes[entry.index] = box
            line.addWidget(box, 1)
            details = " · ".join(part for part in (
                format_time(entry.duration) if entry.duration else "",
                "já baixado" if entry.already_downloaded else "",
            ) if part)
            if details:
                line.addWidget(label(details, "small"))
            rows.addWidget(row)
        rows.addStretch(1)

        refresh()
        dialog.show()

    def _close(self) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.rejected.disconnect(self._rejected)
            dialog.close()
            dialog.deleteLater()

    def _rejected(self) -> None:
        self._close()
        self._on_cancel()

    def _confirm(self, choice: PlaylistChoice) -> None:
        if not choice.can_confirm():
            return
        indices = choice.selected()
        self._close()
        self._on_confirm(indices)

    def is_open(self) -> bool:
        return self._dialog is not None

    def cancel(self) -> None:
        self._close()
        self._on_cancel()
