"""
SDD — Especificação: download de playlist que se retoma, se ordena e se escolhe

CONTRATO
  - `js_runtimes_for(which)` escolhe o runtime de JavaScript que o yt-dlp vai
    usar no YouTube, entre os instalados: deno, depois node, depois bun.
  - `_build_opts` em modo playlist grava um arquivo de histórico por pasta de
    destino (`download_archive`), grava a posição na playlist como número da
    faixa e espaça as requisições; em MP3, `final_ext` faz o yt-dlp reconhecer o arquivo
    já convertido.
  - `playlist_entries(info, archived_ids)` transforma a listagem rápida em
    linhas que a seleção mostra, marcando o que já foi baixado antes.
  - `playlist_items_spec(indices)` traduz a seleção para `playlist_items`.
  - `forget_archived(path, ids)` tira do histórico o que a pessoa pediu para
    baixar de novo.
  - `DownloadManager.cancel()` interrompe o download em andamento e o resumo
    chega como cancelado, não como erro.

POR QUE EXISTE
  Rodar a mesma playlist duas vezes baixava tudo de novo: sem histórico e sem
  `final_ext`, o yt-dlp procura o `.webm` que a conversão já apagou e não
  reconhece o `.mp3` ao lado. A ordem da playlist se perdia; numerar o nome do
  arquivo resolvia, mas a pessoa preferiu o arquivo só com o nome da música
  (test_nome_do_arquivo.py) — a ordem ficou na tag de faixa. E desde o yt-dlp 2025.11 o
  YouTube exige um runtime de JavaScript: só deno é ligado por padrão, então
  uma máquina com node e sem deno recebia poucos formatos ou nenhum.

REGRA DE NEGÓCIO
  - O histórico é por pasta de destino: MP3 baixado não impede o MP4 do mesmo
    vídeo.
  - Item único não usa histórico: pedir de novo o mesmo vídeo é intencional.
  - A posição gravada é a posição ORIGINAL na playlist, mesmo quando só parte
    dela foi escolhida.
  - Item já baixado aparece na seleção desmarcado, nunca escondido: a pessoa
    pode querer de novo, e aí o histórico esquece esse item.
  - Cancelar não é falha: o que já baixou fica, e o histórico lembra.
"""

import queue
from pathlib import Path

import pytest
import yt_dlp

from media_downloader.downloader import (
    ARCHIVE_FILENAME,
    DownloadManager,
    archive_ids,
    forget_archived,
    js_runtimes_for,
    playlist_entries,
    playlist_items_spec,
    summary_lines,
)
from media_downloader.models import DownloadSummary, PlaylistEntry


def _opts(file_format="mp3", playlist_mode=True, include_metadata=False):
    manager = DownloadManager(queue.Queue())
    return manager._build_opts(
        Path("/tmp/destino"), file_format, playlist_mode, DownloadSummary(),
        include_metadata,
    )


