"""
SDD — Spec: fixing metadata of MP3 files already on disk

CONTRACT
  - `music_files(folder)` lists the MP3 files in the folder and its subfolders,
    in a stable order.
  - `read_local_item(path, supplied_artist)` turns an MP3 on disk into the same
    pending item (`MetadataPendingItem`) a download produces: title, artist,
    and duration read from the file, with the reasons for what is missing.
  - `DownloadManager.fix_folder(folder, auto_apply, supplied_artist)` reads the
    folder, applies the confident candidate when the user asked for it, and
    publishes `folder_checked` with the summary; whatever is left goes to the
    same review as a download.

WHY IT EXISTS
  The user has an old library (downloaded earlier, by other means) with MP3
  files carrying no tags at all and others with wrong tags ("Na Sei Lidar").
  Review and confident matching already existed, but only for what the app had
  just downloaded.

BUSINESS RULES
  - What is written in the file wins: title and artist come from the tags;
    only without an embedded title does the filename become the search
    ("Pablo - Agora [Áudio Oficial]").
  - The artist supplied by the user applies only to an MP3 without an embedded
    artist — it never replaces what is already in the file. Blank is the same
    as not supplying one.
  - The supplied artist is the user's statement, not an inference; even so, it
    is only written when `confident_match` approves artist, title **and**
    duration.
  - An unreadable file does not bring down the folder: it goes into
    `failed_items` and the rest carries on.
  - A complete file — title, artist, album, and square cover — is not touched:
    not by automatic application, not by review, not by "apply to all". The
    user asked for it this way; the price is that a typo in a complete file
    ("Na Sei Lidar") is fixed by hand.
  - A 16:9 cover is the video thumbnail, not album art: it counts as
    provisional, just as in a download. A cover that does not open as an image
    does not count either.
  - In review, items with something missing come before items that only need
    checking.
"""

import queue
from pathlib import Path

from io import BytesIO

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from PIL import Image

from media_downloader.downloader import DownloadManager
from media_downloader.library import music_files, read_local_item
from media_downloader.models import MusicMetadataCandidate

# MPEG-1 Layer III, 128 kbps, 44.1 kHz: 417 bytes per frame, 1152 samples.
_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def _mp3(folder: Path, name: str, seconds=3.0, title=None, artist=None,
         album=None, cover=None) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_FRAME * int(seconds * 44100 / 1152))
    if title or artist or album or cover:
        tags = ID3()
        if title:
            tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.add(TPE1(encoding=3, text=artist))
        if album:
            tags.add(TALB(encoding=3, text=album))
        if cover:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_image(cover)))
        tags.save(path)
    return path


def _image(size) -> bytes:
    if isinstance(size, bytes):
        return size
    width, height = (60, 60) if size is True else size
    output = BytesIO()
    Image.new("RGB", (width, height)).save(output, "JPEG")
    return output.getvalue()


def _candidate(title, artist, duration, track_id="1"):
    return MusicMetadataCandidate(
        track_id=track_id, title=title, artist=artist, album="Album",
        year="2024", artwork_url=None, duration_seconds=duration,
    )


class TestFolderListing:
    def test_should_find_mp3_in_subfolders_when_ignoring_everything_else(self, tmp_path):
        _mp3(tmp_path, "b.mp3")
        _mp3(tmp_path, "sub/a.MP3")
        (tmp_path / "capa.jpg").write_bytes(b"jpg")

        assert music_files(tmp_path) == [tmp_path / "b.mp3", tmp_path / "sub/a.MP3"]


