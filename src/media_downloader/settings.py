"""Preferencias que sobrevivem entre execucoes — nao conhece widget nem fila."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .config import BASE_DOWNLOADS_DIR

SETTINGS_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "media-downloader" / "config.json"
)


@dataclass(frozen=True)
class Settings:
    downloads_dir: Path = BASE_DOWNLOADS_DIR


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    # Preferencia ilegivel nao pode impedir o app de abrir: volta ao padrao.
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        downloads_dir = data["downloads_dir"]
        if not isinstance(downloads_dir, str) or not downloads_dir:
            return Settings()
        return Settings(downloads_dir=Path(downloads_dir))
    except (OSError, ValueError, KeyError, TypeError):
        return Settings()


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"downloads_dir": str(settings.downloads_dir)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
