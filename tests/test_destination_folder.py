"""
SDD — Spec: the user chooses where downloads are saved

CONTRACT
  - `load_settings(file)` / `save_settings(file, settings)` keep the choice
    from one run to the next; `downloads_dir` is the base folder.
  - `DownloadManager.set_downloads_dir(base)` changes the base; the four
    subfolders (`audios_unicos`, `videos_unicos`, `playlist_audio`,
    `playlist_video`) stay the same, now inside the chosen base.

WHY IT EXISTS
  Downloads always landed in `Downloads/` inside the repository. The user's
  library lives on another disk (`/mnt/hdd/musicas`), and moving files by hand
  after every download was the step they wanted to eliminate.

BUSINESS RULES
  - Without a saved choice, `Downloads/` at the repository root applies, as
    before.
  - A missing, corrupted, or wrongly typed settings file does not stop the app
    from opening: it falls back to the default.
  - Organization by subfolder is kept: the playlist history depends on it.
"""

import queue

from media_downloader.config import BASE_DOWNLOADS_DIR
from media_downloader.downloader import DownloadManager
from media_downloader.settings import Settings, load_settings, save_settings


class TestSavedChoice:
    def test_should_use_the_default_folder_when_no_choice_is_saved(self, tmp_path):
        assert load_settings(tmp_path / "nao-existe.json").downloads_dir == BASE_DOWNLOADS_DIR

    def test_should_remember_the_folder_when_one_was_chosen(self, tmp_path):
        settings_file = tmp_path / "sub" / "config.json"
        save_settings(settings_file, Settings(downloads_dir=tmp_path / "Musicas"))

        assert load_settings(settings_file).downloads_dir == tmp_path / "Musicas"

    def test_should_fall_back_to_the_default_when_the_file_is_corrupted(self, tmp_path):
        settings_file = tmp_path / "config.json"
        for content in ("{nao e json", '{"downloads_dir": 42}', "[]"):
            settings_file.write_text(content)
            assert load_settings(settings_file).downloads_dir == BASE_DOWNLOADS_DIR, content


class TestDownloadToChosenFolder:
    def test_should_create_the_subfolders_when_inside_the_chosen_folder(self, tmp_path):
        manager = DownloadManager(queue.Queue())
        manager.set_downloads_dir(tmp_path)

        assert manager._ensure_output_dir("mp3", False) == tmp_path / "audios_unicos"
        assert manager._ensure_output_dir("mp4", True) == tmp_path / "playlist_video"
        assert (tmp_path / "audios_unicos").is_dir()
