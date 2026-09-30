"""
SDD — Especificação: o MP3 se chama pelo nome da música

CONTRATO
  - `song_title(titulo_da_origem)` tira do título do vídeo o artista e o que é
    embalagem do vídeo ("(Official Music Video)", "[HD]", "(from ...)"),
    deixando só o nome da música.
  - `safe_file_stem(nome)` torna o nome válido como arquivo em qualquer sistema.
  - `rename_to_song(caminho, nome)` renomeia sem sobrescrever outro arquivo.
  - Ao fim de um download em MP3, cada arquivo passa a se chamar pelo nome da
    música; quando o catálogo é aplicado (sozinho ou na revisão), pelo título
    do catálogo.

POR QUE EXISTE
  Os arquivos saíam como "020 - The Police - Every Breath You Take (Official
  Music Video).mp3". O número e o artista já estão nas tags (faixa e artista), e
  "(Official Music Video)" descreve o vídeo, não a música. A pessoa pediu o
  arquivo com o nome da música, e só ele.

REGRA DE NEGÓCIO
  - Sem número na frente: a ordem da playlist vive na tag de faixa.
  - O título do catálogo, quando confirmado, vence o título do vídeo: é o nome
    oficial ("Picture (feat. Sheryl Crow)", "Creep (Acoustic)").
  - Parênteses que mudam a música (ao vivo, remix, acústico) ficam no nome.
  - Dois arquivos com o mesmo nome não se sobrescrevem: o segundo ganha " (2)".
  - Vídeo em MP4 mantém o título da origem: o arquivo é o vídeo, não a faixa.
"""

import queue
from pathlib import Path

import pytest

from media_downloader.downloader import DownloadManager
from media_downloader.metadata import rename_to_song, safe_file_stem, song_title
from media_downloader.models import DownloadSummary, MetadataPendingItem, MusicMetadataCandidate


class TestNomeDaMusica:
    @pytest.mark.parametrize("origem, esperado", [
        ("The Police - Every Breath You Take (Official Music Video)", "Every Breath You Take"),
        ("Nickelback - Photograph [OFFICIAL VIDEO]", "Photograph"),
        ("Foo Fighters - Everlong (Official HD Video)", "Everlong"),
        ("Pearl Jam - Black (Official Audio)", "Black"),
        ("Bôa -  Duvet (Official Video)", "Duvet"),
        ("Kid Rock - Picture feat. Sheryl Crow [Official Music Video]", "Picture"),
        ("Queen – Bohemian Rhapsody (Official Video Remastered)", "Bohemian Rhapsody"),
        ("Arcángel - FN8 ( Video Lyric )", "FN8"),
        ("I Don't Want To Talk About It (from One Night Only! Rod Stewart Live at Royal Albert Hall)",
         "I Don't Want To Talk About It"),
        ("Creep", "Creep"),
        ("Guns N' Roses - November Rain", "November Rain"),
    ])
    def test_deve_deixar_so_o_nome_da_musica(self, origem, esperado):
        assert song_title(origem) == esperado

    def test_deve_manter_o_parentese_que_muda_a_musica(self):
        assert song_title("Radiohead - Creep (Acoustic)") == "Creep (Acoustic)"
        assert song_title("Bon Jovi - Always (Live)") == "Always (Live)"

    def test_deve_manter_o_titulo_quando_limpar_nao_deixaria_nada(self):
        assert song_title("(Official Video)") == "(Official Video)"


class TestNomeValidoDeArquivo:
    def test_deve_trocar_caracteres_que_o_sistema_recusa(self):
        assert safe_file_stem('AC/DC: "Back" <In> Black?') == "AC-DC - Back In Black"

    def test_nao_deve_terminar_em_ponto_ou_espaco(self):
        assert safe_file_stem("Oops... ") == "Oops"

    def test_deve_limitar_o_tamanho(self):
        assert len(safe_file_stem("a" * 400)) == 150


class TestRenomearParaAMusica:
    def test_deve_renomear_mantendo_a_extensao(self, tmp_path):
        antigo = tmp_path / "020 - The Police - Every Breath.mp3"
        antigo.write_bytes(b"mp3")

        novo = rename_to_song(antigo, "Every Breath You Take")

        assert novo == tmp_path / "Every Breath You Take.mp3"
        assert novo.read_bytes() == b"mp3" and not antigo.exists()

    def test_nao_deve_sobrescrever_outra_musica_de_mesmo_nome(self, tmp_path):
        (tmp_path / "Creep.mp3").write_bytes(b"radiohead")
        outro = tmp_path / "TLC - Creep.mp3"
        outro.write_bytes(b"tlc")

        novo = rename_to_song(outro, "Creep")

        assert novo == tmp_path / "Creep (2).mp3"
        assert (tmp_path / "Creep.mp3").read_bytes() == b"radiohead"

    def test_nao_deve_mexer_quando_o_nome_ja_e_o_certo(self, tmp_path):
        certo = tmp_path / "Creep.mp3"
        certo.write_bytes(b"x")
        assert rename_to_song(certo, "Creep") == certo
        assert certo.exists()


