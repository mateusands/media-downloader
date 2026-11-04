"""
SDD — Spec: activating the main controls

CONTRACT
  The main window's controls trigger the action they announce: "Baixar" calls
  `start_download`, "Abrir" creates and opens the destination folder, and the
  link field accepts pasting over a selection by replacing the selected text.

WHY IT EXISTS
  In CustomTkinter, a button with visual feedback overrode the internal method
  that calls `command`, and "Baixar mídia" did nothing on a seemingly normal
  screen. After the switch to Qt, the same risk exists in any custom widget
  (format card, option row): styling must not prevent the action.
  Pasting over the selected URL duplicated the link in X11 Tk; `QLineEdit`
  replaces the selection natively, and the test makes sure it stays that way.

BUSINESS RULES
  Additional visual styling must not prevent the action requested by the user.

---

SDD — Spec: closing the metadata review

CONTRACT
  `MetadataReview.resolve(item)` removes from the screen the pending item that
  was just imported. With no pending item left, the review dialog closes on its
  own. The owner of this state is `review.py`, not the main window.

WHY IT EXISTS
  After picking a catalog result, the candidates window closed but the review
  window stayed open, offering "Buscar metadata" for a file already resolved.

BUSINESS RULES
  - Only a completed import (`metadata_applied`) resolves the row.
  - In a playlist, resolving one item must not close the review of the others.

---

SDD — Spec: catalog search queue

CONTRACT
  `start_search(item)` enqueues; one search runs at a time; `search_finished`
  (on result or on error) releases the next one.

WHY IT EXISTS
  In a playlist the user clicks several items in a row; rejecting the second
  click turned that into click, wait, click again.

---

SDD — Spec: keyboard and page states

CONTRACT
  - Every control on the download page is reachable with Tab and shows focus:
    format cards, quality chips, the section switch, the ruler, and the extras'
    switches.
  - The empty history explains and offers the next step ("Baixar algo"), which
    leads to the download page with focus on the link field.

WHY IT EXISTS
  The cards and switches are widgets drawn here, not native ones: Qt does not
  give them focus or a focus ring unless asked. And an empty state that only
  says "nothing yet" leaves the user with no way forward (the "nothing yet =
  explain + create" rule from crewkit's app-shell skill).

---

SDD — Spec: the preview belongs to the link in the field

CONTRACT
  The window only applies a `preview` event whose URL is the one currently in
  the field.

WHY IT EXISTS
  The preview runs in a thread and may arrive after the user has replaced the
  link: showing the previous video's thumbnail and duration would make the
  section ruler cut with the wrong duration.

---

SDD — Spec: the link field starts clean and forgets the previous link

CONTRACT
  - The link field is empty when the app opens and stays empty when the
    window regains focus, whatever is in the clipboard. A link only enters
    the field when the user pastes, types, or drops it.
  - Clearing or replacing the link forgets what the previous preview said:
    every quality is offered again and the section ruler has no duration.

WHY IT EXISTS
  The window filled the field from the clipboard every time it became active.
  The clipboard usually still held the link downloaded last time, so the app
  opened with an old link the user had to delete before pasting the new one.
  And after clearing it, 1080p/720p/480p stayed disabled because the old
  preview's resolutions were never dropped.

BUSINESS RULES
  - Nothing reads the clipboard on its own; "Colar", Ctrl+V and drag-and-drop
    are the user's explicit actions and keep working.
  - Without a preview, nothing is disabled on its behalf.

---

SDD — Spec: what the user chose is what the download receives

CONTRACT
  Clicking "Baixar" hands the backend exactly the choices on screen at the
  moment of the click: format, cover and metadata, automatic application,
  quality, section, subtitles, chapters, and SponsorBlock.

WHY IT EXISTS
  `start_download` locked the screen (`_set_busy(True)`) before reading the
  choices. Reading checks `isEnabled()` so that an unavailable option never
  counts — and a locked card makes every child report disabled. So every
  download went out with no section, no subtitles, no chapters, no
  SponsorBlock, and no cover/metadata: "Baixar só um trecho" downloaded the
  whole video. The earlier tests read `_media_options()` directly and never
  went through the click, so they passed.

BUSINESS RULES
  - The choices are read before anything on screen changes state.
  - The test goes through the click, the path the user takes.
  - An option that becomes unavailable is not sent, but keeps the user's
    choice for when it is available again. `setAvailable(False)` used to
    switch it off for good: "Aplicar sozinho" lost its default (on) at
    startup, and subtitles chosen in MP4 were gone after a trip to MP3.
"""

