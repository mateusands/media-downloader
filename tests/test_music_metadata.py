"""
SDD — Spec: suggesting and choosing music metadata

CONTRACT
  When a finished MP3 does not contain enough music data, the application
  suggests a search based on the original title and presents candidates from
  the catalog. The user picks a candidate; inferred text never becomes a tag on
  its own.

WHY IT EXISTS
  Music videos often use titles like "ABBA - Dancing Queen (Official Music
  Video)". That is useful for finding the right recording, but not reliable
  enough to write artist or album without confirmation.

BUSINESS RULES
  - The "Artist - Track" pattern yields a suggested artist and track, removing
    known promotional suffixes.
  - A title without a separator is still searchable, but with no inferred artist.
  - A candidate shows the first available release; human confirmation is
    required before importing into the MP3.
  - When the title suggests an artist, the interface asks for confirmation in
    the catalog; it does not claim the artist is missing nor write the
    suggestion as a tag.
  - Each candidate's cover is loaded by the backend and published to the queue
    for the UI to show, with no network access from the graphical thread.
  - The query is free text. iTunes has no search syntax, so a title with quotes
    (`Best of You "Live"`) goes raw and works — on MusicBrainz this closed the
    Lucene phrase midway and returned zero results with no error at all.
  - A missing file is not a file without tags. `read_embedded` rejects an
    absent path (including the empty path) instead of returning empty metadata,
    otherwise the interface announces "artista gravado: nenhum" for a file that
    disappeared.
  - One iTunes result already includes track, artist, album, year, and artwork
    in a single response; the order the catalog returns is respected, with no
    reordering on our side.
  - The artwork comes as a 100 px thumbnail. The size is part of the URL, so the
    cover is requested at `ITUNES_ARTWORK_SIZE`; the file extension is
    preserved, since not all artwork is `.jpg`.

DIAGNOSING THE DOWNLOADED FILE (the reason for this review)
  yt-dlp only fills `artist` when the source itself publishes music data — on a
  regular YouTube video it is null, and FFmpegMetadata writes the channel name
  into the MP3 ("AudioslaveVEVO"). Saying "faltam: artista" for
  "Audioslave - Like a Stone (Official Video)" was false twice over: the file
  has an artist written, and the title carries the real artist.

BUSINESS RULES
  - An artist from the source (artist/artists/creator/creators) does not
    trigger review.
  - An artist derived from the channel is PROVISIONAL, not missing: the review
    names the channel that became the tag.
  - Only what has neither source nor channel is "missing".
  - A cover from the video thumbnail is PROVISIONAL. The YouTube thumbnail is
    16:9 — a video frame, not album art — and `EmbedThumbnail` embeds it as is.
    Treating "thumbnail exists" as "cover resolved" closed the review and left
    the file with a 776 KB 1280x720 PNG in place of the cover.
  - Without declared dimensions nothing is claimed about the cover: the
    diagnosis describes what was measured, not what is assumed.
  - The preview shows what is already written in the MP3 (artist and cover),
    read by the backend and published to the queue — the graphical thread does
    not open files.
"""

import queue
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from media_downloader.config import CATALOG_RESULTS_SHOWN, ITUNES_ARTWORK_SIZE
from media_downloader.downloader import DownloadManager
from media_downloader.metadata import (
    MusicMetadataService,
    metadata_review_detail,
    metadata_review_reasons,
    suggest_music_search,
)
from media_downloader.models import (
    EmbeddedMetadata,
    MetadataPendingItem,
    MusicMetadataCandidate,
    MusicSearchSuggestion,
)
from mutagen.id3 import APIC, ID3, TPE1


class TestMusicSearchSuggestion:
    def test_should_suggest_artist_and_track_when_the_title_has_a_known_format(self):
        suggestion = suggest_music_search("ABBA - Dancing Queen (Official Music Video)")

        assert suggestion.artist == "ABBA"
        assert suggestion.title == "Dancing Queen"

    def test_should_keep_the_title_without_inventing_an_artist_when_there_is_no_separator(self):
        suggestion = suggest_music_search("Dancing Queen official video")

        assert suggestion.artist is None
        assert suggestion.title == "Dancing Queen official video"

    def test_should_ask_for_confirmation_instead_of_saying_the_artist_is_missing_when_the_title_suggests_one(self):
        item = MetadataPendingItem(
            "Audioslave - Like a Stone (Official Video)", "/tmp/musica.mp3",
            ("artista provisorio: canal AudioslaveVEVO",),
        )

        detail = metadata_review_detail(item)

        assert "Audioslave" in detail
        assert "AudioslaveVEVO" in detail
        assert "ausente" not in detail.lower()
        assert "faltam: artista" not in detail.lower()


