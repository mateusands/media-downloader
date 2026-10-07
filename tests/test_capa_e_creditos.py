"""
SDD — Especificação: créditos longos, "(Album Version)", catálogo que não
respondeu e capa recortada da miniatura

CONTRATO
  - `search_suggestion` usa só o primeiro nome de um crédito separado por
    vírgula que veio da origem ("Jeremy Renner, Brandon Sammons, ...").
  - "(Album Version)" não é versão diferente: é a faixa de estúdio.
  - `apply_suggested` conta à parte o item cuja consulta ao catálogo falhou
    (`failed`), para a revisão dizer "tente de novo" em vez de "sem sugestão".
  - `square_cover(imagem)` recorta o centro quadrado; `crop_cover_to_square`
    troca a capa do MP3 por esse recorte sem mexer nas outras tags.

POR QUE EXISTE
  Numa playlist corrigida, três casos sobraram para a pessoa resolver à mão:
  - O YouTube credita todos os compositores no artista. Com o crédito inteiro,
    a busca do iTunes voltava vazia; só com "Jeremy Renner" a faixa aparece
    com a duração exata.
  - "Let Me Be Myself (Album Version)" era tratado como versão, igual a "(Live)".
  - O catálogo bloqueou (403) e o lote anunciou "0 aplicado(s); 5 sem sugestão
    segura" — a pessoa resolveu à mão o que um novo clique resolveria.
  Faixa que não está à venda nunca vai ter arte do catálogo; a miniatura 16:9
  recortada no centro é a capa quadrada possível — por escolha da pessoa.

REGRA DE NEGÓCIO
  - Crédito vindo do título ("Queen & David Bowie - ...") não é cortado: só o
    da origem, que é lista de nomes.
  - "(Live)", remix e acústica continuam versões diferentes.
  - Recortar é ação explícita (botão); nunca acontece sozinho.
  - Capa já quadrada não é recortada de novo.
"""

import queue
from io import BytesIO
from urllib.error import HTTPError

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from PIL import Image

from media_downloader.downloader import DownloadManager
from media_downloader.library import crop_cover_to_square, read_local_item, square_cover
from media_downloader.metadata import confident_match, search_suggestion
from media_downloader.models import (
    MetadataPendingItem,
    MusicMetadataCandidate,
    MusicSearchSuggestion,
)

_QUADRO = b"\xff\xfb\x90\x64" + b"\x00" * 413


def _item(title, artist=None, duration=200.0):
    return MetadataPendingItem(
        title=title, file_path=f"/tmp/{title}.mp3", review_reasons=(),
        duration=duration, source_artist=artist)


def _candidato(title, artist, duration=200.0):
    return MusicMetadataCandidate(
        track_id="1", title=title, artist=artist, album="A", year="2020",
        artwork_url=None, duration_seconds=duration)


def _jpeg(largura, altura, cor=(255, 0, 0)) -> bytes:
    saida = BytesIO()
    Image.new("RGB", (largura, altura), cor).save(saida, "JPEG")
    return saida.getvalue()


class TestCreditoLongo:
    def test_deve_buscar_so_pelo_primeiro_nome_do_credito_da_origem(self):
        item = _item("Main Attraction", "Jeremy Renner, Eric Zareski, Jeremy Lee Renner")
        assert search_suggestion(item) == MusicSearchSuggestion(
            title="Main Attraction", artist="Jeremy Renner")

    def test_nao_deve_cortar_o_credito_que_veio_do_titulo(self):
        item = _item("Queen & David Bowie - Under Pressure")
        assert search_suggestion(item).artist == "Queen & David Bowie"

    def test_deve_casar_com_o_catalogo_pelo_primeiro_nome(self):
        item = _item("Main Attraction", "Jeremy Renner, Eric Zareski", duration=203.0)
        candidato = _candidato("Main Attraction", "Jeremy Renner", duration=202.878)
        assert confident_match(item, [candidato]) == candidato


class TestAlbumVersion:
    def test_album_version_deve_casar_com_a_faixa_de_estudio(self):
        item = _item("Let Me Be Myself (Album Version)", "3 Doors Down")
        candidato = _candidato("Let Me Be Myself", "3 Doors Down")
        assert confident_match(item, [candidato]) == candidato

    def test_ao_vivo_continua_sendo_outra_versao(self):
        item = _item("Let Me Be Myself (Live)", "3 Doors Down")
        assert confident_match(item, [_candidato("Let Me Be Myself", "3 Doors Down")]) is None