from collections import deque
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from media_downloader import window
from media_downloader.models import MediaPreview, MetadataPendingItem
from media_downloader.review import MetadataReview
from media_downloader.window import MediaDownloaderApp


@pytest.fixture
def app_window(qapp, tmp_path):
    main_window = MediaDownloaderApp()
    main_window._downloads_dir = tmp_path / "Downloads"
    main_window._probe_timer.stop()
    yield main_window
    main_window._poll_timer.stop()
    main_window.close()
    main_window.deleteLater()


class TestControlActivation:
    def test_should_start_the_download_when_the_download_button_is_clicked(self, app_window, monkeypatch):
        calls = []
        monkeypatch.setattr(app_window, "start_download", lambda: calls.append("download"))
        # The signal was connected to the original method; reconnect it to see the click arrive.
        app_window.download_btn.clicked.disconnect()
        app_window.download_btn.clicked.connect(app_window.start_download)

        QTest.mouseClick(app_window.download_btn, Qt.MouseButton.LeftButton)

        assert calls == ["download"]

    def test_should_switch_the_format_when_the_mp4_card_is_clicked(self, app_window):
        QTest.mouseClick(app_window._mp4_card, Qt.MouseButton.LeftButton)

        assert app_window._format == "mp4"
        assert app_window._mp4_card.isSelected()
        assert not app_window._mp3_card.isSelected()

    def test_should_turn_the_switch_on_when_the_option_row_is_clicked(self, app_window):
        app_window._set_format("mp4")

        QTest.mouseClick(app_window.subtitles_row, Qt.MouseButton.LeftButton)

        assert app_window.subtitles_row.isChecked()

    def test_should_warn_without_starting_a_download_when_the_link_is_invalid(self, app_window):
        app_window.url_entry.setText("nao e link")

        app_window.start_download()

        assert app_window._thread is None
        assert "inválido" in app_window._url_hint.text()


class TestKeyboardAndPageStates:
    def test_should_reach_every_page_control_with_tab(self, app_window):
        controls = [app_window._mp3_card, app_window._mp4_card, app_window.clip_switch,
                    app_window.clip_slider, app_window.subtitles_row.switch,
                    *app_window._quality_buttons.values()]

        without_tab = [type(c).__name__ for c in controls
                       if not c.focusPolicy() & Qt.FocusPolicy.TabFocus]

        assert without_tab == []

    def test_should_lead_to_the_download_page_when_the_history_is_empty(self, app_window):
        app_window._show_page(1)

        app_window._history_empty_action.click()

        assert app_window._pages.currentIndex() == 0


class TestCleanLinkField:
    def _preview(self, url):
        return MediaPreview(url=url, title="Video", duration=245.0, heights=(360,))

    def test_should_open_with_an_empty_link_field_when_the_clipboard_has_a_link(self, qapp):
        QGuiApplication.clipboard().setText("https://youtu.be/downloaded-last-time")

        main_window = MediaDownloaderApp()
        qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)

        assert main_window.url_entry.text() == ""
        main_window._poll_timer.stop()
        main_window.deleteLater()

    def test_should_offer_every_quality_again_when_the_link_is_cleared(self, app_window):
        url = "https://youtu.be/only-360p"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._preview(url)})

        app_window.url_entry.clear()

        assert all(chip.isEnabled() for chip in app_window._quality_buttons.values())

    def test_should_forget_the_previous_duration_when_the_link_is_cleared(self, app_window):
        url = "https://youtu.be/only-360p"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._preview(url)})

        app_window.url_entry.clear()

        assert app_window.clip_slider.maximum() == 0


