"""
SDD — Spec: interpreting the yt-dlp result and reporting failures

CONTRACT
  After `_extract_info`, the `DownloadManager` must decide two things from a
  dictionary that yt-dlp returns in a variable shape:
  `_is_playlist_result(info)` — is what came back really a playlist?
  (How many items will be downloaded now is the user's choice; the numbering
  that respects the gaps lives in `playlist_entries`, covered in
  test_playlist.py.)
  And `ReportingLogger` translates yt-dlp output into two distinct lists:
  `summary.failed_items` (error) and `summary.extractor_notices` (warning),
  which the final summary reconciles with the items actually downloaded.

WHY IT EXISTS
  `ignoreerrors: True` makes yt-dlp CONTINUE the playlist when an item fails
  and put `None` in place of the broken entry — a gap, not an item. Playlist
  mode is also re-evaluated here: the URL may look like a playlist and the
  result come back single. In addition, yt-dlp may log a technical diagnostic
  as an error and still finish the download; without reconciliation, the
  summary shows a false failure.
  The MP3 metadata option may also not find an artist or cover for every item
  of a playlist. That is pending enrichment, never a download failure.

BUSINESS RULES
  - A `None` entry in a playlist is a gap, not an item.
  - A repeated failure is not listed twice in the summary.
  - An extractor warning is not a failed item. The missing JavaScript runtime
    alert shows up in downloads that finish completely; counting it as a
    failure turned a single video into "2 item(s) com falha" in the summary.
  - The message is cleaned before being compared and displayed: yt-dlp output
    is coloured, and without removing the ANSI escape the prefix does not
    match, the same error goes in twice, and the user reads terminal codes in
    the dialog.
  - If all expected items were downloaded, extractor diagnostics are not shown
    to the user as failures.
  - Missing metadata in a finished MP3 goes to `metadata_pending_items`,
    separate from `failed_items`.
"""

import queue
from pathlib import Path

from media_downloader.downloader import (
    FAILURES_SHOWN,
    DownloadManager,
    ReportingLogger,
    summary_lines,
)
from media_downloader.models import DownloadSummary, MetadataPendingItem

is_playlist_result = DownloadManager._is_playlist_result


class TestResultClassification:
    def test_should_be_a_playlist_when_there_are_entries(self):
        assert is_playlist_result({"entries": [{"id": "a"}]}) is True

    def test_should_be_a_playlist_when_the_type_declares_playlist(self):
        assert is_playlist_result({"_type": "playlist"}) is True

    def test_should_not_be_a_playlist_when_the_result_is_a_single_video(self):
        assert is_playlist_result({"id": "abc", "title": "Um vídeo"}) is False

    def test_should_not_be_a_playlist_when_entries_is_empty(self):
        # Real case: a playlist URL that resolved to a single item.
        assert is_playlist_result({"entries": []}) is False


class TestFailureReporting:
    def _logger(self):
        event_queue = queue.Queue()
        summary = DownloadSummary()
        return ReportingLogger(event_queue, summary), event_queue, summary

    def test_should_record_the_failure_and_say_it_continues_with_the_rest(self):
        logger, event_queue, summary = self._logger()

        logger.error("ERROR: Video unavailable")

        assert summary.failed_items == ["Video unavailable"]
        event = event_queue.get_nowait()
        assert event["type"] == "status"
        assert "continuando" in event["message"].lower()

    def test_should_remove_the_error_prefix_from_the_message(self):
        logger, _, summary = self._logger()

        logger.error("ERROR:   Private video  ")

        assert summary.failed_items == ["Private video"]

    def test_should_not_list_the_same_failure_twice(self):
        logger, event_queue, summary = self._logger()

        logger.error("ERROR: Video unavailable")
        logger.error("ERROR: Video unavailable")

        assert summary.failed_items == ["Video unavailable"]
        assert event_queue.qsize() == 1, "the second report must not produce a new notice"

    def test_should_record_a_warning_as_a_notice_and_not_as_a_failure(self):
        logger, event_queue, summary = self._logger()

        logger.warning("WARNING: alguns formatos podem faltar")

        assert summary.failed_items == []
        assert summary.extractor_notices == ["alguns formatos podem faltar"]
        assert event_queue.empty(), "a warning does not announce a failed item"

    def test_should_not_record_an_empty_message(self):
        logger, event_queue, summary = self._logger()

        logger.error("ERROR:   ")

        assert summary.failed_items == []
        assert event_queue.empty()

    def test_should_record_nothing_when_the_message_is_debug(self):
        logger, event_queue, summary = self._logger()

        logger.debug("baixando fragmento 3/10")

        assert summary.failed_items == []
        assert event_queue.empty()


class TestFailureSummary:
    def test_should_ignore_a_technical_alert_when_all_items_were_downloaded(self):
        summary = DownloadSummary(
            total_items=1,
            downloaded_count=1,
            failed_items=["[youtube] No supported JavaScript runtime could be found."],
        )

        DownloadManager._reconcile_failure_reports(summary)

        assert summary.failed_items == []

    def test_should_keep_the_failure_when_not_all_items_were_downloaded(self):
        summary = DownloadSummary(
            total_items=2,
            downloaded_count=1,
            failed_items=["Private video"],
        )

        DownloadManager._reconcile_failure_reports(summary)

        assert summary.failed_items == ["Private video"]