class TestDownloadedMp3Diagnosis:
    def test_should_report_a_provisional_artist_when_the_tag_came_from_the_channel(self):
        reasons = metadata_review_reasons({
            "title": "Audioslave - Like a Stone (Official Video)",
            "uploader": "AudioslaveVEVO",
            "thumbnail": "https://exemplo.com/capa.jpg",
        })

        assert reasons == ("artista provisorio: canal AudioslaveVEVO",)

    def test_should_report_a_missing_artist_when_there_is_neither_artist_nor_channel(self):
        reasons = metadata_review_reasons({
            "title": "Faixa sem dados", "thumbnail": "https://exemplo.com/capa.jpg",
        })

        assert reasons == ("artista ausente",)

    def test_should_not_ask_for_review_when_the_source_provides_artist_and_cover(self):
        reasons = metadata_review_reasons({
            "title": "Dancing Queen", "artist": "ABBA", "uploader": "ABBAVEVO",
            "thumbnail": "https://exemplo.com/capa.jpg",
        })

        assert reasons == ()

    def test_should_report_a_missing_cover_when_the_source_publishes_no_thumbnail(self):
        reasons = metadata_review_reasons({"title": "Dancing Queen", "artist": "ABBA"})

        assert reasons == ("capa ausente",)

    def test_should_report_a_provisional_cover_when_the_thumbnail_has_the_video_shape(self):
        reasons = metadata_review_reasons({
            "title": "PinkPantheress - Girl Like Me (Official Video)",
            "artist": "PinkPantheress",
            "thumbnails": [{"width": 640, "height": 360}, {"width": 1920, "height": 1080}],
        })

        assert reasons == ("capa provisoria: miniatura do video",)

    def test_should_not_complain_about_the_cover_when_the_thumbnail_is_square(self):
        reasons = metadata_review_reasons({
            "title": "Like a Stone", "artist": "Audioslave",
            "thumbnails": [{"width": 544, "height": 544}],
        })

        assert reasons == ()

    def test_should_not_assume_the_shape_when_the_thumbnail_has_no_dimensions(self):
        reasons = metadata_review_reasons({
            "title": "Like a Stone", "artist": "Audioslave",
            "thumbnail": "https://exemplo.com/capa.jpg",
        })

        assert reasons == ()


_ITUNES_RESULT = {
    "trackId": 265452073,
    "trackName": "Like a Stone",
    "artistName": "Audioslave",
    "collectionName": "Audioslave",
    "releaseDate": "2002-11-19T08:00:00Z",
    "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/Music/dj.jpg/100x100bb.jpg",
}


class TestCatalogCandidate:
    def test_should_map_track_artist_album_and_year_from_the_result(self):
        candidate = MusicMetadataCandidate.from_itunes(_ITUNES_RESULT)

        assert candidate.track_id == "265452073"
        assert candidate.title == "Like a Stone"
        assert candidate.artist == "Audioslave"
        assert candidate.album == "Audioslave"
        assert candidate.year == "2002"

    def test_should_request_artwork_at_a_usable_size_instead_of_the_thumbnail(self):
        candidate = MusicMetadataCandidate.from_itunes(_ITUNES_RESULT)

        assert candidate.artwork_url.endswith(f"/{ITUNES_ARTWORK_SIZE}x{ITUNES_ARTWORK_SIZE}bb.jpg")

    def test_should_preserve_the_artwork_extension_when_changing_the_size(self):
        candidate = MusicMetadataCandidate.from_itunes(
            {**_ITUNES_RESULT, "artworkUrl100": "https://exemplo.com/arte/100x100bb.png"})

        assert candidate.artwork_url == (
            f"https://exemplo.com/arte/{ITUNES_ARTWORK_SIZE}x{ITUNES_ARTWORK_SIZE}bb.png")

    def test_should_accept_a_result_without_artwork(self):
        candidate = MusicMetadataCandidate.from_itunes(
            {k: v for k, v in _ITUNES_RESULT.items() if k != "artworkUrl100"})

        assert candidate.artwork_url is None

    def test_should_name_the_unknown_when_the_result_is_incomplete(self):
        candidate = MusicMetadataCandidate.from_itunes({"trackId": 1})

        assert candidate.artist == "Artista desconhecido"
        assert candidate.title == "Faixa sem titulo"
        assert candidate.album is None
        assert candidate.year is None


