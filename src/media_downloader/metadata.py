"""Music catalog and file tags — knows nothing about widgets or the queue."""

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mutagen import MutagenError
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1

from .config import (
    CATALOG_MIN_INTERVAL_SECONDS,
    CATALOG_RESULTS_SHOWN,
    CATALOG_USER_AGENT,
    ITUNES_SEARCH_URL,
)
from .models import (
    EmbeddedMetadata,
    MetadataPendingItem,
    MusicMetadataCandidate,
    MusicSearchSuggestion,
)

_PROMOTIONAL_SUFFIX = re.compile(
    r"\s*[\[(](?:official\s+(?:music\s+)?video|official\s+audio|lyrics?|hd|4k)[\])]\s*$",
    flags=re.IGNORECASE,
)


# Hyphen, en dash, and em dash: Queen's official video uses "–".
_ARTIST_SEPARATOR = re.compile(r"\s+[-–—]\s+")


def suggest_music_search(source_title: str) -> MusicSearchSuggestion:
    """Builds a catalog query without turning inference into metadata."""
    cleaned = _PROMOTIONAL_SUFFIX.sub("", source_title).strip()
    parts = _ARTIST_SEPARATOR.split(cleaned, maxsplit=1)
    if len(parts) < 2:
        return MusicSearchSuggestion(title=cleaned)
    artist, title = (part.strip() for part in parts)
    if artist and title:
        return MusicSearchSuggestion(title=title, artist=artist)
    return MusicSearchSuggestion(title=cleaned)


def metadata_review_reasons(info: dict[str, Any]) -> tuple[str, ...]:
    """Describes what yt-dlp actually wrote, without calling missing what exists.

    FFmpegMetadata uses artist/artists/creator/creators and, when all are missing,
    falls back to the channel name. An ordinary YouTube video never has `artist`, so
    the MP3 ends up with the channel as its artist — provisional, not missing.
    """
    reasons = []
    if not any(info.get(key) for key in ("artist", "artists", "creator", "creators")):
        channel = info.get("uploader") or info.get("uploader_id")
        reasons.append(
            f"artista provisorio: canal {channel}" if channel else "artista ausente")
    cover = _cover_reason(info)
    if cover:
        reasons.append(cover)
    return tuple(reasons)


# Album art is square. A YouTube thumbnail is 16:9 — a video frame — and
# EmbedThumbnail writes it as is. The margin is wide because the catalog's own art
# is not always exact ("Transit of Venus" comes as 600x538, 1.12); what must be
# kept out is video, and the narrowest of those (4:3) is already 1.33.
SQUARE_RATIO = (0.8, 1.25)


def _cover_reason(info: dict[str, Any]) -> str | None:
    """Says whether the embedded cover settles it or is just the source's thumbnail."""
    if not (info.get("thumbnail") or info.get("thumbnails")):
        return "capa ausente"
    measured = [
        t for t in (info.get("thumbnails") or [])
        if t.get("width") and t.get("height")
    ]
    if not measured:
        # Without dimensions nothing can be asserted: the diagnosis describes what
        # was measured, never what is assumed.
        return None
    largest = max(measured, key=lambda t: t["width"])
    ratio = largest["width"] / largest["height"]
    if SQUARE_RATIO[0] <= ratio <= SQUARE_RATIO[1]:
        return None
    return "capa provisoria: miniatura do video"


def metadata_review_detail(pending_item: MetadataPendingItem) -> str:
    """Explains the review without mistaking a search suggestion for a missing tag."""
    suggestion = suggest_music_search(pending_item.title)
    parts = list(pending_item.review_reasons)
    if suggestion.artist:
        parts.append(f"titulo sugere {suggestion.artist} — {suggestion.title}")
    parts.append("confirme um resultado no catalogo")
    detail = " · ".join(parts)
    return detail[:1].upper() + detail[1:]


