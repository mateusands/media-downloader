"""Janela principal: consome a fila do backend por polling."""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import urlparse

import customtkinter as ctk

from .config import (
    APP_ICON_ICO_PATH,
    APP_ICON_PATH,
    APP_TITLE,
    SUPPORTED_PLATFORM_NAMES,
    SUPPORTED_SITES_URL,
)
from .downloader import DownloadManager, folder_summary_lines, summary_lines
from .models import DownloadSummary, MusicMetadataCandidate
from .theme import (
    BG_CARD,
    BG_DARK,
    BG_HOVER,
    BG_INPUT,
    CLR_ACCENT,
    CLR_ACCENT_DARK,
    CLR_ACCENT_LIGHT,
    CLR_BORDER,
    CLR_ERROR,
    CLR_GREEN,
    CLR_MUTED,
    CLR_TEXT,
    FONT_FAMILY,
)
from .review import MetadataReview
from .settings import SETTINGS_PATH, Settings, load_settings, save_settings
from .selection import PlaylistSelection
from .widgets import FormatCard, HoverButton

class MediaDownloaderApp:
    # Altura medida pelo conteudo: abaixo disso a secao de progresso sai da
    # janela, e e ela que diz se o download andou.
    _W, _H = 860, 920

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(740, 910)
        self.root.configure(fg_color=BG_DARK)
        self._app_icon = tk.PhotoImage(file=str(APP_ICON_PATH))
        self._header_icon = self._app_icon.subsample(8, 8)
        if sys.platform.startswith("win"):
            self.root.iconbitmap(default=str(APP_ICON_ICO_PATH))
        self.root.iconphoto(True, self._app_icon)

        self._q: queue.Queue = queue.Queue()
        self._manager = DownloadManager(self._q)
        self._downloads_dir = load_settings().downloads_dir
        self._manager.set_downloads_dir(self._downloads_dir)
        self._thread: threading.Thread | None = None
        self._review = MetadataReview(self.root, self._manager)
        self._selection = PlaylistSelection(
            self.root, self._download_selected, self._selection_cancelled)

        self._build_ui()
        self._center(self._W, self._H)
        self.root.after(100, self._poll)

    def _center(self, w: int, h: int) -> None:
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_header()
        self._build_body()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color=BG_DARK, corner_radius=0, height=108)
        header.pack(fill="x")
        header.pack_propagate(False)

        inner = ctk.CTkFrame(header, fg_color="transparent")
        inner.pack(fill="x", padx=32, pady=(18, 0))

        title_row = ctk.CTkFrame(inner, fg_color="transparent")
        title_row.pack(anchor="w")

        tk.Label(
            title_row, image=self._header_icon, bg=BG_DARK,
            borderwidth=0, highlightthickness=0,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(
            title_row, text=APP_TITLE,
            font=(FONT_FAMILY, 24, "bold"), text_color=CLR_TEXT,
        ).pack(side="left")

        ctk.CTkLabel(
            inner,
            text="Baixe video ou audio de plataformas compativeis com o yt-dlp.",
            font=(FONT_FAMILY, 12), text_color=CLR_MUTED,
        ).pack(anchor="w", pady=(6, 0))

    def _build_body(self) -> None:
        card = ctk.CTkFrame(self.root, fg_color=BG_CARD, corner_radius=16, border_width=1,
                            border_color=CLR_BORDER)
        card.pack(fill="both", expand=True, padx=24, pady=(12, 24))

        container = ctk.CTkFrame(card, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=28, pady=24)

        self._build_url_section(container)
        self._divider(container)
        self._build_format_section(container)
        self._divider(container)
        self._build_button_section(container)
        self._divider(container)
        self._build_progress_section(container)

    def _divider(self, parent) -> None:
        ctk.CTkFrame(parent, fg_color=CLR_BORDER, height=1, corner_radius=0).pack(
            fill="x", pady=12)

    # ── URL Section ───────────────────────────────────────────────────────────

    def _build_url_section(self, parent) -> None:
        ctk.CTkLabel(
            parent, text="Link da midia",
            font=(FONT_FAMILY, 13, "bold"), text_color=CLR_TEXT,
        ).pack(anchor="w")

        input_frame = ctk.CTkFrame(parent, fg_color=BG_INPUT, corner_radius=10,
                                   border_width=2, border_color=CLR_BORDER)
        input_frame.pack(fill="x", pady=(8, 0))
        self._input_frame = input_frame

        inner = ctk.CTkFrame(input_frame, fg_color="transparent")
        inner.pack(fill="x", padx=4, pady=4)
        inner.grid_columnconfigure(0, weight=1)

        self.url_var = ctk.StringVar()
        self.url_entry = ctk.CTkEntry(
            inner, textvariable=self.url_var,
            font=(FONT_FAMILY, 12),
            placeholder_text="https://exemplo.com/video",
            fg_color="transparent", border_width=0,
            text_color=CLR_TEXT, placeholder_text_color=CLR_MUTED,
            height=36,
        )
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(12, 4))

        self.paste_btn = HoverButton(
            inner, text="Colar", width=70, height=32,
            font=(FONT_FAMILY, 11, "bold"),
            base_color=CLR_BORDER, hover_color="#444444", press_color="#555555",
            text_color=CLR_TEXT, command=self._paste_url,
        )
        self.paste_btn.grid(row=0, column=1, padx=(0, 4))

        self.url_entry.bind("<<Paste>>", self._paste_over_selection)
        self.url_entry.bind("<FocusIn>", lambda _: input_frame.configure(border_color=CLR_ACCENT))
        self.url_entry.bind("<FocusOut>", lambda _: input_frame.configure(border_color=CLR_BORDER))

        self._url_hint = ctk.CTkLabel(
            parent, text="Cole um link de video, audio ou colecao.",
            font=(FONT_FAMILY, 11), text_color=CLR_MUTED,
        )
        self._url_hint.pack(anchor="w", pady=(4, 0))

        self._build_supported_sites_section(parent)

    def _build_supported_sites_section(self, parent) -> None:
        ctk.CTkLabel(
            parent, text="Plataformas populares compativeis",
            font=(FONT_FAMILY, 11, "bold"), text_color=CLR_TEXT,
        ).pack(anchor="w", pady=(14, 0))

        sites_row = ctk.CTkFrame(parent, fg_color="transparent")
        sites_row.pack(anchor="w", pady=(7, 0))
        for site in SUPPORTED_PLATFORM_NAMES:
            ctk.CTkLabel(
                sites_row, text=site, font=(FONT_FAMILY, 10), text_color=CLR_MUTED,
                fg_color=BG_HOVER, corner_radius=8, padx=10, pady=5,
            ).pack(side="left", padx=(0, 6))

        link = ctk.CTkLabel(
            parent, text="Ver lista completa e atualizada do yt-dlp",
            font=(FONT_FAMILY, 10), text_color=CLR_ACCENT_LIGHT, cursor="hand2",
        )
        link.pack(anchor="w", pady=(8, 0))
        link.bind("<Button-1>", lambda _: self._open_supported_sites())

    @staticmethod
    def _open_supported_sites() -> None:
        webbrowser.open_new_tab(SUPPORTED_SITES_URL)

    def _paste_over_selection(self, _event=None) -> str:
        """Colar troca o trecho selecionado.

        O binding de classe do Tk no X11 insere no cursor sem apagar a seleção,
        entao colar sobre um link selecionado duplicava a URL. O "break" impede
        que esse binding rode depois e insira de novo.
        """
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return "break"
        if self.url_entry.select_present():
            self.url_entry.delete("sel.first", "sel.last")
        self.url_entry.insert("insert", text.strip())
        return "break"

    def _paste_url(self) -> None:
        try:
            text = self.root.clipboard_get()
            self.url_var.set(text.strip())
        except tk.TclError:
            pass

    # ── Format Section ────────────────────────────────────────────────────────

    def _build_format_section(self, parent) -> None:
        ctk.CTkLabel(
            parent, text="Formato de saida",
            font=(FONT_FAMILY, 13, "bold"), text_color=CLR_TEXT,
        ).pack(anchor="w")

        self.format_var = ctk.StringVar(value="mp3")

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=1)

        self._mp3_card = FormatCard(
            row, icon="MP3", title="Audio MP3",
            subtitle="192 kbps — apenas audio",
            value="mp3", variable=self.format_var)
        self._mp3_card.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self._mp4_card = FormatCard(
            row, icon="MP4", title="Video MP4",
            subtitle="Melhor qualidade disponivel",
            value="mp4", variable=self.format_var)
        self._mp4_card.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        self.include_metadata_var = ctk.BooleanVar(value=False)
        self.metadata_checkbox = ctk.CTkCheckBox(
            parent, text="Adicionar capa e metadados",
            variable=self.include_metadata_var,
            onvalue=True, offvalue=False,
            font=(FONT_FAMILY, 12, "bold"), text_color=CLR_TEXT,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK,
            border_color=CLR_BORDER, checkmark_color=CLR_TEXT,
        )
        self.metadata_checkbox.pack(anchor="w", pady=(14, 0))
        ctk.CTkLabel(
            parent,
            text=("Usa titulo, artista e capa disponiveis na plataforma. "
                  "Títulos no formato Artista - Musica podem ajudar depois."),
            font=(FONT_FAMILY, 10), text_color=CLR_MUTED,
        ).pack(anchor="w", pady=(3, 0))

        self.auto_metadata_var = ctk.BooleanVar(value=True)
        self.auto_metadata_checkbox = ctk.CTkCheckBox(
            parent,
            text=("Aplicar sozinho quando artista, titulo e duracao baterem com o catalogo "
                  "— o que ficar em duvida vai para a revisao"),
            variable=self.auto_metadata_var,
            onvalue=True, offvalue=False,
            font=(FONT_FAMILY, 11), text_color=CLR_TEXT,
            fg_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK,
            border_color=CLR_BORDER, checkmark_color=CLR_TEXT,
            checkbox_width=20, checkbox_height=20,
        )
        self.auto_metadata_checkbox.pack(anchor="w", padx=(28, 0), pady=(8, 0))
        self.format_var.trace_add("write", lambda *_: self._update_metadata_option())
        self.include_metadata_var.trace_add("write", lambda *_: self._update_metadata_option())
        self._update_metadata_option()

    # ── Button Section ────────────────────────────────────────────────────────

    def _build_button_section(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(anchor="w")

        self.download_btn = HoverButton(
            row, text="Baixar midia",
            font=(FONT_FAMILY, 13, "bold"),
            base_color=CLR_ACCENT, hover_color=CLR_ACCENT_DARK, press_color="#4036aa",
            text_color="#ffffff", width=170, height=44,
            command=self.start_download,
        )
        self.download_btn.pack(side="left", padx=(0, 12))

        self.folder_btn = HoverButton(
            row, text="Abrir pasta",
            font=(FONT_FAMILY, 12),
            base_color="transparent", hover_color=BG_HOVER, press_color=CLR_BORDER,
            text_color=CLR_TEXT, border_width=2, border_color=CLR_BORDER,
            width=120, height=44,
            command=self.open_downloads_folder,
        )
        self.folder_btn.pack(side="left")

        self.fix_folder_btn = HoverButton(
            row, text="Corrigir pasta MP3",
            font=(FONT_FAMILY, 12),
            base_color="transparent", hover_color=BG_HOVER, press_color=CLR_BORDER,
            text_color=CLR_TEXT, border_width=2, border_color=CLR_BORDER,
            width=170, height=44,
            command=self.fix_music_folder,
        )
        self.fix_folder_btn.pack(side="left", padx=(12, 0))

        self.cancel_btn = HoverButton(
            row, text="Cancelar",
            font=(FONT_FAMILY, 12),
            base_color="transparent", hover_color=BG_HOVER, press_color=CLR_BORDER,
            text_color=CLR_TEXT, border_width=2, border_color=CLR_BORDER,
            width=120, height=44, state="disabled",
            command=self.cancel_download,
        )
        self.cancel_btn.pack(side="left", padx=(12, 0))

        dest_row = ctk.CTkFrame(parent, fg_color="transparent")
        dest_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(
            dest_row, text="Salvar em:", font=(FONT_FAMILY, 11, "bold"), text_color=CLR_TEXT,
        ).pack(side="left")
        self._dest_label = ctk.CTkLabel(
            dest_row, text=self._short_path(self._downloads_dir),
            font=(FONT_FAMILY, 11), text_color=CLR_MUTED, anchor="w",
        )
        self._dest_label.pack(side="left", padx=(6, 10))
        self.dest_link = ctk.CTkLabel(
            dest_row, text="Alterar pasta",
            font=(FONT_FAMILY, 11), text_color=CLR_ACCENT_LIGHT, cursor="hand2",
        )
        self.dest_link.pack(side="left")
        self.dest_link.bind("<Button-1>", lambda _: self.choose_downloads_dir())

    # ── Progress Section ──────────────────────────────────────────────────────

    def _build_progress_section(self, parent) -> None:
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.pack(fill="x")

        ctk.CTkLabel(
            hdr, text="Progresso",
            font=(FONT_FAMILY, 13, "bold"), text_color=CLR_TEXT,
        ).pack(side="left")

        self.pct_var = ctk.StringVar(value="0%")
        self._pct_label = ctk.CTkLabel(
            hdr, textvariable=self.pct_var,
            font=(FONT_FAMILY, 13, "bold"), text_color=CLR_MUTED,
        )
        self._pct_label.pack(side="right")

        self.progress_var = ctk.DoubleVar(value=0)
        self.progress_bar = ctk.CTkProgressBar(
            parent, variable=self.progress_var,
            height=12, corner_radius=6,
            fg_color=CLR_BORDER, progress_color=CLR_ACCENT,
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(10, 12))

        self.status_var = ctk.StringVar(value="Pronto para iniciar.")
        self._status_label = ctk.CTkLabel(
            parent, textvariable=self.status_var,
            font=(FONT_FAMILY, 12), text_color=CLR_TEXT,
            anchor="w", justify="left", wraplength=720,
        )
        self._status_label.pack(fill="x")

        self.info_var = ctk.StringVar(
            value="Os arquivos serao organizados automaticamente por formato e tipo.")
        ctk.CTkLabel(
            parent, textvariable=self.info_var,
            font=(FONT_FAMILY, 11), text_color=CLR_MUTED,
            anchor="w", justify="left", wraplength=720,
        ).pack(fill="x", pady=(4, 0))

    # ── Actions ───────────────────────────────────────────────────────────────

    def start_download(self) -> None:
        url = self.url_var.get().strip()
        if not self._valid_url(url):
            self._input_frame.configure(border_color=CLR_ERROR)
            self._url_hint.configure(
                text="URL invalida — informe um link HTTP ou HTTPS valido.",
                text_color=CLR_ERROR)
            self.root.after(3000, self._reset_url_hint)
            return

        self._input_frame.configure(border_color=CLR_BORDER)
        self._reset_url_hint()

        if self._thread and self._thread.is_alive():
            messagebox.showwarning(
                "Download em andamento",
                "Aguarde o download atual terminar antes de iniciar outro.")
            return

        self._set_busy(True)
        self.progress_bar.set(0)
        self.pct_var.set("0%")
        self._pct_label.configure(text_color=CLR_MUTED)
        self.status_var.set("Iniciando...")
        self._status_label.configure(text_color=CLR_TEXT)
        self.info_var.set("Analisando link...")

        self._start_worker(
            self._manager.download,
            url, self.format_var.get(), self.include_metadata_var.get(),
            self.auto_metadata_var.get(),
        )

    def fix_music_folder(self) -> None:
        """Corrige capa e metadata de MP3 que ja estavam no disco."""
        if self._thread and self._thread.is_alive():
            messagebox.showwarning(
                "Operacao em andamento",
                "Aguarde o download atual terminar antes de corrigir uma pasta.")
            return
        folder = filedialog.askdirectory(
            parent=self.root, title="Pasta com os MP3 a corrigir", mustexist=True)
        if not folder:
            return
        auto = messagebox.askyesno(
            "Corrigir pasta MP3",
            "Aplicar sozinho quando artista, titulo e duracao baterem com o catalogo?\n\n"
            "Isso grava as tags e a capa e renomeia o arquivo para o nome da musica. "
            "O que ficar em duvida vai para a revisao.\n\n"
            "Com \"Nao\", tudo vai para a revisao e nada e gravado sem voce confirmar.",
            parent=self.root)
        artist = ctk.CTkInputDialog(
            title="Artista dos MP3 sem artista",
            text=("Opcional: artista para os MP3 que nao tem artista gravado "
                  "(ex.: uma pasta so de um artista). Deixe em branco para pular."),
        ).get_input()
        if artist is None:
            return

        self._set_busy(True, busy_text="Corrigindo...")
        self.progress_bar.set(0)
        self.pct_var.set("")
        self._pct_label.configure(text_color=CLR_MUTED)
        self._status_label.configure(text_color=CLR_TEXT)
        self.status_var.set("Lendo os MP3 da pasta...")
        self.info_var.set(f"Pasta: {folder}")
        self._start_worker(self._manager.fix_folder, folder, auto, artist)

    def _start_worker(self, target, *args) -> None:
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self._thread.start()

    def cancel_download(self) -> None:
        if self._selection.is_open():
            self._selection.cancel()
            return
        self._manager.cancel()
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("Cancelando — termina o item atual e para...")

    def _download_selected(self, indices: list[int]) -> None:
        self.status_var.set("Iniciando...")
        self._start_worker(self._manager.download_selected, indices)

    def _selection_cancelled(self) -> None:
        self._manager.discard_listing()
        self._set_busy(False)
        self.progress_bar.set(0)
        self.pct_var.set("0%")
        self.status_var.set("Download cancelado antes de comecar.")
        self.info_var.set("Nenhum item foi baixado.")

    def _reset_url_hint(self):
        self._url_hint.configure(
            text="Cole um link de video, audio ou colecao.",
            text_color=CLR_MUTED)

    def choose_downloads_dir(self) -> None:
        if self._thread and self._thread.is_alive():
            messagebox.showwarning(
                "Download em andamento",
                "Aguarde o download atual terminar antes de trocar a pasta.")
            return
        folder = filedialog.askdirectory(
            parent=self.root, title="Onde salvar os downloads",
            initialdir=str(self._downloads_dir), mustexist=False)
        if not folder:
            return
        self._downloads_dir = Path(folder)
        self._manager.set_downloads_dir(self._downloads_dir)
        self._dest_label.configure(text=self._short_path(self._downloads_dir))
        try:
            save_settings(SETTINGS_PATH, Settings(downloads_dir=self._downloads_dir))
        except OSError as exc:
            messagebox.showwarning(
                "Pasta de destino",
                f"A pasta vale para esta sessao, mas nao deu para lembrar dela.\n\n{exc}")

    def open_downloads_folder(self) -> None:
        folder = self._downloads_dir
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            messagebox.showerror("Erro ao abrir pasta", str(exc))

    # ── Event loop ────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        while True:
            try:
                event = self._q.get_nowait()
            except queue.Empty:
                break
            self._handle(event)
        self.root.after(100, self._poll)

    def _handle(self, event: dict[str, Any]) -> None:
        etype = event.get("type")

        if etype == "status":
            self.status_var.set(event.get("message", ""))

        elif etype == "meta":
            total = event.get("total_items", 0)
            is_pl = event.get("playlist_mode", False)
            dest  = event.get("target_dir", "")
            tipo  = "Colecao" if is_pl else "Midia"
            self.info_var.set(f"Tipo: {tipo}  |  Itens: {total}  |  Destino: {dest}")

        elif etype == "progress":
            pct = event.get("progress", 0.0)
            self.progress_bar.set(pct / 100.0)
            self.pct_var.set(f"{pct:.0f}%")
            self.status_var.set(event.get("message", "Baixando..."))

        elif etype == "done":
            summary: DownloadSummary = event["summary"]
            self._set_busy(False)
            pct = 100.0 if summary.downloaded_count else 0.0
            self.progress_bar.set(pct / 100.0)
            self.pct_var.set(f"{pct:.0f}%")
            if summary.downloaded_count:
                self._pct_label.configure(text_color=CLR_GREEN)
                self._status_label.configure(text_color=CLR_GREEN)
            self._show_summary(summary)

        elif etype == "folder_checked":
            summary = event["summary"]
            self._set_busy(False)
            self.progress_bar.set(1 if summary.total_items else 0)
            self.pct_var.set("")
            pending = len(summary.metadata_pending_items)
            self.status_var.set(
                f"Pasta conferida — {len(summary.metadata_auto_applied)} corrigido(s) "
                f"sozinho(s), {pending} para revisar.")
            self.info_var.set(f"Pasta: {summary.target_dir}")
            messagebox.showinfo("Correcao da pasta", "\n".join(folder_summary_lines(summary)))
            if pending:
                self._review.open(summary.metadata_pending_items)

        elif etype == "playlist_listed":
            entries = event.get("entries", [])
            self.status_var.set(f"Playlist com {len(entries)} item(s) — escolha o que baixar.")
            self._selection.open(event.get("title", "Playlist"), entries)

        elif etype == "cancelled":
            summary = event["summary"]
            self._set_busy(False)
            self._pct_label.configure(text_color=CLR_MUTED)
            self._status_label.configure(text_color=CLR_TEXT)
            if summary.total_items:
                self.status_var.set(
                    f"Cancelado — {summary.downloaded_count} de {summary.total_items} "
                    "item(s) baixado(s) antes de parar.")
                self.info_var.set(f"Destino: {summary.target_dir}")
            else:
                self.status_var.set("Download cancelado antes de comecar.")
                self.info_var.set("Nenhum item foi baixado.")
            if summary.metadata_pending_items:
                self._review.open(summary.metadata_pending_items)

        elif etype == "error":
            self._set_busy(False)
            self.progress_bar.set(0)
            self.pct_var.set("0%")
            self._pct_label.configure(text_color=CLR_ERROR)
            self._status_label.configure(text_color=CLR_ERROR)
            self.status_var.set("Falha no download.")
            self.info_var.set("Verifique o link informado e tente novamente.")
            messagebox.showerror("Erro no download",
                                 event.get("message", "Erro desconhecido."))

        elif etype == "metadata_results":
            self._review.show_candidates(
                event["pending_item"], event.get("candidates", []),
            )

        elif etype == "metadata_search_error":
            self._review.search_finished(event["pending_item"])
            messagebox.showerror(
                "Busca de metadata",
                f"Nao foi possivel buscar metadata para {event['pending_item'].title}.\n\n"
                f"{event.get('message', 'Erro desconhecido.')}",
            )

        elif etype == "metadata_applied":
            self._review.resolve(event["pending_item"])
            candidate: MusicMetadataCandidate = event["candidate"]
            cover_message = " com capa incorporada" if event.get("cover_embedded") else " sem capa disponivel"
            messagebox.showinfo(
                "Metadata importada",
                f"Metadata de {candidate.artist} — {candidate.title} importada{cover_message}.",
            )

        elif etype == "metadata_bulk_progress":
            self._review.bulk_progress(event["done"], event["total"])
            self.status_var.set(
                f"Aplicando sugestao do catalogo ({event['done'] + 1}/{event['total']})...")

        elif etype == "metadata_bulk_applied":
            self._review.resolve(event["pending_item"])

        elif etype == "metadata_bulk_done":
            applied, kept = event["applied"], event["kept"]
            self._review.bulk_finished(applied, kept, event.get("failed", 0))
            self.status_var.set(
                f"Sugestao aplicada em {applied} MP3"
                + (f"; {kept} continuam na revisao." if kept else "."))

        elif etype == "metadata_cover_cropped":
            self._review.resolve(event["pending_item"])

        elif etype == "metadata_crop_done":
            self._review.crop_finished(event["cropped"], event["failed"])
            self.status_var.set(f"{event['cropped']} capa(s) recortada(s) da miniatura do video.")

        elif etype == "metadata_import_error":
            messagebox.showerror(
                "Importacao de metadata",
                f"Nao foi possivel atualizar {event['pending_item'].title}.\n\n"
                f"{event.get('message', 'Erro desconhecido.')}",
            )

        elif etype == "metadata_embedded":
            self._review.show_embedded(event["pending_item"], event["embedded"])

        elif etype == "metadata_embedded_unavailable":
            self._review.show_embedded_unavailable(event["pending_item"])

        elif etype == "metadata_cover_preview":
            self._review.show_candidate_cover(
                event["candidate"], event["data"], event["mime"],
            )

        elif etype == "metadata_cover_unavailable":
            self._review.show_candidate_cover_unavailable(event["candidate"])

    def _show_summary(self, s: DownloadSummary) -> None:
        failed = len(s.failed_items)
        pending_metadata = len(s.metadata_pending_items)
        self.status_var.set(
            f"Concluido — {s.downloaded_count} baixado(s)"
            + (f", {failed} com falha" if failed else "")
            + (f", {pending_metadata} com metadata a confirmar" if pending_metadata else ""))
        self.info_var.set(f"Destino: {s.target_dir}")

        messagebox.showinfo("Resumo do download", "\n".join(summary_lines(s)))
        if pending_metadata:
            self._review.open(s.metadata_pending_items)

    # ── State helpers ─────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool, busy_text: str = "Baixando...") -> None:
        state = "disabled" if busy else "normal"
        self.download_btn.configure(
            state=state,
            text=busy_text if busy else "Baixar midia")
        self.fix_folder_btn.configure(state=state)
        self.url_entry.configure(state=state)
        self.paste_btn.configure(state=state)
        self.folder_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        if busy:
            self.metadata_checkbox.configure(state="disabled")
            self.auto_metadata_checkbox.configure(state="disabled")
        else:
            self._update_metadata_option()

    def _update_metadata_option(self) -> None:
        if self.format_var.get() == "mp3":
            self.metadata_checkbox.configure(state="normal", text_color=CLR_TEXT)
        else:
            # So grava quando muda: o trace desta variavel chama esta funcao, e
            # o Tk dispara o trace a cada escrita, mesmo de valor igual.
            if self.include_metadata_var.get():
                self.include_metadata_var.set(False)
            self.metadata_checkbox.configure(state="disabled", text_color=CLR_MUTED)
        # A aplicacao automatica so existe dentro da opcao de metadata.
        if self.include_metadata_var.get():
            self.auto_metadata_checkbox.configure(state="normal", text_color=CLR_TEXT)
        else:
            self.auto_metadata_checkbox.configure(state="disabled", text_color=CLR_MUTED)

    @staticmethod
    def _short_path(path: Path, limit: int = 70) -> str:
        # Caminho longo empurraria o "Alterar pasta" para fora da linha; o fim
        # do caminho e o que diz onde o arquivo cai.
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
