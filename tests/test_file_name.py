"""
SDD — Spec: the MP3 is named after the song

CONTRACT
  - `song_title(source_title)` removes from the video title the artist and
    whatever is video packaging ("(Official Music Video)", "[HD]",
    "(from ...)"), leaving only the song name.
  - `safe_file_stem(name)` makes the name valid as a filename on any system.
  - `rename_to_song(path, name)` renames without overwriting another file.
  - At the end of an MP3 download, each file is named after the song; when the
    catalog is applied (automatically or during review), after the catalog
    title.

WHY IT EXISTS
  Files came out as "020 - The Police - Every Breath You Take (Official Music
  Video).mp3". The number and the artist are already in the tags (track and
  artist), and "(Official Music Video)" describes the video, not the song. The
  user asked for the file to carry the song name, and only that.

BUSINESS RULES
  - No number in front: playlist order lives in the track tag.
  - The catalog title, when confirmed, wins over the video title: it is the
    official name ("Picture (feat. Sheryl Crow)", "Creep (Acoustic)").
  - Parentheses that change the song (live, remix, acoustic) stay in the name.
  - Two files with the same name do not overwrite each other: the second one
    gets " (2)".
  - An MP4 video keeps the source title: the file is the video, not the track.

WHERE THE FILE ENDS UP
  The final path comes from the post-processing hook, in the "finished" notice
  of the postprocessor that moves the file into the folder. That
  postprocessor's name is the one yt-dlp publishes
  (`MoveFilesAfterDownloadPP.pp_key()`, which is "MoveFiles"), not the class
  name. The code compared with "MoveFilesAfterDownload": no file was ever
  recorded, the real MP3 never got the song name — and the tests passed
  because the yt-dlp double used the same wrong name. The test below asks
  yt-dlp itself for the name.
"""

import queue
from pathlib import Path

import pytest

from media_downloader.downloader import DownloadManager
from media_downloader.metadata import rename_to_song, safe_file_stem, song_title
from media_downloader.models import DownloadSummary, MetadataPendingItem, MusicMetadataCandidate


class TestSongName:
    @pytest.mark.parametrize("source, expected", [
        ("The Police - Every Breath You Take (Official Music Video)", "Every Breath You Take"),
        ("Nickelback - Photograph [OFFICIAL VIDEO]", "Photograph"),
        ("Foo Fighters - Everlong (Official HD Video)", "Everlong"),
        ("Pearl Jam - Black (Official Audio)", "Black"),
        ("Bôa -  Duvet (Official Video)", "Duvet"),
        ("Kid Rock - Picture feat. Sheryl Crow [Official Music Video]", "Picture"),
        ("Queen – Bohemian Rhapsody (Official Video Remastered)", "Bohemian Rhapsody"),
        ("Arcángel - FN8 ( Video Lyric )", "FN8"),
        ("I Don't Want To Talk About It (from One Night Only! Rod Stewart Live at Royal Albert Hall)",
         "I Don't Want To Talk About It"),
        ("Creep", "Creep"),
        ("Guns N' Roses - November Rain", "November Rain"),
    ])
    def test_should_keep_only_the_song_name_when_the_title_has_packaging(self, source, expected):
        assert song_title(source) == expected

    def test_should_keep_the_parenthetical_when_it_changes_the_song(self):
        assert song_title("Radiohead - Creep (Acoustic)") == "Creep (Acoustic)"
        assert song_title("Bon Jovi - Always (Live)") == "Always (Live)"

    def test_should_keep_the_title_when_cleaning_would_leave_nothing(self):
        assert song_title("(Official Video)") == "(Official Video)"


class TestValidFileName:
    def test_should_replace_characters_when_the_system_rejects_them(self):
        assert safe_file_stem('AC/DC: "Back" <In> Black?') == "AC-DC - Back In Black"

    def test_should_not_end_in_dot_or_space_when_the_name_has_them(self):
        assert safe_file_stem("Oops... ") == "Oops"

    def test_should_limit_the_length_when_the_name_is_too_long(self):
        assert len(safe_file_stem("a" * 400)) == 150


class TestRenameToSong:
    def test_should_rename_keeping_the_extension_when_renaming(self, tmp_path):
        old = tmp_path / "020 - The Police - Every Breath.mp3"
        old.write_bytes(b"mp3")

        new = rename_to_song(old, "Every Breath You Take")

        assert new == tmp_path / "Every Breath You Take.mp3"
        assert new.read_bytes() == b"mp3" and not old.exists()

    def test_should_not_overwrite_another_song_when_it_has_the_same_name(self, tmp_path):
        (tmp_path / "Creep.mp3").write_bytes(b"radiohead")
        other = tmp_path / "TLC - Creep.mp3"
        other.write_bytes(b"tlc")

        new = rename_to_song(other, "Creep")

        assert new == tmp_path / "Creep (2).mp3"
        assert (tmp_path / "Creep.mp3").read_bytes() == b"radiohead"

    def test_should_not_touch_the_file_when_the_name_is_already_right(self, tmp_path):
        correct = tmp_path / "Creep.mp3"
        correct.write_bytes(b"x")
        assert rename_to_song(correct, "Creep") == correct
        assert correct.exists()