class TestRuntimeDeJavaScript:
    def test_deve_preferir_deno_quando_esta_instalado(self):
        instalados = {"deno": "/usr/bin/deno", "node": "/usr/bin/node"}
        assert js_runtimes_for(instalados.get) == {"deno": {"path": "/usr/bin/deno"}}

    def test_deve_usar_node_quando_deno_nao_esta_instalado(self):
        instalados = {"node": "/usr/bin/node"}
        assert js_runtimes_for(instalados.get) == {"node": {"path": "/usr/bin/node"}}

    def test_deve_manter_o_padrao_do_yt_dlp_quando_nenhum_runtime_esta_instalado(self):
        assert js_runtimes_for(lambda _: None) == {"deno": {}}

    def test_deve_passar_o_runtime_na_listagem_e_no_download(self, monkeypatch):
        monkeypatch.setattr(
            "media_downloader.downloader.shutil.which",
            lambda nome: "/usr/bin/node" if nome == "node" else None)
        manager = DownloadManager(queue.Queue())

        assert manager._base_opts()["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}
        assert _opts()["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}


class TestHistoricoDaPlaylist:
    def test_deve_gravar_historico_na_pasta_de_destino_em_modo_playlist(self):
        assert _opts()["download_archive"] == str(Path("/tmp/destino") / ARCHIVE_FILENAME)

    def test_nao_deve_usar_historico_para_item_unico(self):
        assert "download_archive" not in _opts(playlist_mode=False)

    def test_deve_reconhecer_o_mp3_ja_convertido(self):
        assert _opts("mp3")["final_ext"] == "mp3"

    def test_deve_reconhecer_o_mp4_ja_juntado(self):
        assert _opts("mp4")["final_ext"] == "mp4"

    def test_deve_ler_os_ids_do_historico(self, tmp_path):
        historico = tmp_path / ARCHIVE_FILENAME
        historico.write_text("youtube abc\nyoutube def\n\n", encoding="utf-8")

        assert archive_ids(historico) == {"youtube abc", "youtube def"}

    def test_deve_tratar_historico_inexistente_como_vazio(self, tmp_path):
        assert archive_ids(tmp_path / ARCHIVE_FILENAME) == set()

    def test_deve_esquecer_so_os_itens_pedidos_de_novo(self, tmp_path):
        historico = tmp_path / ARCHIVE_FILENAME
        historico.write_text("youtube abc\nyoutube def\nyoutube ghi\n", encoding="utf-8")

        forget_archived(historico, {"youtube def"})

        assert historico.read_text(encoding="utf-8") == "youtube abc\nyoutube ghi\n"

    def test_nao_deve_criar_historico_ao_esquecer_quando_ele_nao_existe(self, tmp_path):
        historico = tmp_path / ARCHIVE_FILENAME
        forget_archived(historico, {"youtube abc"})
        assert not historico.exists()


class TestNomeEOrdemDosArquivos:
    def test_deve_manter_o_nome_so_pelo_titulo_para_item_unico(self):
        assert _opts(playlist_mode=False)["outtmpl"]["default"] == str(
            Path("/tmp/destino") / "%(title)s.%(ext)s")

    def test_nao_deve_gravar_a_capa_da_propria_playlist_na_pasta(self):
        # Caso real: com a metadata ligada, sobrava "000 - Popular Music Videos.jpg".
        assert _opts(include_metadata=True)["outtmpl"]["pl_thumbnail"] == ""

    def test_deve_gravar_a_posicao_como_numero_da_faixa_quando_ha_metadata(self):
        pps = _opts(include_metadata=True)["postprocessors"]
        parser = next(pp for pp in pps if pp["key"] == "MetadataParser")

        assert parser["when"] == "pre_process"
        assert [acao[1:] for acao in parser["actions"]] == [
            ("playlist_index", "%(track_number)s")]

    def test_nao_deve_gravar_numero_da_faixa_para_item_unico(self):
        pps = _opts(playlist_mode=False, include_metadata=True)["postprocessors"]
        assert "MetadataParser" not in [pp["key"] for pp in pps]

    def test_deve_aceitar_as_opcoes_no_yt_dlp_de_verdade(self):
        # As chaves de pós-processador só são validadas quando o YoutubeDL monta.
        with yt_dlp.YoutubeDL(_opts(include_metadata=True)):
            pass


class TestRitmoDasRequisicoes:
    def test_deve_espacar_requisicoes_em_playlist(self):
        opts = _opts()
        assert opts["sleep_interval_requests"] > 0
        assert 0 < opts["sleep_interval"] <= opts["max_sleep_interval"]

    def test_nao_deve_atrasar_o_item_unico(self):
        opts = _opts(playlist_mode=False)
        assert "sleep_interval" not in opts
        assert "sleep_interval_requests" not in opts

    def test_deve_esperar_cada_vez_mais_entre_novas_tentativas_ate_um_teto(self):
        espera = _opts()["retry_sleep_functions"]["http"]
        assert [espera(n) for n in (0, 1, 2, 3)] == [1, 2, 4, 8]
        assert espera(20) == 30


class TestListagemParaEscolha:
    def test_deve_numerar_pela_posicao_original_mesmo_com_buracos(self):
        info = {"entries": [
            {"id": "a", "ie_key": "Youtube", "title": "Faixa A"},
            None,
            {"id": "c", "ie_key": "Youtube", "title": "Faixa C", "duration": 200},
        ]}

        assert playlist_entries(info, set()) == [
            PlaylistEntry(1, "Faixa A", "youtube a", None, False),
            PlaylistEntry(3, "Faixa C", "youtube c", 200, False),
        ]

    def test_deve_marcar_o_que_ja_esta_no_historico(self):
        info = {"entries": [
            {"id": "a", "ie_key": "Youtube", "title": "Faixa A"},
            {"id": "b", "ie_key": "Youtube", "title": "Faixa B"},
        ]}

        linhas = playlist_entries(info, {"youtube b"})

        assert [linha.already_downloaded for linha in linhas] == [False, True]

    def test_deve_nomear_item_sem_titulo_pelo_id(self):
        info = {"entries": [{"id": "xyz", "ie_key": "Youtube"}]}
        assert playlist_entries(info, set())[0].title == "xyz"

    def test_deve_traduzir_a_escolha_para_o_yt_dlp_em_ordem(self):
        assert playlist_items_spec([5, 1, 3]) == "1,3,5"


class TestDownloadDosItensEscolhidos:
    """O fluxo em duas etapas, com o yt-dlp trocado por um registro."""

    class _YoutubeDLFalso:
        chamadas: list = []
        info: dict = {}

        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download=False):
            return self.info

        def download(self, urls):
            type(self).chamadas.append(self.opts)

    @pytest.fixture
    def falso(self, monkeypatch, tmp_path):
        falso = self._YoutubeDLFalso
        falso.chamadas = []
        falso.info = {"_type": "playlist", "entries": [
            {"id": "a", "ie_key": "Youtube", "title": "Faixa A"},
            {"id": "b", "ie_key": "Youtube", "title": "Faixa B"},
            {"id": "c", "ie_key": "Youtube", "title": "Faixa C"},
        ]}
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", falso)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS",
            {("mp3", True): tmp_path, ("mp3", False): tmp_path})
        return falso

    def _eventos(self, fila):
        eventos = []
        while not fila.empty():
            eventos.append(fila.get_nowait())
        return eventos

    def test_deve_listar_a_playlist_e_esperar_a_escolha_antes_de_baixar(self, falso, tmp_path):
        (tmp_path / ARCHIVE_FILENAME).write_text("youtube b\n", encoding="utf-8")
        fila = queue.Queue()
        manager = DownloadManager(fila)

        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        listado = [e for e in self._eventos(fila) if e["type"] == "playlist_listed"]
        assert falso.chamadas == []
        assert [e.already_downloaded for e in listado[0]["entries"]] == [False, True, False]

    def test_deve_baixar_so_os_itens_escolhidos(self, falso):
        fila = queue.Queue()
        manager = DownloadManager(fila)
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        manager.download_selected([1, 3])

        assert falso.chamadas[0]["playlist_items"] == "1,3"
        concluido = [e for e in self._eventos(fila) if e["type"] == "done"][0]
        assert concluido["summary"].total_items == 2

    def test_deve_contar_no_resumo_o_que_ja_estava_baixado(self, falso, tmp_path):
        (tmp_path / ARCHIVE_FILENAME).write_text("youtube b\n", encoding="utf-8")
        fila = queue.Queue()
        manager = DownloadManager(fila)
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        manager.download_selected([1, 3])

        concluido = [e for e in self._eventos(fila) if e["type"] == "done"][0]
        assert concluido["summary"].already_downloaded_count == 1

    def test_deve_esquecer_do_historico_o_item_pedido_de_novo(self, falso, tmp_path):
        historico = tmp_path / ARCHIVE_FILENAME
        historico.write_text("youtube a\nyoutube b\n", encoding="utf-8")
        manager = DownloadManager(queue.Queue())
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        manager.download_selected([2])

        assert historico.read_text(encoding="utf-8") == "youtube a\n"

    def test_deve_baixar_item_unico_direto_sem_pedir_escolha(self, falso):
        falso.info = {"id": "a", "title": "Faixa A"}
        fila = queue.Queue()

        DownloadManager(fila).download("https://www.youtube.com/watch?v=a", "mp3")

        assert len(falso.chamadas) == 1
        assert "playlist_items" not in falso.chamadas[0]
        assert "playlist_listed" not in [e["type"] for e in self._eventos(fila)]

    def test_nao_deve_abrir_a_escolha_quando_cancelaram_durante_a_listagem(
        self, falso, monkeypatch,
    ):
        fila = queue.Queue()
        manager = DownloadManager(fila)
        monkeypatch.setattr(
            falso, "extract_info",
            lambda self, url, download=False: (manager.cancel(), self.info)[1])

        manager.download("https://www.youtube.com/playlist?list=PLx", "mp3")

        tipos = [e["type"] for e in self._eventos(fila)]
        assert "playlist_listed" not in tipos
        assert tipos[-1] == "cancelled"

    def test_deve_recusar_escolha_sem_playlist_listada(self):
        fila = queue.Queue()
        DownloadManager(fila).download_selected([1])
        assert [e["type"] for e in self._eventos(fila)] == ["error"]


