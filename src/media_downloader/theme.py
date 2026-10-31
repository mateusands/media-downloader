"""The interface's palette, typography, and stylesheet (QSS).

In the dark, depth comes from luminance, not shadow: each stacked surface
is one step lighter than the one below it (see test_contrast.py).
"""

BG_DARK       = "#09090e"   # window background
BG_SIDEBAR    = "#0d0d14"
BG_CARD       = "#12121a"
BG_INPUT      = "#1a1a24"
BG_HOVER      = "#23232f"
CLR_BORDER    = "#2b2b3a"
CLR_TEXT      = "#f4f4f8"
CLR_MUTED     = "#9b9bb0"   # 5.6:1 on BG_HOVER, the lightest surface it appears on
CLR_ACCENT    = "#6d4aff"
CLR_ACCENT_END = "#9333ea"  # end of the primary button gradient; #9b3cf0 measured 4.45
CLR_ACCENT_HOVER_END = "#8429d9"
CLR_ACCENT_DARK = "#5a38e8"
CLR_ACCENT_LIGHT = "#b3a1ff"
CLR_ACCENT_SOFT = "#1e1838"  # background of what is selected
CLR_ACCENT_TILE = "#2a2150"  # icon tile of the selected card
CLR_ACCENT_PRESSED = "#4b2fc4"
CLR_FOCUS_HALO = "#466d4aff"  # CLR_ACCENT at 27%, in Qt's #AARRGGBB
CLR_ON_ACCENT = "#ffffff"
CLR_ON_LIGHT  = "#111118"   # text on the checked chip, which is light
CLR_BORDER_HOVER = "#3d3d52"
# State indicators and graphics: 3:1 against the surface (WCAG 1.4.11).
# The off track was #34344a, 1.54:1 — "off" was barely visible.
CLR_SWITCH_OFF = "#686884"
CLR_CONTROL_BORDER = "#686884"
CLR_DISABLED  = "#4a4a5c"   # exempt from contrast, but legible as "dimmed"
CLR_DANGER_BORDER = "#5a2a35"
CLR_DANGER_BG = "#2a1218"
CLR_GREEN     = "#3dd68c"
MOTION_FAST_MS = 140        # switch, hover — dense screen: up to 200 ms
CLR_ERROR     = "#ff7085"
FONT_FAMILIES = ["Inter", "Segoe UI", "SF Pro Text", "Cantarell", "Noto Sans", "sans-serif"]