class TestChoicesReachTheDownload:
    def _click_download(self, app_window):
        sent = []
        app_window._manager.download = lambda *args: sent.append(args)
        app_window.start_download()
        app_window._thread.join()
        url, file_format, include_metadata, auto_metadata, options = sent[0]
        return file_format, include_metadata, auto_metadata, options

    def test_should_send_the_section_and_subtitles_when_download_is_clicked(self, app_window):
        url = "https://youtu.be/long-video"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": MediaPreview(
            url=url, title="Video", duration=273.0, heights=(1080,))})
        app_window._set_format("mp4")
        app_window.clip_switch.setChecked(True)
        app_window.clip_start.setText("1:00")
        app_window.clip_end.setText("1:30")
        app_window.subtitles_row.switch.setChecked(True)

        file_format, _, _, options = self._click_download(app_window)

        assert file_format == "mp4"
        assert (options.clip_start, options.clip_end) == (60, 90)
        assert options.subtitles is True

    def test_should_send_cover_and_metadata_when_download_is_clicked(self, app_window):
        app_window.url_entry.setText("https://youtu.be/song")
        app_window._set_format("mp3")
        app_window.metadata_row.switch.setChecked(True)

        _, include_metadata, auto_metadata, _ = self._click_download(app_window)

        assert include_metadata is True
        assert auto_metadata is True


class TestUnavailableOptionKeepsTheChoice:
    def test_should_keep_subtitles_on_when_the_format_goes_to_mp3_and_back(self, app_window):
        app_window._set_format("mp4")
        app_window.subtitles_row.switch.setChecked(True)

        app_window._set_format("mp3")
        unavailable = app_window.subtitles_row.isChecked()
        app_window._set_format("mp4")

        assert unavailable is False
        assert app_window.subtitles_row.isChecked() is True


class TestOpeningTheDownloadsFolder:
    def test_should_create_and_open_the_folder_with_xdg_open_on_linux(self, app_window, monkeypatch):
        calls = []
        monkeypatch.setattr(window.sys, "platform", "linux")
        monkeypatch.setattr(window.subprocess, "Popen", lambda arguments: calls.append(arguments))

        app_window.open_downloads_folder()

        assert app_window._downloads_dir.is_dir()
        assert calls == [["xdg-open", str(app_window._downloads_dir)]]


class TestPastingIntoTheLinkField:
    def test_should_replace_the_selected_text_with_the_pasted_link(self, app_window):
        app_window.url_entry.setText("https://youtu.be/AAA")
        app_window.url_entry.selectAll()
        QGuiApplication.clipboard().setText("https://youtu.be/BBB")

        app_window.url_entry.paste()

        assert app_window.url_entry.text() == "https://youtu.be/BBB"

    def test_should_replace_the_whole_field_when_the_paste_button_is_used(self, app_window):
        app_window.url_entry.setText("https://youtu.be/AAA")
        QGuiApplication.clipboard().setText("  https://youtu.be/BBB  ")

        app_window._paste_url()

        assert app_window.url_entry.text() == "https://youtu.be/BBB"


class TestPreviewOfTheCurrentLink:
    def _preview(self, url, duration=300.0, playlist=False):
        return MediaPreview(url=url, title="Video", duration=None if playlist else duration,
                            is_playlist=playlist, heights=(720, 360))

    def test_should_ignore_the_preview_of_a_link_that_was_already_replaced(self, app_window):
        app_window.url_entry.setText("https://youtu.be/novo")

        app_window._handle({"type": "preview", "url": "https://youtu.be/antigo",
                            "preview": self._preview("https://youtu.be/antigo")})

        assert app_window._preview is None

    def test_should_use_the_preview_duration_in_the_section_ruler(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)

        app_window._handle({"type": "preview", "url": url, "preview": self._preview(url, 245)})

        assert app_window.clip_slider.maximum() == 245

    def test_should_not_offer_quality_above_what_the_video_has(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)

        app_window._handle({"type": "preview", "url": url, "preview": self._preview(url)})

        assert not app_window._quality_buttons["1080"].isEnabled()
        assert app_window._quality_buttons["720"].isEnabled()

    def test_should_turn_off_the_section_when_the_link_is_a_playlist(self, app_window):
        url = "https://www.youtube.com/playlist?list=PL"
        app_window.url_entry.setText(url)
        app_window.clip_switch.setChecked(True)

        app_window._handle({"type": "preview", "url": url,
                            "preview": self._preview(url, playlist=True)})

        assert not app_window.clip_switch.isChecked()
        assert not app_window.clip_switch.isEnabled()

    def test_should_block_the_download_button_when_the_section_is_invalid(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._preview(url, 300)})
        app_window.clip_switch.setChecked(True)

        app_window.clip_start.setText("2:00")
        app_window.clip_end.setText("1:00")
        app_window._times_edited("")

        assert not app_window.download_btn.isEnabled()
        assert "depois do início" in app_window._clip_error.text()

    def test_should_pass_the_chosen_section_in_the_download_options(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._preview(url, 300)})
        app_window.clip_switch.setChecked(True)
        app_window.clip_start.setText("1:00")
        app_window.clip_end.setText("1:30")

        options = app_window._media_options()

        assert (options.clip_start, options.clip_end) == (60, 90)

    def test_should_go_to_the_end_when_the_end_equals_the_duration(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._preview(url, 300)})
        app_window.clip_switch.setChecked(True)
        app_window.clip_start.setText("1:00")
        app_window.clip_end.setText("5:00")

        assert app_window._media_options().clip_end is None