class TestCancelamento:
    def test_deve_interromper_o_download_no_proximo_progresso(self):
        resumo = DownloadSummary(total_items=3)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(resumo)

        manager.cancel()

        with pytest.raises(yt_dlp.utils.DownloadCancelled):
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})

    def test_deve_publicar_cancelado_e_nao_erro(self, monkeypatch, tmp_path):
        manager = DownloadManager(queue.Queue())

        class _Cancela(TestDownloadDosItensEscolhidos._YoutubeDLFalso):
            def download(self, urls):
                raise yt_dlp.utils.DownloadCancelled("cancelado")

        _Cancela.info = {"id": "a", "title": "Faixa A"}
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _Cancela)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp3", False): tmp_path})

        manager.download("https://www.youtube.com/watch?v=a", "mp3")

        tipos = []
        while not manager._q.empty():
            tipos.append(manager._q.get_nowait()["type"])
        assert "cancelled" in tipos
        assert "error" not in tipos

    def test_novo_download_deve_comecar_sem_o_cancelamento_anterior(self):
        resumo = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        manager.cancel()
        manager._reset_cancel()

        manager._make_progress_hook(resumo)(
            {"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})


class TestProgressoComEscolha:
    def test_deve_mostrar_a_contagem_do_que_foi_escolhido_e_nao_a_posicao(self):
        fila = queue.Queue()
        resumo = DownloadSummary(total_items=2, downloaded_count=1)
        hook = DownloadManager(fila)._make_progress_hook(resumo)

        hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10,
              "info_dict": {"title": "Faixa C", "playlist_index": 30}})

        assert fila.get_nowait()["message"] == "Baixando: Faixa C (2/2)"


class TestResumoDaPlaylist:
    def test_deve_contar_o_que_ja_estava_baixado(self):
        linhas = summary_lines(DownloadSummary(downloaded_count=2, already_downloaded_count=5))
        assert "==  5 item(s) ja baixado(s) antes, ignorado(s)" in linhas

    def test_nao_deve_falar_de_historico_quando_nada_estava_baixado(self):
        linhas = summary_lines(DownloadSummary(downloaded_count=2))
        assert not any("ja baixado" in linha for linha in linhas)
