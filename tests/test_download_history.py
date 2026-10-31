"""
SDD — Spec: history of downloaded files

CONTRACT
  - `history.py` keeps the latest downloads in
    `$XDG_CONFIG_HOME/media-downloader/historico.json`:
    `append_history(file, entries)` appends, `load_history(file)` reads,
    newest first.
  - `DownloadManager.set_history_path(file)` turns recording on: at the end of a
    download, every file that finished goes into the history, and the summary
    carries the paths in `finished_files`.

WHY IT EXISTS
  After downloading, the user had to open the folder and look for the file,
  without knowing which of the four subfolders it landed in. The history (a
  pattern from Seal and MeTube) answers "where is what I just downloaded?" with
  one click, and survives closing the app.

BUSINESS RULES
  - Stores title, path, format and when it finished. Does NOT store the URL: a
    link may carry session parameters, and AGENTS.md forbids full URLs in
    persistent output.
  - The recorded path is the final one: after conversion, the song name (MP3)
    and the section suffix.
  - It has a limit: the file does not grow forever; the oldest entry goes.
  - An unreadable or corrupted history does not stop the app from opening: it
    counts as empty.
  - Failing to write the history does not become a download failure.
  - Without `set_history_path`, nothing is recorded (tests and anyone using the
    backend without a screen do not pollute the user's config).
"""

import json
import queue
from pathlib import Path

from yt_dlp.postprocessor import MoveFilesAfterDownloadPP

from media_downloader.downloader import DownloadManager
from media_downloader.history import (
    HISTORY_LIMIT,
    HistoryEntry,
    append_history,
    load_history,
)


def _entry(n, kind="mp3"):
    return HistoryEntry(
        title=f"Faixa {n}", path=f"/musicas/Faixa {n}.{kind}", kind=kind,
        finished_at=f"2026-10-30T10:{n:02d}:00",
    )


class TestHistoryRecord:
    def test_should_start_empty_when_there_is_no_file(self, tmp_path):
        assert load_history(tmp_path / "nao-existe.json") == []

    def test_should_return_newest_first(self, tmp_path):
        file = tmp_path / "sub" / "historico.json"
        append_history(file, [_entry(1)])
        append_history(file, [_entry(2), _entry(3)])

        assert [e.title for e in load_history(file)] == ["Faixa 3", "Faixa 2", "Faixa 1"]

    def test_should_drop_the_oldest_when_over_the_limit(self, tmp_path):
        file = tmp_path / "historico.json"
        append_history(file, [_entry(n % 60) for n in range(HISTORY_LIMIT + 5)])

        history = load_history(file)
        assert len(history) == HISTORY_LIMIT

    def test_should_count_as_empty_when_the_file_is_corrupted(self, tmp_path):
        file = tmp_path / "historico.json"
        file.write_text("{nao e json", encoding="utf-8")

        assert load_history(file) == []

    def test_should_skip_a_malformed_entry_without_losing_the_others(self, tmp_path):
        file = tmp_path / "historico.json"
        append_history(file, [_entry(1)])
        data = json.loads(file.read_text(encoding="utf-8"))
        data.append({"title": 42})
        file.write_text(json.dumps(data), encoding="utf-8")

        assert [e.title for e in load_history(file)] == ["Faixa 1"]

    def test_should_not_store_the_url(self, tmp_path):
        file = tmp_path / "historico.json"
        append_history(file, [_entry(1)])

        assert "http" not in file.read_text(encoding="utf-8")


class _FakeYoutubeDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {"id": "v1", "title": "Clipe"}

    def download(self, urls):
        destination = Path(self.opts["outtmpl"]["default"]).parent
        final = destination / "Clipe.mp4"
        final.write_bytes(b"mp4")
        for hook in self.opts["progress_hooks"]:
            hook({"status": "finished", "filename": str(final),
                  "info_dict": {"id": "v1", "title": "Clipe"}})
        for hook in self.opts["postprocessor_hooks"]:
            hook({"status": "finished", "postprocessor": MoveFilesAfterDownloadPP.pp_key(),
                  "info_dict": {"title": "Clipe", "filepath": str(final)}})


class TestDownloadHistory:
    def _download(self, monkeypatch, tmp_path, history=None):
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _FakeYoutubeDL)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp4", False): tmp_path})
        event_queue = queue.Queue()
        manager = DownloadManager(event_queue)
        if history is not None:
            manager.set_history_path(history)
        manager.download("https://youtu.be/v1", "mp4")
        events = []
        while not event_queue.empty():
            events.append(event_queue.get_nowait())
        return events

    def test_should_record_the_finished_file(self, monkeypatch, tmp_path):
        history = tmp_path / "historico.json"

        self._download(monkeypatch, tmp_path, history)

        entry, = load_history(history)
        assert entry.title == "Clipe"
        assert entry.path == str(tmp_path / "Clipe.mp4")
        assert entry.kind == "mp4"

    def test_should_carry_the_file_paths_in_the_summary(self, monkeypatch, tmp_path):
        events = self._download(monkeypatch, tmp_path, tmp_path / "historico.json")

        done = next(e for e in events if e["type"] == "done")
        assert done["summary"].finished_files == [str(tmp_path / "Clipe.mp4")]

    def test_should_record_nothing_when_history_is_not_enabled(self, monkeypatch, tmp_path):
        self._download(monkeypatch, tmp_path)

        assert not list(tmp_path.glob("*.json"))

    def test_should_not_fail_the_download_when_writing_the_history_fails(self, monkeypatch, tmp_path):
        blocker = tmp_path / "arquivo-comum"
        blocker.write_text("x", encoding="utf-8")

        events = self._download(monkeypatch, tmp_path, blocker / "historico.json")

        assert "done" in [e["type"] for e in events]
        assert "error" not in [e["type"] for e in events]
