"""
SDD — Spec: playlist download that resumes, keeps its order, and lets the user choose

CONTRACT
  - `js_runtimes_for(which)` picks the JavaScript runtime yt-dlp will use on
    YouTube, among the installed ones: deno, then node, then bun.
  - `_build_opts` in playlist mode keeps one history file per destination
    folder (`download_archive`), writes the playlist position as the track
    number, and spaces out requests; for MP3, `final_ext` makes yt-dlp
    recognise the already converted file.
  - `playlist_entries(info, archived_ids)` turns the quick listing into rows
    the selection shows, marking what was downloaded before.
  - `playlist_items_spec(indices)` translates the selection into `playlist_items`.
  - `forget_archived(path, ids)` removes from the history what the user asked
    to download again.
  - `DownloadManager.cancel()` interrupts the download in progress, and the
    summary arrives as cancelled, not as an error.

WHY IT EXISTS
  Running the same playlist twice downloaded everything again: without history
  and without `final_ext`, yt-dlp looks for the `.webm` the conversion already
  deleted and does not recognise the `.mp3` next to it. The playlist order was
  lost; numbering the filename solved that, but the user preferred the file with
  only the song name (test_file_name.py) — the order went into the track tag.
  And since yt-dlp 2025.11 YouTube requires a JavaScript runtime: only deno is
  enabled by default, so a machine with node and no deno got few formats or none.

BUSINESS RULES
  - The history is per destination folder: a downloaded MP3 does not prevent
    the MP4 of the same video.
  - A single item does not use the history: asking for the same video again is
    intentional.
  - The recorded position is the ORIGINAL position in the playlist, even when
    only part of it was chosen.
  - An already downloaded item appears unchecked in the selection, never hidden:
    the user may want it again, and then the history forgets that item.
  - Cancelling is not a failure: what was downloaded stays, and the history
    remembers it.
"""

import queue
from pathlib import Path

import pytest
import yt_dlp

from media_downloader.downloader import (
    ARCHIVE_FILENAME,
    DownloadManager,
    archive_ids,
    forget_archived,
    js_runtimes_for,
    playlist_entries,
    playlist_items_spec,
    summary_lines,
)
from media_downloader.models import DownloadSummary, PlaylistEntry


def _opts(file_format="mp3", playlist_mode=True, include_metadata=False):
    manager = DownloadManager(queue.Queue())
    return manager._build_opts(
        Path("/tmp/destino"), file_format, playlist_mode, DownloadSummary(),
        include_metadata,
    )


