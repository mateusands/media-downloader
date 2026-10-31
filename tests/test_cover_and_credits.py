"""
SDD — Specification: long credits, "(Album Version)", unresponsive catalog,
and a cover cropped from the thumbnail

CONTRACT
  - `search_suggestion` uses only the first name of a comma-separated credit
    that came from the source ("Jeremy Renner, Brandon Sammons, ...").
  - "(Album Version)" is not a different version: it is the studio track.
  - `apply_suggested` separately counts the item whose catalog query failed
    (`failed`), so the review can say "try again" instead of "no suggestion".
  - `square_cover(image)` crops the square center; `crop_cover_to_square`
    replaces the MP3's cover with that crop without touching the other tags.

WHY IT EXISTS
  In a fixed playlist, three cases were left for the user to solve by hand:
  - YouTube credits every composer in the artist field. With the full credit,
    the iTunes search came back empty; with just "Jeremy Renner" the track
    shows up with the exact duration.
  - "Let Me Be Myself (Album Version)" was treated as a version, like "(Live)".
  - The catalog blocked (403) and the batch announced "0 applied; 5 without a
    confident suggestion" — the user fixed by hand what another click would.
  A track not sold in the store will never have catalog artwork; the 16:9
  thumbnail cropped at the center is the best square cover available — by the
  user's choice.

BUSINESS RULES
  - A credit taken from the title ("Queen & David Bowie - ...") is not cut:
    only the source's, which is a list of names.
  - "(Live)", remix, and acoustic remain different versions.
  - Cropping is an explicit action (button); it never happens on its own.
  - A cover that is already square is not cropped again.
"""

import queue
from io import BytesIO
from urllib.error import HTTPError

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from PIL import Image

from media_downloader.downloader import DownloadManager
from media_downloader.library import crop_cover_to_square, read_local_item, square_cover
from media_downloader.metadata import confident_match, search_suggestion
from media_downloader.models import (
    MetadataPendingItem,
    MusicMetadataCandidate,
    MusicSearchSuggestion,
)

_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 413


def _item(title, artist=None, duration=200.0):
    return MetadataPendingItem(
        title=title, file_path=f"/tmp/{title}.mp3", review_reasons=(),
        duration=duration, source_artist=artist)


def _candidate(title, artist, duration=200.0):
    return MusicMetadataCandidate(
        track_id="1", title=title, artist=artist, album="A", year="2020",
        artwork_url=None, duration_seconds=duration)


def _jpeg(width, height, color=(255, 0, 0)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), color).save(output, "JPEG")
    return output.getvalue()


class TestLongCredit:
    def test_should_search_only_by_first_name_when_credit_comes_from_source(self):
        item = _item("Main Attraction", "Jeremy Renner, Eric Zareski, Jeremy Lee Renner")
        assert search_suggestion(item) == MusicSearchSuggestion(
            title="Main Attraction", artist="Jeremy Renner")

    def test_should_not_cut_credit_when_it_comes_from_title(self):
        item = _item("Queen & David Bowie - Under Pressure")
        assert search_suggestion(item).artist == "Queen & David Bowie"

    def test_should_match_catalog_when_only_first_name_matches(self):
        item = _item("Main Attraction", "Jeremy Renner, Eric Zareski", duration=203.0)
        candidate = _candidate("Main Attraction", "Jeremy Renner", duration=202.878)
        assert confident_match(item, [candidate]) == candidate


class TestAlbumVersion:
    def test_should_match_studio_track_when_title_says_album_version(self):
        item = _item("Let Me Be Myself (Album Version)", "3 Doors Down")
        candidate = _candidate("Let Me Be Myself", "3 Doors Down")
        assert confident_match(item, [candidate]) == candidate

    def test_should_remain_a_different_version_when_title_says_live(self):
        item = _item("Let Me Be Myself (Live)", "3 Doors Down")
        assert confident_match(item, [_candidate("Let Me Be Myself", "3 Doors Down")]) is None