class MusicMetadataService:
    """Minimal catalog client; knows nothing about files or widgets."""

    def __init__(
        self,
        fetch_json: Callable[[str], dict[str, Any]] | None = None,
        fetch_cover: Callable[[str], tuple[bytes, str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._fetch_json = fetch_json or self._request_json
        self._fetch_cover = fetch_cover or self._request_cover
        self._sleep = sleep
        self._last_request_at = 0.0

    def search(self, suggestion: MusicSearchSuggestion) -> list[MusicMetadataCandidate]:
        # Free text: iTunes has no search syntax, and the order it returns
        # already puts the studio album ahead of live recordings.
        term = " ".join(part for part in (suggestion.artist, suggestion.title) if part)
        query = urlencode({
            "term": term, "media": "music", "entity": "song",
            "limit": CATALOG_RESULTS_SHOWN,
        })
        payload = self._fetch_with_backoff(f"{ITUNES_SEARCH_URL}?{query}")
        candidates = [
            MusicMetadataCandidate.from_itunes(result)
            for result in payload.get("results", [])
        ]
        return candidates[:CATALOG_RESULTS_SHOWN]

    # iTunes accepts about 20 requests per minute and, past that, answers 403
    # (sometimes 429) for a while. When fixing a whole folder, giving up on the first
    # 403 left obvious tracks in review — the limit passes, the request does not.
    _RATE_LIMIT_CODES = (403, 429)
    _BACKOFF_SECONDS = (15.0, 30.0, 60.0)

    def _fetch_with_backoff(self, url: str) -> dict[str, Any]:
        for wait in self._BACKOFF_SECONDS:
            try:
                return self._fetch_json(url)
            except HTTPError as exc:
                if exc.code not in self._RATE_LIMIT_CODES:
                    raise
                self._sleep(wait)
        return self._fetch_json(url)

    def get_cover_preview(
        self, candidate: MusicMetadataCandidate,
    ) -> tuple[bytes, str] | None:
        if not candidate.artwork_url:
            return None
        try:
            return self._fetch_cover(candidate.artwork_url)
        except Exception:
            return None

    def _request_json(self, url: str) -> dict[str, Any]:
        wait = CATALOG_MIN_INTERVAL_SECONDS - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        request = Request(url, headers={"User-Agent": CATALOG_USER_AGENT, "Accept": "application/json"})
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
        self._last_request_at = time.monotonic()
        return payload

    def read_embedded(self, file_path: Path) -> EmbeddedMetadata:
        """Reads the tags already written — the preview shows the file, not an assumption."""
        # A file that disappeared is not a file without tags: returning empty metadata
        # here would make the interface announce "artista gravado: nenhum" for something that does not exist.
        if not file_path.name or not file_path.is_file():
            raise FileNotFoundError(f"MP3 nao encontrado: {file_path}")
        try:
            tags = ID3(file_path)
        except (ID3NoHeaderError, MutagenError):
            return EmbeddedMetadata()
        covers = tags.getall("APIC")
        return EmbeddedMetadata(
            artist=self._first_text(tags, "TPE1"),
            title=self._first_text(tags, "TIT2"),
            album=self._first_text(tags, "TALB"),
            cover=covers[0].data if covers else None,
        )

    @staticmethod
    def _first_text(tags: ID3, frame_id: str) -> str | None:
        frames = tags.getall(frame_id)
        if not frames or not frames[0].text:
            return None
        return str(frames[0].text[0]) or None

    def apply_to_mp3(self, file_path: Path, candidate: MusicMetadataCandidate) -> bool:
        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()

        for frame_id in ("TIT2", "TPE1", "TALB", "TDRC", "APIC"):
            tags.delall(frame_id)
        tags.add(TIT2(encoding=3, text=candidate.title))
        tags.add(TPE1(encoding=3, text=candidate.artist))
        if candidate.album:
            tags.add(TALB(encoding=3, text=candidate.album))
        if candidate.year:
            tags.add(TDRC(encoding=3, text=candidate.year))

        cover_embedded = False
        cover = self.get_cover_preview(candidate)
        if cover:
            data, mime = cover
            tags.add(APIC(encoding=3, mime=mime, type=3, desc="Capa", data=data))
            cover_embedded = True
        tags.save(file_path)
        return cover_embedded

    @staticmethod
    def _request_cover(artwork_url: str) -> tuple[bytes, str]:
        request = Request(artwork_url, headers={"User-Agent": CATALOG_USER_AGENT})
        with urlopen(request, timeout=10) as response:
            return response.read(), response.headers.get_content_type()


def search_suggestion(pending_item: MetadataPendingItem) -> MusicSearchSuggestion:
    """Search for a pending item: the title wins; the source fills in a missing artist."""
    suggestion = suggest_music_search(pending_item.title)
    if suggestion.artist or not pending_item.source_artist:
        return suggestion
    # The source credits every composer ("Jeremy Renner, Brandon Sammons,
    # ..."), and with the full credit the iTunes search comes back empty.
    artist = pending_item.source_artist.split(",")[0].strip() or pending_item.source_artist
    return MusicSearchSuggestion(title=suggestion.title, artist=artist)


# Duration is what separates the studio track from the live one, the remix, and the
# extended version — which iTunes returns with the same name and the same artist.
# The official video usually has a few seconds of intro more than the track.
_DURATION_TOLERANCE_SECONDS = 5.0
_PARENTHETICAL = re.compile(r"\s*[\[(]([^\])]*)[\])]")
_REMASTER_SUFFIX = re.compile(r"\s+[-–—]\s+[^-–—]*remaster[^-–—]*$")
# A suffix that changes the audio stays in the title: ignoring "(Live At Wembley)"
# would leave only the duration separating the concert from the studio track.
_VERSION_WORDS = re.compile(
    r"\b(?:live|ao vivo|en vivo|remix|mix|acoustic|acustic[oa]|unplugged|"
    r"version|versao|edit|instrumental|karaoke|cover|demo|sped up|slowed|"
    r"a ?cc?apella)\b")


# "(Album Version)" is the studio track itself, despite the "version".
_STUDIO_VERSION = re.compile(r"^\s*(?:album|lp|original)\s+version\s*$")


def _drop_harmless_parenthetical(match: re.Match) -> str:
    group = match.group(1)
    if _STUDIO_VERSION.match(group) or not _VERSION_WORDS.search(group):
        return ""
    return f" {group} "
_FEATURING = re.compile(r"\s+(?:feat|ft|featuring)\b.*$")
_NON_WORD = re.compile(r"[^\w]+")


def _normalized_words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _REMASTER_SUFFIX.sub("", folded)
    folded = _FEATURING.sub("", _PARENTHETICAL.sub(_drop_harmless_parenthetical, folded))
    words = _NON_WORD.sub(" ", folded).split()
    return words[1:] if words[:1] == ["the"] and len(words) > 1 else words


def _same_title(a: str, b: str) -> bool:
    return _normalized_words(a) == _normalized_words(b)


def _same_artist(source: str, catalog: str) -> bool:
    # A joint credit ("Queen & David Bowie") contains the main artist as a
    # word sequence; loose containment would match "Kiss" with "Kissin' Dynamite".
    wanted, found = _normalized_words(source), _normalized_words(catalog)
    if not wanted:
        return False
    return any(found[i:i + len(wanted)] == wanted for i in range(len(found) - len(wanted) + 1))


def confident_match(
    pending_item: MetadataPendingItem, candidates: list[MusicMetadataCandidate],
) -> MusicMetadataCandidate | None:
    """The candidate that needs no review: artist, title, and duration all match."""
    suggestion = search_suggestion(pending_item)
    if not suggestion.artist or pending_item.duration is None:
        return None
    for candidate in candidates:
        if (candidate.duration_seconds is not None
                and abs(candidate.duration_seconds - pending_item.duration)
                <= _DURATION_TOLERANCE_SECONDS
                and _same_artist(suggestion.artist, candidate.artist)
                and _same_title(suggestion.title, candidate.title)):
            return candidate
    return None



def suggested_match(
    pending_item: MetadataPendingItem, candidates: list[MusicMetadataCandidate],
) -> MusicMetadataCandidate | None:
    """What the app suggests when the user asks to apply to all.

    Exists only behind the batch-apply button — the click is the authorization.
    Relaxes only the duration, which separates a video with an intro from the album
    track; artist and title (including the version) remain required.
    """
    safe = confident_match(pending_item, candidates)
    if safe is not None:
        return safe
    suggestion = search_suggestion(pending_item)
    if not suggestion.artist:
        return None
    for candidate in candidates:
        if (_same_artist(suggestion.artist, candidate.artist)
                and _same_title(suggestion.title, candidate.title)):
            return candidate
    return None

# What describes the video rather than the song: "(Official Music Video)", "[HD]".
_PACKAGING_WORDS = re.compile(
    r"\b(?:official|oficial|video|clipe|audio|lyrics?|letra|hd|4k|visuali[sz]er|"
    r"remaster(?:ed)?|mv)\b")
_TRAILING_GROUP = re.compile(r"\s*[\[(]([^\])]*)[\])]\s*$")
_TRAILING_FEATURING = re.compile(r"\s+(?:feat|ft|featuring)\b\.?\s.*$", flags=re.IGNORECASE)


def _folded(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _is_packaging(group: str) -> bool:
    inner = _folded(group).strip()
    # "(from One Night Only! ... Live at ...)" cites the source: the "live" there is
    # the concert the video came from, not a version that must stay in the name.
    if inner.startswith("from "):
        return True
    return bool(_PACKAGING_WORDS.search(inner)) and not _VERSION_WORDS.search(inner)


def song_title(source_title: str) -> str:
    """Only the song name: no artist and none of the video's packaging."""
    title = suggest_music_search(source_title).title
    while (match := _TRAILING_GROUP.search(title)) and _is_packaging(match.group(1)):
        title = title[:match.start()]
    title = _TRAILING_FEATURING.sub("", title).strip()
    return title or source_title.strip()


_FORBIDDEN_IN_FILENAME = str.maketrans({
    "/": "-", "\\": "-", "|": "-", ":": " -",
    "*": "", "?": "", '"': "", "<": "", ">": "",
})
_MAX_STEM = 150


def safe_file_stem(name: str) -> str:
    """A name accepted on Linux, Windows, and macOS."""
    stem = " ".join(name.translate(_FORBIDDEN_IN_FILENAME).split())
    # Windows rejects names ending in a dot or a space.
    stem = stem[:_MAX_STEM].rstrip(" .")
    return stem or "Faixa"


def rename_to_song(path: Path, name: str) -> Path:
    """Renames to `name` without overwriting another song with the same name."""
    stem = safe_file_stem(name)
    target = path.with_name(f"{stem}{path.suffix}")
    copy = 2
    while target != path and target.exists() and not target.samefile(path):
        target = path.with_name(f"{stem} ({copy}){path.suffix}")
        copy += 1
    if target == path:
        return path
    path.rename(target)
    return target