class TestJavaScriptRuntime:
    def test_should_prefer_deno_when_it_is_installed(self):
        installed = {"deno": "/usr/bin/deno", "node": "/usr/bin/node"}
        assert js_runtimes_for(installed.get) == {"deno": {"path": "/usr/bin/deno"}}

    def test_should_use_node_when_deno_is_not_installed(self):
        installed = {"node": "/usr/bin/node"}
        assert js_runtimes_for(installed.get) == {"node": {"path": "/usr/bin/node"}}

    def test_should_keep_the_yt_dlp_default_when_no_runtime_is_installed(self):
        assert js_runtimes_for(lambda _: None) == {"deno": {}}

    def test_should_pass_the_runtime_to_listing_and_download(self, monkeypatch):
        monkeypatch.setattr(
            "media_downloader.downloader.shutil.which",
            lambda name: "/usr/bin/node" if name == "node" else None)
        manager = DownloadManager(queue.Queue())

        assert manager._base_opts()["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}
        assert _opts()["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}


class TestPlaylistHistory:
    def test_should_keep_history_in_the_destination_folder_when_in_playlist_mode(self):
        assert _opts()["download_archive"] == str(Path("/tmp/destino") / ARCHIVE_FILENAME)

    def test_should_not_use_history_when_it_is_a_single_item(self):
        assert "download_archive" not in _opts(playlist_mode=False)

    def test_should_recognise_the_already_converted_mp3(self):
        assert _opts("mp3")["final_ext"] == "mp3"

    def test_should_recognise_the_already_merged_mp4(self):
        assert _opts("mp4")["final_ext"] == "mp4"

    def test_should_read_the_ids_from_the_history(self, tmp_path):
        history = tmp_path / ARCHIVE_FILENAME
        history.write_text("youtube abc\nyoutube def\n\n", encoding="utf-8")

        assert archive_ids(history) == {"youtube abc", "youtube def"}

    def test_should_treat_a_missing_history_as_empty(self, tmp_path):
        assert archive_ids(tmp_path / ARCHIVE_FILENAME) == set()

    def test_should_forget_only_the_items_requested_again(self, tmp_path):
        history = tmp_path / ARCHIVE_FILENAME
        history.write_text("youtube abc\nyoutube def\nyoutube ghi\n", encoding="utf-8")

        forget_archived(history, {"youtube def"})

        assert history.read_text(encoding="utf-8") == "youtube abc\nyoutube ghi\n"

    def test_should_not_create_history_when_forgetting_and_it_does_not_exist(self, tmp_path):
        history = tmp_path / ARCHIVE_FILENAME
        forget_archived(history, {"youtube abc"})
        assert not history.exists()


class TestFileNameAndOrder:
    def test_should_name_by_title_only_when_it_is_a_single_item(self):
        assert _opts(playlist_mode=False)["outtmpl"]["default"] == str(
            Path("/tmp/destino") / "%(title)s.%(ext)s")

    def test_should_not_save_the_playlist_own_cover_in_the_folder(self):
        # Real case: with metadata enabled, "000 - Popular Music Videos.jpg" was left over.
        assert _opts(include_metadata=True)["outtmpl"]["pl_thumbnail"] == ""

    def test_should_write_the_position_as_track_number_when_there_is_metadata(self):
        pps = _opts(include_metadata=True)["postprocessors"]
        parser = next(pp for pp in pps if pp["key"] == "MetadataParser")

        assert parser["when"] == "pre_process"
        assert [action[1:] for action in parser["actions"]] == [
            ("playlist_index", "%(track_number)s")]

    def test_should_not_write_track_number_when_it_is_a_single_item(self):
        pps = _opts(playlist_mode=False, include_metadata=True)["postprocessors"]
        assert "MetadataParser" not in [pp["key"] for pp in pps]

    def test_should_be_accepted_by_the_real_yt_dlp(self):
        # Postprocessor keys are only validated when YoutubeDL is built.
        with yt_dlp.YoutubeDL(_opts(include_metadata=True)):
            pass


class TestRequestPacing:
    def test_should_space_out_requests_when_in_a_playlist(self):
        opts = _opts()
        assert opts["sleep_interval_requests"] > 0
        assert 0 < opts["sleep_interval"] <= opts["max_sleep_interval"]

    def test_should_not_delay_a_single_item(self):
        opts = _opts(playlist_mode=False)
        assert "sleep_interval" not in opts
        assert "sleep_interval_requests" not in opts

    def test_should_wait_longer_between_retries_up_to_a_ceiling(self):
        wait = _opts()["retry_sleep_functions"]["http"]
        assert [wait(n) for n in (0, 1, 2, 3)] == [1, 2, 4, 8]
        assert wait(20) == 30


class TestListingForSelection:
    def test_should_number_by_original_position_even_with_gaps(self):
        info = {"entries": [
            {"id": "a", "ie_key": "Youtube", "title": "Faixa A"},
            None,
            {"id": "c", "ie_key": "Youtube", "title": "Faixa C", "duration": 200},
        ]}

        assert playlist_entries(info, set()) == [
            PlaylistEntry(1, "Faixa A", "youtube a", None, False),
            PlaylistEntry(3, "Faixa C", "youtube c", 200, False),
        ]

    def test_should_mark_what_is_already_in_the_history(self):
        info = {"entries": [
            {"id": "a", "ie_key": "Youtube", "title": "Faixa A"},
            {"id": "b", "ie_key": "Youtube", "title": "Faixa B"},
        ]}

        rows = playlist_entries(info, {"youtube b"})

        assert [row.already_downloaded for row in rows] == [False, True]

    def test_should_name_an_untitled_item_by_its_id(self):
        info = {"entries": [{"id": "xyz", "ie_key": "Youtube"}]}
        assert playlist_entries(info, set())[0].title == "xyz"

    def test_should_translate_the_selection_for_yt_dlp_in_order(self):
        assert playlist_items_spec([5, 1, 3]) == "1,3,5"


class TestDownloadingChosenItems:
    """The two-stage flow, with yt-dlp replaced by a recorder."""

    class _FakeYoutubeDL:
        calls: list = []
        info: dict = {}

        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=False):
            return self.info

        def download(self, urls):
            type(self).calls.append(self.opts)

    @pytest.fixture
    def fake(self, monkeypatch, tmp_path):
        fake = self._FakeYoutubeDL
        fake.calls = []
        fake.info = {"_type": "playlist", "entries": [
            {"id": "a", "ie_key": "Youtube", "title": "Faixa A"},
            {"id": "b", "ie_key": "Youtube", "title": "Faixa B"},
            {"id": "c", "ie_key": "Youtube", "title": "Faixa C"},
        ]}
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", fake)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS",
            {("mp3", True): tmp_path, ("mp3", False): tmp_path})
        return fake

    def _events(self, event_queue):
        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        return events

    def test_should_list_the_playlist_and_wait_for_the_choice_before_downloading(self, fake, tmp_path):
        (tmp_path / ARCHIVE_FILENAME).write_text("youtube b\n", encoding="utf-8")
        event_queue = queue.Queue()
        manager = DownloadManager(event_queue)

        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        listed = [e for e in self._events(event_queue) if e["type"] == "playlist_listed"]
        assert fake.calls == []
        assert [e.already_downloaded for e in listed[0]["entries"]] == [False, True, False]

    def test_should_download_only_the_chosen_items(self, fake):
        event_queue = queue.Queue()
        manager = DownloadManager(event_queue)
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        manager.download_selected([1, 3])

        assert fake.calls[0]["playlist_items"] == "1,3"
        done = [e for e in self._events(event_queue) if e["type"] == "done"][0]
        assert done["summary"].total_items == 2

    def test_should_count_in_the_summary_what_was_already_downloaded(self, fake, tmp_path):
        (tmp_path / ARCHIVE_FILENAME).write_text("youtube b\n", encoding="utf-8")
        event_queue = queue.Queue()
        manager = DownloadManager(event_queue)
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        manager.download_selected([1, 3])

        done = [e for e in self._events(event_queue) if e["type"] == "done"][0]
        assert done["summary"].already_downloaded_count == 1

    def test_should_forget_from_the_history_the_item_requested_again(self, fake, tmp_path):
        history = tmp_path / ARCHIVE_FILENAME
        history.write_text("youtube a\nyoutube b\n", encoding="utf-8")
        manager = DownloadManager(queue.Queue())
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        manager.download_selected([2])

        assert history.read_text(encoding="utf-8") == "youtube a\n"

    def test_should_download_a_single_item_directly_without_asking_for_a_choice(self, fake):
        fake.info = {"id": "a", "title": "Faixa A"}
        event_queue = queue.Queue()

        DownloadManager(event_queue).download("https://www.youtube.com/watch?v=a", "mp3")

        assert len(fake.calls) == 1
        assert "playlist_items" not in fake.calls[0]
        assert "playlist_listed" not in [e["type"] for e in self._events(event_queue)]

    def test_should_not_open_the_choice_when_cancelled_during_listing(
        self, fake, monkeypatch,
    ):
        event_queue = queue.Queue()
        manager = DownloadManager(event_queue)
        monkeypatch.setattr(
            fake, "extract_info",
            lambda self, url, download=False: (manager.cancel(), self.info)[1])

        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        types = [e["type"] for e in self._events(event_queue)]
        assert "playlist_listed" not in types
        assert types[-1] == "cancelled"

    def test_should_reject_a_choice_when_no_playlist_was_listed(self):
        event_queue = queue.Queue()
        DownloadManager(event_queue).download_selected([1])
        assert [e["type"] for e in self._events(event_queue)] == ["error"]


