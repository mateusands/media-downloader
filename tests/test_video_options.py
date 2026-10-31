"""
SDD — Spec: quality, subtitles, chapters, and sponsors

CONTRACT
  `MediaOptions` carries what the user chose beyond the format, and
  `DownloadManager._build_opts(..., options=...)` translates each choice into
  the yt-dlp option or postprocessor:
  - `quality`: "best" or a height ("1080", "720", "480", "360") — a cap, not a
    requirement.
  - `subtitles`: downloads Portuguese and English subtitles (including the
    automatic ones) and embeds them in the MP4.
  - `chapters`: writes the source's chapters into the MP4.
  - `skip_sponsors`: removes sponsor segments marked in SponsorBlock.
  The options also apply to playlists: `download` keeps them in the listing,
  and `download_selected` uses them in the actual download.

WHY IT EXISTS
  The MP4 was always "the best available quality" — for a twenty-minute 4K
  video that means gigabytes the user did not ask for. Apps of the same kind
  (Seal, Parabolic, Open Video Downloader, MeTube) offer resolution,
  subtitles, chapters, and SponsorBlock; they are the most requested options
  of a yt-dlp front end.

BUSINESS RULES
  - Quality is a cap: a video that only exists in 720p, requested in 1080p,
    downloads in 720p instead of failing. Prefers MP4+M4A to avoid re-encoding
    when merging.
  - Subtitles and chapters exist only in the MP4; for MP3 they are ignored.
  - Embedded subtitles are not left loose in the folder as .vtt.
  - A subtitle file goes through the progress hook as a separate download
    (no `id` in `info_dict`); counting it made a video with PT and EN
    subtitles finish as "3 item(s) baixado(s)".
  - SponsorBlock runs before conversion (`after_filter`), and cutting the
    segments (`ModifyChapters`) before writing metadata, otherwise the written
    chapters point to times that no longer exist.
  - SponsorBlock does not combine with a section: segments come in the whole
    video's timeline, and applied to a section they would cut the wrong place.
"""

import queue
from pathlib import Path

import pytest

from media_downloader.downloader import DownloadManager, video_format
from media_downloader.models import DownloadSummary, MediaOptions


def _opts(file_format="mp4", playlist=False, include_metadata=False, **options):
    return DownloadManager(queue.Queue())._build_opts(
        Path("/tmp/destino"), file_format, playlist, DownloadSummary(),
        include_metadata, options=MediaOptions(**options),
    )


def _keys(opts):
    return [pp["key"] for pp in opts.get("postprocessors", [])]


class TestVideoQuality:
    def test_should_keep_the_current_selector_when_quality_is_best(self):
        assert _opts()["format"] == "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"

    @pytest.mark.parametrize("height", ["1080", "720", "480", "360"])
    def test_should_cap_at_the_chosen_height(self, height):
        selector = _opts(quality=height)["format"]
        alternatives = selector.split("/")
        assert all(f"[height<={height}]" in part for part in alternatives[:-1])

    def test_should_prefer_mp4_with_m4a_to_avoid_reencoding(self):
        assert video_format("720").startswith("bv*[height<=720][ext=mp4]+ba[ext=m4a]")

    def test_should_fall_back_to_the_best_available_instead_of_failing(self):
        # The last alternative has no filter: a video with no format below the
        # cap still downloads.
        assert video_format("360").split("/")[-1] == "b"

    def test_should_treat_an_unknown_quality_as_best(self):
        assert video_format("8k") == video_format("best")

    def test_should_not_change_the_mp3_when_quality_is_set(self):
        assert _opts(file_format="mp3", quality="360")["format"] == "bestaudio/best"


class TestSubtitles:
    def test_should_download_and_embed_portuguese_and_english_subtitles(self):
        opts = _opts(subtitles=True)
        assert opts["writesubtitles"] is True
        assert opts["writeautomaticsub"] is True
        assert opts["subtitleslangs"] == ["pt.*", "en.*"]
        assert "FFmpegEmbedSubtitle" in _keys(opts)

    def test_should_not_leave_an_embedded_subtitle_loose_in_the_folder(self):
        embed = next(pp for pp in _opts(subtitles=True)["postprocessors"]
                     if pp["key"] == "FFmpegEmbedSubtitle")
        assert embed["already_have_subtitle"] is False

    def test_should_not_download_subtitles_when_the_option_is_off(self):
        opts = _opts()
        assert "writesubtitles" not in opts
        assert "FFmpegEmbedSubtitle" not in _keys(opts)

    def test_should_ignore_subtitles_when_the_format_is_mp3(self):
        opts = _opts(file_format="mp3", subtitles=True)
        assert "writesubtitles" not in opts
        assert "FFmpegEmbedSubtitle" not in _keys(opts)


