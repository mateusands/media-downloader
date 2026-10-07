"""MP3 que ja estao no disco — nao conhece widget nem fila."""

from io import BytesIO
from pathlib import Path

from mutagen import MutagenError
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from PIL import Image

from .metadata import PROPORCAO_QUADRADA
from .models import MetadataPendingItem


def music_files(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() == ".mp3"
    )


def _first_text(tags: ID3 | None, frame_id: str) -> str | None:
    if tags is None:
        return None
    frames = tags.getall(frame_id)
    if not frames or not frames[0].text:
        return None
    return str(frames[0].text[0]).strip() or None


def _cover_reason(tags: ID3 | None) -> str | None:
    """Arte de album e quadrada; a miniatura 16:9 do video e capa provisoria."""
    covers = tags.getall("APIC") if tags is not None else []
    if not covers:
        return "capa ausente"
    try:
        with Image.open(BytesIO(covers[0].data)) as image:
            width, height = image.size
    except Exception:
        return "capa ilegivel"
    if PROPORCAO_QUADRADA[0] <= width / height <= PROPORCAO_QUADRADA[1]:
        return None
    return "capa provisoria: miniatura do video"


def square_cover(data: bytes) -> bytes:
    """Centro quadrado da imagem, em JPEG — a capa possivel sem arte de catalogo."""
    with Image.open(BytesIO(data)) as image:
        rgb = image.convert("RGB")
    side = min(rgb.size)
    left, top = (rgb.width - side) // 2, (rgb.height - side) // 2
    output = BytesIO()
    rgb.crop((left, top, left + side, top + side)).save(output, "JPEG", quality=92)
    return output.getvalue()


def crop_cover_to_square(path: Path) -> None:
    """Troca a capa do MP3 pelo recorte quadrado; as outras tags ficam."""
    tags = ID3(path)
    covers = tags.getall("APIC")
    if not covers:
        raise ValueError("o arquivo nao tem capa para recortar")
    data = square_cover(covers[0].data)
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=data))
    tags.save(path)


def read_local_item(path: Path, artist_hint: str | None) -> MetadataPendingItem:
    """O MP3 do disco como pendencia: o que esta gravado manda, o nome so supre a falta."""
    # MP3() levanta para o que nao e audio — arquivo ilegivel e falha, nao "sem tags".
    duration = MP3(path).info.length or None
    try:
        tags = ID3(path)
    except (ID3NoHeaderError, MutagenError):
        tags = None
    title = _first_text(tags, "TIT2")
    artist = _first_text(tags, "TPE1")
    hint = (artist_hint or "").strip() or None

    if title is None and artist is None and not (tags and tags.getall("APIC")):
        reasons = ["arquivo sem tags"]
    else:
        reasons = [
            reason for reason, missing in (
                ("titulo ausente", title is None),
                ("artista ausente", artist is None and hint is None),
                ("album ausente", _first_text(tags, "TALB") is None),
            ) if missing
        ]
        cover = _cover_reason(tags)
        if cover:
            reasons.append(cover)
    if artist is None and hint:
        reasons.append(f"artista informado por voce: {hint}")

    return MetadataPendingItem(
        title=title or path.stem,
        file_path=str(path),
        review_reasons=tuple(reasons),
        duration=duration,
        source_artist=artist or hint,
    )
