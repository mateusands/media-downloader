"""
SDD — Spec: downloading only a section of the video or audio

CONTRACT
  `clip.py` is pure (no yt-dlp, no widgets):
  - `parse_time(text)` reads what the user types ("90", "1:30", "1:02:03",
    "90,5", "1:30.5") and returns seconds; empty is `None` (no limit); text
    that is not a time raises `ValueError`.
  - `format_time(seconds)` is the inverse, to fill the fields and the ruler.
  - `clip_error(start, end, duration)` says in Portuguese why the section is
    invalid, or `None` when it is valid.
  - `youtube_start_hint(url)` reads the `t=` of a YouTube link ("t=90",
    "t=1m30s") so the cut already opens at the point the user shared.
  - `clip_label(start, end)` is the part of the filename that identifies the
    section.
  `DownloadManager._build_opts(..., options=MediaOptions(clip_start, clip_end))`
  translates the section into `download_ranges`, and enables
  `force_keyframes_at_cuts` only when the section is short.

WHY IT EXISTS
  From an hour-long video, the user wants only "from this minute to that
  minute". Downloading everything and cutting in an editor was the step they
  wanted to eliminate. The sibling extension (zumbue/video-extensao-download)
  already does this with the same yt-dlp mechanism; the 3-minute rule comes
  from there:
  - A precise cut (`force_keyframes_at_cuts`) re-encodes the section. On a short
    section it costs seconds; on a long one it means re-encoding a film, and
    cancellation cannot reach FFmpeg in the middle of that.
  - Without a precise cut, yt-dlp cuts at the nearest keyframe — the section may
    start a few seconds early. Acceptable for a long section.

BUSINESS RULES
  - Empty start and end = the whole file, without `download_ranges`.
  - Start without end goes to the end of the video and never re-encodes (it
    would be the whole video).
  - The end must come after the start; neither may exceed the known duration.
    An unknown duration does not block: yt-dlp cuts whatever there is.
  - The section does not apply to playlists: each item has its own duration, and
    "from minute 2 to 3" of fifty songs is not what anyone wants.
  - The section file has its own name. Without it, `final_ext` makes yt-dlp
    think the whole MP3 already downloaded is the requested section and download
    nothing — and a section downloaded later would overwrite the whole file.
"""

import queue
from pathlib import Path

import pytest

from media_downloader.clip import (
    PRECISE_CUT_SECONDS,
    clip_error,
    clip_label,
    format_time,
    parse_time,
    youtube_start_hint,
)
from media_downloader.downloader import DownloadManager
from media_downloader.models import DownloadSummary, MediaOptions


class TestReadingTypedTime:
    @pytest.mark.parametrize("text, seconds", [
        ("90", 90.0),
        ("1:30", 90.0),
        ("01:30", 90.0),
        ("1:02:03", 3723.0),
        ("90,5", 90.5),
        ("1:30.5", 90.5),
        ("  2:00  ", 120.0),
    ])
    def test_should_understand_the_formats_the_user_types(self, text, seconds):
        assert parse_time(text) == seconds

    def test_should_treat_an_empty_field_as_no_limit(self):
        assert parse_time("") is None
        assert parse_time("   ") is None

    @pytest.mark.parametrize("text", ["abc", "1:75", "-5", "1::2", "1:2:3:4"])
    def test_should_reject_what_is_not_a_time(self, text):
        with pytest.raises(ValueError):
            parse_time(text)


class TestWritingTime:
    @pytest.mark.parametrize("seconds, text", [
        (0, "0:00"),
        (90, "1:30"),
        (3723, "1:02:03"),
        (90.5, "1:30.5"),
    ])
    def test_should_write_in_clock_format(self, seconds, text):
        assert format_time(seconds) == text

    def test_should_read_back_what_it_wrote(self):
        assert parse_time(format_time(4321.5)) == 4321.5


class TestSectionValidation:
    def test_should_accept_when_the_section_is_inside_the_video(self):
        assert clip_error(60, 120, 300) is None

    def test_should_reject_when_the_end_is_before_the_start(self):
        assert clip_error(120, 60, 300) == "O fim precisa ser depois do início."

    def test_should_reject_when_the_end_equals_the_start(self):
        assert clip_error(60, 60, 300) == "O fim precisa ser depois do início."

    def test_should_reject_when_the_start_is_past_the_duration(self):
        assert clip_error(400, None, 300) == "O início passa da duração do vídeo."

    def test_should_reject_when_the_end_is_past_the_duration(self):
        assert clip_error(10, 400, 300) == "O fim passa da duração do vídeo."

    def test_should_not_block_when_the_duration_is_unknown(self):
        assert clip_error(10, 4000, None) is None


