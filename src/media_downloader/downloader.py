"""Download backend. Publishes events to the queue; never touches the interface."""

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

# How many failures the summary lists before switching to a count. `messagebox` does
# not scroll: a long list grows the box past the screen and hides its own button.
FAILURES_SHOWN = 5


def summary_lines(summary: DownloadSummary) -> list[str]:
    """Builds the final summary text.

    The summary answers "what happened?"; the review that opens right after
    answers "what should be fixed?", item by item with a cover preview. Repeating
    each pending item's detail here only made the box grow without saying anything
    new — for a seven-track playlist it already filled the whole screen.
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
        lines += [f"  - {notice}" for notice in summary.extractor_notices[:FAILURES_SHOWN]]
    if pending:
        lines += ["", f"A revisao abre em seguida, com {pending} item(s) para confirmar."]
    return lines



def folder_summary_lines(summary: DownloadSummary) -> list[str]:
    """Summary of a folder fix — nothing was downloaded, so it does not mention downloads."""
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

# One per destination folder: a video's MP3 must not block its MP4.
ARCHIVE_FILENAME = ".historico-de-downloads.txt"

# Since yt-dlp 2025.11 YouTube requires a JavaScript runtime, and only deno is
# enabled by default: with node installed and no deno, yt-dlp did not even try.
_JS_RUNTIME_PREFERENCE = ("deno", "node", "bun")


def js_runtimes_for(which: Callable[[str], str | None]) -> dict[str, dict[str, str]]:
    for name in _JS_RUNTIME_PREFERENCE:
        path = which(name)
        if path:
            return {name: {"path": path}}
    return {"deno": {}}


# Since late 2026 YouTube answers "Sign in to confirm you're not a bot" to anonymous
# requests from this connection; the cookies of a browser where the user is logged
# in get past it. The first installed browser wins (profile folder present).
_COOKIE_BROWSERS = (
    ("brave", ".config/BraveSoftware/Brave-Browser"),
    ("chrome", ".config/google-chrome"),
    ("chromium", ".config/chromium"),
    ("firefox", ".mozilla/firefox"),
)


def cookie_browser_for(home: Path) -> tuple[str] | None:
    for name, profile in _COOKIE_BROWSERS:
        if (home / profile).is_dir():
            return (name,)
    return None


_BEST_VIDEO = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
VIDEO_QUALITIES = ("best", "1080", "720", "480", "360")

# Sponsors, self-promotion, subscribe reminders, and the non-music part of a
# music video: what the user would skip by hand.
SPONSOR_CATEGORIES = ("sponsor", "selfpromo", "interaction", "music_offtopic")
_SUBTITLE_LANGS = ["pt.*", "en.*"]
_SUBTITLE_EXTS = {"vtt", "srt", "ass", "ssa", "ttml", "srv1", "srv2", "srv3", "json3", "lrc"}
_MOVE_FILES = MoveFilesAfterDownloadPP.pp_key()


def _is_subtitle_download(info_dict: dict[str, Any]) -> bool:
    """A subtitle goes through the hook as a separate download, with the subtitle's dict."""
    return not info_dict.get("id") and info_dict.get("ext") in _SUBTITLE_EXTS


def video_format(quality: str) -> str:
    """Height as a cap: with nothing below it, download whatever exists instead of failing."""
    if quality not in VIDEO_QUALITIES or quality == "best":
        return _BEST_VIDEO
    cap = f"[height<={quality}]"
    return f"bv*{cap}[ext=mp4]+ba[ext=m4a]/bv*{cap}+ba/b{cap}/b"


def transfer_text(speed: float | None, eta: float | None) -> str:
    """'4.2 MB/s · faltam 0:12' — only what yt-dlp reported."""
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
    """Same key yt-dlp writes to the download archive (`_make_archive_id`)."""
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
    """Selection rows, numbered by the position `playlist_items` understands.

    The `None` that ignoreerrors leaves in place of a broken item still takes up
    its position: skipping the count there would misalign every following index.
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
    """A listed playlist waiting for the user's selection to start."""
    url: str
    file_format: str
    include_metadata: bool
    auto_metadata: bool
    target_dir: Path
    entries: tuple[PlaylistEntry, ...]
    options: MediaOptions = field(default_factory=MediaOptions)