def stylesheet() -> str:
    return f"""
    * {{
        color: {CLR_TEXT};
        font-size: 13px;
        outline: none;
    }}
    QMainWindow, QDialog, #page {{ background: {BG_DARK}; }}
    QToolTip {{
        background: {BG_HOVER}; color: {CLR_TEXT};
        border: 1px solid {CLR_BORDER}; border-radius: 6px; padding: 6px 8px;
    }}

    /* ── Sidebar ───────────────────────────────────── */
    #sidebar {{ background: {BG_SIDEBAR}; border-right: 1px solid {CLR_BORDER}; }}
    #brand {{ font-size: 15px; font-weight: 700; }}
    #brandSub {{ color: {CLR_MUTED}; font-size: 11px; }}
    #navButton {{
        text-align: left; padding: 10px 12px; border-radius: 10px;
        background: transparent; border: none; color: {CLR_MUTED}; font-weight: 600;
    }}
    #navButton:hover {{ background: {BG_INPUT}; color: {CLR_TEXT}; }}
    /* Active by more than colour: the bar on the left is shape, not just tone. */
    #navButton:checked {{
        background: {CLR_ACCENT_SOFT}; color: {CLR_TEXT};
        border-left: 3px solid {CLR_ACCENT}; padding-left: 9px;
        border-top-left-radius: 3px; border-bottom-left-radius: 3px;
    }}
    #navButton:focus {{ color: {CLR_TEXT}; background: {BG_INPUT}; }}
    #sideLabel {{
        color: {CLR_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;
    }}
    #destPath {{ color: {CLR_TEXT}; font-size: 12px; }}

    /* ── Typography ────────────────────────────────── */
    #h1 {{ font-size: 26px; font-weight: 800; letter-spacing: -0.4px; }}
    #h2 {{ font-size: 14px; font-weight: 700; }}
    #muted {{ color: {CLR_MUTED}; }}
    #small {{ color: {CLR_MUTED}; font-size: 11px; }}
    #error {{ color: {CLR_ERROR}; font-size: 12px; }}
    #link {{ color: {CLR_ACCENT_LIGHT}; font-size: 12px; }}

    /* ── Cards and sections ────────────────────────── */
    #card {{
        background: {BG_CARD}; border: 1px solid {CLR_BORDER}; border-radius: 16px;
    }}
    #urlBar {{
        background: {BG_INPUT}; border: 1.5px solid {CLR_BORDER}; border-radius: 14px;
    }}
    #urlBar[focused="true"] {{ border-color: {CLR_ACCENT}; }}
    #urlBar[invalid="true"] {{ border-color: {CLR_ERROR}; }}
    #urlInput {{
        background: transparent; border: none; font-size: 15px; padding: 6px 2px;
        selection-background-color: {CLR_ACCENT};
    }}

    #formatCard {{
        background: {BG_INPUT}; border: 1.5px solid {CLR_BORDER}; border-radius: 14px;
    }}
    #formatCard:hover {{ border-color: {CLR_BORDER_HOVER}; }}
    #formatCard[selected="true"] {{
        background: {CLR_ACCENT_SOFT}; border-color: {CLR_ACCENT};
    }}
    /* Focus only from the keyboard (TabFocus): a mouse click leaves no ring. */
    #formatCard:focus {{ border-color: {CLR_ACCENT_LIGHT}; }}
    #formatTitle {{ font-size: 14px; font-weight: 700; }}

    #previewCard {{
        background: {BG_CARD}; border: 1px solid {CLR_BORDER}; border-radius: 16px;
    }}
    #previewTitle {{ font-size: 16px; font-weight: 700; }}
    #badge {{
        background: {CLR_ACCENT_SOFT}; color: {CLR_ACCENT_LIGHT};
        border-radius: 8px; padding: 3px 9px; font-size: 11px; font-weight: 700;
    }}
    #skeleton {{ background: {BG_HOVER}; border-radius: 6px; }}
    #badgeMuted {{
        background: {BG_HOVER}; color: {CLR_MUTED};
        border-radius: 8px; padding: 3px 9px; font-size: 11px; font-weight: 600;
    }}

    #optionRow {{ background: transparent; border-radius: 12px; }}
    #optionRow:hover {{ background: {BG_INPUT}; }}
    #optionTitle {{ font-weight: 600; }}

    /* ── Buttons ───────────────────────────────────── */
    QPushButton {{
        background: {BG_INPUT}; border: 1px solid {CLR_BORDER}; border-radius: 10px;
        padding: 8px 14px; font-weight: 600;
    }}
    QPushButton:hover {{ background: {BG_HOVER}; }}
    QPushButton:pressed {{ background: {CLR_BORDER}; }}
    QPushButton:disabled {{ color: {CLR_MUTED}; background: {BG_CARD}; border-color: {BG_HOVER}; }}

    #primary {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {CLR_ACCENT}, stop:1 {CLR_ACCENT_END});
        border: none; border-radius: 12px; padding: 12px 26px;
        font-size: 14px; font-weight: 700; color: {CLR_ON_ACCENT};
    }}
    /* Hover darkens instead of lightening: the light violet tones
       fail contrast against white text. */
    #primary:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {CLR_ACCENT_DARK}, stop:1 {CLR_ACCENT_HOVER_END});
    }}
    #primary:focus {{ border: 2px solid {CLR_ACCENT_LIGHT}; }}
    #primary:pressed {{ background: {CLR_ACCENT_PRESSED}; }}
    /* Disabled removes the fill, not just the text colour: the gradient with
       dimmed text still looked clickable. */
    #primary:disabled {{ background: {BG_HOVER}; color: {CLR_MUTED}; }}

    #ghost {{ background: transparent; border: 1px solid {CLR_BORDER}; }}
    #ghost:focus, #danger:focus, #pasteButton:focus {{ border: 2px solid {CLR_ACCENT_LIGHT}; }}
    #ghost:hover {{ background: {BG_INPUT}; }}
    #danger {{ background: transparent; border: 1px solid {CLR_DANGER_BORDER}; color: {CLR_ERROR}; }}
    #danger:hover {{ background: {CLR_DANGER_BG}; }}
    #danger:disabled {{ color: {CLR_MUTED}; border-color: {BG_HOVER}; }}

    #chip {{
        background: {BG_INPUT}; border: 1px solid {CLR_BORDER}; border-radius: 15px;
        padding: 6px 16px; min-height: 18px; font-weight: 600; color: {CLR_MUTED};
    }}
    #chip:hover {{ color: {CLR_TEXT}; border-color: {CLR_BORDER_HOVER}; }}
    #chip:focus {{ border: 2px solid {CLR_ACCENT_LIGHT}; }}
    #chip:checked {{ background: {CLR_TEXT}; color: {CLR_ON_LIGHT}; border-color: {CLR_TEXT}; }}
    #chip:disabled {{ color: {CLR_DISABLED}; background: {BG_CARD}; border-color: {BG_INPUT}; }}

    #pasteButton {{ padding: 7px 14px; border-radius: 9px; }}

    /* ── Time fields ───────────────────────────────── */
    #timeInput {{
        background: {BG_INPUT}; border: 1.5px solid {CLR_BORDER}; border-radius: 10px;
        padding: 8px 10px; font-size: 14px; font-weight: 600;
        selection-background-color: {CLR_ACCENT};
    }}
    #timeInput:focus {{ border-color: {CLR_ACCENT}; }}
    #timeInput[invalid="true"] {{ border-color: {CLR_ERROR}; }}

    /* ── Action bar and progress ───────────────────── */
    #actionBar {{ background: {BG_SIDEBAR}; border-top: 1px solid {CLR_BORDER}; }}
    #status {{ font-weight: 600; }}
    #status[tone="ok"] {{ color: {CLR_GREEN}; }}
    #status[tone="error"] {{ color: {CLR_ERROR}; }}
    #pct {{ font-size: 20px; font-weight: 800; }}
    #pct[tone="ok"] {{ color: {CLR_GREEN}; }}
    QProgressBar {{
        background: {BG_HOVER}; border: none; border-radius: 3px; max-height: 6px;
    }}
    QProgressBar::chunk {{
        border-radius: 3px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {CLR_ACCENT}, stop:1 {CLR_ACCENT_END});
    }}
    QProgressBar[tone="ok"]::chunk {{ background: {CLR_GREEN}; }}

    /* ── Lists and scrolling ───────────────────────── */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {CLR_BORDER}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {CLR_BORDER_HOVER}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    #historyRow, #listRow {{
        background: {BG_CARD}; border: 1px solid {CLR_BORDER}; border-radius: 12px;
    }}
    #historyRow:hover, #listRow:hover {{ border-color: {CLR_BORDER_HOVER}; }}
    QCheckBox {{ spacing: 10px; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px; border-radius: 5px;
        border: 1.5px solid {CLR_CONTROL_BORDER}; background: {BG_INPUT};
    }}
    QCheckBox::indicator:checked {{ background: {CLR_ACCENT}; border-color: {CLR_ACCENT}; }}
    QLineEdit {{
        background: {BG_INPUT}; border: 1.5px solid {CLR_BORDER}; border-radius: 10px;
        padding: 8px 10px;
    }}
    QLineEdit:focus {{ border-color: {CLR_ACCENT}; }}
    QMessageBox {{ background: {BG_CARD}; }}
    QMessageBox QLabel {{ font-size: 13px; }}
    """
