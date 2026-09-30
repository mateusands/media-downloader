"""Escolha dos itens da playlist antes de baixar.

`PlaylistChoice` e o estado — o que esta marcado — e nao conhece widget, para
ser testado sem abrir janela. `PlaylistSelection` e o dialogo que o desenha.
Como a revisao, vive fora de `window.py`: a janela principal so recebe de volta
os indices escolhidos ou o aviso de que a pessoa desistiu.
"""

from typing import Callable

import customtkinter as ctk

from .models import PlaylistEntry
from .theme import (
    BG_CARD,
    BG_DARK,
    BG_HOVER,
    BG_INPUT,
    CLR_ACCENT,
    CLR_ACCENT_DARK,
    CLR_BORDER,
    CLR_MUTED,
    CLR_TEXT,
    FONT_FAMILY,
)
from .widgets import HoverButton


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


def _duration_text(seconds: float | None) -> str:
    if not seconds:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


class PlaylistSelection:
    """Dialogo da escolha. Chama exatamente um dos dois retornos ao fechar."""

    def __init__(
        self, root: ctk.CTk,
        on_confirm: Callable[[list[int]], None], on_cancel: Callable[[], None],
    ):
        self._root = root
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._window: ctk.CTkToplevel | None = None

    def open(self, title: str, entries: list[PlaylistEntry]) -> None:
        choice = PlaylistChoice(entries)
        window = ctk.CTkToplevel(self._root)
        window.title("Escolher itens da playlist")
        window.geometry("720x560")
        window.minsize(560, 400)
        window.configure(fg_color=BG_DARK)
        window.transient(self._root)
        window.protocol("WM_DELETE_WINDOW", self.cancel)
        self._window = window

        already = sum(1 for e in entries if e.already_downloaded)
        ctk.CTkLabel(
            window, text=title, font=(FONT_FAMILY, 18, "bold"), text_color=CLR_TEXT,
            anchor="w", wraplength=660, justify="left",
        ).pack(anchor="w", fill="x", padx=24, pady=(22, 3))
        ctk.CTkLabel(
            window,
            text=(f"{len(entries)} item(s) na playlist"
                  + (f", {already} ja baixado(s) antes nesta pasta." if already else ".")
                  + " O que ja foi baixado vem desmarcado; marque para baixar de novo."),
            font=(FONT_FAMILY, 11), text_color=CLR_MUTED, wraplength=660, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        actions = ctk.CTkFrame(window, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(0, 8))

        items_frame = ctk.CTkScrollableFrame(window, fg_color=BG_CARD, corner_radius=12)
        items_frame.pack(fill="both", expand=True, padx=24, pady=(0, 12))

        footer = ctk.CTkFrame(window, fg_color="transparent")
        footer.pack(fill="x", padx=24, pady=(0, 20))
        confirm_btn = HoverButton(
            footer, text=choice.confirm_label(), width=200, height=40,
            font=(FONT_FAMILY, 12, "bold"),
            base_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK, press_color="#4036aa",
            text_color="#ffffff",
            command=lambda: self._confirm(choice),
        )
        confirm_btn.pack(side="right")
        HoverButton(
            footer, text="Cancelar", width=120, height=40, font=(FONT_FAMILY, 12),
            base_color="transparent", hover_color=BG_HOVER, press_color=CLR_BORDER,
            text_color=CLR_TEXT, border_width=2, border_color=CLR_BORDER,
            command=self.cancel,
        ).pack(side="right", padx=(0, 10))

        variables: dict[int, ctk.BooleanVar] = {}

        def refresh() -> None:
            for index, var in variables.items():
                var.set(choice.is_selected(index))
            confirm_btn.configure(
                text=choice.confirm_label(),
                state="normal" if choice.can_confirm() else "disabled")

        for label, action in (
            ("So os novos", choice.select_new),
            ("Marcar todos", choice.select_all),
            ("Desmarcar todos", choice.select_none),
        ):
            HoverButton(
                actions, text=label, width=130, height=30, font=(FONT_FAMILY, 11),
                base_color=BG_INPUT, hover_color=BG_HOVER, press_color=CLR_BORDER,
                text_color=CLR_TEXT,
                command=lambda action=action: (action(), refresh()),
            ).pack(side="left", padx=(0, 8))

        for entry in entries:
            var = ctk.BooleanVar(value=choice.is_selected(entry.index))
            variables[entry.index] = var
            details = " · ".join(
                part for part in (
                    _duration_text(entry.duration),
                    "ja baixado" if entry.already_downloaded else "",
                ) if part)
            ctk.CTkCheckBox(
                items_frame,
                text=f"{entry.index:03d}   {entry.title}" + (f"   ({details})" if details else ""),
                variable=var, onvalue=True, offvalue=False,
                font=(FONT_FAMILY, 11),
                text_color=CLR_MUTED if entry.already_downloaded else CLR_TEXT,
                fg_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK,
                border_color=CLR_BORDER, checkmark_color=CLR_TEXT,
                command=lambda index=entry.index: (choice.toggle(index), refresh()),
            ).pack(anchor="w", fill="x", padx=8, pady=3)

        refresh()

    def _close(self) -> None:
        if self._window is not None and self._window.winfo_exists():
            self._window.destroy()
        self._window = None

    def _confirm(self, choice: PlaylistChoice) -> None:
        if not choice.can_confirm():
            return
        indices = choice.selected()
        self._close()
        self._on_confirm(indices)

    def is_open(self) -> bool:
        return self._window is not None

    def cancel(self) -> None:
        self._close()
        self._on_cancel()
