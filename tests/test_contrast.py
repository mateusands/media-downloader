"""
SDD — Spec: minimum palette contrast

CONTRACT
  Every (text, surface) pair the interface actually uses reaches the WCAG AA
  contrast ratio of 4.5:1 for normal text.

WHY IT EXISTS
  The palette grew by accretion, and contrast is judged by number, not by eye.
  Measured, three pairs failed: `CLR_MUTED` on `BG_HOVER` (4.05:1, the platform
  chips), `CLR_MUTED` on `BG_INPUT` (4.27:1, the album line and the review's
  reasons line) and `CLR_ERROR` on `BG_INPUT` (4.02:1) — the file-not-found
  warning, precisely the text that most needs to be read.

BUSINESS RULES
  - The floor is 4.5:1 for normal text; the pair is measured against the DARKEST
    surface on which that text appears.
  - A decorative border is not text and does not count here: `CLR_BORDER`
    separates surfaces by luminance, on purpose.
  - Darkening the background counts as much as lightening the text; what is not
    allowed is lightening only one of the two and making the pair worse.
"""

from media_downloader import theme

NORMAL_TEXT_FLOOR = 4.5


def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


PAIRS_IN_USE = [
    ("title and label", theme.CLR_TEXT, theme.BG_CARD),
    # The primary button is a gradient: the text must pass at both ends.
    ("primary button text (start)", theme.CLR_TEXT, theme.CLR_ACCENT),
    ("primary button text (end)", theme.CLR_TEXT, theme.CLR_ACCENT_END),
    ("primary button on hover (start)", theme.CLR_TEXT, theme.CLR_ACCENT_DARK),
    ("primary button on hover (end)", theme.CLR_TEXT, theme.CLR_ACCENT_HOVER_END),
    ("checked quality chip", theme.CLR_ON_LIGHT, theme.CLR_TEXT),
    ("preview badge", theme.CLR_ACCENT_LIGHT, theme.CLR_ACCENT_SOFT),
    ("sidebar label", theme.CLR_MUTED, theme.BG_SIDEBAR),
    ("hint and caption", theme.CLR_MUTED, theme.BG_CARD),
    ("candidate album line", theme.CLR_MUTED, theme.BG_INPUT),
    ("platform chip", theme.CLR_MUTED, theme.BG_HOVER),
    ("yt-dlp list link", theme.CLR_ACCENT_LIGHT, theme.BG_CARD),
    ("file-not-found warning", theme.CLR_ERROR, theme.BG_INPUT),
    ("finished progress", theme.CLR_GREEN, theme.BG_CARD),
]


class TestPaletteContrast:
    def test_should_reach_the_aa_floor_on_every_pair_the_interface_uses(self):
        failing = {
            name: round(contrast(fg, bg), 2)
            for name, fg, bg in PAIRS_IN_USE
            if contrast(fg, bg) < NORMAL_TEXT_FLOOR
        }

        assert failing == {}, f"pairs below {NORMAL_TEXT_FLOOR}:1 — {failing}"

    def test_should_keep_the_surface_luminance_ladder(self):
        # In the dark, depth comes from luminance, not shadow: each stacked
        # surface must be lighter than the one below it.
        ladder = [theme.BG_DARK, theme.BG_SIDEBAR, theme.BG_CARD, theme.BG_INPUT, theme.BG_HOVER]

        assert [_luminance(c) for c in ladder] == sorted(_luminance(c) for c in ladder)


# ---------------------------------------------------------------------------
# SDD — Spec: component contrast and a single source of colour
#
# CONTRACT
#   - A state indicator (switch off, unchecked checkbox border, ruler handle
#     ring) reaches 3:1 against the surface it sits on — WCAG 1.4.11.
#   - No interface module writes a colour literal: every colour comes from `theme.py`.
#
# WHY IT EXISTS
#   The text floor (4.5:1) did not cover graphics. The switch when off (#34344a on
#   the card) and the unchecked checkbox border were judged by eye; someone with
#   low vision cannot tell "off" from "absent". And loose colours in widgets
#   (`#2a2150`, `#1e1838`, `#9b9bb0`...) escaped this test: changing the palette
#   left pieces of the screen in the old palette. Rule from the crewkit
#   design-system skill: "one source for every visual value".
#
# BUSINESS RULES
#   - The component floor is 3:1, measured against the surface where it appears.
#   - A colour in a widget, embedded QSS or QPainter comes from a theme name.
# ---------------------------------------------------------------------------

import re
from pathlib import Path

COMPONENT_FLOOR = 3.0

COMPONENTS = [
    ("switch track when off", theme.CLR_SWITCH_OFF, theme.BG_CARD),
    ("switch track when off on a hovered row", theme.CLR_SWITCH_OFF, theme.BG_INPUT),
    ("unchecked checkbox border", theme.CLR_CONTROL_BORDER, theme.BG_INPUT),
    ("ruler handle", theme.CLR_ACCENT, theme.BG_CARD),
    ("ruler track under the white handle", theme.CLR_TEXT, theme.BG_HOVER),
]

_UI_MODULES = ("widgets.py", "window.py", "review.py", "selection.py", "icons.py")
_COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{6}\b|QColor\(\s*\d")


class TestComponentContrast:
    def test_should_reach_3_to_1_on_every_state_indicator(self):
        failing = {
            name: round(contrast(fg, bg), 2)
            for name, fg, bg in COMPONENTS
            if contrast(fg, bg) < COMPONENT_FLOOR
        }

        assert failing == {}, f"components below {COMPONENT_FLOOR}:1 — {failing}"


class TestSingleSourceOfColor:
    def test_no_interface_module_should_write_a_color_literal(self):
        package = Path(theme.__file__).parent
        literals = {
            name: _COLOR_LITERAL.findall((package / name).read_text(encoding="utf-8"))
            for name in _UI_MODULES
        }

        assert {name: found for name, found in literals.items() if found} == {}
