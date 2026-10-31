"""Widgets reutilizaveis da interface (Qt)."""

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .clip import format_time
from .icons import pixmap
from .theme import (
    BG_HOVER,
    BG_INPUT,
    CLR_ACCENT,
    CLR_ACCENT_TILE,
    CLR_DISABLED,
    CLR_FOCUS_HALO,
    CLR_SWITCH_OFF,
    MOTION_FAST_MS,
    CLR_ACCENT_END,
    CLR_ACCENT_LIGHT,
    CLR_BORDER,
    CLR_MUTED,
    CLR_TEXT,
)


def repolish(widget: QWidget) -> None:
    """Propriedade dinamica so muda o estilo depois de repolir."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_prop(widget: QWidget, name: str, value) -> None:
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


def tabular(widget: QWidget) -> QWidget:
    """Algarismos de largura fixa: porcentagem e tempo nao pulam ao mudar."""
    font = widget.font()
    font.setFeature(QFont.Tag("tnum"), 1)
    widget.setFont(font)
    return widget


def keyboard_focus(widget: QWidget) -> QWidget:
    """Foco por Tab e nao por clique: o anel aparece para quem navega pelo teclado."""
    widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    return widget


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def icon_label(name: str, color: str, size: int = 18) -> QLabel:
    widget = QLabel()
    widget.setPixmap(pixmap(name, color, size))
    widget.setFixedSize(size + 2, size + 2)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return widget


def rounded_pixmap(data: bytes, width: int, height: int, radius: float = 12) -> QPixmap | None:
    """Recorta ao centro para preencher o quadro, com cantos arredondados."""
    image = QImage.fromData(data)
    if image.isNull():
        return None
    ratio = 2.0
    target = QSize(int(width * ratio), int(height * ratio))
    scaled = image.scaled(target, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
    x = (scaled.width() - target.width()) // 2
    y = (scaled.height() - target.height()) // 2
    scaled = scaled.copy(x, y, target.width(), target.height())
    result = QPixmap(target)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, target.width(), target.height()), radius * ratio, radius * ratio)
    painter.setClipPath(path)
    painter.drawImage(0, 0, scaled)
    painter.end()
    result.setDevicePixelRatio(ratio)
    return result


class FormatCard(QFrame):
    """Cartao selecionavel do formato (MP3/MP4)."""

    clicked = Signal()

    def __init__(self, icon_name: str, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setObjectName("formatCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._icon_name = icon_name
        self._selected = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        self._icon = QLabel()
        self._icon.setFixedSize(42, 42)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._icon)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(title, "formatTitle"))
        texts.addWidget(label(subtitle, "small"))
        layout.addLayout(texts, 1)
        self._check = QLabel()
        self._check.setFixedSize(22, 22)
        layout.addWidget(self._check)
        self._refresh()

    def setSelected(self, selected: bool) -> None:
        self._selected = selected
        set_prop(self, "selected", selected)
        self._refresh()

    def isSelected(self) -> bool:
        return self._selected

    def _refresh(self) -> None:
        color = CLR_ACCENT_LIGHT if self._selected else CLR_MUTED
        self._icon.setPixmap(pixmap(self._icon_name, color, 26))
        self._icon.setStyleSheet(
            f"background: {CLR_ACCENT_TILE if self._selected else BG_HOVER}; border-radius: 12px;")
        self._check.setPixmap(pixmap("check", CLR_ACCENT_LIGHT, 20) if self._selected else QPixmap())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class ToggleSwitch(QAbstractButton):
    """Chave liga/desliga com o botao deslizando."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(48, 30)
        self._offset = 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(MOTION_FAST_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802 — API do Qt
        super().setChecked(checked)
        self._animation.stop()
        self._offset = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(48, 30)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 3 px de folga em volta para o halo de foco caber no proprio widget.
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        if self.hasFocus():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(CLR_FOCUS_HALO))
            halo = rect.adjusted(-3, -3, 3, 3)
            painter.drawRoundedRect(halo, halo.height() / 2, halo.height() / 2)
        if not self.isEnabled():
            track = QColor(BG_INPUT)
        elif self._offset > 0.5:
            track = QColor(CLR_ACCENT)
        else:
            track = QColor(CLR_SWITCH_OFF)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob = rect.height() - 6
        x = rect.left() + 3 + self._offset * (rect.width() - knob - 6)
        painter.setBrush(QColor(CLR_TEXT if self.isEnabled() else CLR_DISABLED))
        painter.drawEllipse(QRectF(x, rect.top() + 3, knob, knob))