class _CatalogoBloqueado:
    def search(self, suggestion):
        raise HTTPError("https://itunes", 403, "limite", {}, None)

    def apply_to_mp3(self, path, candidate):
        raise AssertionError("nao deveria aplicar")


class TestCatalogoQueNaoRespondeu:
    def test_deve_contar_a_parte_o_que_o_catalogo_nao_respondeu(self):
        fila = queue.Queue()

        DownloadManager(fila, _CatalogoBloqueado()).apply_suggested(
            [_item("Chalk Outline", "Three Days Grace")])

        eventos = []
        while not fila.empty():
            eventos.append(fila.get_nowait())
        fim = eventos[-1]
        assert fim["type"] == "metadata_bulk_done"
        assert (fim["applied"], fim["kept"], fim["failed"]) == (0, 1, 1)


class TestCapaRecortada:
    def test_deve_recortar_o_centro_quadrado_da_miniatura(self):
        # Faixas laterais pretas e centro vermelho, como num video de capa.
        imagem = Image.new("RGB", (160, 90), (0, 0, 0))
        imagem.paste((255, 0, 0), (35, 0, 125, 90))
        saida = BytesIO()
        imagem.save(saida, "PNG")

        with Image.open(BytesIO(square_cover(saida.getvalue()))) as recorte:
            assert recorte.size == (90, 90)
            vermelho = recorte.convert("RGB").getpixel((2, 45))
            assert vermelho[0] > 200 and vermelho[1] < 60

    def test_deve_trocar_so_a_capa_do_mp3(self, tmp_path):
        caminho = tmp_path / "Main Attraction.mp3"
        caminho.write_bytes(_QUADRO * 120)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Main Attraction"))
        tags.add(TPE1(encoding=3, text="Jeremy Renner"))
        tags.add(TALB(encoding=3, text="The Medicine"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(160, 90)))
        tags.save(caminho)

        crop_cover_to_square(caminho)

        assert read_local_item(caminho, None).review_reasons == ()
        assert str(ID3(caminho)["TALB"]) == "The Medicine"
        assert len(ID3(caminho).getall("APIC")) == 1


class TestRecorteEmLote:
    def test_deve_recortar_so_quem_tem_miniatura_e_contar_as_falhas(self, tmp_path):
        com_miniatura = tmp_path / "a.mp3"
        com_miniatura.write_bytes(_QUADRO * 120)
        tags = ID3()
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(160, 90)))
        tags.save(com_miniatura)
        sem_capa = tmp_path / "b.mp3"
        sem_capa.write_bytes(_QUADRO * 120)
        itens = [
            MetadataPendingItem("a", str(com_miniatura), ("capa provisoria: miniatura do video",)),
            MetadataPendingItem("b", str(sem_capa), ("capa provisoria: miniatura do video",)),
        ]
        fila = queue.Queue()

        DownloadManager(fila).crop_covers(itens)

        eventos = []
        while not fila.empty():
            eventos.append(fila.get_nowait())
        assert [e["pending_item"].title for e in eventos
                if e["type"] == "metadata_cover_cropped"] == ["a"]
        assert (eventos[-1]["type"], eventos[-1]["cropped"], eventos[-1]["failed"]) == (
            "metadata_crop_done", 1, 1)


class TestArteQuaseQuadrada:
    """Caso real: a arte de "Transit of Venus" no iTunes vem em 600x538 e era
    tratada como miniatura de vídeo — o arquivo nunca saía da revisão."""

    def test_arte_de_catalogo_quase_quadrada_deve_contar_como_capa(self, tmp_path):
        caminho = tmp_path / "Chalk Outline.mp3"
        caminho.write_bytes(_QUADRO * 120)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Chalk Outline"))
        tags.add(TPE1(encoding=3, text="Three Days Grace"))
        tags.add(TALB(encoding=3, text="Transit of Venus"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(600, 538)))
        tags.save(caminho)

        assert read_local_item(caminho, None).review_reasons == ()

    def test_miniatura_4_por_3_continua_provisoria(self, tmp_path):
        caminho = tmp_path / "Clipe.mp3"
        caminho.write_bytes(_QUADRO * 120)
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Clipe"))
        tags.add(TPE1(encoding=3, text="X"))
        tags.add(TALB(encoding=3, text="Y"))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_jpeg(480, 360)))
        tags.save(caminho)

        assert read_local_item(caminho, None).review_reasons == (
            "capa provisoria: miniatura do video",)
