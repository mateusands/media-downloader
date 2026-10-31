"""
SDD — Spec: choosing playlist items before downloading

CONTRACT
  `PlaylistChoice` holds what is checked in the selection dialog, with no
  knowledge of widgets: initial selection, select all, select none, only new
  items, toggling one item, and the label of the confirm button.

WHY IT EXISTS
  The playlist used to be all or nothing: to get three tracks from a playlist of
  a hundred, it downloaded the hundred. And running a playlist again after it
  gained two tracks downloaded everything again. Choosing solves both, and the
  initial selection is what makes the common case ("only what is new") cost one
  click.

BUSINESS RULES
  - Everything not yet downloaded starts checked.
  - If everything was already downloaded, nothing starts checked and the button
    says so, instead of pretending there is something to download.
  - Confirming with no item checked is not possible.
  - The returned order is the playlist's, not the order of the clicks.
"""

from media_downloader.models import PlaylistEntry
from media_downloader.selection import PlaylistChoice


def _entries(*downloaded):
    return [
        PlaylistEntry(index=n, title=f"Faixa {n}", archive_id=f"youtube {n}",
                      duration=None, already_downloaded=n in downloaded)
        for n in range(1, 6)
    ]


class TestInitialSelection:
    def test_should_check_only_what_was_not_downloaded_yet(self):
        choice = PlaylistChoice(_entries(2, 4))
        assert choice.selected() == [1, 3, 5]

    def test_should_start_empty_when_everything_was_already_downloaded(self):
        choice = PlaylistChoice(_entries(1, 2, 3, 4, 5))
        assert choice.selected() == []
        assert choice.can_confirm() is False
        assert choice.confirm_label() == "Nada novo para baixar"


class TestSelectionActions:
    def test_should_check_all_including_already_downloaded(self):
        choice = PlaylistChoice(_entries(2))
        choice.select_all()
        assert choice.selected() == [1, 2, 3, 4, 5]

    def test_should_uncheck_all(self):
        choice = PlaylistChoice(_entries())
        choice.select_none()
        assert choice.selected() == []
        assert choice.confirm_label() == "Escolha ao menos um item"

    def test_should_go_back_to_only_new_items(self):
        choice = PlaylistChoice(_entries(2))
        choice.select_all()
        choice.select_new()
        assert choice.selected() == [1, 3, 4, 5]

    def test_should_return_in_playlist_order_when_toggling_items(self):
        choice = PlaylistChoice(_entries(1, 2, 3, 4, 5))
        choice.toggle(4)
        choice.toggle(1)
        assert choice.selected() == [1, 4]

    def test_should_count_on_the_button_what_will_be_downloaded(self):
        choice = PlaylistChoice(_entries(2))
        assert choice.can_confirm() is True
        assert choice.confirm_label() == "Baixar 4 de 5"