class OptionRow(QFrame):
    """Linha de opcao: icone, titulo, explicacao e a chave. A linha toda clica."""

    def __init__(self, icon_name: str, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setObjectName("optionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)
        self._icon = icon_label(icon_name, CLR_MUTED, 20)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self._title = label(title, "optionTitle")
        self._subtitle = label(subtitle, "small", wrap=True)
        texts.addWidget(self._title)
        texts.addWidget(self._subtitle)
        layout.addLayout(texts, 1)
        self.switch = ToggleSwitch()
        layout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switch.toggled.connect(self._refresh_icon)

    def _refresh_icon(self, checked: bool) -> None:
        color = CLR_ACCENT_LIGHT if checked and self.isEnabled() else CLR_MUTED
        self._icon.setPixmap(pixmap(self._icon_name, color, 20))

    def setAvailable(self, available: bool, reason: str = "") -> None:
        """Indisponivel desliga e explica — chave apagada sem motivo parece defeito."""
        self.setEnabled(available)
        if not available:
            self.switch.setChecked(False)
        self.setToolTip("" if available else reason)
        self._title.setStyleSheet("" if available else f"color: {CLR_MUTED};")
        self._refresh_icon(self.switch.isChecked())

    def isChecked(self) -> bool:
        return self.switch.isChecked() and self.isEnabled()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.switch.toggle()
        super().mouseReleaseEvent(event)


class RangeSlider(QWidget):
    """Regua do trecho: duas alcas sobre a duracao do video.

    As alcas nao se cruzam e ficam a pelo menos `MIN_GAP` segundos: um trecho
    de zero segundos nao e trecho. Teclado: setas movem 1 s, Shift+setas 10 s;
    Tab alterna a alca.
    """

    rangeChanged = Signal(float, float)
    MIN_GAP = 1.0
    _MARGIN = 12
    _HANDLE = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._maximum = 0.0
        self._start = 0.0
        self._end = 0.0
        self._active = 0     # 0 = inicio, 1 = fim
        self._dragging = False

    # ── Valores ─────────────────────────────────────
    def maximum(self) -> float:
        return self._maximum

    def setMaximum(self, maximum: float) -> None:
        self._maximum = max(0.0, float(maximum))
        self.setValues(0.0, self._maximum, emit=False)

    def values(self) -> tuple[float, float]:
        return self._start, self._end

    def setValues(self, start: float, end: float, emit: bool = True) -> None:
        top = self._maximum
        gap = min(self.MIN_GAP, top)
        start = min(max(0.0, start), max(0.0, top - gap))
        end = max(min(top, end), start + gap)
        changed = (start, end) != (self._start, self._end)
        self._start, self._end = start, end
        self.update()
        if changed and emit:
            self.rangeChanged.emit(start, end)

    def _move_handle(self, which: int, value: float) -> None:
        if which == 0:
            self.setValues(min(value, self._end - self.MIN_GAP), self._end)
        else:
            self.setValues(self._start, max(value, self._start + self.MIN_GAP))

    # ── Geometria ───────────────────────────────────
    def _track(self) -> QRectF:
        return QRectF(self._MARGIN, 18, max(1, self.width() - 2 * self._MARGIN), 6)

    def x_for(self, value: float) -> float:
        track = self._track()
        if not self._maximum:
            return track.left()
        return track.left() + track.width() * value / self._maximum

    def value_at(self, x: float) -> float:
        track = self._track()
        fraction = min(max((x - track.left()) / track.width(), 0.0), 1.0)
        return round(fraction * self._maximum, 1)

    # ── Mouse e teclado ─────────────────────────────
    def mousePressEvent(self, event) -> None:
        if not self._maximum or event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        # A alca mais perto pega o clique; empate (alcas juntas) vai para o lado do clique.
        to_start = abs(x - self.x_for(self._start))
        to_end = abs(x - self.x_for(self._end))
        if to_start == to_end:
            self._active = 0 if x < self.x_for(self._start) else 1
        else:
            self._active = 0 if to_start < to_end else 1
        self._dragging = True
        self._move_handle(self._active, self.value_at(x))

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._move_handle(self._active, self.value_at(event.position().x()))

    def mouseReleaseEvent(self, _event) -> None:
        self._dragging = False

    def keyPressEvent(self, event) -> None:
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        current = self._start if self._active == 0 else self._end
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._move_handle(self._active, current - step)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._move_handle(self._active, current + step)
        elif event.key() == Qt.Key.Key_Home:
            self._move_handle(self._active, 0.0)
        elif event.key() == Qt.Key.Key_End:
            self._move_handle(self._active, self._maximum)
        else:
            super().keyPressEvent(event)
            return
        self.update()

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: N802 — API do Qt
        # Tab passa do inicio para o fim antes de sair da regua.
        if forward and self._active == 0:
            self._active = 1
            self.update()
            return True
        if not forward and self._active == 1:
            self._active = 0
            self.update()
            return True
        return super().focusNextPrevChild(forward)

    # ── Desenho ─────────────────────────────────────
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BG_HOVER))
        painter.drawRoundedRect(track, 3, 3)

        x1, x2 = self.x_for(self._start), self.x_for(self._end)
        fill = QRectF(x1, track.top(), max(0.0, x2 - x1), track.height())
        gradient = QLinearGradient(fill.topLeft(), fill.topRight())
        gradient.setColorAt(0, QColor(CLR_ACCENT))
        gradient.setColorAt(1, QColor(CLR_ACCENT_END))
        painter.setBrush(gradient if self.isEnabled() else QColor(CLR_BORDER))
        painter.drawRoundedRect(fill, 3, 3)

        # Marcas de tempo: quartos da duracao, para a regua ter escala.
        painter.setPen(QColor(CLR_MUTED))
        font = painter.font()
        font.setPixelSize(10)
        font.setFeature(QFont.Tag("tnum"), 1)
        painter.setFont(font)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = track.left() + track.width() * fraction
            painter.drawLine(QPointF(x, track.bottom() + 6), QPointF(x, track.bottom() + 9))
            text = format_time(round(self._maximum * fraction)) if self._maximum else ""
            width = painter.fontMetrics().horizontalAdvance(text)
            left = min(max(x - width / 2, 0), self.width() - width)
            painter.drawText(QPointF(left, track.bottom() + 24), text)

        for which, x in ((0, x1), (1, x2)):
            center = QPointF(x, track.center().y())
            radius = self._HANDLE / 2
            if self.hasFocus() and which == self._active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(CLR_FOCUS_HALO))
                painter.drawEllipse(center, radius + 5, radius + 5)
            painter.setPen(QPen(QColor(CLR_ACCENT if self.isEnabled() else CLR_BORDER), 3))
            painter.setBrush(QColor(CLR_TEXT))
            painter.drawEllipse(center, radius - 1, radius - 1)


