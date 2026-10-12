# AGENTS.md — Media Downloader

## Project purpose

Python desktop application with a dark mode interface (CustomTkinter) for downloading video or audio from
platforms compatible with yt-dlp. Accepts HTTP/HTTPS links and leaves support checks to yt-dlp; preserves
explicit YouTube playlist detection to avoid downloading an entire collection when the user supplied a
single-video link. For playlists, it lists items and lets users choose what to download, remembering
previous downloads. Organizes downloads into folders by type, shows real-time progress, allows cancellation,
and finishes with a summary of failed items. Also fixes cover art and metadata for MP3 files already on disk
("Corrigir pasta MP3", meaning "Fix MP3 folder"), using the same catalog and review process as downloads.

Portfolio project.

---

## Source of truth

**The actual system state is the code.** It lives in `src/media_downloader/`, with one module per layer
(see below). Read the relevant module before making changes — not the entire package.

---

## Stack

- **Python 3.10+** (the local environment uses 3.14)
- **yt-dlp** — downloading and metadata extraction
- **mutagen** — reading and writing ID3 tags and cover art in MP3 files
- **Pillow** — resizes covers displayed during metadata review. Imported at the top of `app.py`:
  without it, the application **will not open**, even if nobody uses the metadata option.
- **CustomTkinter** — dark mode widgets built on Tkinter
- **FFmpeg** (system binary) — required for MP3 and for merging video+audio into MP4
- **pytest** — pure backend logic tests, without opening a window

### Structure

```
media-downloader/
├── requirements.txt        # dependencies for running the app
├── requirements-dev.txt    # + pytest (development)
├── pytest.ini              # pythonpath=src, testpaths=tests
├── src/
│   ├── app.py              # entry point: only builds the window and calls mainloop
│   └── media_downloader/
│       ├── config.py       # paths, external services, and limits
│       ├── theme.py        # palette and typography
│       ├── models.py       # immutable data passed through the queue
│       ├── metadata.py     # iTunes catalog and file tags
│       ├── library.py      # MP3 files already on disk become items awaiting metadata
│       ├── settings.py     # saved preferences (destination folder) in ~/.config/media-downloader
│       ├── downloader.py   # ReportingLogger and DownloadManager
│       ├── widgets.py      # FormatCard and HoverButton
│       ├── review.py       # MetadataReview — metadata review dialogs
│       ├── selection.py    # PlaylistChoice (state) and PlaylistSelection (dialog)
│       └── window.py       # MediaDownloaderApp
├── assets/                 # master SVG icon, UI PNG, and Windows ICO
└── tests/
    ├── test_url.py              # URL validation and classification
    ├── test_caminhos.py         # repository root for assets and downloads
    ├── test_download_manager.py # interpreting yt-dlp results + failure reporting
    ├── test_playlist.py         # history, numbering, item selection, cancellation, JS runtime
    ├── test_music_metadata.py   # suggestions, catalog, and importing selected metadata
    ├── test_metadata_automatica.py # confident matching and automatic application
    ├── test_pasta_existente.py  # fixing an MP3 folder and catalog rate limits
    ├── test_aceitar_em_lote.py  # "Apply suggestion to all" during review
    ├── test_pasta_de_destino.py # choosing and remembering the destination folder
    ├── test_capa_e_creditos.py  # long credits, "(Album Version)", unresponsive catalog, cropping
    ├── test_selecao.py          # playlist item selection state
    ├── test_contraste.py        # palette AA contrast
    └── test_ui_controls.py      # activating controls and review dialogs
```

**Module dependencies flow in only one direction:** `window` and `widgets` import from the backend;
`downloader` and `metadata` never import from `window`, `widgets`, or `theme`. If you need the reverse,
the rule belongs in the wrong layer.

Downloads go to the folder selected under "Salvar em / Alterar pasta" ("Save to / Change folder"), stored in
`$XDG_CONFIG_HOME/media-downloader/config.json` by `settings.py`; without a selection, they use `Downloads/`
at the repository root. The subfolders by type are the same under any base folder:

| Format | Single item | Playlist |
|---|---|---|
| mp3 | `Downloads/audios_unicos/` | `Downloads/playlist_audio/` |
| mp4 | `Downloads/videos_unicos/` | `Downloads/playlist_video/` |

