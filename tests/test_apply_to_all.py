"""
SDD — Specification: apply the catalog suggestion to every item under review

CONTRACT
  - `suggested_match(item, candidates)` returns the candidate the app suggests:
    the `confident_match` one when it exists; otherwise, the first catalog entry
    where artist **and** title match, ignoring duration. Without one, `None`.
  - `DownloadManager.apply_suggested(items)` searches and applies the suggestion
    for each item, publishing `metadata_bulk_applied` per resolved item,
    `metadata_bulk_progress` at each step, and `metadata_bulk_done` at the end.

WHY IT EXISTS
  A 136-track playlist ended with 75 items under review — almost all of them
  with the right artist and title and only the video's provisional cover.
  Confirming them one by one (search, wait, import) took the whole afternoon.
  The user asked for a button to accept the app's default all at once.

BUSINESS RULES
  - The button is the authorization: `confident_match` (applying without
    asking) still requires all three criteria. The relaxed criterion exists
    only here, because the user saw the list and clicked.
  - Only duration is relaxed — it is what separates a music video (with an
    intro) from a track. Artist and title remain required, and a different
    version (live, remix, acoustic) never matches the studio one.
  - Without a known artist, nothing is applied: a title alone matches a cover.
  - Whatever has no suggestion stays under review; a catalog failure on one
    item does not stop the others. Stopping interrupts between items.
"""

import queue

from media_downloader.downloader import DownloadManager
from media_downloader.metadata import suggested_match
from media_downloader.models import MetadataPendingItem, MusicMetadataCandidate


def _item(title="Feel Good Drag", artist="Anberlin", duration=230.0):
    return MetadataPendingItem(
        title=title, file_path=f"/tmp/{title}.mp3",
        review_reasons=("capa provisoria: miniatura do video",),
        duration=duration, source_artist=artist,
    )


def _candidate(title="Feel Good Drag", artist="Anberlin", duration=201.0, track_id="1"):
    return MusicMetadataCandidate(
        track_id=track_id, title=title, artist=artist, album="New Surrender",
        year="2008", artwork_url=None, duration_seconds=duration,
    )


class TestAppSuggestion:
    def test_should_accept_artist_and_title_when_duration_differs(self):
        candidate = _candidate(duration=201.0)
        assert suggested_match(_item(duration=230.0), [candidate]) == candidate

    def test_should_prefer_the_one_whose_duration_also_matches(self):
        far = _candidate(duration=260.0, track_id="far")
        near = _candidate(duration=231.0, track_id="near")
        assert suggested_match(_item(), [far, near]) == near

    def test_should_not_swap_studio_for_live_version(self):
        assert suggested_match(_item(), [_candidate(title="Feel Good Drag (Live)")]) is None

    def test_should_not_apply_when_artist_is_unknown(self):
        assert suggested_match(_item(artist=None), [_candidate()]) is None

    def test_should_not_apply_when_artist_is_different(self):
        assert suggested_match(_item(), [_candidate(artist="Outra Banda")]) is None


class _FakeCatalog:
    def __init__(self, results, fail_on=()):
        self._results = results
        self._fail_on = fail_on
        self.applied = []

    def search(self, suggestion):
        if suggestion.title in self._fail_on:
            raise OSError("catalog is down")
        return self._results.get(suggestion.title, [])

    def apply_to_mp3(self, path, candidate):
        self.applied.append(candidate.title)
        return True


def _events(event_queue):
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events


class TestApplyToAll:
    def test_should_apply_items_with_suggestion_and_keep_the_rest(self):
        with_match = _item()
        without_match = _item(title="Re-Education", artist="Rise Against")
        failing = _item(title="Falling On", artist="Finger Eleven")
        catalog = _FakeCatalog(
            {"Feel Good Drag": [_candidate()]}, fail_on=("Falling On",))
        event_queue = queue.Queue()

        DownloadManager(event_queue, catalog).apply_suggested(
            [with_match, without_match, failing])

        events = _events(event_queue)
        assert catalog.applied == ["Feel Good Drag"]
        assert [e["pending_item"] for e in events
                if e["type"] == "metadata_bulk_applied"] == [with_match]
        done = events[-1]
        assert done["type"] == "metadata_bulk_done"
        assert (done["applied"], done["kept"]) == (1, 2)

    def test_should_stop_between_items_when_requested(self):
        catalog = _FakeCatalog({"Feel Good Drag": [_candidate()]})
        event_queue = queue.Queue()
        manager = DownloadManager(event_queue, catalog)
        original_search = catalog.search

        def search_and_request_stop(suggestion):
            manager.stop_bulk()
            return original_search(suggestion)

        catalog.search = search_and_request_stop

        manager.apply_suggested([_item(), _item(title="Outra")])

        assert catalog.applied == ["Feel Good Drag"]
        assert _events(event_queue)[-1]["kept"] == 1