class TestCancellation:
    def test_should_interrupt_the_download_on_the_next_progress_update(self):
        summary = DownloadSummary(total_items=3)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(summary)

        manager.cancel()

        with pytest.raises(yt_dlp.utils.DownloadCancelled):
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})

    def test_should_publish_cancelled_and_not_error(self, monkeypatch, tmp_path):
        manager = DownloadManager(queue.Queue())

        class _CancellingYoutubeDL(TestDownloadingChosenItems._FakeYoutubeDL):
            def download(self, urls):
                raise yt_dlp.utils.DownloadCancelled("cancelled")

        _CancellingYoutubeDL.info = {"id": "a", "title": "Faixa A"}
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _CancellingYoutubeDL)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp3", False): tmp_path})

        manager.download("https://www.youtube.com/watch?v=a", "mp3")

        types = []
        while not manager._q.empty():
            types.append(manager._q.get_nowait()["type"])
        assert "cancelled" in types
        assert "error" not in types

    def test_should_start_without_the_previous_cancellation_when_a_new_download_begins(self):
        summary = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        manager.cancel()
        manager._reset_cancel()

        manager._make_progress_hook(summary)(
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})


class TestProgressWithSelection:
    def test_should_show_the_count_of_chosen_items_and_not_the_position(self):
        event_queue = queue.Queue()
        summary = DownloadSummary(total_items=2, downloaded_count=1)
        hook = DownloadManager(event_queue)._make_progress_hook(summary)

        hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10,
              "info_dict": {"title": "Faixa C", "playlist_index": 30}})

        assert event_queue.get_nowait()["message"] == "Baixando: Faixa C (2/2)"


class TestPlaylistSummary:
    def test_should_count_what_was_already_downloaded(self):
        rows = summary_lines(DownloadSummary(downloaded_count=2, already_downloaded_count=5))
        assert "==  5 item(s) ja baixado(s) antes, ignorado(s)" in rows

    def test_should_not_mention_history_when_nothing_was_downloaded_before(self):
        rows = summary_lines(DownloadSummary(downloaded_count=2))
        assert not any("ja baixado" in row for row in rows)
