from pathlib import Path

from media_downloader.downloader import cookie_browser_for


def test_uses_the_first_installed_browser(tmp_path: Path) -> None:
    (tmp_path / ".config/BraveSoftware/Brave-Browser").mkdir(parents=True)
    (tmp_path / ".mozilla/firefox").mkdir(parents=True)
    assert cookie_browser_for(tmp_path) == ("brave",)


def test_falls_back_to_firefox(tmp_path: Path) -> None:
    (tmp_path / ".mozilla/firefox").mkdir(parents=True)
    assert cookie_browser_for(tmp_path) == ("firefox",)


def test_no_browser_means_no_cookies(tmp_path: Path) -> None:
    assert cookie_browser_for(tmp_path) is None