class PreviewCard(QFrame):
    """O que o link e: miniatura, titulo, canal e duracao — ou que esta lendo, ou que falhou."""

    THUMB_W, THUMB_H = 176, 99

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 16, 12)
        layout.setSpacing(16)
        self._thumb = QLabel()
        self._thumb.setFixedSize(self.THUMB_W, self.THUMB_H)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._thumb)

        self._skeleton = QWidget()
        bars = QVBoxLayout(self._skeleton)
        bars.setContentsMargins(0, 0, 0, 0)
        bars.setSpacing(10)
        bars.addStretch(1)
        for width, height in ((360, 16), (180, 12), (110, 18)):
            bar = QFrame()
            bar.setObjectName("skeleton")
            bar.setFixedSize(width, height)
            bars.addWidget(bar)
        bars.addStretch(1)
        layout.addWidget(self._skeleton, 1)

        texts = QVBoxLayout()
        texts.setSpacing(6)
        self._title = label("", "previewTitle", wrap=True)
        self._meta = label("", "muted", wrap=True)
        self._badges = QHBoxLayout()
        self._badges.setSpacing(6)
        self._badges.addStretch(1)
        texts.addStretch(1)
        texts.addWidget(self._title)
        texts.addWidget(self._meta)
        texts.addLayout(self._badges)
        texts.addStretch(1)
        self._texts = QWidget()
        self._texts.setLayout(texts)
        layout.addWidget(self._texts, 1)

    def _set_loading(self, loading: bool) -> None:
        self._skeleton.setVisible(loading)
        self._texts.setVisible(not loading)

    def _placeholder(self, icon_name: str) -> None:
        self._thumb.setPixmap(pixmap(icon_name, CLR_MUTED, 30))
        self._thumb.setStyleSheet(f"background: {BG_HOVER}; border-radius: 12px;")

    def _set_badges(self, badges: list[tuple[str, bool]]) -> None:
        while self._badges.count() > 1:
            item = self._badges.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for position, (text, accent) in enumerate(badges):
            self._badges.insertWidget(position, label(text, "badge" if accent else "badgeMuted"))

    def show_loading(self) -> None:
        """Esqueleto no formato do que vem — miniatura e tres linhas, sem texto."""
        self._thumb.setPixmap(QPixmap())
        self._thumb.setStyleSheet(f"background: {BG_HOVER}; border-radius: 12px;")
        self._thumb.setAccessibleName("Lendo o link")
        self._set_loading(True)
        self.show()

    def show_failed(self, message: str) -> None:
        self._set_loading(False)
        self._placeholder("link")
        self._title.setText("Sem prévia para este link")
        short = message.splitlines()[0] if message else ""
        self._meta.setText(
            "Ainda dá para tentar baixar — quem decide é o yt-dlp."
            + (f"\n{short[:160]}" if short else ""))
        self._set_badges([])
        self.show()

    def show_preview(self, preview) -> None:
        self._set_loading(False)
        image = rounded_pixmap(preview.thumbnail, self.THUMB_W, self.THUMB_H) \
            if preview.thumbnail else None
        if image is None:
            self._placeholder("video")
        else:
            self._thumb.setStyleSheet("")
            self._thumb.setPixmap(image)
        self._title.setText(preview.title)
        meta = [part for part in (
            preview.uploader,
            format_time(preview.duration) if preview.duration else None,
        ) if part]
        self._meta.setText(" · ".join(meta))
        badges: list[tuple[str, bool]] = []
        if preview.is_playlist:
            badges.append((f"Playlist · {preview.item_count} itens", True))
        else:
            badges.append(("Vídeo", True))
            if preview.heights:
                badges.append((f"até {preview.heights[0]}p", False))
            if preview.start_hint:
                badges.append((f"link em {format_time(preview.start_hint)}", False))
        self._set_badges(badges)
        self.show()
