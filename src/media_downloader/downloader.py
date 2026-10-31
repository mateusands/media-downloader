"""Backend do download. Publica eventos na fila; nunca toca na interface."""

import queue
import re
import shutil
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yt_dlp.postprocessor import MoveFilesAfterDownloadPP
from yt_dlp.postprocessor.metadataparser import MetadataParserPP
from yt_dlp.utils import DownloadCancelled, download_range_func

from .clip import clip_label, is_precise
from .config import DOWNLOAD_FOLDERS
from .history import HistoryEntry, append_history
from .library import crop_cover_to_square, music_files, read_local_item
from .metadata import (
    MusicMetadataService,
    confident_match,
    metadata_review_reasons,
    rename_to_song,
    search_suggestion,
    song_title,
    suggested_match,
)
from .models import (
    DownloadSummary,
    MediaOptions,
    MetadataPendingItem,
    MusicMetadataCandidate,
    PlaylistEntry,
)
from .preview import fetch_thumbnail, preview_from_info, thumbnail_url

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Quantas falhas o resumo lista antes de virar contagem. `messagebox` nao rola:
# lista longa cresce a caixa ate passar da tela e esconder o proprio botao.
FAILURES_SHOWN = 5


def summary_lines(summary: DownloadSummary) -> list[str]:
    """Monta o texto do resumo final.

    O resumo responde "o que aconteceu?"; a revisao que abre logo depois
    responde "o que corrigir?", item a item e com previa da capa. Repetir o
    detalhe de cada pendencia aqui so fazia a caixa crescer sem informar nada
    novo — numa playlist de sete faixas ela ja ocupava a tela inteira.
    """
    failed = len(summary.failed_items)
    pending = len(summary.metadata_pending_items)
    lines = [
        f"OK  {summary.downloaded_count} item(s) baixado(s) com sucesso",
        f"--  {failed} item(s) com falha",
        f"--  {pending} MP3 com metadata a confirmar",
    ]
    if summary.metadata_auto_applied:
        lines.append(
            f"OK  {len(summary.metadata_auto_applied)} MP3 com metadata aplicada automaticamente")
    if summary.already_downloaded_count:
        lines.append(
            f"==  {summary.already_downloaded_count} item(s) ja baixado(s) antes, ignorado(s)")
    if failed:
        lines += ["", "Itens com falha:"]
        lines += [f"  - {item}" for item in summary.failed_items[:FAILURES_SHOWN]]
        if failed > FAILURES_SHOWN:
            lines.append(f"  ... e mais {failed - FAILURES_SHOWN}")
    if summary.extractor_notices and (failed or not summary.downloaded_count):
        lines += ["", "Avisos do extrator:"]
        lines += [f"  - {aviso}" for aviso in summary.extractor_notices[:FAILURES_SHOWN]]
    if pending:
        lines += ["", f"A revisao abre em seguida, com {pending} item(s) para confirmar."]
    return lines



def folder_summary_lines(summary: DownloadSummary) -> list[str]:
    """Resumo da correcao de uma pasta — nada foi baixado, entao nao fala em download."""
    failed = len(summary.failed_items)
    lines = [
        f"{summary.total_items} MP3 lido(s) na pasta",
        f"==  {summary.already_complete_count} ja completo(s), nao tocado(s)",
        f"OK  {len(summary.metadata_auto_applied)} com metadata aplicada automaticamente",
        f"--  {len(summary.metadata_pending_items)} para confirmar na revisao",
    ]
    if failed:
        lines.append(f"--  {failed} arquivo(s) que nao deu para ler")
        lines += [f"  - {item}" for item in summary.failed_items[:FAILURES_SHOWN]]
        if failed > FAILURES_SHOWN:
            lines.append(f"  ... e mais {failed - FAILURES_SHOWN}")
    return lines

# Um por pasta de destino: o MP3 de um video nao pode impedir o MP4 dele.
ARCHIVE_FILENAME = ".historico-de-downloads.txt"

# Desde o yt-dlp 2025.11 o YouTube exige um runtime de JavaScript, e so o deno
# vem ligado por padrao: com node instalado e sem deno, o yt-dlp nem tentava.
_JS_RUNTIME_PREFERENCE = ("deno", "node", "bun")


