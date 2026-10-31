"""Configuration constants: paths, external services, and limits."""

from pathlib import Path

APP_TITLE = "Media Downloader"
# config.py → media_downloader → src → root. The level count changes if this
# file moves, and the symptom is silent: the icon disappears and downloads land
# in the wrong folder, with no error until the window tries to open.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DOWNLOADS_DIR = _REPO_ROOT / "Downloads"
ASSETS_DIR = _REPO_ROOT / "assets"
APP_ICON_PATH = ASSETS_DIR / "media-downloader-icon.png"
APP_ICON_ICO_PATH = ASSETS_DIR / "media-downloader-icon.ico"
SUPPORTED_SITES_URL = "https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_ARTWORK_SIZE = 600
CATALOG_USER_AGENT = "MediaDownloader/1.0 (metadata lookup)"
# ~20 requests per minute is what iTunes tolerates before answering 403.
CATALOG_MIN_INTERVAL_SECONDS = 3.0
COVER_PREVIEW_SIZE = 88
CATALOG_RESULTS_SHOWN = 5
SUPPORTED_PLATFORM_NAMES = (
    "YouTube", "Vimeo", "TikTok", "Instagram", "SoundCloud", "Dailymotion",
)
DOWNLOAD_FOLDERS = {
    ("mp3", False): BASE_DOWNLOADS_DIR / "audios_unicos",
    ("mp4", False): BASE_DOWNLOADS_DIR / "videos_unicos",
    ("mp3", True):  BASE_DOWNLOADS_DIR / "playlist_audio",
    ("mp4", True):  BASE_DOWNLOADS_DIR / "playlist_video",
}
