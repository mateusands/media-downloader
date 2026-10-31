"""
SDD — Spec: metadata applied automatically when the match is confident

CONTRACT
  - `confident_match(item, candidates)` returns the catalog candidate that
    confidently matches the downloaded MP3, or `None`.
  - `search_suggestion(item)` builds the search from the title and, when the
    title carries no artist, from the artist the source itself published.
  - With automatic application enabled, `DownloadManager` searches and applies,
    at the end of the download, the confident candidate of each pending item;
    only what is left goes to review. The summary counts what was applied
    automatically.

WHY IT EXISTS
  In a playlist, every pending MP3 required opening the search and choosing
  among five results — even when the first one was obviously the right track.
  The user asked for the obvious case to resolve itself and for only the
  ambiguous one to reach them, as beets and MusicBrainz Picard do.

BUSINESS RULES
  - "Confident" requires all three: artist, title, and duration (up to 5 s of
    difference — the official music video is usually a few seconds longer than
    the album track). Two out of three is ambiguous and goes to review.
  - A different version is never the same track: "(Live at Wembley)", remix,
    acoustic. The only suffix ignored is one that does not change the audio
    (remaster, featured artist).
  - Without a known artist, it is never confident: a title alone matches
    re-recordings, covers, and namesakes.
  - Differences in case, accents, a leading "The", "feat.", and a
    parenthetical suffix ("Remastered 2011") do not undo the match.
  - Automatic application is the user's explicit choice, per download. When
    disabled, nothing is written without confirmation, as before.
  - A catalog failure on one item does not bring down the others: the item
    stays pending.
  - Cancelling interrupts the automatic search; whatever is left stays pending.
"""

import queue

from media_downloader.downloader import DownloadManager, summary_lines
from media_downloader.metadata import confident_match, search_suggestion
from media_downloader.models import (
    DownloadSummary,
    MetadataPendingItem,
    MusicMetadataCandidate,
    MusicSearchSuggestion,
)


def _item(title="Queen - Bohemian Rhapsody", duration=354.0, source_artist=None):
    return MetadataPendingItem(
        title=title, file_path=f"/tmp/{title}.mp3",
        review_reasons=("artista ausente",), duration=duration,
        source_artist=source_artist,
    )


def _candidate(title="Bohemian Rhapsody", artist="Queen", duration=355.0, track_id="1"):
    return MusicMetadataCandidate(
        track_id=track_id, title=title, artist=artist, album="A Night at the Opera",
        year="1975", artwork_url=None, duration_seconds=duration,
    )