def js_runtimes_for(which: Callable[[str], str | None]) -> dict[str, dict[str, str]]:
    for name in _JS_RUNTIME_PREFERENCE:
        path = which(name)
        if path:
            return {name: {"path": path}}
    return {"deno": {}}


_BEST_VIDEO = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
VIDEO_QUALITIES = ("best", "1080", "720", "480", "360")

# Patrocinio, autopromocao, pedido de inscricao e o trecho sem musica de um
# clipe: o que a pessoa pularia na mao.
SPONSOR_CATEGORIES = ("sponsor", "selfpromo", "interaction", "music_offtopic")
_SUBTITLE_LANGS = ["pt.*", "en.*"]
_SUBTITLE_EXTS = {"vtt", "srt", "ass", "ssa", "ttml", "srv1", "srv2", "srv3", "json3", "lrc"}
_MOVE_FILES = MoveFilesAfterDownloadPP.pp_key()


def _is_subtitle_download(info_dict: dict[str, Any]) -> bool:
    """Legenda passa pelo gancho como download a parte, com o dicionario da legenda."""
    return not info_dict.get("id") and info_dict.get("ext") in _SUBTITLE_EXTS


def video_format(quality: str) -> str:
    """Altura como teto: sem nada abaixo dela, baixa o que houver em vez de falhar."""
    if quality not in VIDEO_QUALITIES or quality == "best":
        return _BEST_VIDEO
    cap = f"[height<={quality}]"
    return f"bv*{cap}[ext=mp4]+ba[ext=m4a]/bv*{cap}+ba/b{cap}/b"


def transfer_text(speed: float | None, eta: float | None) -> str:
    """'4.2 MB/s · faltam 0:12' — so o que o yt-dlp informou."""
    if not speed:
        return ""
    if speed >= 1024 * 1024:
        text = f"{speed / (1024 * 1024):.1f} MB/s"
    else:
        text = f"{speed / 1024:.0f} KB/s"
    if eta:
        minutes, seconds = divmod(int(eta), 60)
        text += f" · faltam {minutes}:{seconds:02d}"
    return text


def _retry_sleep(attempt: int) -> float:
    return min(2 ** attempt, 30)


def _archive_id(entry: dict[str, Any]) -> str | None:
    """Mesma chave que o yt-dlp grava no historico (`_make_archive_id`)."""
    extractor = entry.get("extractor_key") or entry.get("ie_key")
    video_id = entry.get("id")
    if not (extractor and video_id):
        return None
    return f"{extractor.lower()} {video_id}"


def archive_ids(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {line.strip() for line in lines if line.strip()}


def forget_archived(path: Path, ids: set[str]) -> None:
    if not ids or not path.is_file():
        return
    kept = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and line.strip() not in ids
    ]
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


def playlist_entries(info: dict[str, Any], archived: set[str]) -> list[PlaylistEntry]:
    """Linhas da escolha, numeradas pela posicao que o `playlist_items` entende.

    O `None` que o ignoreerrors deixa no lugar de um item quebrado ainda ocupa a
    posicao: pular a contagem ali desalinharia todos os indices seguintes.
    """
    rows = []
    for index, entry in enumerate(info.get("entries") or [], start=1):
        if not entry:
            continue
        archive_id = _archive_id(entry)
        rows.append(PlaylistEntry(
            index=index,
            title=entry.get("title") or entry.get("id") or f"Item {index}",
            archive_id=archive_id,
            duration=entry.get("duration"),
            already_downloaded=archive_id in archived,
        ))
    return rows


def playlist_items_spec(indices: list[int]) -> str:
    return ",".join(str(index) for index in sorted(set(indices)))


@dataclass(frozen=True)
class _PlaylistListing:
    """Playlist listada que espera a escolha da pessoa para comecar."""
    url: str
    file_format: str
    include_metadata: bool
    auto_metadata: bool
    target_dir: Path
    entries: tuple[PlaylistEntry, ...]
    options: MediaOptions = field(default_factory=MediaOptions)


