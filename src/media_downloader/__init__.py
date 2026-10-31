"""Media Downloader — a video and audio download application.

The package follows the project's three layers: `models` and `config` are data,
`downloader` and `metadata` are the backend that knows nothing about the UI, and
`widgets` and `window` are the interface. The boundary between backend and UI is
the queue in `downloader`; nothing from the interface is imported on the backend side.
"""