class TestConfidentMatch:
    def test_should_accept_when_artist_title_and_duration_match(self):
        candidate = _candidate()
        assert confident_match(_item(), [candidate]) == candidate

    def test_should_reject_when_the_duration_differs_by_more_than_five_seconds(self):
        assert confident_match(_item(duration=354.0), [_candidate(duration=360.0)]) is None

    def test_should_accept_when_the_official_video_has_a_few_extra_seconds(self):
        # Real case: the video is 359 s and the album track 355.155 s.
        candidate = _candidate(duration=355.155)
        assert confident_match(_item(duration=359.0), [candidate]) == candidate

    def test_should_reject_when_it_is_the_live_version_even_with_similar_duration(self):
        live = _candidate(title="Bohemian Rhapsody (Live At Wembley Stadium / July 1986)")
        assert confident_match(_item(), [live]) is None

    def test_should_reject_when_it_is_a_remix_or_acoustic_version(self):
        for title in ("Bohemian Rhapsody (Remix)", "Bohemian Rhapsody - Acoustic Version",
                      "Bohemian Rhapsody (ACAPELLA)", "Bohemian Rhapsody (A Capella)"):
            assert confident_match(_item(), [_candidate(title=title)]) is None, title

    def test_should_reject_when_the_artist_is_different(self):
        assert confident_match(_item(), [_candidate(artist="Panic! at the Disco")]) is None

    def test_should_reject_when_the_title_is_different(self):
        assert confident_match(_item(), [_candidate(title="Somebody to Love")]) is None

    def test_should_reject_when_the_artist_is_unknown(self):
        assert confident_match(_item(title="Bohemian Rhapsody"), [_candidate()]) is None

    def test_should_reject_when_either_side_lacks_the_duration(self):
        assert confident_match(_item(duration=None), [_candidate()]) is None
        assert confident_match(_item(), [_candidate(duration=None)]) is None

    def test_should_ignore_case_accents_the_and_suffix_when_matching(self):
        item = _item(title="the beatles - Hey Jude", duration=431.0)
        candidate = _candidate(
            title="Hey Jude (Remastered 2015)", artist="The Beatles", duration=429.0)
        assert confident_match(item, [candidate]) == candidate

    def test_should_ignore_the_featured_artist_when_it_is_in_the_title(self):
        item = _item(title="Mark Ronson - Uptown Funk ft. Bruno Mars", duration=270.0)
        candidate = _candidate(
            title="Uptown Funk (feat. Bruno Mars)", artist="Mark Ronson", duration=270.0)
        assert confident_match(item, [candidate]) == candidate

    def test_should_accept_the_main_artist_when_the_credit_is_shared(self):
        item = _item(title="Queen - Under Pressure", duration=248.0)
        candidate = _candidate(
            title="Under Pressure", artist="Queen & David Bowie", duration=248.0)
        assert confident_match(item, [candidate]) == candidate

    def test_should_pick_the_first_confident_one_when_following_catalog_order(self):
        live = _candidate(duration=380.0, track_id="ao-vivo")
        studio = _candidate(track_id="estudio")
        compilation = _candidate(track_id="coletanea")
        assert confident_match(_item(), [live, studio, compilation]) == studio

    def test_should_use_the_artist_when_published_by_the_source(self):
        item = _item(title="Bohemian Rhapsody", source_artist="Queen")
        candidate = _candidate()
        assert confident_match(item, [candidate]) == candidate


class TestPendingItemSearchSuggestion:
    def test_should_recognize_the_dash_when_it_separates_the_artist(self):
        # Real case: Queen's official video uses "–", not "-".
        for title in ("Queen – Bohemian Rhapsody", "Queen — Bohemian Rhapsody"):
            assert search_suggestion(_item(title=title)) == MusicSearchSuggestion(
                title="Bohemian Rhapsody", artist="Queen"), title

    def test_should_use_the_artist_when_it_is_in_the_title(self):
        assert search_suggestion(_item()) == MusicSearchSuggestion(
            title="Bohemian Rhapsody", artist="Queen")

    def test_should_fill_in_the_source_artist_when_the_title_has_none(self):
        item = _item(title="Bohemian Rhapsody", source_artist="Queen")
        assert search_suggestion(item) == MusicSearchSuggestion(
            title="Bohemian Rhapsody", artist="Queen")

    def test_should_keep_only_the_title_when_nobody_reports_an_artist(self):
        assert search_suggestion(_item(title="Bohemian Rhapsody")) == MusicSearchSuggestion(
            title="Bohemian Rhapsody")


class TestPendingItemKeepsWhatMatchingNeeds:
    def test_should_keep_duration_and_source_artist_when_the_download_finishes(self):
        summary = DownloadSummary(total_items=1)
        hook = DownloadManager(queue.Queue())._make_progress_hook(summary, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/Bohemian Rhapsody.webm",
            "info_dict": {
                "id": "q1", "title": "Bohemian Rhapsody", "duration": 354,
                "artists": ["Queen"], "thumbnail": "https://exemplo.com/capa.jpg",
                "thumbnails": [{"url": "x", "width": 1280, "height": 720}],
            },
        })

        pending = summary.metadata_pending_items[0]
        assert pending.duration == 354
        assert pending.source_artist == "Queen"

    def test_should_carry_the_catalog_duration_when_building_a_candidate(self):
        candidate = MusicMetadataCandidate.from_itunes({
            "trackId": 1, "trackName": "Bohemian Rhapsody", "artistName": "Queen",
            "trackTimeMillis": 354320,
        })
        assert candidate.duration_seconds == 354.32


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
        self.applied.append((str(path), candidate))
        return True