For playlists, each file goes into `<folder>/<playlist name>/`, and each playlist folder contains a
`.historico-de-downloads.txt` file (yt-dlp's `download_archive`). MP3 files use the **song name**: the catalog
title when applied, otherwise the video's `song_title` (without the artist or "(Official Video)"). Do not
number filenames — the user requested only the song name; playlist order goes into the track tag (`TRCK`).

---

## Architecture — the separation that gives this project value

Each layer is a module, and they **must not be mixed**:

### 1. Data — `models.py`, `config.py`, `theme.py`

- `DownloadSummary` (dataclass) — counters, failed items, items awaiting metadata, destination folder, and playlist mode.
- `MetadataPendingItem` and `MusicMetadataCandidate` — immutable data passed through the queue; no tag field
  is inferred and written without confirmation.

### 2. Backend — `downloader.py` and `metadata.py`

**Has no knowledge of the UI.** Does not import widgets, call `messagebox`, or touch `root`. Communicates
exclusively through a `queue.Queue`, publishing events with `_emit(tipo, **payload)`.

- `download(url, file_format, include_metadata, auto_metadata)` — single item: extracts info → downloads.
  Playlist: extracts a quick listing, marks items in the history, and publishes `playlist_listed`; **does not download**.
- `download_selected(indices)` — second playlist stage: removes requested repeat downloads from the history
  and downloads only the selected indices (`playlist_items`). Uses `discard_listing()` when the user backs out.
- `cancel()` — the progress hook raises `DownloadCancelled` on the next update; completion arrives as a
  `cancelled` event with the partial summary, never as `error`.
- `_base_opts()` — shared listing and download requirements (JS runtime, remote components). Options needed
  by both stages belong here, without duplication.
- `_build_opts(...)` — yt-dlp options (format, output template, hooks)
- `_make_progress_hook(...)` — translates yt-dlp progress into queued events
- `confident_match(item, candidatos)` — the candidate that needs no review: artist, title **and** duration
  (±5 s) must all match; different versions (live, remix, acoustic) never match. `_auto_apply_metadata` uses
  it after downloading when the user has enabled automatic application.
- `fix_folder(pasta, auto_metadata, artista)` — "Fix MP3 folder" ("Corrigir pasta MP3"):
  `library.read_local_item` reads each MP3 as a `MetadataPendingItem` (title and artist from tags; without
  an embedded title, the filename becomes the search query), applies confident matches through
  `_auto_apply_metadata` if requested, and publishes `folder_checked`. The artist supplied by the user
  applies only to MP3 files without an embedded artist and still goes through `confident_match`.
  Complete MP3 files — title, artist, album, and **square** cover art — are left untouched (counted in
  `already_complete_count`); 16:9 covers are video thumbnails and need correction. Typos in complete files
  are corrected manually.
- `MusicMetadataService` — searches candidates through the iTunes Search API and embeds only the selected
  candidate in the MP3. One response already includes track, artist, album, year, and artwork, so no second
  cover request is needed. `read_embedded` reads back what is already written in the file — the source
  for the preview displayed in the UI.
- `metadata_review_reasons(info)` — describes what yt-dlp actually wrote (source artist, provisional artist
  from the channel, or missing artist); `metadata_review_detail` turns this into UI text.
- `ReportingLogger` — captures yt-dlp warnings/errors and accumulates them in `summary.failed_items`;
  on completion, `_reconcile_failure_reports` removes technical diagnostics when all expected items downloaded.

### 3. UI — `window.py` and `widgets.py`

Consumes the queue through **polling**: `self.root.after(100, self._poll)` reschedules every 100 ms,
draining the `queue` with `get_nowait()` and dispatching to `_handle(event)`.

> **This is the project's concurrency pattern: queue + polling.** It does not call `root.after(0, callback)`
> from within a thread — the thread publishes to the queue and the UI reads it independently. When adding
> thread→UI communication, **use the existing queue**; do not invent a second mechanism.

Downloads run in `threading.Thread(target=self._manager.download, daemon=True)`, stored in
`self._thread` for the "download in progress" guard.

---

## Development rules

- **`DownloadManager` never imports or touches widgets.** This boundary makes the logic independently
  testable — it is the project's best feature; do not break it for convenience.
- **Thread→UI communication only through the `queue`.** New event = new `type` handled in `_handle`.
- **One download at a time.** `start_download` already blocks when `self._thread.is_alive()`. Keep the guard,
  and start every download thread through `_start_worker` so the guard also sees the second playlist stage.
- **Pending metadata is not a failure.** Keep `metadata_pending_items` separate from `failed_items`,
  especially in playlist summaries.
- **Inference never means importing — except where the user has authorized it.** `suggest_music_search`
  may interpret the title to formulate a search; write only the `MusicMetadataCandidate` confirmed by the
  user during review **or** approved by `confident_match` for a download where the user enabled
  "Apply automatically" ("Aplicar sozinho"). With the option disabled, nothing is written without confirmation.
  Relaxing `confident_match` (accepting two out of three criteria or matching only the title) means writing
  guesses into the user's file — ambiguous cases go to review.
  Explicit exception: the review's **"Apply suggestion to all"** ("Aplicar sugestão em todos") button uses
  `suggested_match`, which relaxes only duration (artist and title, including version, remain required) —
  the click is authorization, and the relaxed criterion must not carry over into automatic application.
  For tracks outside the catalog, "Crop cover"/"Crop thumbnails" ("Recortar capa"/"Recortar miniaturas")
  (`crop_covers`) replaces the 16:9 thumbnail with its square center — also only after a click.
- **Do not call embedded data missing.** yt-dlp's `FFmpegMetadata` falls back to the channel name when
  the source does not publish `artist` — the MP3 has a provisional artist (`AudioslaveVEVO`), not a missing
  artist. Review names the channel; saying "missing: artist" was false and led to `metadata_review_reasons`.
- **Thumbnail artwork is provisional, like the channel artist.** YouTube thumbnails are 16:9 video frames,
  and `EmbedThumbnail` embeds them as supplied. Treating "thumbnail exists" as "cover resolved" closed
  the review and left a 1280x720 PNG in the MP3 instead of artwork. `_cover_reason` measures the declared
  aspect ratio; without dimensions, it makes no assertion.
- **The preview shows the file, not an assumption.** Review dialog content comes from `read_embedded`,
  read by the backend in a thread and published to the queue — the graphical thread does not open files.
- **`ignoreerrors: True`** makes yt-dlp continue a playlist when an item fails — this is why `failed_items`
  exists in the summary. Do not "fix" this by aborting everything at the first error.
- **Previously downloaded items appear in the selection, unchecked — never hidden.** If the user checks
  one, `forget_archived` removes it from the history before downloading; otherwise yt-dlp silently skips
  what the user requested.
- **Rename after writing tags.** `_apply_candidate` writes tags and only then renames the file to the
  catalog title; a rename failure does not undo the import. Every candidate application path goes through it.
- **Do not log full URLs** in persistent output; they may contain session parameters.
- **Do not add yt-dlp options that bypass access restrictions.** The tool downloads what the user can
  already access.

---

## Non-negotiable rule: SDD + BDD + TDD

No production code is written without a spec (SDD) → behavior (BDD) → failing test (TDD).
No exceptions, even for small changes. All three belong together, **in that order**, in the same test file.

### 1. SDD — the spec comes first, at the top of the test file

A header explains **the contract**, **why it exists** (the bug or decision that led to it), and
**the business rules**. The spec preserves context over time; the test alone does not explain why.

### 2. BDD — describe BEHAVIOR, not implementation

`class Test<CenárioDeNegócio>` → `def test_deve_<resultado>_quando_<condição>`, in Portuguese, using
operational language (download, playlist, failed item). These templates mean "Test<BusinessScenario>"
and "test_should_<result>_when_<condition>". If the test name mentions an internal variable, it is testing
implementation — rewrite it.

### 3. TDD — mandatory cycle

**Red** (write the test, run it, **it must fail**) → **Green** (minimum needed to pass) → **Refactor**.

### What to test (by priority)

| Priority | Target | Examples |
|---|---|---|
| 🔴 High | Pure backend helpers | `_valid_url`, `_url_is_playlist`, `_is_playlist_result`, `_count_items` |
| 🔴 High | Event translation | `ReportingLogger` (failure deduplication, prefix removal) |
| 🟡 Medium | Option construction | `_build_opts` — format, `outtmpl`, `noplaylist` |
| 🟢 Low | UI | There is no jsdom here; widget behavior is validated manually |

**Mock only external systems** (yt-dlp, network, FFmpeg). `DownloadManager` is already testable without
mocks because it has no knowledge of the UI — **this boundary supports the test suite**, making it non-negotiable.

⚠️ **Beware of mocks that hide bugs:** if behavior exists only when yt-dlp is mocked, isolate the rule
in a pure function and test it directly.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                      # full suite
pytest -k playlist          # a subset
```

After green, **exercise the app for real** — downloading a video is what the suite does not cover.

---

### Commit convention

Conventional Commits: `feat: add quality selection`, `fix: handle private playlists`,
`test: cover item counting with null entries`, `refactor: extract option construction`.

---

## Known pitfalls

1. **FFmpeg is required, and the failure is confusing.** MP3 uses the `FFmpegExtractAudio` postprocessor;
   MP4 uses `merge_output_format` (yt-dlp downloads video and audio separately, then merges them).
   With metadata enabled, `FFmpegMetadata` and `EmbedThumbnail` are also used, with `writethumbnail` —
   everything depends on the same binary. Without FFmpeg in PATH, the download appears to progress but
   fails during conversion, with a message that does not say "install ffmpeg".

2. **yt-dlp can break on its own.** YouTube changes, and the installed version stops working without any
   code changes. For extraction errors, **first** suspect an outdated version: `pip install -U yt-dlp`.

3. **`"remote_components": ["ejs:github"]`** in `_base_opts` makes yt-dlp fetch remote components —
   it needs network access beyond YouTube and may fail in restricted environments.

4. **Without `final_ext`, existing MP3 files are downloaded again.** yt-dlp looks for the file using the
   download extension (`.webm`), which conversion removed. `final_ext` and history are the two safeguards;
   history applies only to playlists.

5. **JavaScript runtime.** Since yt-dlp 2025.11, YouTube requires one, and only deno is enabled by default.
   `js_runtimes_for` selects deno → node → bun among installed runtimes; scripts come from the
   `yt-dlp[default]` extra (`yt-dlp-ejs`). Without a runtime, YouTube provides few formats or none —
   `./run.sh --verificar` shows which runtime was selected.

6. **Output templates using `%(title)s`** — titles containing `/`, emoji, or very long names can cause
   filename problems, depending on the file system.

7. **An accepted URL is not necessarily supported.** `_valid_url` only requires HTTP/HTTPS with a host;
   support, availability, and access checks belong to yt-dlp. Do not list platforms as a guarantee:
   the screen shows examples and links to the current official list.

8. **Paths are relative to the file declaring them, not the working directory.**
   `config.py` goes up **three** levels (`config.py` → `media_downloader` → `src` → root) to find
   `Downloads/` and `assets/`. This pitfall has already occurred: when splitting `app.py` into modules,
   the old count stopped at `src/`, the icon disappeared, and the window would not open — **with the
   suite passing**, because no test checked paths. `tests/test_caminhos.py` now covers this; moving
   `config.py` requires recalculating the path.

9. **iTunes artwork size is in the URL, and requesting more does not create pixels.** The API returns
   `artworkUrl100` (a 100 px thumbnail); replacing `100x100bb` with the desired size provides usable
   artwork — `ITUNES_ARTWORK_SIZE` (600). Requests for `1200x1200` are accepted but return **actual 600×600**
   images. The extension is not always `.jpg`, so `_ARTWORK_SIZE_SUFFIX` preserves the original extension
   rather than hardcoding one in `replace`.

10. **The iTunes catalog is commercial.** Music not sold in the store — bootlegs, releases unavailable on
    platforms, rare recordings — simply does not appear, and searches return no results without an error.
    This is the tradeoff for almost always having cover art available: the source is a storefront, not an archive.

11. **A `wraplength` wider than the column cuts off text.** Tk does not shrink the line to fit: text is
    clipped in the middle, without an ellipsis. This applies to **both** dialogs: the review's `minsize`
    follows the reasons line's `wraplength`, and the results dialog's follows the candidate title's —
    which needs wrapping because iTunes returns long track names. Changing one requires changing the other.

12. **iTunes limits searches to ~20 per minute and then returns 403 (sometimes 429).** The error was
    swallowed by `_auto_apply_metadata`, sending obvious matches to review — when fixing a folder of
    136 MP3 files, 87 ended up there. `CATALOG_MIN_INTERVAL_SECONDS` spaces searches, and
    `_fetch_with_backoff` waits and retries only on 403/429. Even so, batch processing separately counts
    unanswered catalog requests (`failed`): "no suggestion" and "not queried" require different user responses.

13. **The source credits all composers in the artist field** ("Jeremy Renner, Brandon Sammons, …"), and
    iTunes finds nothing with the full credit. `search_suggestion` uses only the first name in the source
    credit; artist text from the title ("Queen & David Bowie - …") is kept intact.

---

## General rules

- **Code is the source of truth.** If anything here appears inconsistent with the code, the code wins —
  update this file accordingly.
- Document non-obvious technical decisions (in the commit and/or here).
- **Do not commit or push without an explicit instruction.**