class TestSubtitleIsNotAnItem:
    def _count(self, *notices):
        summary = DownloadSummary(total_items=1)
        hook = DownloadManager(queue.Queue())._make_progress_hook(summary)
        for notice in notices:
            hook(notice)
        return summary.downloaded_count

    def test_should_not_count_downloaded_subtitles_as_items(self):
        video = {"status": "finished", "filename": "/v/Clipe.mp4",
                 "info_dict": {"id": "v1", "title": "Clipe"}}
        subtitle_pt = {"status": "finished", "filename": "/v/Clipe.pt.vtt",
                       "info_dict": {"ext": "vtt", "url": "https://x/pt"}}
        subtitle_en = {"status": "finished", "filename": "/v/Clipe.en.vtt",
                       "info_dict": {"ext": "vtt", "url": "https://x/en"}}

        assert self._count(subtitle_pt, subtitle_en, video) == 1


class TestChapters:
    def test_should_write_chapters_into_the_mp4(self):
        meta = next(pp for pp in _opts(chapters=True)["postprocessors"]
                    if pp["key"] == "FFmpegMetadata")
        assert meta["add_chapters"] is True

    def test_should_not_touch_the_mp4_when_the_option_is_off(self):
        assert "FFmpegMetadata" not in _keys(_opts())

    def test_should_ignore_chapters_when_the_format_is_mp3(self):
        assert "FFmpegMetadata" not in _keys(_opts(file_format="mp3", chapters=True))


class TestSponsors:
    def test_should_mark_and_remove_sponsor_segments(self):
        opts = _opts(skip_sponsors=True)
        sponsor = next(pp for pp in opts["postprocessors"] if pp["key"] == "SponsorBlock")
        cut = next(pp for pp in opts["postprocessors"] if pp["key"] == "ModifyChapters")
        assert sponsor["when"] == "after_filter"
        assert "sponsor" in sponsor["categories"]
        assert set(cut["remove_sponsor_segments"]) == set(sponsor["categories"])

    def test_should_remove_segments_before_writing_metadata(self):
        keys = _keys(_opts(file_format="mp3", include_metadata=True, skip_sponsors=True))
        assert keys.index("ModifyChapters") < keys.index("FFmpegMetadata")

    def test_should_convert_to_mp3_after_querying_sponsorblock(self):
        keys = _keys(_opts(file_format="mp3", skip_sponsors=True))
        assert keys.index("SponsorBlock") < keys.index("FFmpegExtractAudio")

    def test_should_not_combine_when_there_is_a_section(self):
        opts = _opts(skip_sponsors=True, clip_start=60, clip_end=120)
        assert "SponsorBlock" not in _keys(opts)
        assert "ModifyChapters" not in _keys(opts)


class TestOptionsInPlaylist:
    @pytest.fixture
    def fake(self, monkeypatch, tmp_path):
        class _FakeYoutubeDL:
            calls: list = []
            info: dict = {}

            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                return _FakeYoutubeDL.info

            def download(self, urls):
                _FakeYoutubeDL.calls.append(self.opts)

        _FakeYoutubeDL.calls = []
        _FakeYoutubeDL.info = {"_type": "playlist", "entries": [
            {"id": "a", "ie_key": "Youtube", "title": "A"},
            {"id": "b", "ie_key": "Youtube", "title": "B"},
        ]}
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _FakeYoutubeDL)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp4", True): tmp_path})
        return _FakeYoutubeDL

    def test_should_use_in_the_playlist_the_quality_chosen_before_listing(self, fake):
        manager = DownloadManager(queue.Queue())
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp4",
                         options=MediaOptions(quality="720", subtitles=True))

        manager.download_selected([1, 2])

        assert "[height<=720]" in fake.calls[0]["format"]
        assert fake.calls[0]["writesubtitles"] is True