class TestAutomaticApplicationAtEndOfDownload:
    def _summary(self, *items):
        return DownloadSummary(
            downloaded_count=len(items), total_items=len(items),
            metadata_pending_items=list(items))

    def test_should_apply_the_confident_one_when_leaving_the_ambiguous_for_review(self):
        confident = _item()
        ambiguous = _item(title="Queen - Somebody to Love", duration=296.0)
        catalog = _FakeCatalog({
            "Bohemian Rhapsody": [_candidate()],
            "Somebody to Love": [_candidate(title="Somebody to Love", duration=330.0)],
        })
        summary = self._summary(confident, ambiguous)

        DownloadManager(queue.Queue(), catalog)._auto_apply_metadata(summary)

        assert [c.title for _, c in catalog.applied] == ["Bohemian Rhapsody"]
        assert summary.metadata_pending_items == [ambiguous]
        assert summary.metadata_auto_applied == ["Queen — Bohemian Rhapsody"]

    def test_should_keep_the_item_pending_and_carry_on_when_its_search_failed(self):
        failing = _item(title="Queen - Somebody to Love", duration=296.0)
        confident = _item()
        catalog = _FakeCatalog(
            {"Bohemian Rhapsody": [_candidate()]}, fail_on=("Somebody to Love",))
        summary = self._summary(failing, confident)

        DownloadManager(queue.Queue(), catalog)._auto_apply_metadata(summary)

        assert summary.metadata_pending_items == [failing]
        assert summary.failed_items == []

    def test_should_stop_when_the_download_was_cancelled(self):
        catalog = _FakeCatalog({"Bohemian Rhapsody": [_candidate()]})
        summary = self._summary(_item())
        manager = DownloadManager(queue.Queue(), catalog)
        manager.cancel()

        manager._auto_apply_metadata(summary)

        assert catalog.applied == []
        assert len(summary.metadata_pending_items) == 1

    def test_should_count_in_the_summary_when_metadata_was_applied_automatically(self):
        lines = summary_lines(DownloadSummary(
            downloaded_count=2, metadata_auto_applied=["Queen — Bohemian Rhapsody"]))
        assert "OK  1 MP3 com metadata aplicada automaticamente" in lines

    def test_should_not_mention_automatic_application_when_nothing_was_applied(self):
        lines = summary_lines(DownloadSummary(downloaded_count=2))
        assert not any("automaticamente" in line for line in lines)


class TestUserChoice:
    """With automatic application disabled, nothing is written without confirmation."""

    def _manager(self, monkeypatch, tmp_path, catalog):
        class _FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, url, download=False):
                return {"id": "q1", "title": "Queen - Bohemian Rhapsody"}

            def download(self, urls):
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "finished", "filename": "/tmp/q.webm",
                          "info_dict": {"id": "q1", "title": "Queen - Bohemian Rhapsody",
                                        "duration": 354}})

        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _FakeYoutubeDL)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp3", False): tmp_path})
        return DownloadManager(queue.Queue(), catalog)

    def test_should_write_nothing_when_the_user_did_not_ask(self, monkeypatch, tmp_path):
        catalog = _FakeCatalog({"Bohemian Rhapsody": [_candidate()]})
        manager = self._manager(monkeypatch, tmp_path, catalog)

        manager.download("https://www.youtube.com/watch?v=q1", "mp3",
                         include_metadata=True, auto_metadata=False)

        assert catalog.applied == []

    def test_should_apply_when_the_user_asked(self, monkeypatch, tmp_path):
        catalog = _FakeCatalog({"Bohemian Rhapsody": [_candidate()]})
        manager = self._manager(monkeypatch, tmp_path, catalog)

        manager.download("https://www.youtube.com/watch?v=q1", "mp3",
                         include_metadata=True, auto_metadata=True)

        assert len(catalog.applied) == 1