class TestCandidateCover:
    def _candidate(self, artwork_url):
        return MusicMetadataCandidate(
            track_id="1", title="Like a Stone", artist="Audioslave",
            album="Audioslave", year="2002", artwork_url=artwork_url)

    def test_should_return_the_candidate_artwork(self):
        requests = []

        def fetch(url):
            requests.append(url)
            return b"capa", "image/jpeg"

        service = MusicMetadataService(fetch_cover=fetch)

        assert service.get_cover_preview(self._candidate("https://exemplo.com/600.jpg")) == (
            b"capa", "image/jpeg")
        assert requests == ["https://exemplo.com/600.jpg"]

    def test_should_report_absence_when_the_candidate_has_no_artwork(self):
        assert MusicMetadataService().get_cover_preview(self._candidate(None)) is None

    def test_should_report_absence_when_the_artwork_cannot_be_downloaded(self):
        def fetch(_url):
            raise OSError("404")

        service = MusicMetadataService(fetch_cover=fetch)

        assert service.get_cover_preview(self._candidate("https://exemplo.com/600.jpg")) is None


class TestCatalogSearch:
    def _service(self, results):
        urls = []

        def respond(url: str):
            urls.append(url)
            return {"resultCount": len(results), "results": results}

        return MusicMetadataService(fetch_json=respond), urls

    def test_should_turn_itunes_results_into_candidates(self):
        service, urls = self._service([_ITUNES_RESULT])

        candidates = service.search(
            MusicSearchSuggestion(title="Like a Stone", artist="Audioslave"))

        assert [candidate.title for candidate in candidates] == ["Like a Stone"]
        params = parse_qs(urlparse(urls[0]).query)
        assert params["term"] == ["Audioslave Like a Stone"]
        assert params["entity"] == ["song"]
        assert params["media"] == ["music"]

    def test_should_search_by_title_only_when_the_title_suggests_no_artist(self):
        service, urls = self._service([])

        service.search(MusicSearchSuggestion(title="Dancing Queen official video"))

        assert parse_qs(urlparse(urls[0]).query)["term"] == ["Dancing Queen official video"]

    def test_should_send_title_quotes_without_special_handling(self):
        # iTunes takes free text; the escaping MusicBrainz required is gone.
        service, urls = self._service([])

        service.search(
            MusicSearchSuggestion(title='Best of You "Live"', artist="Foo Fighters"))

        assert parse_qs(urlparse(urls[0]).query)["term"] == ['Foo Fighters Best of You "Live"']

    def test_should_limit_what_it_shows_to_the_number_of_results_displayed(self):
        service, urls = self._service([_ITUNES_RESULT] * 10)

        candidates = service.search(MusicSearchSuggestion(title="Like a Stone"))

        assert parse_qs(urlparse(urls[0]).query)["limit"] == [str(CATALOG_RESULTS_SHOWN)]
        assert len(candidates) == CATALOG_RESULTS_SHOWN


class TestCandidateImport:
    def test_should_write_to_the_mp3_only_the_chosen_candidate_data(self, tmp_path):
        mp3 = tmp_path / "dancing-queen.mp3"
        ID3().save(mp3)
        candidate = MusicMetadataCandidate(
            track_id="265452073", title="Dancing Queen", artist="ABBA",
            album="Arrival", year="1976", artwork_url=None,
        )

        MusicMetadataService().apply_to_mp3(mp3, candidate)

        tags = ID3(mp3)
        assert tags["TIT2"].text == ["Dancing Queen"]
        assert tags["TPE1"].text == ["ABBA"]
        assert tags["TALB"].text == ["Arrival"]
        assert [str(value) for value in tags["TDRC"].text] == ["1976"]

    def test_should_embed_the_cover_when_the_candidate_has_a_release(self, tmp_path):
        mp3 = tmp_path / "dancing-queen.mp3"
        ID3().save(mp3)
        candidate = MusicMetadataCandidate(
            track_id="265452073", title="Dancing Queen", artist="ABBA",
            album="Arrival", year="1976", artwork_url="https://exemplo.com/600x600bb.jpg",
        )
        service = MusicMetadataService(fetch_cover=lambda _release_id: (b"capa", "image/jpeg"))

        service.apply_to_mp3(mp3, candidate)

        tags = ID3(mp3)
        assert tags.getall("APIC")[0].data == b"capa"