class TestStartSuggestedByLink:
    @pytest.mark.parametrize("url, seconds", [
        ("https://www.youtube.com/watch?v=abc&t=90", 90.0),
        ("https://youtu.be/abc?t=90s", 90.0),
        ("https://www.youtube.com/watch?v=abc&t=1m30s", 90.0),
        ("https://www.youtube.com/watch?v=abc&t=1h2m3s", 3723.0),
    ])
    def test_should_use_the_time_from_the_shared_link(self, url, seconds):
        assert youtube_start_hint(url) == seconds

    def test_should_not_suggest_a_start_when_the_link_has_no_time(self):
        assert youtube_start_hint("https://www.youtube.com/watch?v=abc") is None

    def test_should_not_suggest_a_start_when_the_time_is_unreadable(self):
        assert youtube_start_hint("https://www.youtube.com/watch?v=abc&t=xyz") is None


class TestSectionLabel:
    def test_should_identify_start_and_end_in_the_name(self):
        assert clip_label(90, 150) == "trecho 1m30s-2m30s"

    def test_should_say_end_when_it_goes_to_the_end(self):
        assert clip_label(90, None) == "trecho 1m30s-fim"

    def test_should_include_hours_when_the_video_is_long(self):
        assert clip_label(3723, 3800) == "trecho 1h02m03s-1h03m20s"

    def test_should_not_contain_characters_forbidden_in_filenames(self):
        assert not set(clip_label(90.5, 150.25)) & set('/\\:*?"<>|%')


def _opts(file_format="mp4", playlist=False, **options):
    return DownloadManager(queue.Queue())._build_opts(
        Path("/tmp/destino"), file_format, playlist, DownloadSummary(),
        options=MediaOptions(**options),
    )


def _sections(opts):
    return list(opts["download_ranges"]({"duration": 3600}, None))


class TestSectionOptions:
    def test_should_download_the_whole_file_when_there_is_no_section(self):
        opts = _opts()
        assert "download_ranges" not in opts
        assert "force_keyframes_at_cuts" not in opts

    def test_should_ask_yt_dlp_for_only_the_chosen_section(self):
        opts = _opts(clip_start=60, clip_end=120)
        assert _sections(opts) == [{"start_time": 60, "end_time": 120}]

    def test_should_cut_precisely_when_the_section_is_short(self):
        opts = _opts(clip_start=60, clip_end=60 + PRECISE_CUT_SECONDS)
        assert opts["force_keyframes_at_cuts"] is True

    def test_should_not_reencode_when_the_section_is_long(self):
        opts = _opts(clip_start=0, clip_end=PRECISE_CUT_SECONDS + 1)
        assert "force_keyframes_at_cuts" not in opts

    def test_should_go_to_the_end_without_reencoding_when_there_is_only_a_start(self):
        opts = _opts(clip_start=60)
        assert _sections(opts)[0]["start_time"] == 60
        assert _sections(opts)[0]["end_time"] == float("inf")
        assert "force_keyframes_at_cuts" not in opts

    def test_should_start_from_zero_when_there_is_only_an_end(self):
        opts = _opts(clip_end=30)
        assert _sections(opts) == [{"start_time": 0, "end_time": 30}]
        assert opts["force_keyframes_at_cuts"] is True

    def test_should_also_apply_when_the_format_is_mp3(self):
        opts = _opts(file_format="mp3", clip_start=60, clip_end=120)
        assert _sections(opts) == [{"start_time": 60, "end_time": 120}]

    def test_should_give_the_section_a_name_that_does_not_collide_with_the_whole_file(self):
        whole = _opts()["outtmpl"]["default"]
        section = _opts(clip_start=60, clip_end=120)["outtmpl"]["default"]
        assert section != whole
        assert section.endswith("%(title)s (trecho 1m00s-2m00s).%(ext)s")

    def test_should_not_cut_when_the_items_are_from_a_playlist(self):
        opts = _opts(playlist=True, clip_start=60, clip_end=120)
        assert "download_ranges" not in opts
        assert "trecho" not in opts["outtmpl"]["default"]


class TestSectionMp3Name:
    def test_should_keep_the_suffix_when_the_section_mp3_gets_the_song_name(self, tmp_path):
        whole = tmp_path / "Every Breath You Take.mp3"
        whole.write_bytes(b"inteiro")
        downloaded = tmp_path / "The Police - Every Breath You Take (trecho 1m00s-2m00s).mp3"
        downloaded.write_bytes(b"trecho")

        finals = DownloadManager(queue.Queue())._rename_downloads(
            DownloadSummary(),
            [("The Police - Every Breath You Take (Official Video)", downloaded)],
            clip_label(60, 120),
        )

        assert finals[0][1].name == "Every Breath You Take (trecho 1m00s-2m00s).mp3"
        assert whole.read_bytes() == b"inteiro"
