"""Constantes de configuracao: caminhos, servicos externos e limites."""

from pathlib import Path

APP_TITLE = "Media Downloader"
# config.py → media_downloader → src → raiz. A contagem de níveis muda se este
# arquivo mudar de lugar, e o sintoma é silencioso: o ícone some e os downloads
# caem na pasta errada, sem nenhum erro até a janela tentar abrir.
_RAIZ_DO_REPOSITORIO = Path(__file__).resolve().parent.parent.parent
BASE_DOWNLOADS_DIR = _RAIZ_DO_REPOSITORIO / "Downloads"
ASSETS_DIR = _RAIZ_DO_REPOSITORIO / "assets"
APP_ICON_PATH = ASSETS_DIR / "media-downloader-icon.png"
APP_ICON_ICO_PATH = ASSETS_DIR / "media-downloader-icon.ico"
SUPPORTED_SITES_URL = "https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_ARTWORK_SIZE = 600
CATALOG_USER_AGENT = "MediaDownloader/1.0 (metadata lookup)"
# ~20 pedidos por minuto e o que o iTunes tolera antes de responder 403.
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
