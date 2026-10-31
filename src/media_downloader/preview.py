"""Link summary before downloading — pure, built from what yt-dlp extracted."""

from typing import Any
from urllib.request import Request, urlopen

from .clip import youtube_start_hint
from .config import CATALOG_USER_AGENT
from .models import MediaPreview

# A maxres YouTube thumbnail is over 200 KB; anything above this is not a thumbnail.
_THUMBNAIL_MAX_BYTES = 4 * 1024 * 1024


def available_heights(info: dict[str, Any]) -> tuple[int, ...]:
    heights = {
        f["height"] for f in info.get("formats") or []
        if f.get("height") and f.get("vcodec") not in (None, "none")
    }
    return tuple(sorted(heights, reverse=True))


def preview_from_info(url: str, info: dict[str, Any]) -> MediaPreview:
    is_playlist = bool(info.get("entries")) or info.get("_type") == "playlist"
    return MediaPreview(
        url=url,
        title=info.get("title") or info.get("id") or "Sem titulo",
        uploader=info.get("uploader") or info.get("channel"),
        duration=None if is_playlist else info.get("duration"),
        is_playlist=is_playlist,
        item_count=sum(1 for entry in info.get("entries") or [] if entry),
        heights=() if is_playlist else available_heights(info),
        start_hint=None if is_playlist else youtube_start_hint(url),
    )


def thumbnail_url(info: dict[str, Any]) -> str | None:
    if info.get("thumbnail"):
        return info["thumbnail"]
    # A playlist listed without resolving its items only has thumbnails in the list.
    thumbnails = info.get("thumbnails") or []
    return thumbnails[-1].get("url") if thumbnails else None


def fetch_thumbnail(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": CATALOG_USER_AGENT})
    with urlopen(request, timeout=10) as response:
        return response.read(_THUMBNAIL_MAX_BYTES)
