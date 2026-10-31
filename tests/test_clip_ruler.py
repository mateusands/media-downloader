"""
SDD — Spec: section ruler (two handles)

CONTRACT
  `RangeSlider` holds start and end in seconds over the video's duration
  (`setMaximum`). `setValues(start, end)` corrects anything outside the ruler
  and emits `rangeChanged`. The mouse drags the handle closest to the click;
  arrow keys move 1 s, Shift+arrows 10 s.

WHY IT EXISTS
  Typing "1:02:03" works, but dragging is how people think "from here to
  there" — it is the control of the sibling extension and of Seal. The time
  fields and the ruler show the same section; the window keeps them in sync.

BUSINESS RULES
  - The handles never cross and stay at least 1 s apart: a zero-second section
    is not a section.
  - Nothing goes below zero or past the duration.
  - A new duration (another video) resets the ruler to the whole video.
"""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from media_downloader.widgets import RangeSlider


@pytest.fixture
def ruler(qapp):
    widget = RangeSlider()
    widget.resize(424, 56)   # 400 px track between the 12 px margins
    widget.setMaximum(400)
    return widget


class TestRulerValues:
    def test_should_cover_the_whole_video_when_the_duration_is_new(self, ruler):
        assert ruler.values() == (0, 400)

    def test_should_not_go_below_zero_or_past_the_duration(self, ruler):
        ruler.setValues(-30, 900)
        assert ruler.values() == (0, 400)

    def test_should_not_cross_the_handles_when_start_is_after_end(self, ruler):
        ruler.setValues(200, 100)
        start, end = ruler.values()
        assert end - start >= RangeSlider.MIN_GAP

    def test_should_notify_when_the_section_changes(self, ruler):
        notices = []
        ruler.rangeChanged.connect(lambda a, b: notices.append((a, b)))

        ruler.setValues(60, 120)

        assert notices == [(60, 120)]


class TestMouseOnRuler:
    def test_should_move_the_start_when_clicking_near_the_start(self, ruler):
        QTest.mouseClick(ruler, Qt.MouseButton.LeftButton, pos=QPoint(12 + 100, 20))
        assert ruler.values() == (100, 400)

    def test_should_move_the_end_when_clicking_near_the_end(self, ruler):
        QTest.mouseClick(ruler, Qt.MouseButton.LeftButton, pos=QPoint(12 + 300, 20))
        assert ruler.values() == (0, 300)


class TestKeyboardOnRuler:
    def test_should_move_one_second_with_arrow_and_ten_with_shift(self, ruler):
        ruler.setFocus()
        QTest.keyClick(ruler, Qt.Key.Key_Right)
        QTest.keyClick(ruler, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        assert ruler.values()[0] == 11
