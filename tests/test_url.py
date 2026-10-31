"""
SDD — Spec: URL validation and classification

CONTRACT
  `MediaDownloaderApp._valid_url(url)` decides whether the download button can
  be activated. Since the app delegates platform compatibility to yt-dlp, it
  accepts well-formed HTTP/HTTPS links from any domain and leaves real support
  validation to extraction. `DownloadManager._url_is_playlist` keeps the
  explicit detection of YouTube playlists, which decides the mode (playlist or
  single item), the destination folder, and yt-dlp's `noplaylist`.

WHY IT EXISTS
  The product is no longer YouTube-specific: blocking Vimeo, TikTok, or other
  platforms supported by yt-dlp contradicts its purpose. Even so, the YouTube
  playlist distinction is subtle: a video link that IS inside a playlist
  (`/watch?v=...&list=...`) is a valid URL but is NOT playlist mode — the path
  must be exactly `/playlist`. Getting this wrong makes the app download the
  whole playlist when the user wanted a single video.

BUSINESS RULES
  - Any HTTP/HTTPS URL with a host is accepted; this does not guarantee that
    the site or content is supported, accessible, or available.
  - Playlist mode requires the `/playlist` path AND the `list` parameter.
  - `_valid_url` validates FORMAT, never the video's existence or accessibility.
"""

import pytest

from media_downloader.config import SUPPORTED_PLATFORM_NAMES
from media_downloader.downloader import DownloadManager
from media_downloader.window import MediaDownloaderApp

is_valid = MediaDownloaderApp._valid_url
is_playlist = DownloadManager._url_is_playlist


class TestUrlValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/abc123",
            "https://www.youtube.com/playlist?list=PLabc",
            "http://www.youtube.com/watch?v=abc",
        ],
    )
    def test_should_accept_when_the_link_is_well_formed_http_or_https(self, url):
        assert is_valid(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://vimeo.com/76979871",
            "https://www.tiktok.com/@usuario/video/123456789",
            "https://soundcloud.com/artista/faixa",
            "https://www.dailymotion.com/video/x123abc",
        ],
    )
    def test_should_accept_when_the_link_is_from_another_compatible_platform(self, url):
        assert is_valid(url) is True

    @pytest.mark.parametrize(
        "url, reason",
        [
            ("", "empty string"),
            ("abc", "not a URL"),
            ("ftp://www.youtube.com/watch?v=abc", "scheme is not http/https"),
            ("https:///video", "missing host"),
        ],
    )
    def test_should_reject_when_the_link_is_invalid(self, url, reason):
        assert is_valid(url) is False, f"should reject: {reason}"


class TestPlaylistDetection:
    def test_should_detect_playlist_when_the_path_is_playlist_and_list_is_present(self):
        assert is_playlist("https://www.youtube.com/playlist?list=PLabc") is True

    def test_should_detect_playlist_even_with_a_trailing_slash(self):
        assert is_playlist("https://www.youtube.com/playlist/?list=PLabc") is True

    def test_should_not_detect_playlist_when_it_is_a_video_inside_a_playlist(self):
        # The costliest distinction: the link has `list=`, but the user asked for ONE video.
        assert is_playlist("https://www.youtube.com/watch?v=abc&list=PLabc") is False

    def test_should_not_detect_playlist_when_the_list_parameter_is_missing(self):
        assert is_playlist("https://www.youtube.com/playlist") is False

    @pytest.mark.parametrize("url", ["", "abc", "não é url"])
    def test_should_return_false_when_the_url_is_garbage(self, url):
        assert is_playlist(url) is False


class TestSuggestedPlatforms:
    def test_should_show_popular_compatible_platforms_on_the_home_screen(self):
        assert {"YouTube", "Vimeo", "TikTok", "Instagram", "SoundCloud", "Dailymotion"} <= set(
            SUPPORTED_PLATFORM_NAMES
        )