def _source_artist(info: dict[str, Any]) -> str | None:
    """Artista que a origem publicou — nunca o canal, que e provisorio."""
    if info.get("artist"):
        return info["artist"]
    artists = info.get("artists") or []
    return artists[0] if artists else None


class _SilentLogger:
    """A previa nao relata nada: o download, se vier, relata por ela."""

    def debug(self, _: str) -> None:
        pass

    warning = error = debug


class ReportingLogger:
    def __init__(self, event_queue: queue.Queue, summary: DownloadSummary):
        self._q = event_queue
        self._summary = summary

    def debug(self, _: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        # Aviso do extrator não é item que falhou: o alerta de runtime
        # JavaScript ausente aparece em download que conclui inteiro.
        self._capture(msg, failure=False)

    def error(self, msg: str) -> None:
        self._capture(msg, failure=True)

    def _capture(self, msg: str, *, failure: bool) -> None:
        # A saída do yt-dlp vem colorida: sem tirar o escape ANSI, o prefixo não
        # casa e o resumo mostra "\x1b[0;31mERROR:\x1b[0m ..." como texto.
        # Prefixo sem o espaço: o .strip() anterior já removeu o espaço de
        # "ERROR: " quando a mensagem vinha vazia, e "ERROR:" virava um item.
        cleaned = _ANSI_ESCAPE.sub("", msg).strip()
        for prefix in ("ERROR:", "WARNING:"):
            cleaned = cleaned.removeprefix(prefix).strip()
        if not cleaned:
            return
        destination = (
            self._summary.failed_items if failure else self._summary.extractor_notices)
        if cleaned in destination:
            return
        destination.append(cleaned)
        if failure:
            self._q.put({"type": "status", "message": "Um item falhou — continuando com os demais..."})


class DownloadManager:
    def __init__(
        self,
        event_queue: queue.Queue,
        metadata_service: MusicMetadataService | None = None,
    ):
        self._q = event_queue
        self._metadata_service = metadata_service or MusicMetadataService()
        self._cancel = threading.Event()
        # Separado do cancelamento do download: parar o lote na revisao nao
        # pode interromper um download que esteja rodando ao mesmo tempo.
        self._stop_bulk = threading.Event()
        # None mantem DOWNLOAD_FOLDERS como esta (Downloads/ do repositorio).
        self._downloads_dir: Path | None = None
        self._listing: _PlaylistListing | None = None
        self._finished_files: list[tuple[str, Path]] = []
        self._history_path: Path | None = None

    def cancel(self) -> None:
        """Pede a interrupcao; o yt-dlp para no proximo aviso de progresso."""
        self._cancel.set()

    def _reset_cancel(self) -> None:
        self._cancel.clear()

    def search_metadata(self, pending_item: MetadataPendingItem) -> None:
        try:
            suggestion = search_suggestion(pending_item)
            candidates = self._metadata_service.search(suggestion)
            self._emit(
                "metadata_results", pending_item=pending_item, candidates=candidates,
            )
        except Exception as exc:
            self._emit("metadata_search_error", pending_item=pending_item, message=str(exc))

    def _apply_candidate(
        self, pending_item: MetadataPendingItem, candidate: MusicMetadataCandidate,
    ) -> bool:
        """Grava as tags e da ao arquivo o nome oficial da faixa."""
        path = Path(pending_item.file_path)
        cover_embedded = self._metadata_service.apply_to_mp3(path, candidate)
        try:
            rename_to_song(path, candidate.title)
        except OSError:
            # As tags ja estao gravadas, que e o que a pessoa confirmou; o nome
            # antigo e so menos bonito, nao um arquivo errado.
            pass
        return cover_embedded

    def apply_metadata(
        self, pending_item: MetadataPendingItem, candidate: MusicMetadataCandidate,
    ) -> None:
        try:
            cover_embedded = self._apply_candidate(pending_item, candidate)
            self._emit(
                "metadata_applied",
                pending_item=pending_item,
                candidate=candidate,
                cover_embedded=cover_embedded,
            )
        except Exception as exc:
            self._emit("metadata_import_error", pending_item=pending_item, message=str(exc))

    def load_embedded_metadata(self, pending_item: MetadataPendingItem) -> None:
        try:
            embedded = self._metadata_service.read_embedded(Path(pending_item.file_path))
            self._emit("metadata_embedded", pending_item=pending_item, embedded=embedded)
        except Exception:
            self._emit("metadata_embedded_unavailable", pending_item=pending_item)

    def load_metadata_cover_preview(self, candidate: MusicMetadataCandidate) -> None:
        try:
            preview = self._metadata_service.get_cover_preview(candidate)
            if preview is None:
                self._emit("metadata_cover_unavailable", candidate=candidate)
                return
            data, mime = preview
            self._emit(
                "metadata_cover_preview", candidate=candidate, data=data, mime=mime,
            )
        except Exception:
            self._emit("metadata_cover_unavailable", candidate=candidate)

    def set_history_path(self, path: Path) -> None:
        """Sem chamar isto, nada vai para o historico — testes nao sujam a configuracao."""
        self._history_path = path

    def probe(self, url: str) -> None:
        """Previa do link. Falha aqui nao e erro de download: so nao ha previa."""
        try:
            playlist_mode = self._url_is_playlist(url)
            opts = {
                **self._base_opts(),
                "skip_download": True,
                "extract_flat": "in_playlist",
                "noplaylist": not playlist_mode,
                "no_warnings": True,
                "logger": _SilentLogger(),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Nao foi possivel ler o link.")
            preview = preview_from_info(url, info)
        except Exception as exc:
            self._emit("preview_failed", url=url, message=str(exc))
            return
        thumbnail = None
        if image_url := thumbnail_url(info):
            try:
                thumbnail = fetch_thumbnail(image_url)
            except Exception:
                thumbnail = None
        self._emit("preview", url=url, preview=replace(preview, thumbnail=thumbnail))

    def set_downloads_dir(self, base: Path) -> None:
        """Base escolhida pela pessoa; as subpastas por tipo continuam as mesmas."""
        self._downloads_dir = base

    def stop_bulk(self) -> None:
        self._stop_bulk.set()

    def apply_suggested(self, pending_items: list[MetadataPendingItem]) -> None:
        """Aplica a sugestao do app em cada item; o que nao tem sugestao fica na revisao."""
        self._stop_bulk.clear()
        applied = failed = 0
        for position, item in enumerate(pending_items, start=1):
            if self._stop_bulk.is_set():
                break
            self._emit("metadata_bulk_progress", done=position - 1, total=len(pending_items))
            try:
                candidate = suggested_match(
                    item, self._metadata_service.search(search_suggestion(item)))
                if candidate is None:
                    continue
                self._apply_candidate(item, candidate)
            except Exception:
                # Catalogo fora do ar ou arquivo travado num item: ele so continua
                # na revisao, e o lote segue — mas contado a parte, porque "sem
                # sugestao" e "nao consultado" pedem respostas diferentes.
                failed += 1
                continue
            applied += 1
            self._emit("metadata_bulk_applied", pending_item=item, candidate=candidate)
        self._emit(
            "metadata_bulk_done", applied=applied, kept=len(pending_items) - applied,
            failed=failed)

    def crop_covers(self, pending_items: list[MetadataPendingItem]) -> None:
        """Recorta para quadrada a miniatura gravada — so por escolha da pessoa."""
        cropped = failed = 0
        for item in pending_items:
            try:
                crop_cover_to_square(Path(item.file_path))
            except Exception:
                failed += 1
                continue
            cropped += 1
            self._emit("metadata_cover_cropped", pending_item=item)
        self._emit("metadata_crop_done", cropped=cropped, failed=failed)

    def fix_folder(
        self, folder: str, auto_metadata: bool, artist_hint: str | None = None,
    ) -> None:
        """Leva MP3 que ja estao no disco para a mesma correspondencia e revisao do download."""
        self._reset_cancel()
        try:
            summary = DownloadSummary(target_dir=folder)
            self._emit("status", message="Lendo os MP3 da pasta...")
            paths = music_files(Path(folder))
            for path in paths:
                try:
                    item = read_local_item(path, artist_hint)
                except Exception:
                    summary.failed_items.append(path.name)
                    continue
                # Sem motivo nenhum e arquivo completo: a pessoa pediu que nao se toque.
                if item.review_reasons:
                    summary.metadata_pending_items.append(item)
                else:
                    summary.already_complete_count += 1
            summary.total_items = len(paths)
            if auto_metadata:
                self._auto_apply_metadata(summary)
            # O que tem algo faltando vem antes do que so precisa ser conferido.
            summary.metadata_pending_items.sort(key=lambda item: not item.review_reasons)
            self._emit("folder_checked", summary=summary)
        except Exception as exc:
            self._emit("error", message=str(exc))

    def download(
        self, url: str, file_format: str,
        include_metadata: bool = False, auto_metadata: bool = False,
        options: MediaOptions | None = None,
    ) -> None:
        """Item unico baixa direto; playlist lista e espera `download_selected`."""
        options = options or MediaOptions()
        self._reset_cancel()
        self._listing = None
        try:
            playlist_mode = self._url_is_playlist(url)
            info = self._extract_info(url, playlist_mode)

            if playlist_mode and not self._is_playlist_result(info):
                playlist_mode = False
            if self._cancel.is_set():
                # Listagem de playlist grande demora; quem cancelou nela nao
                # espera ver o dialogo de escolha abrir em seguida.
                self._emit("cancelled", summary=DownloadSummary(playlist_mode=playlist_mode))
                return

            target_dir = self._ensure_output_dir(file_format, playlist_mode)
            if playlist_mode:
                entries = playlist_entries(info, archive_ids(target_dir / ARCHIVE_FILENAME))
                # Trecho nao vale em playlist: cada item tem duracao propria.
                self._listing = _PlaylistListing(
                    url, file_format, include_metadata, auto_metadata,
                    target_dir, tuple(entries),
                    replace(options, clip_start=None, clip_end=None),
                )
                self._emit(
                    "playlist_listed",
                    title=info.get("title") or "Playlist",
                    entries=entries,
                )
                return
        except Exception as exc:
            self._emit("error", message=str(exc))
            return

        self._run(
            url, file_format, include_metadata, auto_metadata,
            DownloadSummary(target_dir=str(target_dir), total_items=1), options=options,
        )

    def download_selected(self, indices: list[int]) -> None:
        listing, self._listing = self._listing, None
        chosen = set(indices)
        if listing is None or not chosen:
            self._emit("error", message="Nenhum item da playlist foi escolhido.")
            return
        self._reset_cancel()

        selected = [entry for entry in listing.entries if entry.index in chosen]
        # Pedir de novo o que ja foi baixado e intencional: sem esquecer, o
        # historico faria o yt-dlp pular o item em silencio.
        try:
            forget_archived(
                listing.target_dir / ARCHIVE_FILENAME,
                {e.archive_id for e in selected if e.already_downloaded and e.archive_id},
            )
        except OSError as exc:
            self._emit("error", message=f"Nao foi possivel atualizar o historico: {exc}")
            return

        summary = DownloadSummary(
            target_dir=str(listing.target_dir),
            playlist_mode=True,
            total_items=len(selected),
            already_downloaded_count=sum(
                1 for e in listing.entries
                if e.already_downloaded and e.index not in chosen),
        )
        self._run(
            listing.url, listing.file_format, listing.include_metadata,
            listing.auto_metadata, summary, playlist_items=playlist_items_spec(indices),
            options=listing.options,
        )

    def discard_listing(self) -> None:
        self._listing = None

    def _run(
        self, url: str, file_format: str, include_metadata: bool, auto_metadata: bool,
        summary: DownloadSummary, playlist_items: str | None = None,
        options: MediaOptions | None = None,
    ) -> None:
        options = options or MediaOptions()
        try:
            label = "colecao" if summary.playlist_mode else "midia"
            self._emit("status", message=f"Preparando download ({label})...")
            self._emit(
                "meta",
                total_items=summary.total_items,
                playlist_mode=summary.playlist_mode,
                target_dir=summary.target_dir,
            )

            opts = self._build_opts(
                Path(summary.target_dir), file_format, summary.playlist_mode,
                summary, include_metadata, options=options,
            )
            if playlist_items:
                opts["playlist_items"] = playlist_items
            self._finished_files = []
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            if file_format == "mp3":
                clip = None if summary.playlist_mode or not options.is_clip else clip_label(
                    options.clip_start, options.clip_end)
                self._finished_files = self._rename_downloads(
                    summary, self._finished_files, clip)
            summary.finished_files = [str(path) for _, path in self._finished_files]
            self._record_history(file_format)
            if include_metadata and auto_metadata:
                self._auto_apply_metadata(summary)
            if self._cancel.is_set():
                raise DownloadCancelled("Download cancelado.")

            self._reconcile_failure_reports(summary)
            self._emit("done", summary=summary)
        except DownloadCancelled:
            self._emit("cancelled", summary=summary)
        except Exception as exc:
            self._emit("error", message=str(exc))

    def _record_final_file(self, data: dict[str, Any]) -> None:
        """Guarda onde cada arquivo terminou, depois de convertido e movido."""
        # O nome publicado e o `pp_key()` ("MoveFiles"), nao o da classe: comparado
        # com "MoveFilesAfterDownload", nenhum arquivo era registrado.
        if data.get("status") != "finished" or data.get("postprocessor") != _MOVE_FILES:
            return
        info = data.get("info_dict") or {}
        if info.get("filepath"):
            entry = (info.get("title") or "", Path(info["filepath"]))
            if entry not in self._finished_files:
                self._finished_files.append(entry)

    def _rename_downloads(
        self, summary: DownloadSummary, files: list[tuple[str, Path]],
        clip: str | None = None,
    ) -> list[tuple[str, Path]]:
        """Da a cada MP3 o nome da musica e leva as pendencias junto.

        O trecho mantem o sufixo: com so o nome da musica, ele tomaria o lugar
        do MP3 inteiro da mesma faixa.
        """
        moved: dict[str, str] = {}
        renamed = []
        for title, path in files:
            name = song_title(title or path.stem)
            try:
                final = rename_to_song(path, f"{name} ({clip})" if clip else name)
            except OSError:
                final = path
            moved[str(path)] = str(final)
            renamed.append((title, final))
        summary.metadata_pending_items = [
            replace(item, file_path=moved.get(item.file_path, item.file_path))
            for item in summary.metadata_pending_items
        ]
        return renamed

    def _record_history(self, file_format: str) -> None:
        if self._history_path is None or not self._finished_files:
            return
        now = datetime.now().isoformat(timespec="seconds")
        try:
            append_history(self._history_path, [
                HistoryEntry(title=title or path.stem, path=str(path),
                             kind=file_format, finished_at=now)
                for title, path in self._finished_files
            ])
        except OSError:
            # Historico e conveniencia: o arquivo ja esta na pasta.
            pass

    def _auto_apply_metadata(self, summary: DownloadSummary) -> None:
        """Aplica o candidato seguro de cada pendencia; o resto segue para a revisao."""
        pending = summary.metadata_pending_items
        remaining: list[MetadataPendingItem] = []
        for position, item in enumerate(pending, start=1):
            if self._cancel.is_set():
                remaining.extend(pending[position - 1:])
                break
            self._emit(
                "status",
                message=f"Conferindo metadata no catalogo ({position}/{len(pending)})...")
            try:
                candidate = confident_match(
                    item, self._metadata_service.search(search_suggestion(item)))
                if candidate is not None:
                    self._apply_candidate(item, candidate)
            except Exception:
                # Catalogo fora do ar em um item nao e falha de download: o item
                # so continua pendente, como estaria sem a aplicacao automatica.
                candidate = None
            if candidate is None:
                remaining.append(item)
            else:
                summary.metadata_auto_applied.append(f"{candidate.artist} — {candidate.title}")
        summary.metadata_pending_items = remaining

    @staticmethod
    def _url_is_playlist(url: str) -> bool:
        try:
            p = urlparse(url)
            return p.path.rstrip("/") == "/playlist" and bool(parse_qs(p.query).get("list"))
        except Exception:
            return False

    @staticmethod
    def _base_opts() -> dict[str, Any]:
        """O que a listagem e o download precisam igual para falar com o YouTube."""
        return {
            "quiet": True,
            "ignoreerrors": True,
            "remote_components": ["ejs:github"],
            "js_runtimes": js_runtimes_for(shutil.which),
        }

    def _extract_info(self, url: str, playlist_mode: bool) -> dict[str, Any]:
        self._emit("status", message="Analisando link...")
        opts = {
            **self._base_opts(),
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": not playlist_mode,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise ValueError("Nao foi possivel obter informacoes do link informado.")
        return info

    def _build_opts(
        self,
        target_dir: Path,
        file_format: str,
        playlist_mode: bool,
        summary: DownloadSummary,
        include_metadata: bool = False,
        options: MediaOptions | None = None,
    ) -> dict[str, Any]:
        options = options or MediaOptions()
        clip = options.is_clip and not playlist_mode
        if playlist_mode:
            outtmpl = str(target_dir / "%(playlist_title,playlist|Playlist)s" / "%(title)s.%(ext)s")
        elif clip:
            # Nome proprio: com o do arquivo inteiro, o `final_ext` acharia o
            # MP3 completo e nao baixaria o trecho.
            label = clip_label(options.clip_start, options.clip_end)
            outtmpl = str(target_dir / f"%(title)s ({label}).%(ext)s")
        else:
            outtmpl = str(target_dir / "%(title)s.%(ext)s")

        opts: dict[str, Any] = {
            **self._base_opts(),
            # A capa da propria playlist nao e de nenhuma faixa: gravada, sobrava
            # solta na pasta como "000 - Nome da playlist.jpg".
            "outtmpl": {"default": outtmpl, "pl_thumbnail": ""},
            "noplaylist": not playlist_mode,
            "progress_hooks": [self._make_progress_hook(summary, include_metadata)],
            "postprocessor_hooks": [self._record_final_file],
            "no_warnings": True,
            "no_color": True,
            "concurrent_fragment_downloads": 1,
            "logger": ReportingLogger(self._q, summary),
            # Sem isto o yt-dlp procura o .webm que a conversao ja apagou e
            # baixa de novo um MP3 que esta na pasta.
            "final_ext": file_format,
            "retry_sleep_functions": {"http": _retry_sleep, "fragment": _retry_sleep},
        }
        if playlist_mode:
            # Playlist grande em rajada e o que leva o YouTube a pedir login
            # "para confirmar que nao e um robo".
            opts.update({
                "download_archive": str(target_dir / ARCHIVE_FILENAME),
                "sleep_interval_requests": 1,
                "sleep_interval": 2,
                "max_sleep_interval": 5,
            })

        if clip:
            start = options.clip_start or 0.0
            stop = float("inf") if options.clip_end is None else options.clip_end
            opts["download_ranges"] = download_range_func(None, [(start, stop)])
            if is_precise(options.clip_start, options.clip_end):
                opts["force_keyframes_at_cuts"] = True

        # Segmentos do SponsorBlock vem na linha do tempo do video inteiro;
        # aplicados a um trecho, cortariam o lugar errado.
        sponsors = options.skip_sponsors and not clip
        sponsor_pps = [{
            "key": "SponsorBlock",
            "categories": list(SPONSOR_CATEGORIES),
            "api": "https://sponsor.ajay.app",
            "when": "after_filter",
        }] if sponsors else []
        # Antes de gravar metadados: os capitulos gravados tem de ser os do
        # arquivo ja sem os segmentos.
        remove_pps = [{
            "key": "ModifyChapters",
            "remove_sponsor_segments": list(SPONSOR_CATEGORIES),
            "remove_chapters_patterns": [],
            "remove_ranges": [],
            "sponsorblock_chapter_title": "[SponsorBlock]: %(category_names)l",
            "force_keyframes": False,
        }] if sponsors else []

        if file_format == "mp3":
            postprocessors = [*sponsor_pps, {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }, *remove_pps]
            if include_metadata and playlist_mode:
                postprocessors.append({
                    "key": "MetadataParser",
                    "when": "pre_process",
                    "actions": [(
                        MetadataParserPP.Actions.INTERPRET,
                        "playlist_index", "%(track_number)s",
                    )],
                })
            if include_metadata:
                postprocessors += [
                    {"key": "FFmpegMetadata", "add_metadata": True},
                    {"key": "EmbedThumbnail"},
                ]
            opts.update({
                "format": "bestaudio/best",
                "postprocessors": postprocessors,
            })
            if include_metadata:
                opts["writethumbnail"] = True
        else:
            postprocessors = [*sponsor_pps, *remove_pps]
            if options.subtitles:
                opts.update({
                    "writesubtitles": True,
                    "writeautomaticsub": True,
                    "subtitleslangs": list(_SUBTITLE_LANGS),
                })
                # False apaga o .vtt depois de embutir: nao sobra solto na pasta.
                postprocessors.append(
                    {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
            if options.chapters:
                postprocessors.append(
                    {"key": "FFmpegMetadata", "add_chapters": True, "add_metadata": False})
            opts.update({
                "format": video_format(options.quality),
                "merge_output_format": "mp4",
            })
            if postprocessors:
                opts["postprocessors"] = postprocessors

        return opts

    def _make_progress_hook(self, summary: DownloadSummary, include_metadata: bool = False):
        seen: set[str] = set()
        metadata_seen: set[str] = set()

        def hook(data: dict[str, Any]) -> None:
            if self._cancel.is_set():
                raise DownloadCancelled("Download cancelado.")
            status = data.get("status")
            info_dict = data.get("info_dict") or {}
            if _is_subtitle_download(info_dict):
                return
            title = info_dict.get("title") or data.get("filename") or "Arquivo"

            if status == "downloading":
                dl = data.get("downloaded_bytes", 0)
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                item_pct = (dl / total * 100) if total else 0

                if summary.total_items > 1:
                    overall = (summary.downloaded_count + item_pct / 100) / summary.total_items * 100
                else:
                    overall = item_pct

                # Contagem do que foi escolhido, nao a posicao na playlist: com
                # parte dela escolhida, "30/2" nao diz quanto falta.
                current = min(summary.downloaded_count + 1, summary.total_items)
                suffix = f" ({current}/{summary.total_items})" if summary.total_items > 1 else ""
                self._emit("progress", progress=max(0.0, min(overall, 100.0)),
                           message=f"Baixando: {title}{suffix}",
                           speed=data.get("speed"), eta=data.get("eta"))

            elif status == "finished":
                uid = info_dict.get("id") or data.get("filename") or title
                if uid not in seen:
                    seen.add(uid)
                    summary.downloaded_count += 1
                if include_metadata and uid not in metadata_seen:
                    metadata_seen.add(uid)
                    review_reasons = metadata_review_reasons(info_dict)
                    if review_reasons:
                        filename = data.get("filename") or info_dict.get("filepath") or ""
                        summary.metadata_pending_items.append(MetadataPendingItem(
                            title=title,
                            file_path=str(Path(filename).with_suffix(".mp3")) if filename else "",
                            review_reasons=review_reasons,
                            duration=info_dict.get("duration"),
                            source_artist=_source_artist(info_dict),
                        ))
                overall = (summary.downloaded_count / summary.total_items * 100
                           if summary.total_items else 100.0)
                self._emit("progress", progress=max(0.0, min(overall, 100.0)),
                           message=f"Processando: {title}")

        return hook

    def _ensure_output_dir(self, file_format: str, playlist_mode: bool) -> Path:
        path = DOWNLOAD_FOLDERS[(file_format, playlist_mode)]
        if self._downloads_dir is not None:
            path = self._downloads_dir / path.name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _is_playlist_result(info: dict[str, Any]) -> bool:
        return bool(info.get("entries")) or info.get("_type") == "playlist"

    @staticmethod
    def _reconcile_failure_reports(summary: DownloadSummary) -> None:
        """Remove diagnósticos do extrator quando o resultado final foi íntegro."""
        if summary.total_items and summary.downloaded_count >= summary.total_items:
            summary.failed_items.clear()
            summary.extractor_notices.clear()

    def _emit(self, event_type: str, **payload: Any) -> None:
        self._q.put({"type": event_type, **payload})