class TestMp3Metadata:
    def _opts(self, include_metadata: bool):
        manager = DownloadManager(queue.Queue())
        return manager._build_opts(
            Path("/tmp/destino"), "mp3", False, DownloadSummary(), include_metadata,
        )

    def test_should_keep_the_current_mp3_when_the_metadata_option_is_off(self):
        opts = self._opts(include_metadata=False)

        assert "writethumbnail" not in opts
        assert [item["key"] for item in opts["postprocessors"]] == ["FFmpegExtractAudio"]

    def test_should_request_source_cover_and_metadata_when_the_option_is_on(self):
        opts = self._opts(include_metadata=True)

        assert opts["writethumbnail"] is True
        assert [item["key"] for item in opts["postprocessors"]] == [
            "FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail",
        ]

    def test_should_list_metadata_to_review_without_marking_the_item_as_failed(self):
        summary = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(summary, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/ABBA - Dancing Queen.webm",
            "info_dict": {"id": "abba-1", "title": "ABBA - Dancing Queen"},
        })

        assert summary.downloaded_count == 1
        assert summary.failed_items == []
        assert len(summary.metadata_pending_items) == 1
        pending = summary.metadata_pending_items[0]
        assert pending.title == "ABBA - Dancing Queen"
        assert pending.review_reasons == ("artista ausente", "capa ausente")

    def test_should_name_the_channel_that_became_the_artist_instead_of_saying_it_is_missing(self):
        summary = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(summary, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/Audioslave - Like a Stone.webm",
            "info_dict": {
                "id": "audioslave-1",
                "title": "Audioslave - Like a Stone (Official Video)",
                "uploader": "AudioslaveVEVO",
                "thumbnail": "https://exemplo.com/capa.jpg",
            },
        })

        assert summary.metadata_pending_items[0].review_reasons == (
            "artista provisorio: canal AudioslaveVEVO",
        )

    def test_should_not_list_a_pending_item_when_artist_and_cover_are_available(self):
        summary = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(summary, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/Dancing Queen.webm",
            "info_dict": {
                "id": "abba-1",
                "title": "Dancing Queen",
                "artist": "ABBA",
                "thumbnail": "https://exemplo.com/capa.jpg",
            },
        })

        assert summary.metadata_pending_items == []


class TestCleanExtractorReport:
    """What the user reads in the summary is not raw yt-dlp output."""

    def _logger(self):
        summary = DownloadSummary(total_items=1)
        return ReportingLogger(queue.Queue(), summary), summary

    def test_should_remove_colour_codes_from_the_reported_message(self):
        logger, summary = self._logger()

        logger.error("\x1b[0;31mERROR:\x1b[0m unable to download video data: HTTP Error 403")

        assert summary.failed_items == ["unable to download video data: HTTP Error 403"]

    def test_should_treat_as_repeated_a_failure_that_differs_only_in_colour(self):
        logger, summary = self._logger()

        logger.error("\x1b[0;31mERROR:\x1b[0m Video unavailable")
        logger.error("ERROR: Video unavailable")

        assert summary.failed_items == ["Video unavailable"]

    def test_should_separate_an_extractor_warning_from_an_item_failure(self):
        logger, summary = self._logger()

        logger.warning("[youtube] No supported JavaScript runtime could be found.")
        logger.error("ERROR: unable to download video data: HTTP Error 403")

        assert summary.failed_items == ["unable to download video data: HTTP Error 403"]
        assert summary.extractor_notices == [
            "[youtube] No supported JavaScript runtime could be found."
        ]

    def test_should_forget_warnings_when_all_items_were_downloaded(self):
        summary = DownloadSummary(
            total_items=1,
            downloaded_count=1,
            extractor_notices=["[youtube] No supported JavaScript runtime could be found."],
        )

        DownloadManager._reconcile_failure_reports(summary)

        assert summary.extractor_notices == []


class TestExtractorOutputOptions:
    def test_should_request_colourless_output_so_the_summary_gets_no_escapes(self):
        manager = DownloadManager(queue.Queue())

        opts = manager._build_opts(Path("/tmp/destino"), "mp3", False, DownloadSummary())

        assert opts["no_color"] is True


class TestSummaryLength:
    """The summary answers 'what happened?'; the review answers 'what to fix?'."""

    def _pending_items(self, count):
        return [
            MetadataPendingItem(
                f"Faixa numero {n} com um titulo bem longo (Official Video)",
                f"/tmp/faixa{n}.mp3",
                ("artista provisorio: canal Algum Canal", "capa provisoria: miniatura do video"),
            )
            for n in range(count)
        ]

    def test_should_count_pending_items_instead_of_listing_each_one(self):
        summary = DownloadSummary(
            total_items=7, downloaded_count=7,
            metadata_pending_items=self._pending_items(7),
        )

        lines = summary_lines(summary)
        text = "\n".join(lines)

        assert "7 MP3 com metadata a confirmar" in text
        assert "Faixa numero" not in text, "per-item detail belongs to the review"
        assert "revisao" in text.lower(), "the summary must say the review comes next"

    def test_should_keep_the_summary_short_even_for_a_large_playlist(self):
        summary = DownloadSummary(
            total_items=40, downloaded_count=40,
            metadata_pending_items=self._pending_items(40),
        )

        assert len(summary_lines(summary)) <= 6

    def test_should_cap_the_failure_list_and_say_how_many_remain(self):
        summary = DownloadSummary(
            total_items=10, downloaded_count=2,
            failed_items=[f"Video {n} indisponivel" for n in range(8)],
        )

        lines = summary_lines(summary)

        assert sum(1 for l in lines if l.startswith("  - Video")) == FAILURES_SHOWN
        assert f"e mais {8 - FAILURES_SHOWN}" in "\n".join(lines)

    def test_should_list_every_failure_when_they_fit(self):
        summary = DownloadSummary(
            total_items=3, downloaded_count=1,
            failed_items=["Video privado", "Video removido"],
        )

        text = "\n".join(summary_lines(summary))

        assert "Video privado" in text and "Video removido" in text
        assert "e mais" not in text