class TestFileBecomesPendingItem:
    def test_should_use_title_and_artist_when_they_are_embedded(self, tmp_path):
        path = _mp3(tmp_path, "Zimbra - Nao Sei Lidar (Audio Oficial).mp3",
                    title="Na Sei Lidar", artist="Zimbra", album="Azul", cover=(60, 60))

        item = read_local_item(path, None)

        assert item.title == "Na Sei Lidar"
        assert item.source_artist == "Zimbra"
        assert item.file_path == str(path)
        assert abs(item.duration - 3.0) < 0.1
        assert item.review_reasons == ()

    def test_should_search_by_filename_when_there_is_no_embedded_title(self, tmp_path):
        path = _mp3(tmp_path, "Pablo - Agora [Áudio Oficial].mp3")

        item = read_local_item(path, None)

        assert item.title == "Pablo - Agora [Áudio Oficial]"
        assert item.source_artist is None
        assert item.review_reasons == ("arquivo sem tags",)

    def test_should_use_the_supplied_artist_when_the_file_has_no_artist(self, tmp_path):
        item = read_local_item(_mp3(tmp_path, "Fragile.mp3"), "Laufey")

        assert item.source_artist == "Laufey"
        assert "artista informado por voce: Laufey" in item.review_reasons

    def test_should_not_replace_the_embedded_artist_when_one_is_supplied(self, tmp_path):
        path = _mp3(tmp_path, "Airbag.mp3", title="Airbag", artist="Radiohead")

        assert read_local_item(path, "Laufey").source_artist == "Radiohead"

    def test_should_count_as_not_supplied_when_the_artist_is_blank(self, tmp_path):
        assert read_local_item(_mp3(tmp_path, "Fragile.mp3"), "   ").source_artist is None

    def test_should_count_as_provisional_cover_when_it_is_the_video_thumbnail(self, tmp_path):
        path = _mp3(tmp_path, "Feel Good Drag.mp3", title="Feel Good Drag",
                    artist="Anberlin", album="New Surrender", cover=(160, 90))

        assert read_local_item(path, None).review_reasons == (
            "capa provisoria: miniatura do video",)

    def test_should_not_count_as_cover_when_the_image_does_not_open(self, tmp_path):
        path = _mp3(tmp_path, "Airbag.mp3", title="Airbag", artist="Radiohead",
                    album="OK Computer", cover=b"nao e imagem")

        assert read_local_item(path, None).review_reasons == ("capa ilegivel",)

    def test_should_say_what_is_missing_when_the_file_is_incomplete(self, tmp_path):
        path = _mp3(tmp_path, "Airbag.mp3", title="Airbag", artist="Radiohead")

        assert read_local_item(path, None).review_reasons == ("album ausente", "capa ausente")


class _FakeCatalog:
    def __init__(self, results):
        self._results = results
        self.applied = []

    def search(self, suggestion):
        return self._results.get(suggestion.title, [])

    def apply_to_mp3(self, path, candidate):
        self.applied.append((Path(path).name, candidate.title))
        return True


def _event(event_queue, event_type):
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return next(e for e in events if e["type"] == event_type)


