"""
SDD — Spec: link preview before downloading

CONTRACT
  - `preview_from_info(url, info)` (pure, in `preview.py`) summarizes what
    yt-dlp extracted into a `MediaPreview`: title, channel, duration, whether
    it is a playlist and how many items it has, the available video heights,
    and the start suggested by the link's `t=`.
  - `available_heights(info)` lists the heights that actually have video, from
    highest to lowest.
  - `DownloadManager.probe(url)` runs in a thread, extracts without
    downloading, fetches the thumbnail, and publishes `preview` (with the
    `MediaPreview`) or `preview_failed`. Both carry the URL: the screen discards
    the preview of a link the user has already replaced.

WHY IT EXISTS
  Pasting the link and pressing "Baixar" blindly meant finding out only at the
  end that the link was another video, or a 200-item playlist. The preview
  (a pattern from Seal and Open Video Downloader) shows what will be
  downloaded; it is also where the section cut gets the duration for the
  ruler, and where the quality choice learns what exists — offering 1080p for
  a 480p video promises what will not come.

BUSINESS RULES
  - A failed preview is not a download error: it does not publish `error`,
    does not lock the screen, and the "Baixar" button keeps working (yt-dlp
    decides).
  - A thumbnail that does not load does not bring the preview down: it comes
    without an image.
  - An audio-only format (no `vcodec` or `vcodec == "none"`) does not count as
    an available height.
  - The full URL goes to no persistent place — it only comes back on the queue.
"""

import queue

from media_downloader.downloader import DownloadManager
from media_downloader.models import MediaPreview
from media_downloader.preview import available_heights, preview_from_info


def _video(**extra):
    return {
        "id": "abc",
        "title": "Paramore - Misery Business",
        "uploader": "Paramore",
        "duration": 211,
        "thumbnail": "https://i.ytimg.com/vi/abc/maxresdefault.jpg",
        "formats": [
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "height": None},
            {"format_id": "137", "vcodec": "avc1", "height": 1080},
            {"format_id": "136", "vcodec": "avc1", "height": 720},
            {"format_id": "398", "vcodec": "av01", "height": 720},
            {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "height": 360},
        ],
        **extra,
    }


class TestLinkSummary:
    def test_should_summarize_title_channel_and_duration(self):
        preview = preview_from_info("https://youtu.be/abc", _video())
        assert preview.title == "Paramore - Misery Business"
        assert preview.uploader == "Paramore"
        assert preview.duration == 211
        assert preview.is_playlist is False

    def test_should_recognize_a_playlist_and_count_its_items(self):
        info = {"_type": "playlist", "title": "Rock 2000", "uploader": "Alguem",
                "entries": [{"id": "a"}, None, {"id": "b"}]}
        preview = preview_from_info("https://www.youtube.com/playlist?list=PL", info)
        assert preview.is_playlist is True
        assert preview.item_count == 2
        assert preview.duration is None

    def test_should_suggest_the_start_of_the_shared_link(self):
        preview = preview_from_info("https://youtu.be/abc?t=95", _video())
        assert preview.start_hint == 95

    def test_should_accept_a_link_without_channel_or_duration(self):
        preview = preview_from_info("https://exemplo.com/v", {"title": "Clipe"})
        assert preview.uploader is None
        assert preview.duration is None
        assert preview.heights == ()

    def test_should_use_the_channel_when_there_is_no_uploader(self):
        preview = preview_from_info("https://youtu.be/abc", _video(uploader=None, channel="Canal"))
        assert preview.uploader == "Canal"


class TestAvailableHeights:
    def test_should_list_heights_with_video_from_highest_to_lowest(self):
        assert available_heights(_video()) == (1080, 720, 360)

    def test_should_not_count_an_audio_only_format(self):
        info = {"formats": [{"vcodec": "none", "height": 1080}]}
        assert available_heights(info) == ()

    def test_should_not_invent_a_height_when_there_are_no_formats(self):
        assert available_heights({}) == ()


class _FakeYoutubeDL:
    info: dict | None = None
    error: Exception | None = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if _FakeYoutubeDL.error:
            raise _FakeYoutubeDL.error
        return _FakeYoutubeDL.info


def _events(event_queue):
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


class TestPreviewProbe:
    def _manager(self, monkeypatch, info=None, error=None, thumbnail=b"jpg"):
        _FakeYoutubeDL.info = info
        _FakeYoutubeDL.error = error
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _FakeYoutubeDL)

        def fetch_thumbnail(_url):
            if isinstance(thumbnail, Exception):
                raise thumbnail
            return thumbnail

        monkeypatch.setattr("media_downloader.downloader.fetch_thumbnail", fetch_thumbnail)
        event_queue = queue.Queue()
        return DownloadManager(event_queue), event_queue

    def test_should_publish_the_preview_with_the_thumbnail(self, monkeypatch):
        manager, event_queue = self._manager(monkeypatch, info=_video())

        manager.probe("https://youtu.be/abc")

        event, = _events(event_queue)
        assert event["type"] == "preview"
        assert event["url"] == "https://youtu.be/abc"
        assert isinstance(event["preview"], MediaPreview)
        assert event["preview"].thumbnail == b"jpg"

    def test_should_keep_the_preview_when_the_thumbnail_fails(self, monkeypatch):
        manager, event_queue = self._manager(monkeypatch, info=_video(), thumbnail=OSError("timeout"))

        manager.probe("https://youtu.be/abc")

        event, = _events(event_queue)
        assert event["type"] == "preview"
        assert event["preview"].thumbnail is None

    def test_should_warn_without_a_download_error_when_extraction_fails(self, monkeypatch):
        manager, event_queue = self._manager(monkeypatch, error=RuntimeError("Video unavailable"))

        manager.probe("https://youtu.be/abc")

        event, = _events(event_queue)
        assert event["type"] == "preview_failed"
        assert event["url"] == "https://youtu.be/abc"
        assert "Video unavailable" in event["message"]

    def test_should_warn_without_an_error_when_extraction_is_empty(self, monkeypatch):
        manager, event_queue = self._manager(monkeypatch, info=None)

        manager.probe("https://youtu.be/abc")

        assert [e["type"] for e in _events(event_queue)] == ["preview_failed"]

    def test_should_list_a_playlist_without_resolving_each_item(self, monkeypatch):
        manager, _ = self._manager(monkeypatch, info=_video())
        options = []
        original = _FakeYoutubeDL.__init__
        monkeypatch.setattr(
            _FakeYoutubeDL, "__init__",
            lambda self, opts: (options.append(opts), original(self, opts))[1])

        manager.probe("https://www.youtube.com/playlist?list=PLx")

        assert options[0]["extract_flat"] == "in_playlist"
        assert options[0]["noplaylist"] is False
