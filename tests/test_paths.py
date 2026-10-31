"""
SDD — Spec: where the application looks for assets and saves downloads

CONTRACT
  `BASE_DOWNLOADS_DIR` and `ASSETS_DIR` point to the repository root, not to
  the folder of the module that declares them.

WHY IT EXISTS
  Both are derived from `Path(__file__)`. While everything lived in
  `src/app.py`, going up two levels reached the root. When the code was split
  into `src/media_downloader/`, the same calculation started stopping at
  `src/`: the window did not open, because the icon was looked up in
  `src/assets/`, and downloads would have landed in `src/Downloads/`. The suite
  stayed green the whole time — no test looked at paths.

BUSINESS RULES
  - The root is derived here independently (starting from `tests/`), otherwise
    the test would repeat the mistake it is meant to catch.
  - The window icon must exist on disk: it is loaded when the UI boots, and
    its absence brings the application down before any screen appears.
  - The four destination folders live under `Downloads/`.
"""

from pathlib import Path

from media_downloader.config import (
    APP_ICON_PATH,
    ASSETS_DIR,
    BASE_DOWNLOADS_DIR,
    DOWNLOAD_FOLDERS,
)

ROOT = Path(__file__).resolve().parent.parent


class TestProjectPaths:
    def test_should_save_downloads_when_at_the_repository_root(self):
        assert BASE_DOWNLOADS_DIR == ROOT / "Downloads"

    def test_should_look_for_assets_when_at_the_repository_root(self):
        assert ASSETS_DIR == ROOT / "assets"

    def test_should_find_the_icon_when_the_window_boots(self):
        assert APP_ICON_PATH.is_file()

    def test_should_keep_the_four_destination_folders_when_under_downloads(self):
        assert len(DOWNLOAD_FOLDERS) == 4
        assert all(folder.parent == BASE_DOWNLOADS_DIR for folder in DOWNLOAD_FOLDERS.values())
