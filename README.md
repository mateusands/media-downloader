# Media Downloader

Desktop application with a graphical interface (CustomTkinter) for downloading video and audio from platforms compatible with yt-dlp.

## Features

- Download videos in MP4 (best available quality)
- Extract audio as MP3 (192 kbps)
- Option to add the cover art and metadata available on the platform to MP3 files
- Guided search for artist, track, album, year, and cover art when an MP3 finishes with incomplete metadata
- Accepts HTTP/HTTPS links and delegates platform compatibility checks to yt-dlp
- Automatic YouTube playlist detection, with item selection before downloading
- Resumable playlists: previously downloaded items are recorded and unchecked the next time
- MP3 files named after the song (`Every Breath You Take.mp3`, without the artist or "(Official Video)");
  playlist order is stored in the track number
- Cancel button that preserves completed downloads
- Real-time progress bar
- Downloads automatically organized into separate folders
- Modern, responsive graphical interface
- Popular platform examples on the home screen and access to the current official list

## Supported platforms

The application works with yt-dlp extractors. The home screen shows YouTube, Vimeo, TikTok,
Instagram, SoundCloud, and Dailymotion as examples, but the full list changes frequently. See the
[official list of platforms supported by yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
for the current reference. Compatibility for a specific link depends on the site and content availability.

## Requirements

System requirements:

- **Python 3.10+** (developed and tested on 3.14)
- **[FFmpeg](https://ffmpeg.org/)** — required. Converts audio to MP3 and merges video and audio into MP4;
  without it, the download appears to progress but fails during conversion.
- **Tk** — Tkinter comes with Python on Windows and macOS, but on many Linux distributions it is a
  separate package (`tk` on Arch, `python3-tk` on Debian and Ubuntu). Without it, the window will not open.
- **A JavaScript runtime** — [Deno](https://deno.com/) (preferred), Node, or Bun. Since yt-dlp 2025.11,
  YouTube requires one to make formats available; the app uses the first one it finds in `PATH`. Without
  one, YouTube provides few formats or none. `./run.sh --verificar` shows which runtime was selected.

Python dependencies, installed through `requirements.txt`:

| Package | Purpose |
|---|---|
| `yt-dlp` | Downloading and extracting source metadata |
| `customtkinter` | Dark mode interface widgets |
| `mutagen` | Reading and writing ID3 tags and cover art in MP3 files |
| `pillow` | Resizing covers displayed during review — **the app will not open without it** |

## Installation

Use a virtual environment. On recent Linux distributions, pip refuses to install into the system Python
(PEP 668, `externally-managed-environment`), and some do not include pip at all — `venv` solves both
cases because it provides its own pip.

**Linux and macOS**

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

**Windows**

```powershell
winget install "FFmpeg (Essentials Build)"
winget install DenoLand.Deno
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Running the app

```bash
.venv/bin/python src/app.py
```

## Tests

Download logic, metadata diagnostics, and catalog searches are separate from the graphical layer, so the
suite runs without opening a window or accessing the network:

```bash
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Common issues

- **`HTTP Error 403: Forbidden`, or an extraction error that appeared unexpectedly** — usually caused by
  an outdated yt-dlp installation. Platforms change frequently, and the installed version can stop working
  without any code changes: `.venv/bin/python -m pip install -U yt-dlp`.
- **The download reaches 100% and fails during conversion** — FFmpeg is missing from PATH. The yt-dlp
  message does not state this clearly.
- **`error: externally-managed-environment` during installation** — the command was run outside the
  virtual environment; see the installation section.
- **`ModuleNotFoundError: No module named 'tkinter'`** — the Tk system package is missing.

## Project structure

```
media-downloader/
├── src/
│   ├── app.py                  # entry point
│   └── media_downloader/
│       ├── config.py           # paths, external services, and limits
│       ├── theme.py            # palette and typography
│       ├── models.py           # data passed through the queue
│       ├── metadata.py         # iTunes catalog and file tags
│       ├── downloader.py       # downloads and failure reporting
│       ├── widgets.py          # reusable widgets
│       ├── review.py           # metadata review
│       ├── selection.py        # playlist item selection
│       └── window.py           # main window
├── tests/
│   ├── test_url.py
│   ├── test_caminhos.py
│   ├── test_download_manager.py
│   ├── test_playlist.py
│   ├── test_music_metadata.py
│   ├── test_metadata_automatica.py
│   ├── test_selecao.py
│   ├── test_contraste.py
│   └── test_ui_controls.py
├── assets/
│   ├── media-downloader-icon.svg
│   ├── media-downloader-icon.png
│   └── media-downloader-icon.ico
├── Downloads/
│   ├── audios_unicos/
│   ├── videos_unicos/
│   ├── playlist_audio/
│   └── playlist_video/
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
├── .gitignore
├── AGENTS.md
├── LICENSE.md
└── README.md
```

The `Downloads/` folder and its subfolders are created automatically on the first download.

## MP3 cover art and metadata

Select **MP3 audio** ("Audio MP3") and check **Add cover art and metadata** ("Adicionar capa e metadados")
before starting. The app embeds the metadata provided by the platform.

For ordinary YouTube videos, the platform does not publish an artist field: yt-dlp writes the channel
name (for example, `AudioslaveVEVO`) into the MP3 and uses the video thumbnail as cover art. This is a
**provisional** artist, rather than a file with no artist — the review screen explains this and names
the channel used for the tag.

The cover art is also provisional: YouTube thumbnails are 16:9 video frames, rather than album artwork.
The review therefore opens even when only the cover has this issue — square artwork from the catalog
replaces the video frame when imported.

These items are kept separate from download failures. For each one, the screen shows a preview of what
is already embedded in the file (cover and artist) and a search button. The search uses the iTunes API:
each result already includes the track, artist, album, year, and album cover. Choose a result to import.
In playlists, each item awaiting review is listed individually.

With **Apply automatically** ("Aplicar sozinho") checked (enabled by default alongside the metadata option),
the app searches the catalog after downloading and writes metadata without asking only when artist,
title, and duration (within 5 seconds) all match. Live, remix, and acoustic versions never count as the
same track. Uncertain cases — titles without "Artist - Song", videos with long introductions, multiple
similar results — go to review. For playlists, the item's position also becomes its track number.
With the option unchecked, nothing is written without your selection.

The iTunes catalog is commercial: it covers music sold in stores well, but does not find bootlegs,
independent releases unavailable on platforms, or rare recordings.

## 🤖 AI use

Transparency matters here: this project was built with artificial intelligence acting as an assistant
throughout development and maintenance.

In practice, AI handles repetitive and low-level work: writing code for an approach already decided on,
converting icons, drafting and updating documentation, creating validation scripts, and locating the
exact source of a defect. Scope, architecture, and design decisions are human, and every change undergoes
human review before being included — even changes written entirely by AI.

The rules the assistant follows in this repository are versioned in [AGENTS.md](AGENTS.md): the same
rules apply to anyone working on the code.

## License

Distributed under the [MIT License](LICENSE.md). Third-party dependencies retain their own licenses —
the note at the end of that file lists them.

## Usage notice

This project is for educational purposes only. Users are responsible for respecting platform terms of
use and copyright laws.
