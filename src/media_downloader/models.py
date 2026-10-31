"""Dados imutaveis que atravessam a fila entre backend e interface."""

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
    # Correcao de pasta: MP3 com titulo, artista, album e capa quadrada, que nao se toca.
    already_complete_count: int = 0
    total_items: int = 0
    target_dir: str = ""
    playlist_mode: bool = False
    # Caminhos finais — depois da conversao, do nome da musica e do sufixo de trecho.
    finished_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MediaOptions:
    """O que a pessoa escolheu alem do formato. Trecho so vale fora de playlist."""
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
    """O que o link e, antes de baixar: alimenta a regua do trecho e as qualidades."""
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
    # O que a correspondencia automatica compara com o catalogo.
    duration: float | None = None
    source_artist: str | None = None


@dataclass(frozen=True)
class PlaylistEntry:
    """Uma linha da escolha de itens: posicao original na playlist e historico."""
    index: int
    title: str
    archive_id: str | None
    duration: float | None
    already_downloaded: bool


@dataclass(frozen=True)
class EmbeddedMetadata:
    """O que já está gravado no MP3 — lido do arquivo, não inferido."""
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    cover: bytes | None = None


@dataclass(frozen=True)
class MusicSearchSuggestion:
    title: str
    artist: str | None = None


# A arte vem em miniatura de 100 px e o tamanho faz parte da URL; a extensão
# muda de resultado para resultado, então não pode ser fixada em ".jpg".
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
