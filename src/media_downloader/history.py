"""Historico dos arquivos baixados — sobrevive a fechar o app.

Guarda o caminho e nao a URL: link pode carregar parametro de sessao, e URL
completa nao vai para saida persistente.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

HISTORY_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "media-downloader" / "historico.json"
)
HISTORY_LIMIT = 200


@dataclass(frozen=True)
class HistoryEntry:
    title: str
    path: str
    kind: str
    finished_at: str


def _read(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def load_history(path: Path = HISTORY_PATH) -> list[HistoryEntry]:
    """Do mais recente para o mais antigo; o que nao se le fica de fora."""
    entries = []
    for raw in reversed(_read(path)):
        try:
            entry = HistoryEntry(**raw)
        except TypeError:
            continue
        if all(isinstance(value, str) for value in asdict(entry).values()):
            entries.append(entry)
    return entries


def append_history(path: Path, entries: list[HistoryEntry]) -> None:
    data = _read(path) + [asdict(entry) for entry in entries]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data[-HISTORY_LIMIT:], ensure_ascii=False, indent=1), encoding="utf-8")