def _candidato(title="Every Breath You Take"):
    return MusicMetadataCandidate(
        track_id="1", title=title, artist="The Police", album="Synchronicity",
        year="1983", artwork_url=None, duration_seconds=253.0)


class _CatalogoFalso:
    def __init__(self, resultados=()):
        self._resultados = list(resultados)

    def search(self, suggestion):
        return self._resultados

    def apply_to_mp3(self, path, candidate):
        assert Path(path).exists(), "a tag e gravada antes de renomear"
        return False


class TestNomeAoFimDoDownload:
    def _baixado(self, tmp_path, nome="The Police - Every Breath You Take (Official Music Video)"):
        arquivo = tmp_path / f"{nome}.mp3"
        arquivo.write_bytes(b"mp3")
        return arquivo

    def test_deve_renomear_o_mp3_pelo_nome_da_musica(self, tmp_path):
        arquivo = self._baixado(tmp_path)
        resumo = DownloadSummary()
        manager = DownloadManager(queue.Queue(), _CatalogoFalso())

        manager._rename_downloads(resumo, [(arquivo.stem, arquivo)])

        assert [p.name for p in tmp_path.iterdir()] == ["Every Breath You Take.mp3"]

    def test_deve_levar_a_pendencia_para_o_arquivo_renomeado(self, tmp_path):
        arquivo = self._baixado(tmp_path)
        pendencia = MetadataPendingItem(arquivo.stem, str(arquivo), ("artista ausente",))
        resumo = DownloadSummary(metadata_pending_items=[pendencia])

        DownloadManager(queue.Queue(), _CatalogoFalso())._rename_downloads(
            resumo, [(arquivo.stem, arquivo)])

        assert resumo.metadata_pending_items[0].file_path == str(
            tmp_path / "Every Breath You Take.mp3")
        assert resumo.metadata_pending_items[0].title == arquivo.stem

    def test_deve_renomear_pelo_titulo_do_catalogo_ao_aplicar_sozinho(self, tmp_path):
        arquivo = self._baixado(tmp_path, "Every Breath You Take")
        pendencia = MetadataPendingItem(
            "The Police - Every Breath You Take", str(arquivo), ("artista ausente",),
            duration=253.0)
        resumo = DownloadSummary(metadata_pending_items=[pendencia])
        catalogo = _CatalogoFalso([_candidato("Every Breath You Take (Remastered 2003)")])

        DownloadManager(queue.Queue(), catalogo)._auto_apply_metadata(resumo)

        assert [p.name for p in tmp_path.iterdir()] == [
            "Every Breath You Take (Remastered 2003).mp3"]

    def test_deve_renomear_pelo_titulo_do_catalogo_ao_confirmar_na_revisao(self, tmp_path):
        arquivo = self._baixado(tmp_path, "Headlock")
        pendencia = MetadataPendingItem("Headlock", str(arquivo), ("artista ausente",))
        fila = queue.Queue()

        DownloadManager(fila, _CatalogoFalso()).apply_metadata(
            pendencia, _candidato("Hide and Seek"))

        assert fila.get_nowait()["type"] == "metadata_applied"
        assert [p.name for p in tmp_path.iterdir()] == ["Hide and Seek.mp3"]

    def test_deve_seguir_quando_o_arquivo_sumiu_antes_de_renomear(self, tmp_path):
        resumo = DownloadSummary()
        DownloadManager(queue.Queue(), _CatalogoFalso())._rename_downloads(
            resumo, [("Faixa", tmp_path / "nao-existe.mp3")])
        assert resumo.failed_items == []


class TestSemNumeroNaFrente:
    def _outtmpl(self, file_format="mp3", playlist_mode=True):
        return DownloadManager(queue.Queue())._build_opts(
            Path("/tmp/destino"), file_format, playlist_mode, DownloadSummary(),
        )["outtmpl"]["default"]

    def test_nao_deve_numerar_o_arquivo_da_playlist(self):
        assert self._outtmpl().endswith("/%(title)s.%(ext)s")
        assert "playlist_index" not in self._outtmpl()

    def test_deve_registrar_o_caminho_final_de_cada_mp3(self):
        opts = DownloadManager(queue.Queue())._build_opts(
            Path("/tmp/destino"), "mp3", True, DownloadSummary())
        assert opts["postprocessor_hooks"]
