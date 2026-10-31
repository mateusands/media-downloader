"""A section of a video: parsing the typed time, validation, and the file name.

Pure on purpose — neither yt-dlp nor widgets — so the screen validates while the
user types and the backend builds the options with the same rule.
"""

import re
from urllib.parse import parse_qs, urlparse

# Up to this length the cut re-encodes to land on the exact frame. Beyond it, that
# would mean re-encoding a film, and cancellation cannot reach FFmpeg midway.
PRECISE_CUT_SECONDS = 180

_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
_INTEGER = re.compile(r"^\d+$")
_YOUTUBE_T = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$")


def parse_time(text: str) -> float | None:
    """'90', '1:30', '1:02:03', '90,5' in seconds; empty means no limit."""
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"Tempo invalido: {text}")
    *bigger, last = parts
    if not _NUMBER.match(last) or not all(_INTEGER.match(p) for p in bigger):
        raise ValueError(f"Tempo invalido: {text}")
    seconds = float(last.replace(",", "."))
    if bigger and seconds >= 60:
        raise ValueError(f"Tempo invalido: {text}")
    if len(bigger) == 2 and int(bigger[1]) >= 60:
        raise ValueError(f"Tempo invalido: {text}")
    total = seconds
    for multiplier, value in zip((60, 3600), reversed(bigger)):
        total += int(value) * multiplier
    return total


def format_time(seconds: float) -> str:
    whole = int(seconds)
    fraction = round(seconds - whole, 2)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    text = f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
    if fraction:
        text += f"{fraction:.2f}".lstrip("0").rstrip("0")
    return text


def clip_error(start: float | None, end: float | None, duration: float | None) -> str | None:
    begin = start or 0.0
    if end is not None and end <= begin:
        return "O fim precisa ser depois do início."
    if duration:
        if begin >= duration:
            return "O início passa da duração do vídeo."
        if end is not None and end > duration:
            return "O fim passa da duração do vídeo."
    return None


def youtube_start_hint(url: str) -> float | None:
    """The `t=` of a shared link: '90', '90s', '1m30s', '1h2m3s'."""
    try:
        value = parse_qs(urlparse(url).query).get("t", [""])[0]
    except ValueError:
        return None
    match = _YOUTUBE_T.match(value)
    if not value or not match:
        return None
    hours, minutes, secs = (int(group or 0) for group in match.groups())
    return float(hours * 3600 + minutes * 60 + secs)


def _label_time(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def clip_label(start: float | None, end: float | None) -> str:
    """Goes into the file name: no ':' (Windows) and no '%' (yt-dlp template)."""
    stop = "fim" if end is None else _label_time(end)
    return f"trecho {_label_time(start or 0)}-{stop}"


def is_precise(start: float | None, end: float | None) -> bool:
    if end is None:
        return False
    return 0 < end - (start or 0.0) <= PRECISE_CUT_SECONDS
