"""
SDD — Especificação: corrigir metadata de MP3 que já estão no disco

CONTRATO
  - `music_files(pasta)` lista os MP3 da pasta e das subpastas, em ordem estável.
  - `read_local_item(caminho, artista_informado)` transforma um MP3 do disco na
    mesma pendência (`MetadataPendingItem`) que o download produz: título,
    artista e duração lidos do arquivo, com os motivos do que falta.
  - `DownloadManager.fix_folder(pasta, aplicar_sozinho, artista_informado)` lê a
    pasta, aplica o candidato seguro quando a pessoa pediu e publica
    `folder_checked` com o resumo; o que sobra vai para a mesma revisão do download.

POR QUE EXISTE
  A pessoa tem uma biblioteca antiga (baixada antes, por outros meios) com MP3
  sem tag nenhuma e outros com tag errada ("Na Sei Lidar"). A revisão e a
  correspondência segura já existiam, mas só para o que o app acabou de baixar.

REGRA DE NEGÓCIO
  - O que está gravado no arquivo manda: título e artista vêm das tags; só sem
    título gravado o nome do arquivo vira a busca ("Pablo - Agora [Áudio Oficial]").
  - O artista informado pela pessoa vale só para o MP3 sem artista gravado — nunca
    substitui o que já está no arquivo. Em branco é o mesmo que não informar.
  - O artista informado é afirmação da pessoa, não inferência; mesmo assim, só é
    gravado quando `confident_match` aprova artista, título **e** duração.
  - Arquivo ilegível não derruba a pasta: entra em `failed_items` e o resto segue.
  - Arquivo completo — título, artista, álbum e capa quadrada — não é tocado: nem
    na aplicação automática, nem na revisão, nem no "aplicar em todos". A pessoa
    pediu assim; o preço é que erro de digitação em arquivo completo ("Na Sei
    Lidar") se corrige à mão.
  - Capa 16:9 é a miniatura do vídeo, não arte de álbum: conta como provisória,
    igual no download. Capa que não abre como imagem também não conta.
  - Na revisão, o que tem algo faltando vem antes do que só precisa ser conferido.
"""

import queue
from pathlib import Path

from io import BytesIO

from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from PIL import Image

from media_downloader.downloader import DownloadManager
from media_downloader.library import music_files, read_local_item
from media_downloader.models import MusicMetadataCandidate

# MPEG-1 Layer III, 128 kbps, 44,1 kHz: 417 bytes por quadro, 1152 amostras.
_QUADRO = b"\xff\xfb\x90\x64" + b"\x00" * 413


def _mp3(pasta: Path, nome: str, segundos=3.0, titulo=None, artista=None,
         album=None, capa=None) -> Path:
    caminho = pasta / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(_QUADRO * int(segundos * 44100 / 1152))
    if titulo or artista or album or capa:
        tags = ID3()
        if titulo:
            tags.add(TIT2(encoding=3, text=titulo))
        if artista:
            tags.add(TPE1(encoding=3, text=artista))
        if album:
            tags.add(TALB(encoding=3, text=album))
        if capa:
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Capa", data=_imagem(capa)))
        tags.save(caminho)
    return caminho


def _imagem(tamanho) -> bytes:
    if isinstance(tamanho, bytes):
        return tamanho
    largura, altura = (60, 60) if tamanho is True else tamanho
    saida = BytesIO()
    Image.new("RGB", (largura, altura)).save(saida, "JPEG")
    return saida.getvalue()


def _candidato(title, artist, duration, track_id="1"):
    return MusicMetadataCandidate(
        track_id=track_id, title=title, artist=artist, album="Album",
        year="2024", artwork_url=None, duration_seconds=duration,
    )


class TestListagemDaPasta:
    def test_deve_encontrar_os_mp3_das_subpastas_e_ignorar_o_resto(self, tmp_path):
        _mp3(tmp_path, "b.mp3")
        _mp3(tmp_path, "sub/a.MP3")
        (tmp_path / "capa.jpg").write_bytes(b"jpg")

        assert music_files(tmp_path) == [tmp_path / "b.mp3", tmp_path / "sub/a.MP3"]