class TestFolderFix:
    def test_should_apply_the_confident_one_when_sending_the_rest_to_review(self, tmp_path):
        _mp3(tmp_path, "Fragile.mp3", seconds=3.0)
        _mp3(tmp_path, "Hi.mp3", seconds=3.0)
        catalog = _FakeCatalog({
            "Fragile": [_candidate("Fragile", "Laufey", 4.0)],
            "Hi": [_candidate("Hi", "Laufey", 60.0)],
        })
        event_queue = queue.Queue()

        DownloadManager(event_queue, catalog).fix_folder(str(tmp_path), True, "Laufey")

        summary = _event(event_queue, "folder_checked")["summary"]
        assert catalog.applied == [("Fragile.mp3", "Fragile")]
        assert [i.title for i in summary.metadata_pending_items] == ["Hi"]
        assert summary.metadata_auto_applied == ["Laufey — Fragile"]
        assert summary.total_items == 2
        assert (tmp_path / "Fragile.mp3").exists()

    def test_should_write_nothing_when_the_user_did_not_ask(self, tmp_path):
        _mp3(tmp_path, "Fragile.mp3", seconds=3.0)
        catalog = _FakeCatalog({"Fragile": [_candidate("Fragile", "Laufey", 3.0)]})
        event_queue = queue.Queue()

        DownloadManager(event_queue, catalog).fix_folder(str(tmp_path), False, "Laufey")

        assert catalog.applied == []
        assert len(_event(event_queue, "folder_checked")["summary"].metadata_pending_items) == 1

    def test_should_not_bring_down_the_folder_when_a_file_is_unreadable(self, tmp_path):
        (tmp_path / "quebrado.mp3").write_bytes(b"nao e mp3")
        _mp3(tmp_path, "Fragile.mp3")
        event_queue = queue.Queue()

        DownloadManager(event_queue, _FakeCatalog({})).fix_folder(str(tmp_path), False, None)

        summary = _event(event_queue, "folder_checked")["summary"]
        assert summary.failed_items == ["quebrado.mp3"]
        assert [i.title for i in summary.metadata_pending_items] == ["Fragile"]

    def test_should_not_touch_the_file_when_it_is_complete(self, tmp_path):
        _mp3(tmp_path, "Airbag.mp3", seconds=3.0, title="Airbag", artist="Radiohead",
             album="OK Computer", cover=True)
        _mp3(tmp_path, "Lucky.mp3", seconds=3.0, title="Lucky", artist="Radiohead",
             album="OK Computer", cover=(160, 90))
        catalog = _FakeCatalog({
            "Airbag": [_candidate("Airbag", "Radiohead", 3.0)],
            "Lucky": [_candidate("Lucky", "Radiohead", 3.0)],
        })
        event_queue = queue.Queue()

        DownloadManager(event_queue, catalog).fix_folder(str(tmp_path), True, None)

        summary = _event(event_queue, "folder_checked")["summary"]
        assert catalog.applied == [("Lucky.mp3", "Lucky")]
        assert summary.already_complete_count == 1
        assert summary.metadata_pending_items == []

    def test_should_show_first_when_something_is_missing(self, tmp_path):
        _mp3(tmp_path, "a.mp3", title="So Capa", artist="X", album="Y", cover=(160, 90))
        _mp3(tmp_path, "b.mp3")
        event_queue = queue.Queue()

        DownloadManager(event_queue, _FakeCatalog({})).fix_folder(str(tmp_path), False, None)

        pending = _event(event_queue, "folder_checked")["summary"].metadata_pending_items
        assert [i.title for i in pending] == ["So Capa", "b"]

class TestFolderSummary:
    def test_should_count_applied_pending_and_unreadable_when_summarizing(self):
        from media_downloader.downloader import folder_summary_lines
        from media_downloader.models import DownloadSummary

        lines = folder_summary_lines(DownloadSummary(
            total_items=5, failed_items=["quebrado.mp3"], already_complete_count=1,
            metadata_auto_applied=["Laufey — Fragile"],
            metadata_pending_items=[object(), object()],
        ))

        assert lines[:5] == [
            "5 MP3 lido(s) na pasta",
            "==  1 ja completo(s), nao tocado(s)",
            "OK  1 com metadata aplicada automaticamente",
            "--  2 para confirmar na revisao",
            "--  1 arquivo(s) que nao deu para ler",
        ]
        assert "  - quebrado.mp3" in lines


class TestCatalogRateLimit:
    """Real case: while fixing 136 MP3 files in a row, iTunes started answering
    403 and 49 obvious tracks (Radiohead — Karma Police) ended up in review for
    no reason."""

    @staticmethod
    def _service(responses):
        from urllib.error import HTTPError

        from media_downloader.metadata import MusicMetadataService

        waits = []

        def respond(_url):
            response = responses.pop(0)
            if isinstance(response, int):
                raise HTTPError("https://itunes", response, "limite", {}, None)
            return response

        return MusicMetadataService(fetch_json=respond, sleep=waits.append), waits

    def test_should_wait_and_retry_when_the_catalog_rate_limits(self):
        from media_downloader.models import MusicSearchSuggestion

        ok = {"results": [{"trackId": 1, "trackName": "Karma Police", "artistName": "Radiohead"}]}
        service, waits = self._service([403, 429, ok])

        candidates = service.search(MusicSearchSuggestion("Karma Police", "Radiohead"))

        assert [c.title for c in candidates] == ["Karma Police"]
        assert len(waits) == 2 and waits[1] > waits[0]

    def test_should_give_up_when_the_error_is_not_a_rate_limit(self):
        import pytest
        from urllib.error import HTTPError

        from media_downloader.models import MusicSearchSuggestion

        service, waits = self._service([500, {"results": []}])

        with pytest.raises(HTTPError):
            service.search(MusicSearchSuggestion("Karma Police"))
        assert waits == []