class TestPreviewOfWhatIsAlreadyInTheFile:
    def test_should_read_artist_and_cover_written_in_the_mp3(self, tmp_path):
        mp3 = tmp_path / "like-a-stone.mp3"
        tags = ID3()
        tags.add(TPE1(encoding=3, text="AudioslaveVEVO"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=b"capa"))
        tags.save(mp3)

        embedded = MusicMetadataService().read_embedded(mp3)

        assert embedded.artist == "AudioslaveVEVO"
        assert embedded.cover == b"capa"

    def test_should_report_a_file_without_tags_without_breaking(self, tmp_path):
        mp3 = tmp_path / "sem-tags.mp3"
        mp3.write_bytes(b"")

        embedded = MusicMetadataService().read_embedded(mp3)

        assert embedded == EmbeddedMetadata()

    def test_should_reject_a_missing_file_instead_of_saying_it_is_empty(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            MusicMetadataService().read_embedded(tmp_path / "nao-existe.mp3")

    def test_should_reject_a_pending_item_without_a_file_path(self):
        with pytest.raises(FileNotFoundError):
            MusicMetadataService().read_embedded(Path(""))

    def test_should_publish_the_preview_to_the_queue_without_the_ui_opening_the_file(self):
        class CatalogWithFile:
            def read_embedded(self, _file_path):
                return EmbeddedMetadata(artist="AudioslaveVEVO", cover=b"capa")

        event_queue = queue.Queue()
        manager = DownloadManager(event_queue, metadata_service=CatalogWithFile())
        pending = MetadataPendingItem(
            "Audioslave - Like a Stone", "/tmp/musica.mp3",
            ("artista provisorio: canal AudioslaveVEVO",),
        )

        manager.load_embedded_metadata(pending)

        event = event_queue.get_nowait()
        assert event["type"] == "metadata_embedded"
        assert event["pending_item"] == pending
        assert event["embedded"].artist == "AudioslaveVEVO"
        assert event["embedded"].cover == b"capa"

    def test_should_notify_when_the_file_cannot_be_read(self):
        class BrokenCatalog:
            def read_embedded(self, _file_path):
                raise OSError("file removed")

        event_queue = queue.Queue()
        manager = DownloadManager(event_queue, metadata_service=BrokenCatalog())
        pending = MetadataPendingItem("Faixa", "/tmp/sumiu.mp3", ("capa ausente",))

        manager.load_embedded_metadata(pending)

        event = event_queue.get_nowait()
        assert event["type"] == "metadata_embedded_unavailable"
        assert event["pending_item"] == pending


class TestMetadataSearchEvents:
    def test_should_publish_candidates_to_the_queue_without_touching_the_ui(self):
        candidate = MusicMetadataCandidate(
            track_id="265452073", title="Dancing Queen", artist="ABBA",
            album="Arrival", year="1976", artwork_url="https://exemplo.com/600x600bb.jpg",
        )

        class FakeCatalog:
            def search(self, _suggestion):
                return [candidate]

        event_queue = queue.Queue()
        manager = DownloadManager(event_queue, metadata_service=FakeCatalog())
        pending = MetadataPendingItem("ABBA - Dancing Queen", "/tmp/musica.mp3", ("artista",))

        manager.search_metadata(pending)

        event = event_queue.get_nowait()
        assert event["type"] == "metadata_results"
        assert event["pending_item"] == pending
        assert event["candidates"] == [candidate]

    def test_should_publish_the_cover_preview_to_the_queue_without_accessing_the_ui(self):
        candidate = MusicMetadataCandidate(
            track_id="265452073", title="Dancing Queen", artist="ABBA",
            album="Arrival", year="1976", artwork_url="https://exemplo.com/600x600bb.jpg",
        )

        class CatalogWithCover:
            def get_cover_preview(self, _candidate):
                return b"capa", "image/jpeg"

        event_queue = queue.Queue()
        manager = DownloadManager(event_queue, metadata_service=CatalogWithCover())

        manager.load_metadata_cover_preview(candidate)

        event = event_queue.get_nowait()
        assert event["type"] == "metadata_cover_preview"
        assert event["candidate"] == candidate
        assert event["data"] == b"capa"
        assert event["mime"] == "image/jpeg"