class TestArquivoViraPendencia:
    def test_deve_usar_titulo_e_artista_gravados(self, tmp_path):
        caminho = _mp3(tmp_path, "Zimbra - Nao Sei Lidar (Audio Oficial).mp3",
                       titulo="Na Sei Lidar", artista="Zimbra", album="Azul", capa=(60, 60))

        item = read_local_item(caminho, None)

        assert item.title == "Na Sei Lidar"
        assert item.source_artist == "Zimbra"
        assert item.file_path == str(caminho)
        assert abs(item.duration - 3.0) < 0.1
        assert item.review_reasons == ()

    def test_deve_buscar_pelo_nome_do_arquivo_quando_nao_ha_titulo_gravado(self, tmp_path):
        caminho = _mp3(tmp_path, "Pablo - Agora [Áudio Oficial].mp3")

        item = read_local_item(caminho, None)

        assert item.title == "Pablo - Agora [Áudio Oficial]"
        assert item.source_artist is None
        assert item.review_reasons == ("arquivo sem tags",)

    def test_deve_usar_o_artista_informado_quando_o_arquivo_nao_tem_artista(self, tmp_path):
        item = read_local_item(_mp3(tmp_path, "Fragile.mp3"), "Laufey")

        assert item.source_artist == "Laufey"
        assert "artista informado por voce: Laufey" in item.review_reasons

    def test_nao_deve_trocar_o_artista_gravado_pelo_informado(self, tmp_path):
        caminho = _mp3(tmp_path, "Airbag.mp3", titulo="Airbag", artista="Radiohead")

        assert read_local_item(caminho, "Laufey").source_artist == "Radiohead"

    def test_artista_em_branco_deve_valer_como_nao_informado(self, tmp_path):
        assert read_local_item(_mp3(tmp_path, "Fragile.mp3"), "   ").source_artist is None

    def test_miniatura_do_video_deve_contar_como_capa_provisoria(self, tmp_path):
        caminho = _mp3(tmp_path, "Feel Good Drag.mp3", titulo="Feel Good Drag",
                       artista="Anberlin", album="New Surrender", capa=(160, 90))

        assert read_local_item(caminho, None).review_reasons == (
            "capa provisoria: miniatura do video",)

    def test_capa_que_nao_abre_nao_deve_contar_como_capa(self, tmp_path):
        caminho = _mp3(tmp_path, "Airbag.mp3", titulo="Airbag", artista="Radiohead",
                       album="OK Computer", capa=b"nao e imagem")

        assert read_local_item(caminho, None).review_reasons == ("capa ilegivel",)

    def test_deve_dizer_o_que_falta_no_arquivo(self, tmp_path):
        caminho = _mp3(tmp_path, "Airbag.mp3", titulo="Airbag", artista="Radiohead")

        assert read_local_item(caminho, None).review_reasons == ("album ausente", "capa ausente")


class _CatalogoFalso:
    def __init__(self, resultados):
        self._resultados = resultados
        self.aplicados = []

    def search(self, suggestion):
        return self._resultados.get(suggestion.title, [])

    def apply_to_mp3(self, path, candidate):
        self.aplicados.append((Path(path).name, candidate.title))
        return True


def _evento(fila, tipo):
    eventos = []
    while not fila.empty():
        eventos.append(fila.get_nowait())
    return next(e for e in eventos if e["type"] == tipo)


