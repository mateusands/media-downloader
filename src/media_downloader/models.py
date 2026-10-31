"""Immutable data that crosses the queue between backend and interface."""

import re
from dataclasses import dataclass, field
from typing import Any

from .config import ITUNES_ARTWORK_SIZE

@dataclass
class DownloadSummary:
    downloaded_count: int = 0
    failed_items: list[str] = field(default_factory=list)
    extractor_notices: list[str] = field(default_factory=list)
    metadata_pending_items: list["MetadataPendingItem"] = field(default_factory=list)
    metadata_auto_applied: list[str] = field(default_factory=list)
    already_downloaded_count: int = 0
    # Folder fix: MP3 files with title, artist, album, and square cover art, left untouched.
    already_complete_count: int = 0
    total_items: int = 0
    target_dir: str = ""
    playlist_mode: bool = False
    # Final paths — after conversion, the song name, and the section suffix.
    finished_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MediaOptions:
    """What the user chose besides the format. A section applies only outside playlists."""
    quality: str = "best"
    clip_start: float | None = None
    clip_end: float | None = None
    subtitles: bool = False
    chapters: bool = False
    skip_sponsors: bool = False

    @property
    def is_clip(self) -> bool:
        return self.clip_end is not None or bool(self.clip_start)


@dataclass(frozen=True)
class MediaPreview:
    """What the link is, before downloading: feeds the section ruler and the qualities."""
    url: str
    title: str
    uploader: str | None = None
    duration: float | None = None
    is_playlist: bool = False
    item_count: int = 0
    heights: tuple[int, ...] = ()
    start_hint: float | None = None
    thumbnail: bytes | None = None


@dataclass(frozen=True)
class MetadataPendingItem:
    title: str
    file_path: str
    review_reasons: tuple[str, ...]
    # What automatic matching compares against the catalog.
    duration: float | None = None
    source_artist: str | None = None


@dataclass(frozen=True)
class PlaylistEntry:
    """One row of the item selection: original playlist position and history."""
    index: int
    title: str
    archive_id: str | None
    duration: float | None
    already_downloaded: bool


@dataclass(frozen=True)
class EmbeddedMetadata:
    """What is already written in the MP3 — read from the file, not inferred."""
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    cover: bytes | None = None


@dataclass(frozen=True)
class MusicSearchSuggestion:
    title: str
    artist: str | None = None


# Artwork comes as a 100 px thumbnail and the size is part of the URL; the extension
# varies from result to result, so it cannot be hardcoded as ".jpg".
_ARTWORK_SIZE_SUFFIX = re.compile(r"/\d+x\d+bb\.(jpg|png)$", flags=re.IGNORECASE)


def _artwork_at(url: str, size: int) -> str:
    return _ARTWORK_SIZE_SUFFIX.sub(lambda m: f"/{size}x{size}bb.{m.group(1)}", url)


@dataclass(frozen=True)
class MusicMetadataCandidate:
    track_id: str
    title: str
    artist: str
    album: str | None
    year: str | None
    artwork_url: str | None
    duration_seconds: float | None = None

    @classmethod
    def from_itunes(cls, result: dict[str, Any]) -> "MusicMetadataCandidate":
        artwork = result.get("artworkUrl100")
        return cls(
            track_id=str(result.get("trackId", "")),
            title=result.get("trackName") or "Faixa sem titulo",
            artist=result.get("artistName") or "Artista desconhecido",
            album=result.get("collectionName"),
            year=(result.get("releaseDate") or "")[:4] or None,
            artwork_url=_artwork_at(artwork, ITUNES_ARTWORK_SIZE) if artwork else None,
            duration_seconds=(
                result["trackTimeMillis"] / 1000 if result.get("trackTimeMillis") else None),
        )
