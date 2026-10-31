# DESIGN.md — Media Downloader

`AGENTS.md` says how to build the project; this file says how it looks. Read it before building or
changing a screen. It describes the tokens that exist in `src/media_downloader/theme.py` — it never
introduces a value the theme does not have. A token change and its line here change together.

## Overview

- **Register:** focused utility, moderately expressive. One task per screen (download a link), so it may
  breathe more than a dense admin tool — but there is **one accent**, and the gradient is reserved for the
  primary action and the progress fill.
- **Feel:** dark, calm, precise, a little cinematic (thumbnail first).
- **Who:** one person on their own desktop, downloading music and clips, often with a playlist.
- **Theme:** dark only. Qt `Fusion` style everywhere — the native style ignores part of the QSS.

## Colours

Depth comes from luminance, not shadow: every stacked surface is one step lighter than the one below
(`test_contraste.py` enforces the ladder).

| Role | Token | Value |
|---|---|---|
| Window | `BG_DARK` | `#09090e` |
| Sidebar, action bar | `BG_SIDEBAR` | `#0d0d14` |
| Card, list row | `BG_CARD` | `#12121a` |
| Input, format card, option row hover | `BG_INPUT` | `#1a1a24` |
| Hover, skeleton, thumbnail placeholder | `BG_HOVER` | `#23232f` |
| Hairline border | `CLR_BORDER` / `CLR_BORDER_HOVER` | `#2b2b3a` / `#3d3d52` |
| Text / secondary text | `CLR_TEXT` / `CLR_MUTED` | `#f4f4f8` / `#9b9bb0` |
| Accent (primary start, selection, handles) | `CLR_ACCENT` | `#6d4aff` |
| Primary gradient end | `CLR_ACCENT_END` | `#9333ea` |
| Primary hover (darker, not lighter) | `CLR_ACCENT_DARK` → `CLR_ACCENT_HOVER_END` | `#5a38e8` → `#8429d9` |
| Pressed | `CLR_ACCENT_PRESSED` | `#4b2fc4` |
| Accent text on dark, focus ring | `CLR_ACCENT_LIGHT` | `#b3a1ff` |
| Selected surface / icon tile | `CLR_ACCENT_SOFT` / `CLR_ACCENT_TILE` | `#1e1838` / `#2a2150` |
| Focus halo (painted widgets) | `CLR_FOCUS_HALO` | `#466d4aff` (accent at 27%) |
| Text on accent / on light chip | `CLR_ON_ACCENT` / `CLR_ON_LIGHT` | `#ffffff` / `#111118` |
| State indicators (switch off, checkbox border) | `CLR_SWITCH_OFF` / `CLR_CONTROL_BORDER` | `#686884` |
| Disabled foreground | `CLR_DISABLED` | `#4a4a5c` |
| Success / error | `CLR_GREEN` / `CLR_ERROR` | `#3dd68c` / `#ff7085` |
| Danger button border / hover | `CLR_DANGER_BORDER` / `CLR_DANGER_BG` | `#5a2a35` / `#2a1218` |

**Measured contrast** (all in `test_contraste.py`; text ≥ 4.5:1, state indicators ≥ 3:1):

| Pair | Ratio |
|---|---|
| Text on card | 16.98 |
| White on primary, both gradient ends | 4.70 / 4.91 |
| White on primary hover, both ends | 6.05 / 5.80 |
| Muted on hover (lightest surface it sits on) | 5.70 |
| Error on input | 6.50 |
| Accent-light badge on accent-soft | 7.61 |
| Switch off on card / on row hover | 3.46 / 3.21 |
| Checkbox border on input | 3.21 |
| Ruler handle ring on card | 3.61 |

Lighter violets fail with white text (`#7d5dff` 3.92, `#9b3cf0` 4.45): hover **darkens** the fill.

## Typography