class TestCorrecaoDaPasta:
    def test_deve_aplicar_o_seguro_e_mandar_o_resto_para_a_revisao(self, tmp_path):
        _mp3(tmp_path, "Fragile.mp3", segundos=3.0)
        _mp3(tmp_path, "Hi.mp3", segundos=3.0)
        catalogo = _CatalogoFalso({
            "Fragile": [_candidato("Fragile", "Laufey", 4.0)],
            "Hi": [_candidato("Hi", "Laufey", 60.0)],
        })
        fila = queue.Queue()

        DownloadManager(fila, catalogo).fix_folder(str(tmp_path), True, "Laufey")

        resumo = _evento(fila, "folder_checked")["summary"]
        assert catalogo.aplicados == [("Fragile.mp3", "Fragile")]
        assert [i.title for i in resumo.metadata_pending_items] == ["Hi"]
        assert resumo.metadata_auto_applied == ["Laufey — Fragile"]
        assert resumo.total_items == 2
        assert (tmp_path / "Fragile.mp3").exists()

    def test_nao_deve_gravar_nada_quando_a_pessoa_nao_pediu(self, tmp_path):
        _mp3(tmp_path, "Fragile.mp3", segundos=3.0)
        catalogo = _CatalogoFalso({"Fragile": [_candidato("Fragile", "Laufey", 3.0)]})
        fila = queue.Queue()

        DownloadManager(fila, catalogo).fix_folder(str(tmp_path), False, "Laufey")

        assert catalogo.aplicados == []
        assert len(_evento(fila, "folder_checked")["summary"].metadata_pending_items) == 1

    def test_arquivo_ilegivel_nao_deve_derrubar_a_pasta(self, tmp_path):
        (tmp_path / "quebrado.mp3").write_bytes(b"nao e mp3")
        _mp3(tmp_path, "Fragile.mp3")
        fila = queue.Queue()

        DownloadManager(fila, _CatalogoFalso({})).fix_folder(str(tmp_path), False, None)

        resumo = _evento(fila, "folder_checked")["summary"]
        assert resumo.failed_items == ["quebrado.mp3"]
        assert [i.title for i in resumo.metadata_pending_items] == ["Fragile"]

    def test_nao_deve_tocar_no_arquivo_completo(self, tmp_path):
        _mp3(tmp_path, "Airbag.mp3", segundos=3.0, titulo="Airbag", artista="Radiohead",
             album="OK Computer", capa=True)
        _mp3(tmp_path, "Lucky.mp3", segundos=3.0, titulo="Lucky", artista="Radiohead",
             album="OK Computer", capa=(160, 90))
        catalogo = _CatalogoFalso({
            "Airbag": [_candidato("Airbag", "Radiohead", 3.0)],
            "Lucky": [_candidato("Lucky", "Radiohead", 3.0)],
        })
        fila = queue.Queue()

        DownloadManager(fila, catalogo).fix_folder(str(tmp_path), True, None)

        resumo = _evento(fila, "folder_checked")["summary"]
        assert catalogo.aplicados == [("Lucky.mp3", "Lucky")]
        assert resumo.already_complete_count == 1
        assert resumo.metadata_pending_items == []

    def test_deve_mostrar_primeiro_o_que_tem_algo_faltando(self, tmp_path):
        _mp3(tmp_path, "a.mp3", titulo="So Capa", artista="X", album="Y", capa=(160, 90))
        _mp3(tmp_path, "b.mp3")
        fila = queue.Queue()

        DownloadManager(fila, _CatalogoFalso({})).fix_folder(str(tmp_path), False, None)

        pendentes = _evento(fila, "folder_checked")["summary"].metadata_pending_items
        assert [i.title for i in pendentes] == ["So Capa", "b"]

class TestResumoDaPasta:
    def test_deve_contar_aplicados_pendentes_e_ilegiveis(self):
        from media_downloader.downloader import folder_summary_lines
        from media_downloader.models import DownloadSummary

        linhas = folder_summary_lines(DownloadSummary(
            total_items=5, failed_items=["quebrado.mp3"], already_complete_count=1,
            metadata_auto_applied=["Laufey — Fragile"],
            metadata_pending_items=[object(), object()],
        ))

        assert linhas[:5] == [
            "5 MP3 lido(s) na pasta",
            "==  1 ja completo(s), nao tocado(s)",
            "OK  1 com metadata aplicada automaticamente",
            "--  2 para confirmar na revisao",
            "--  1 arquivo(s) que nao deu para ler",
        ]
        assert "  - quebrado.mp3" in linhas


class TestLimiteDoCatalogo:
    """Caso real: corrigindo 136 MP3 seguidos, o iTunes passou a responder 403 e
    49 faixas óbvias (Radiohead — Karma Police) ficaram na revisão sem motivo."""

    @staticmethod
    def _servico(respostas):
        from urllib.error import HTTPError

        from media_downloader.metadata import MusicMetadataService

        esperas = []

        def responder(_url):
            resposta = respostas.pop(0)
            if isinstance(resposta, int):
                raise HTTPError("https://itunes", resposta, "limite", {}, None)
            return resposta

        return MusicMetadataService(fetch_json=responder, sleep=esperas.append), esperas

    def test_deve_esperar_e_tentar_de_novo_quando_o_catalogo_limita(self):
        from media_downloader.models import MusicSearchSuggestion

        ok = {"results": [{"trackId": 1, "trackName": "Karma Police", "artistName": "Radiohead"}]}
        servico, esperas = self._servico([403, 429, ok])

        candidatos = servico.search(MusicSearchSuggestion("Karma Police", "Radiohead"))

        assert [c.title for c in candidatos] == ["Karma Police"]
        assert len(esperas) == 2 and esperas[1] > esperas[0]

    def test_deve_desistir_do_erro_que_nao_e_limite(self):
        import pytest
        from urllib.error import HTTPError

        from media_downloader.models import MusicSearchSuggestion

        servico, esperas = self._servico([500, {"results": []}])

        with pytest.raises(HTTPError):
            servico.search(MusicSearchSuggestion("Karma Police"))
        assert esperas == []