class TestClosingTheMetadataReview:
    def _review(self, qapp, pending_items):
        review = MetadataReview(QWidget(), SimpleNamespace())
        review._metadata_review_window = QWidget()
        review._metadata_review_window.show()
        review._metadata_review_rows = {
            item: (QWidget(), None, None, None) for item in pending_items
        }
        return review

    def test_should_close_the_review_when_the_last_pending_item_was_imported(self, qapp):
        item = MetadataPendingItem("Paramore - All I Wanted", "/tmp/faixa.mp3",
                                   ("artista provisorio: canal Paramore",))
        review = self._review(qapp, [item])
        dialog = review._metadata_review_window

        review.resolve(item)

        assert not dialog.isVisible()
        assert review._metadata_review_rows == {}
        assert review._metadata_review_window is None

    def test_should_keep_the_review_open_while_another_item_is_pending(self, qapp):
        imported = MetadataPendingItem("Faixa A", "/tmp/a.mp3", ("artista ausente",))
        remaining = MetadataPendingItem("Faixa B", "/tmp/b.mp3", ("artista ausente",))
        review = self._review(qapp, [imported, remaining])
        dialog = review._metadata_review_window

        review.resolve(imported)

        assert dialog.isVisible()
        assert list(review._metadata_review_rows) == [remaining]

    def test_should_ignore_an_item_that_is_not_in_the_review(self, qapp):
        item = MetadataPendingItem("Faixa A", "/tmp/a.mp3", ("artista ausente",))
        review = self._review(qapp, [item])

        review.resolve(MetadataPendingItem("Outra", "/tmp/outra.mp3", ("artista ausente",)))

        assert list(review._metadata_review_rows) == [item]


class TestMetadataSearchQueue:
    def _review(self, qapp, pending_items):
        from PySide6.QtWidgets import QPushButton

        review = MetadataReview(QWidget(), SimpleNamespace(search_metadata="target"))
        review._metadata_review_rows = {
            item: (None, None, None, QPushButton("Buscar metadata")) for item in pending_items
        }
        review._search_queue = deque()
        review.started_searches = []
        review._spawn = lambda target, *args: review.started_searches.append(args[0])
        return review

    def _items(self, count):
        return [MetadataPendingItem(f"Faixa {n}", f"/tmp/{n}.mp3", ("artista ausente",))
                for n in range(count)]

    def _button(self, review, item):
        return review._metadata_review_rows[item][3]

    def test_should_start_the_first_search_immediately(self, qapp):
        one, = self._items(1)
        review = self._review(qapp, [one])

        review.start_search(one)

        assert review.started_searches == [one]
        assert not self._button(review, one).isEnabled()

    def test_should_queue_the_second_click_instead_of_rejecting_it(self, qapp):
        one, two = self._items(2)
        review = self._review(qapp, [one, two])

        review.start_search(one)
        review.start_search(two)

        assert review.started_searches == [one], "only one search runs at a time"
        assert "fila" in self._button(review, two).text().lower()

    def test_should_start_the_next_one_when_the_previous_finishes(self, qapp):
        one, two = self._items(2)
        review = self._review(qapp, [one, two])
        review.start_search(one)
        review.start_search(two)

        review.search_finished(one)

        assert review.started_searches == [one, two]
        assert self._button(review, one).isEnabled()

    def test_should_ignore_a_repeated_click_on_the_same_item(self, qapp):
        one, = self._items(1)
        review = self._review(qapp, [one])

        review.start_search(one)
        review.start_search(one)

        assert review.started_searches == [one]