def _source_artist(info: dict[str, Any]) -> str | None:
    """The artist the source published — never the channel, which is provisional."""
    if info.get("artist"):
        return info["artist"]
    artists = info.get("artists") or []
    return artists[0] if artists else None


class _SilentLogger:
    """The preview reports nothing: the download, if it happens, reports for it."""

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
        # An extractor notice is not a failed item: the missing JavaScript
        # runtime warning shows up in downloads that complete fully.
        self._capture(msg, failure=False)

    def error(self, msg: str) -> None:
        self._capture(msg, failure=True)

    def _capture(self, msg: str, *, failure: bool) -> None:
        # yt-dlp output is coloured: without stripping the ANSI escape, the prefix does
        # not match and the summary shows "\x1b[0;31mERROR:\x1b[0m ..." as text.
        # Prefix without the space: the earlier .strip() already removed the space in
        # "ERROR: " when the message was empty, and "ERROR:" became an item.
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
        # Separate from download cancellation: stopping the batch in review must
        # not interrupt a download running at the same time.
        self._stop_bulk = threading.Event()
        # None keeps DOWNLOAD_FOLDERS as is (the repository's Downloads/).
        self._downloads_dir: Path | None = None
        self._listing: _PlaylistListing | None = None
        self._finished_files: list[tuple[str, Path]] = []
        self._history_path: Path | None = None

    def cancel(self) -> None:
        """Requests interruption; yt-dlp stops at the next progress update."""
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
        """Writes the tags and gives the file the track's official name."""
        path = Path(pending_item.file_path)
        cover_embedded = self._metadata_service.apply_to_mp3(path, candidate)
        try:
            rename_to_song(path, candidate.title)
        except OSError:
            # The tags are already written, which is what the user confirmed; the old
            # name is just less pretty, not a wrong file.
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
        """Without calling this, nothing goes into the history — tests do not pollute the config."""
        self._history_path = path

    def probe(self, url: str) -> None:
        """Link preview. A failure here is not a download error: there is just no preview."""
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
        """Base folder chosen by the user; the per-type subfolders stay the same."""
        self._downloads_dir = base

    def stop_bulk(self) -> None:
        self._stop_bulk.set()

    def apply_suggested(self, pending_items: list[MetadataPendingItem]) -> None:
        """Applies the app's suggestion to each item; items without one stay in review."""
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
                # Catalog down or a locked file for one item: it just stays in
                # review and the batch continues — but counted separately, because
                # "no suggestion" and "not queried" call for different responses.
                failed += 1
                continue
            applied += 1
            self._emit("metadata_bulk_applied", pending_item=item, candidate=candidate)
        self._emit(
            "metadata_bulk_done", applied=applied, kept=len(pending_items) - applied,
            failed=failed)

    def crop_covers(self, pending_items: list[MetadataPendingItem]) -> None:
        """Crops the embedded thumbnail to a square — only at the user's request."""
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
        """Puts MP3 files already on disk through the same matching and review as downloads."""
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
                # No reason at all means a complete file: the user asked for it to be left alone.
                if item.review_reasons:
                    summary.metadata_pending_items.append(item)
                else:
                    summary.already_complete_count += 1
            summary.total_items = len(paths)
            if auto_metadata:
                self._auto_apply_metadata(summary)
            # Items missing something come before those that only need checking.
            summary.metadata_pending_items.sort(key=lambda item: not item.review_reasons)
            self._emit("folder_checked", summary=summary)
        except Exception as exc:
            self._emit("error", message=str(exc))

    def download(
        self, url: str, file_format: str,
        include_metadata: bool = False, auto_metadata: bool = False,
        options: MediaOptions | None = None,
    ) -> None:
        """A single item downloads directly; a playlist is listed and waits for `download_selected`."""
        options = options or MediaOptions()
        self._reset_cancel()
        self._listing = None
        try:
            playlist_mode = self._url_is_playlist(url)
            info = self._extract_info(url, playlist_mode)

            if playlist_mode and not self._is_playlist_result(info):
                playlist_mode = False
            if self._cancel.is_set():
                # Listing a large playlist takes a while; whoever cancelled during it
                # does not expect the selection dialog to open right after.
                self._emit("cancelled", summary=DownloadSummary(playlist_mode=playlist_mode))
                return

            target_dir = self._ensure_output_dir(file_format, playlist_mode)
            if playlist_mode:
                entries = playlist_entries(info, archive_ids(target_dir / ARCHIVE_FILENAME))
                # A section does not apply to playlists: each item has its own duration.
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
        # Requesting something already downloaded again is intentional: without
        # forgetting it, the archive would make yt-dlp skip the item silently.
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
        """Records where each file ended up, after conversion and moving."""
        # The published name is the `pp_key()` ("MoveFiles"), not the class name: compared
        # with "MoveFilesAfterDownload", no file was ever recorded.
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
        """Gives each MP3 the song name and carries the pending items along.

        A section keeps its suffix: with only the song name, it would replace
        the full MP3 of the same track.
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
            # History is a convenience: the file is already in the folder.
            pass

    def _auto_apply_metadata(self, summary: DownloadSummary) -> None:
        """Applies the confident candidate for each pending item; the rest go to review."""
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
                # The catalog being down for one item is not a download failure: the
                # item just stays pending, as it would without automatic application.
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
        """What listing and downloading both need to talk to YouTube."""
        return {
            "quiet": True,
            "ignoreerrors": True,
            "remote_components": ["ejs:github"],
            "js_runtimes": js_runtimes_for(shutil.which),
            **({"cookiesfrombrowser": browser} if (browser := cookie_browser_for(Path.home())) else {}),
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
            # Its own name: with the full file's name, `final_ext` would find the
            # full MP3 and not download the section.
            label = clip_label(options.clip_start, options.clip_end)
            outtmpl = str(target_dir / f"%(title)s ({label}).%(ext)s")
        else:
            outtmpl = str(target_dir / "%(title)s.%(ext)s")

        opts: dict[str, Any] = {
            **self._base_opts(),
            # The playlist's own cover belongs to no track: written, it was left
            # loose in the folder as "000 - Nome da playlist.jpg".
            "outtmpl": {"default": outtmpl, "pl_thumbnail": ""},
            "noplaylist": not playlist_mode,
            "progress_hooks": [self._make_progress_hook(summary, include_metadata)],
            "postprocessor_hooks": [self._record_final_file],
            "no_warnings": True,
            "no_color": True,
            "concurrent_fragment_downloads": 1,
            "logger": ReportingLogger(self._q, summary),
            # Without this yt-dlp looks for the .webm that conversion already deleted
            # and downloads again an MP3 that is in the folder.
            "final_ext": file_format,
            "retry_sleep_functions": {"http": _retry_sleep, "fragment": _retry_sleep},
        }
        if playlist_mode:
            # A large playlist in a burst is what makes YouTube ask for a login
            # "to confirm you're not a bot".
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

        # SponsorBlock segments come in the full video's timeline;
        # applied to a section, they would cut the wrong place.
        sponsors = options.skip_sponsors and not clip
        sponsor_pps = [{
            "key": "SponsorBlock",
            "categories": list(SPONSOR_CATEGORIES),
            "api": "https://sponsor.ajay.app",
            "when": "after_filter",
        }] if sponsors else []
        # Before writing metadata: the chapters written must be those of the
        # file with the segments already removed.
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
                # False deletes the .vtt after embedding: nothing is left loose in the folder.
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

                # A count of what was chosen, not the playlist position: with only
                # part of it chosen, "30/2" does not say how much is left.
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
        """Removes extractor diagnostics when the final result was complete."""
        if summary.total_items and summary.downloaded_count >= summary.total_items:
            summary.failed_items.clear()
            summary.extractor_notices.clear()

    def _emit(self, event_type: str, **payload: Any) -> None:
        self._q.put({"type": event_type, **payload})