def _candidate(title="Every Breath You Take"):
    return MusicMetadataCandidate(
        track_id="1", title=title, artist="The Police", album="Synchronicity",
        year="1983", artwork_url=None, duration_seconds=253.0)


class _FakeCatalog:
    def __init__(self, results=()):
        self._results = list(results)

    def search(self, suggestion):
        return self._results

    def apply_to_mp3(self, path, candidate):
        assert Path(path).exists(), "the tag is written before renaming"
        return False


class TestNameAtEndOfDownload:
    def _downloaded(self, tmp_path, name="The Police - Every Breath You Take (Official Music Video)"):
        file = tmp_path / f"{name}.mp3"
        file.write_bytes(b"mp3")
        return file

    def test_should_rename_the_mp3_when_the_download_finishes(self, tmp_path):
        file = self._downloaded(tmp_path)
        summary = DownloadSummary()
        manager = DownloadManager(queue.Queue(), _FakeCatalog())

        manager._rename_downloads(summary, [(file.stem, file)])

        assert [p.name for p in tmp_path.iterdir()] == ["Every Breath You Take.mp3"]

    def test_should_carry_the_pending_item_when_the_file_is_renamed(self, tmp_path):
        file = self._downloaded(tmp_path)
        pending = MetadataPendingItem(file.stem, str(file), ("artista ausente",))
        summary = DownloadSummary(metadata_pending_items=[pending])

        DownloadManager(queue.Queue(), _FakeCatalog())._rename_downloads(
            summary, [(file.stem, file)])

        assert summary.metadata_pending_items[0].file_path == str(
            tmp_path / "Every Breath You Take.mp3")
        assert summary.metadata_pending_items[0].title == file.stem

    def test_should_rename_by_catalog_title_when_applied_automatically(self, tmp_path):
        file = self._downloaded(tmp_path, "Every Breath You Take")
        pending = MetadataPendingItem(
            "The Police - Every Breath You Take", str(file), ("artista ausente",),
            duration=253.0)
        summary = DownloadSummary(metadata_pending_items=[pending])
        catalog = _FakeCatalog([_candidate("Every Breath You Take (Remastered 2003)")])

        DownloadManager(queue.Queue(), catalog)._auto_apply_metadata(summary)

        assert [p.name for p in tmp_path.iterdir()] == [
            "Every Breath You Take (Remastered 2003).mp3"]

    def test_should_rename_by_catalog_title_when_confirmed_in_review(self, tmp_path):
        file = self._downloaded(tmp_path, "Headlock")
        pending = MetadataPendingItem("Headlock", str(file), ("artista ausente",))
        event_queue = queue.Queue()

        DownloadManager(event_queue, _FakeCatalog()).apply_metadata(
            pending, _candidate("Hide and Seek"))

        assert event_queue.get_nowait()["type"] == "metadata_applied"
        assert [p.name for p in tmp_path.iterdir()] == ["Hide and Seek.mp3"]

    def test_should_carry_on_when_the_file_vanished_before_renaming(self, tmp_path):
        summary = DownloadSummary()
        DownloadManager(queue.Queue(), _FakeCatalog())._rename_downloads(
            summary, [("Faixa", tmp_path / "nao-existe.mp3")])
        assert summary.failed_items == []


class TestFinalFilePath:
    def _notice(self, path, status="finished"):
        from yt_dlp.postprocessor import MoveFilesAfterDownloadPP

        return {"status": status, "postprocessor": MoveFilesAfterDownloadPP.pp_key(),
                "info_dict": {"title": "Faixa", "filepath": path}}

    def test_should_record_the_file_when_named_as_yt_dlp_publishes_it(self):
        manager = DownloadManager(queue.Queue())

        manager._record_final_file(self._notice("/musicas/Faixa.mp3"))

        assert manager._finished_files == [("Faixa", Path("/musicas/Faixa.mp3"))]

    def test_should_not_record_when_the_move_has_not_finished(self):
        manager = DownloadManager(queue.Queue())

        manager._record_final_file(self._notice("/musicas/Faixa.mp3", status="started"))

        assert manager._finished_files == []


class TestNoNumberInFront:
    def _outtmpl(self, file_format="mp3", playlist_mode=True):
        return DownloadManager(queue.Queue())._build_opts(
            Path("/tmp/destino"), file_format, playlist_mode, DownloadSummary(),
        )["outtmpl"]["default"]

    def test_should_not_number_the_file_when_it_is_in_a_playlist(self):
        assert self._outtmpl().endswith("/%(title)s.%(ext)s")
        assert "playlist_index" not in self._outtmpl()

    def test_should_record_the_final_path_when_each_mp3_finishes(self):
        opts = DownloadManager(queue.Queue())._build_opts(
            Path("/tmp/destino"), "mp3", True, DownloadSummary())
        assert opts["postprocessor_hooks"]