class _BlockedCatalog:
    def search(self, suggestion):
        raise HTTPError("https://itunes", 403, "limit", {}, None)

    def apply_to_mp3(self, path, candidate):
        raise AssertionError("should not apply")


class TestUnresponsiveCatalog:
    def test_should_count_separately_when_catalog_did_not_respond(self):
        event_queue = queue.Queue()

        DownloadManager(event_queue, _BlockedCatalog()).apply_suggested(
            [_item("Chalk Outline", "Three Days Grace")])

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        done = events[-1]
        assert done["type"] == "metadata_bulk_done"
        assert (done["applied"], done["kept"], done["failed"]) == (0, 1, 1)


class TestCroppedCover:
    def test_should_crop_square_center_when_cover_is_a_thumbnail(self):
        # Black side bars and a red center, as in a cover-art video.
        image = Image.new("RGB", (160, 90), (0, 0, 0))
        image.paste((255, 0, 0), (35, 0, 125, 90))
        output = BytesIO()
        image.save(output, "PNG")

        with Image.open(BytesIO(square_cover(output.getvalue()))) as cropped:
            assert cropped.size == (90, 90)
            red = cropped.convert("RGB").getpixel((2, 45))
            assert red[0] > 200 and red[1] < 60

    def test_should_replace_only_the_cover_when_cropping_an_mp3(self, tmp_path):
        path = tmp_path / "Main Attraction.mp3"
        path.write_bytes(_FRAME * 120)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Main Attraction"))
        tags.add(TPE1(encoding=3, text="Jeremy Renner"))
        tags.add(TALB(encoding=3, text="The Medicine"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(160, 90)))
        tags.save(path)

        crop_cover_to_square(path)

        assert read_local_item(path, None).review_reasons == ()
        assert str(ID3(path)["TALB"]) == "The Medicine"
        assert len(ID3(path).getall("APIC")) == 1


class TestBatchCrop:
    def test_should_crop_only_files_with_thumbnail_and_count_failures(self, tmp_path):
        with_thumbnail = tmp_path / "a.mp3"
        with_thumbnail.write_bytes(_FRAME * 120)
        tags = ID3()
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(160, 90)))
        tags.save(with_thumbnail)
        without_cover = tmp_path / "b.mp3"
        without_cover.write_bytes(_FRAME * 120)
        items = [
            MetadataPendingItem("a", str(with_thumbnail), ("capa provisoria: miniatura do video",)),
            MetadataPendingItem("b", str(without_cover), ("capa provisoria: miniatura do video",)),
        ]
        event_queue = queue.Queue()

        DownloadManager(event_queue).crop_covers(items)

        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        assert [e["pending_item"].title for e in events
                if e["type"] == "metadata_cover_cropped"] == ["a"]
        assert (events[-1]["type"], events[-1]["cropped"], events[-1]["failed"]) == (
            "metadata_crop_done", 1, 1)


class TestNearlySquareArtwork:
    """Real case: the iTunes artwork for "Transit of Venus" comes as 600x538 and
    was treated as a video thumbnail — the file never left review."""

    def test_should_count_as_cover_when_catalog_artwork_is_nearly_square(self, tmp_path):
        path = tmp_path / "Chalk Outline.mp3"
        path.write_bytes(_FRAME * 120)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Chalk Outline"))
        tags.add(TPE1(encoding=3, text="Three Days Grace"))
        tags.add(TALB(encoding=3, text="Transit of Venus"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(600, 538)))
        tags.save(path)

        assert read_local_item(path, None).review_reasons == ()

    def test_should_remain_provisional_when_thumbnail_is_4_by_3(self, tmp_path):
        path = tmp_path / "Clip.mp3"
        path.write_bytes(_FRAME * 120)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Clip"))
        tags.add(TPE1(encoding=3, text="X"))
        tags.add(TALB(encoding=3, text="Y"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(480, 360)))
        tags.save(path)

        assert read_local_item(path, None).review_reasons == (
            "capa provisoria: miniatura do video",)
