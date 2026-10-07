"""Revisao de metadata: os dois dialogos e a previa de capa.

Vive fora de `window.py` porque e um fluxo proprio, com estado proprio (as
linhas em cena, as imagens que precisam de referencia viva) e ciclo de vida
proprio — as janelas abrem e fecham sem a principal saber. `window.py` so
encaminha os eventos da fila para ca.
"""

import threading
from collections import deque
from io import BytesIO
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageOps

from .config import COVER_PREVIEW_SIZE
from .downloader import DownloadManager
from .metadata import metadata_review_detail
from .models import EmbeddedMetadata, MetadataPendingItem, MusicMetadataCandidate
from .theme import (
    BG_CARD,
    CLR_BORDER,
    BG_DARK,
    BG_HOVER,
    BG_INPUT,
    CLR_ACCENT,
    CLR_ACCENT_DARK,
    CLR_ERROR,
    CLR_MUTED,
    CLR_TEXT,
    FONT_FAMILY,
)
from .widgets import HoverButton


def _has_provisional_cover(item: MetadataPendingItem) -> bool:
    return any(reason.startswith("capa provisoria") for reason in item.review_reasons)


class MetadataReview:
    """Dona das janelas de revisao. Nao conhece a janela principal."""

    def __init__(self, root: ctk.CTk, manager: DownloadManager):
        self._root = root
        self._manager = manager
        self._import_thread: threading.Thread | None = None
        self._search_queue: deque[MetadataPendingItem] = deque()
        self._searching: MetadataPendingItem | None = None
        self._metadata_cover_labels: dict[str, ctk.CTkLabel] = {}
        self._metadata_cover_images: dict[str, ctk.CTkImage] = {}
        self._metadata_review_rows: dict[
            MetadataPendingItem, tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel, HoverButton],
        ] = {}
        self._metadata_embedded_images: dict[MetadataPendingItem, ctk.CTkImage] = {}
        self._metadata_review_window: ctk.CTkToplevel | None = None
        self._bulk_thread: threading.Thread | None = None
        self._bulk_btn: HoverButton | None = None
        self._bulk_label: ctk.CTkLabel | None = None
        self._crop_btn: HoverButton | None = None

    def open(self, pending_items: list[MetadataPendingItem]) -> None:
        window = ctk.CTkToplevel(self._root)
        window.title("MP3 com metadata a confirmar")
        window.geometry("760x480")
        # A largura minima acompanha o wraplength da linha de motivos: coluna
        # mais estreita que o texto corta a frase em vez de quebrar a linha.
        window.minsize(720, 380)
        window.configure(fg_color=BG_DARK)
        window.transient(self._root)
        self._metadata_review_window = window
        self._metadata_review_rows = {}
        self._metadata_embedded_images = {}

        ctk.CTkLabel(
            window, text="Confirmar metadata do MP3 concluido",
            font=(FONT_FAMILY, 18, "bold"), text_color=CLR_TEXT,
        ).pack(anchor="w", padx=24, pady=(22, 3))
        ctk.CTkLabel(
            window,
            text=("A previa mostra o que ja esta gravado no arquivo. Busque no catalogo "
                  "para substituir; a selecao do catalogo, e nao o titulo sugerido, e o "
                  "que sera importado."),
            font=(FONT_FAMILY, 11), text_color=CLR_MUTED, wraplength=650, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 10))

        bulk_row = ctk.CTkFrame(window, fg_color="transparent")
        bulk_row.pack(fill="x", padx=24, pady=(0, 12))
        self._bulk_btn = HoverButton(
            bulk_row, text="Aplicar sugestao em todos", width=210, height=34,
            font=(FONT_FAMILY, 11, "bold"),
            base_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK, text_color=CLR_TEXT,
            command=self._toggle_bulk,
        )
        self._bulk_btn.pack(side="left")
        self._crop_btn = HoverButton(
            bulk_row, text="Recortar miniaturas", width=160, height=34,
            font=(FONT_FAMILY, 11),
            base_color="transparent", hover_color=BG_HOVER, press_color=CLR_BORDER,
            text_color=CLR_TEXT, border_width=2, border_color=CLR_BORDER,
            command=self._crop_all,
        )
        self._crop_btn.pack(side="left", padx=(8, 0))
        self._bulk_label = ctk.CTkLabel(
            bulk_row,
            text=("Aplicar: resultado do catalogo com o mesmo artista e titulo. "
                  "Recortar: usa o centro da miniatura do video como capa quadrada."),
            font=(FONT_FAMILY, 10), text_color=CLR_MUTED, wraplength=290, justify="left",
        )
        self._bulk_label.pack(side="left", padx=(12, 0))

        items_frame = ctk.CTkScrollableFrame(window, fg_color=BG_CARD, corner_radius=12)
        items_frame.pack(fill="both", expand=True, padx=24, pady=(0, 22))
        for pending_item in pending_items:
            row = ctk.CTkFrame(items_frame, fg_color=BG_INPUT, corner_radius=10)
            row.pack(fill="x", pady=5, padx=4)
            row.grid_columnconfigure(1, weight=1)

            cover_label = ctk.CTkLabel(
                row, text="Lendo\ncapa...", width=COVER_PREVIEW_SIZE, height=COVER_PREVIEW_SIZE,
                font=(FONT_FAMILY, 9), text_color=CLR_MUTED, fg_color=BG_HOVER,
                corner_radius=8, justify="center",
            )
            cover_label.grid(row=0, column=0, rowspan=3, padx=(10, 4), pady=10)
            ctk.CTkLabel(
                row, text=pending_item.title, font=(FONT_FAMILY, 12, "bold"),
                text_color=CLR_TEXT, anchor="w",
            ).grid(row=0, column=1, sticky="ew", padx=12, pady=(10, 1))
            embedded_label = ctk.CTkLabel(
                row, text="Lendo o que ja esta gravado...",
                font=(FONT_FAMILY, 10), text_color=CLR_TEXT, anchor="w",
            )
            embedded_label.grid(row=1, column=1, sticky="ew", padx=12)
            ctk.CTkLabel(
                row, text=metadata_review_detail(pending_item),
                font=(FONT_FAMILY, 10), text_color=CLR_MUTED, anchor="w",
                wraplength=340, justify="left",
            ).grid(row=2, column=1, sticky="ew", padx=12, pady=(1, 10))
            search_btn = HoverButton(
                row, text="Buscar metadata", width=145, height=32,
                font=(FONT_FAMILY, 11, "bold"),
                base_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK,
                text_color=CLR_TEXT,
                command=lambda item=pending_item: self.start_search(item),
            )
            if _has_provisional_cover(pending_item):
                search_btn.grid(row=0, column=2, rowspan=2, padx=10, pady=(10, 2), sticky="s")
                HoverButton(
                    row, text="Recortar capa", width=145, height=28,
                    font=(FONT_FAMILY, 10),
                    base_color="transparent", hover_color=BG_HOVER, press_color=CLR_BORDER,
                    text_color=CLR_TEXT, border_width=1, border_color=CLR_BORDER,
                    command=lambda item=pending_item: self._spawn(
                        self._manager.crop_covers, [item]),
                ).grid(row=2, column=2, padx=10, pady=(2, 10), sticky="n")
            else:
                search_btn.grid(row=0, column=2, rowspan=3, padx=10, pady=10)

            # Chaveado pelo próprio item: dois pendentes podem compartilhar o
            # caminho (o yt-dlp nem sempre informa o nome do arquivo), e aí a
            # prévia de um sobrescrevia a linha do outro.
            self._metadata_review_rows[pending_item] = (
                row, cover_label, embedded_label, search_btn,
            )
            threading.Thread(
                target=self._manager.load_embedded_metadata,
                args=(pending_item,), daemon=True,
            ).start()

    def show_embedded(
        self, pending_item: MetadataPendingItem, embedded: EmbeddedMetadata,
    ) -> None:
        row = self._metadata_review_rows.get(pending_item)
        if not row:
            return
        _, cover_label, embedded_label, _ = row
        if embedded_label.winfo_exists():
            gravado = [f"Artista gravado: {embedded.artist or 'nenhum'}"]
            if embedded.album:
                gravado.append(f"album: {embedded.album}")
            embedded_label.configure(text=" · ".join(gravado))
        if not cover_label.winfo_exists():
            return
        image = self._make_cover_image(embedded.cover) if embedded.cover else None
        if image is None:
            cover_label.configure(text="Sem capa\nno arquivo")
            return
        self._metadata_embedded_images[pending_item] = image
        cover_label.configure(image=image, text="")

    def show_embedded_unavailable(self, pending_item: MetadataPendingItem) -> None:
        """Sem arquivo para ler, importar so produziria erro — a acao sai de cena."""
        row = self._metadata_review_rows.get(pending_item)
        if not row:
            return
        _, cover_label, embedded_label, search_btn = row
        if cover_label.winfo_exists():
            cover_label.configure(text="Arquivo\nnao lido")
        if embedded_label.winfo_exists():
            embedded_label.configure(
                text="Arquivo nao localizado — verifique se a conversao terminou.",
                text_color=CLR_ERROR,
            )
        if search_btn.winfo_exists():
            search_btn.configure(state="disabled")

    def resolve(self, pending_item: MetadataPendingItem) -> None:
        """Item confirmado sai de cena; sem pendencia restante, a revisao fecha."""
        row = self._metadata_review_rows.pop(pending_item, None)
        self._metadata_embedded_images.pop(pending_item, None)
        if row and row[0].winfo_exists():
            row[0].destroy()
        if self._metadata_review_rows:
            return
        window = self._metadata_review_window
        if window is not None and window.winfo_exists():
            window.destroy()
        self._metadata_review_window = None

    def _toggle_bulk(self) -> None:
        """Um clique aplica a sugestao em todos; o segundo para entre um item e outro."""
        if self._bulk_thread and self._bulk_thread.is_alive():
            self._manager.stop_bulk()
            self._set_bulk_text("Parando...", "Termina o item atual e para.", busy=True)
            return
        items = list(self._metadata_review_rows)
        if not items:
            return
        self._set_bulk_text("Parar", f"Conferindo 1/{len(items)} no catalogo...", busy=False)
        self._bulk_thread = self._spawn(self._manager.apply_suggested, items)

    def bulk_progress(self, done: int, total: int) -> None:
        if self._bulk_label is not None and self._bulk_label.winfo_exists():
            self._bulk_label.configure(text=f"Conferindo {done + 1}/{total} no catalogo...")

    def bulk_finished(self, applied: int, kept: int, failed: int = 0) -> None:
        parts = [f"{applied} aplicado(s)"]
        if failed:
            parts.append(f"{failed} sem resposta do catalogo — clique de novo em alguns minutos")
        if kept - failed:
            parts.append(f"{kept - failed} sem sugestao no catalogo")
        self._set_bulk_text("Aplicar sugestao em todos", "; ".join(parts) + ".", busy=False)

    def _crop_all(self) -> None:
        items = [item for item in self._metadata_review_rows if _has_provisional_cover(item)]
        if not items:
            if self._bulk_label is not None and self._bulk_label.winfo_exists():
                self._bulk_label.configure(text="Nenhum item com miniatura do video para recortar.")
            return
        if self._crop_btn is not None and self._crop_btn.winfo_exists():
            self._crop_btn.configure(state="disabled", text="Recortando...")
        self._spawn(self._manager.crop_covers, items)

    def crop_finished(self, cropped: int, failed: int) -> None:
        if self._crop_btn is not None and self._crop_btn.winfo_exists():
            self._crop_btn.configure(state="normal", text="Recortar miniaturas")
        text = f"{cropped} capa(s) recortada(s)"
        if failed:
            text += f"; {failed} nao deu para recortar"
        if self._bulk_label is not None and self._bulk_label.winfo_exists():
            self._bulk_label.configure(text=text + ".")

    def _set_bulk_text(self, button: str, label: str, busy: bool) -> None:
        if self._bulk_btn is not None and self._bulk_btn.winfo_exists():
            self._bulk_btn.configure(text=button, state="disabled" if busy else "normal")
        if self._bulk_label is not None and self._bulk_label.winfo_exists():
            self._bulk_label.configure(text=label)

    @staticmethod
    def _make_cover_image(data: bytes) -> ctk.CTkImage | None:
        try:
            with Image.open(BytesIO(data)) as source:
                preview = ImageOps.fit(
                    source.convert("RGB"),
                    (COVER_PREVIEW_SIZE, COVER_PREVIEW_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
        except Exception:
            return None
        return ctk.CTkImage(
            light_image=preview, dark_image=preview,
            size=(COVER_PREVIEW_SIZE, COVER_PREVIEW_SIZE),
        )

    def start_search(self, pending_item: MetadataPendingItem) -> None:
        """Enfileira em vez de recusar.

        Numa playlist a pessoa clica em varios itens seguidos. Responder
        "aguarde a busca atual terminar" transformava isso em clicar, esperar,
        clicar de novo — a fila faz o trabalho que era da pessoa. Uma por vez
        continua: o catalogo tem limite de requisicoes.
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
        """Chamado tanto no resultado quanto no erro — a fila nao pode travar."""
        if self._searching == pending_item:
            self._searching = None
        self._mark_search_button(pending_item, "Buscar metadata", busy=False)
        self._run_next_search()

    def _mark_search_button(self, pending_item: MetadataPendingItem, text: str, busy: bool) -> None:
        row = self._metadata_review_rows.get(pending_item)
        if not row:
            return
        botao = row[3]
        if botao.winfo_exists():
            botao.configure(text=text, state="disabled" if busy else "normal")

    def _spawn(self, target, *args) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        return thread

    def show_candidates(
        self, pending_item: MetadataPendingItem, candidates: list[MusicMetadataCandidate],
    ) -> None:
        self.search_finished(pending_item)
        window = ctk.CTkToplevel(self._root)
        window.title("Resultados de metadata")
        window.geometry("700x460")
        # A largura minima acompanha o wraplength do titulo do candidato.
        window.minsize(680, 340)
        window.configure(fg_color=BG_DARK)
        window.transient(self._root)
        self._metadata_cover_labels = {}
        self._metadata_cover_images = {}

        ctk.CTkLabel(
            window, text=f"Resultados para: {pending_item.title}",
            font=(FONT_FAMILY, 16, "bold"), text_color=CLR_TEXT, wraplength=620,
            justify="left",
        ).pack(anchor="w", padx=24, pady=(22, 14))

        if not candidates:
            ctk.CTkLabel(
                window,
                text="Nenhum resultado encontrado. Tente novamente quando tiver mais dados na origem.",
                font=(FONT_FAMILY, 12), text_color=CLR_MUTED, wraplength=620, justify="left",
            ).pack(anchor="w", padx=24, pady=(0, 22))
            return

        candidates_frame = ctk.CTkScrollableFrame(window, fg_color=BG_CARD, corner_radius=12)
        candidates_frame.pack(fill="both", expand=True, padx=24, pady=(0, 22))
        for candidate in candidates:
            row = ctk.CTkFrame(candidates_frame, fg_color=BG_INPUT, corner_radius=10)
            row.pack(fill="x", pady=5, padx=4)
            row.grid_columnconfigure(0, weight=1)
            cover_label = ctk.CTkLabel(
                row, text="Carregando\ncapa...",
                width=COVER_PREVIEW_SIZE, height=COVER_PREVIEW_SIZE,
                font=(FONT_FAMILY, 9), text_color=CLR_MUTED, fg_color=BG_HOVER,
                corner_radius=8, justify="center",
            )
            cover_label.grid(row=0, column=1, rowspan=2, padx=(8, 2), pady=8)
            self._metadata_cover_labels[candidate.track_id] = cover_label
            ctk.CTkLabel(
                row, text=f"{candidate.artist} — {candidate.title}",
                font=(FONT_FAMILY, 12, "bold"), text_color=CLR_TEXT, anchor="w",
                # O iTunes devolve nome de faixa longo ("... Live At Queen
                # Elizabeth Theatre, Toronto, ON April 20, 2011"); sem quebra o
                # Tk clipa a frase no meio em vez de encolher.
                wraplength=360, justify="left",
            ).grid(row=0, column=0, sticky="ew", padx=12, pady=(9, 1))
            details = " · ".join(part for part in [candidate.album, candidate.year] if part)
            ctk.CTkLabel(
                row, text=details or "Album nao informado",
                font=(FONT_FAMILY, 10), text_color=CLR_MUTED, anchor="w",
            ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 9))
            HoverButton(
                row, text="Importar", width=105, height=32,
                font=(FONT_FAMILY, 11, "bold"),
                base_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK,
                text_color=CLR_TEXT,
                command=lambda item=pending_item, result=candidate, dialog=window:
                    self._start_import(item, result, dialog),
            ).grid(row=0, column=2, rowspan=2, padx=10, pady=10)

            threading.Thread(
                target=self._manager.load_metadata_cover_preview,
                args=(candidate,), daemon=True,
            ).start()

    def show_candidate_cover(
        self, candidate: MusicMetadataCandidate, data: bytes, _mime: str,
    ) -> None:
        label = self._metadata_cover_labels.get(candidate.track_id)
        if not label or not label.winfo_exists():
            return
        image = self._make_cover_image(data)
        if image is None:
            self.show_candidate_cover_unavailable(candidate)
            return
        self._metadata_cover_images[candidate.track_id] = image
        label.configure(image=image, text="")

    def show_candidate_cover_unavailable(self, candidate: MusicMetadataCandidate) -> None:
        label = self._metadata_cover_labels.get(candidate.track_id)
        if label and label.winfo_exists():
            label.configure(image=None, text="Capa\nindisponivel")

    def _start_import(
        self,
        pending_item: MetadataPendingItem,
        candidate: MusicMetadataCandidate,
        window: ctk.CTkToplevel,
    ) -> None:
        if self._import_thread and self._import_thread.is_alive():
            messagebox.showinfo("Importacao de metadata", "Aguarde a operacao atual terminar.")
            return
        window.destroy()
        self._import_thread = self._spawn(self._manager.apply_metadata, pending_item, candidate)