- Families: `Inter, Segoe UI, SF Pro Text, Cantarell, Noto Sans, sans-serif` (`FONT_FAMILIES`), base 10 pt.
- Scale (QSS): page title `#h1` 26 px / 800 / -0.4 tracking · section `#h2` 14 px / 700 · preview title
  16 px / 700 · body 13 px · secondary `#small` 11 px · sidebar label 11 px / 700 / +0.6 tracking, caps.
- **Tabular numbers** (`widgets.tabular`, QFont feature `tnum`): percentage, speed/ETA line, clip time
  fields, ruler tick labels — anything that changes while you look at it.

## Layout

- Window 1120×820, minimum 980×700. Sidebar fixed 236 px. Content column max 820 px, centred, 40 px side
  margins, 16 px between cards; it scrolls, the sidebar and the action bar do not.
- Card padding 20×18; row padding 12×10; gaps 8 / 12 / 14 / 16.
- The action bar (progress, status, Cancel, Download) is fixed at the bottom of every page; "Download"
  is hidden on the history page, where there is no link.

## Depth

No shadows. Elevation = one luminance step + 1 px `CLR_BORDER`. Selected = `CLR_ACCENT_SOFT` fill +
accent border. Hover = `CLR_BORDER_HOVER` border or `BG_INPUT`/`BG_HOVER` fill.

## Shapes

Radius scale: 6 (skeleton bars, progress), 8 (badges), 9–10 (buttons, inputs, cover boxes), 12 (list
rows, thumbnail), 14 (format card, URL bar), 15 (pill chips — Qt ignores a radius above half the
height), 16 (cards). Inner radius ≤ outer radius.

## Motion

- `MOTION_FAST_MS` = 140 ms, `OutCubic`: the switch knob. Nothing else animates.
- Loading is a static skeleton (no shimmer), so there is nothing to reduce for reduced-motion users.
  Qt exposes no reduced-motion setting on Linux; any new animation must stay ≤ 200 ms and be optional.

## Components

| Component | Anatomy and states |
|---|---|
| URL bar (`#urlBar`) | link icon · line edit · "Colar". Border: focused → accent, invalid → error, with the reason below |
| Preview card (`PreviewCard`) | 176×99 thumbnail (cropped to fill, radius 12) · title · channel · duration · badges. Loading → skeleton in the same shape. Failed → placeholder + "still possible to download" |
| Format card (`FormatCard`) | 42 px icon tile · title · subtitle · check. Selected: soft fill, accent border, accent icon. Keyboard focus: light-accent border |
| Quality chip (`#chip`) | pill; checked = light fill + dark text; disabled when the video does not reach that height, with a tooltip saying why |
| Range slider (`RangeSlider`) | 6 px track, gradient fill between two 18 px white handles with accent ring, quarter ticks with times. Focus: halo on the active handle |
| Option row (`OptionRow`) | icon · title · explanation · switch; the whole row toggles. Unavailable: dimmed, switched off, tooltip with the reason |
| Switch (`ToggleSwitch`) | 48×30 incl. focus halo; on = accent, off = `CLR_SWITCH_OFF` |
| Primary button (`#primary`) | gradient accent→end, 12 px radius, white text; disabled = flat `BG_HOVER` + muted text |
| Navigation (`#navButton`) | icon + label; active = soft fill **and** a 3 px accent bar on the left (more than colour) |
| Empty state | icon · what is missing · what to do · one primary action |

## Do and don't

- Do take every colour from `theme.py`; `test_contraste.py` fails on a hex literal in a UI module.
- Do measure a new colour pair before using it, and add it to `test_contraste.py`.
- Do give focus by Tab, not by click (`keyboard_focus`), and paint a visible ring on custom widgets.
- Do explain a disabled control (tooltip or subtitle) instead of just greying it out.
- Don't use the gradient anywhere except the primary action and the progress fill.
- Don't add a second accent hue, emoji, or shadows.
- Don't show a spinner for page content — use a skeleton in the content's shape.
