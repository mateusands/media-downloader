"""
SDD — Especificação: interpretação do resultado do yt-dlp e relato de falhas

CONTRATO
  Depois de `_extract_info`, o `DownloadManager` precisa decidir duas coisas a
  partir de um dicionário que o yt-dlp devolve num formato variável:
  `_is_playlist_result(info)` — o que veio é mesmo uma playlist?
  (Quantos itens serão baixados agora é a escolha da pessoa; a numeração que
  respeita os buracos vive em `playlist_entries`, coberta em test_playlist.py.)
  E o `ReportingLogger` traduz a saída do yt-dlp em duas listas distintas:
  `summary.failed_items` (erro) e `summary.extractor_notices` (aviso), que o
  resumo final reconcilia com os itens efetivamente baixados.

POR QUE EXISTE
  `ignoreerrors: True` faz o yt-dlp CONTINUAR a playlist quando um item falha e
  colocar `None` no lugar da entrada quebrada — um buraco, não um item. O modo playlist
  também é reavaliado aqui: a URL pode parecer playlist e o resultado vir único.
  Além disso, o yt-dlp pode registrar diagnóstico técnico como erro e ainda
  concluir o download; sem reconciliação, o resumo mostra uma falha falsa.
  A opção de metadata de MP3 também pode não encontrar artista ou capa em todos
  os itens de uma playlist. Isso é pendência de enriquecimento, nunca falha de
  download.

REGRA DE NEGÓCIO
  - Entrada `None` numa playlist é buraco, não item.
  - Falha repetida não é listada duas vezes no resumo.
  - Aviso do extrator não é item que falhou. O alerta de runtime JavaScript
    ausente aparece em download que conclui inteiro; contá-lo como falha fazia
    um vídeo só virar "2 item(s) com falha" no resumo.
  - A mensagem é limpa antes de ser comparada e exibida: a saída do yt-dlp vem
    colorida, e sem remover o escape ANSI o prefixo não casa, o mesmo erro entra
    duas vezes e o usuário lê códigos de terminal no diálogo.
  - Se todos os itens previstos foram baixados, diagnósticos do extrator não são
    exibidos como falhas ao usuário.
  - Metadata ausente em MP3 concluído vai para `metadata_pending_items`, separada
    de `failed_items`.
"""

import queue
from pathlib import Path

from media_downloader.downloader import (
    FAILURES_SHOWN,
    DownloadManager,
    ReportingLogger,
    summary_lines,
)
from media_downloader.models import DownloadSummary, MetadataPendingItem

eh_playlist_resultado = DownloadManager._is_playlist_result


class TestClassificacaoDoResultado:
    def test_deve_ser_playlist_quando_ha_entries(self):
        assert eh_playlist_resultado({"entries": [{"id": "a"}]}) is True

    def test_deve_ser_playlist_quando_o_tipo_declara_playlist(self):
        assert eh_playlist_resultado({"_type": "playlist"}) is True

    def test_nao_deve_ser_playlist_quando_o_resultado_e_um_video_unico(self):
        assert eh_playlist_resultado({"id": "abc", "title": "Um vídeo"}) is False

    def test_nao_deve_ser_playlist_quando_entries_vem_vazio(self):
        # Caso real: URL de playlist que resolveu para um item só.
        assert eh_playlist_resultado({"entries": []}) is False


class TestRelatoDeFalhas:
    def _logger(self):
        fila = queue.Queue()
        resumo = DownloadSummary()
        return ReportingLogger(fila, resumo), fila, resumo

    def test_deve_registrar_a_falha_e_avisar_que_segue_com_os_demais(self):
        logger, fila, resumo = self._logger()

        logger.error("ERROR: Video unavailable")

        assert resumo.failed_items == ["Video unavailable"]
        evento = fila.get_nowait()
        assert evento["type"] == "status"
        assert "continuando" in evento["message"].lower()

    def test_deve_remover_o_prefixo_error_da_mensagem(self):
        logger, _, resumo = self._logger()

        logger.error("ERROR:   Private video  ")

        assert resumo.failed_items == ["Private video"]

    def test_nao_deve_listar_a_mesma_falha_duas_vezes(self):
        logger, fila, resumo = self._logger()

        logger.error("ERROR: Video unavailable")
        logger.error("ERROR: Video unavailable")

        assert resumo.failed_items == ["Video unavailable"]
        assert fila.qsize() == 1, "o segundo relato não deve gerar novo aviso"

    def test_deve_registrar_warning_como_aviso_e_nao_como_falha(self):
        logger, fila, resumo = self._logger()

        logger.warning("WARNING: alguns formatos podem faltar")

        assert resumo.failed_items == []
        assert resumo.extractor_notices == ["alguns formatos podem faltar"]
        assert fila.empty(), "aviso nao anuncia item que falhou"

    def test_nao_deve_registrar_mensagem_vazia(self):
        logger, fila, resumo = self._logger()

        logger.error("ERROR:   ")

        assert resumo.failed_items == []
        assert fila.empty()

    def test_debug_nao_deve_registrar_nada(self):
        logger, fila, resumo = self._logger()

        logger.debug("baixando fragmento 3/10")

        assert resumo.failed_items == []
        assert fila.empty()


class TestResumoDeFalhas:
    def test_deve_ignorar_alerta_tecnico_quando_todos_os_itens_foram_baixados(self):
        resumo = DownloadSummary(
            total_items=1,
            downloaded_count=1,
            failed_items=["[youtube] No supported JavaScript runtime could be found."],
        )

        DownloadManager._reconcile_failure_reports(resumo)

        assert resumo.failed_items == []

    def test_deve_manter_falha_quando_nem_todos_os_itens_foram_baixados(self):
        resumo = DownloadSummary(
            total_items=2,
            downloaded_count=1,
            failed_items=["Private video"],
        )

        DownloadManager._reconcile_failure_reports(resumo)

        assert resumo.failed_items == ["Private video"]


class TestMetadataDeMp3:
    def _opts(self, incluir_metadata: bool):
        manager = DownloadManager(queue.Queue())
        return manager._build_opts(
            Path("/tmp/destino"), "mp3", False, DownloadSummary(), incluir_metadata,
        )

    def test_deve_manter_o_mp3_atual_quando_a_opcao_de_metadata_esta_desligada(self):
        opts = self._opts(incluir_metadata=False)

        assert "writethumbnail" not in opts
        assert [item["key"] for item in opts["postprocessors"]] == ["FFmpegExtractAudio"]

    def test_deve_pedir_capa_e_metadados_da_origem_quando_a_opcao_esta_ligada(self):
        opts = self._opts(incluir_metadata=True)

        assert opts["writethumbnail"] is True
        assert [item["key"] for item in opts["postprocessors"]] == [
            "FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail",
        ]

    def test_deve_listar_metadata_a_revisar_sem_marcar_o_item_como_falha(self):
        resumo = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(resumo, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/ABBA - Dancing Queen.webm",
            "info_dict": {"id": "abba-1", "title": "ABBA - Dancing Queen"},
        })

        assert resumo.downloaded_count == 1
        assert resumo.failed_items == []
        assert len(resumo.metadata_pending_items) == 1
        pendencia = resumo.metadata_pending_items[0]
        assert pendencia.title == "ABBA - Dancing Queen"
        assert pendencia.review_reasons == ("artista ausente", "capa ausente")

    def test_deve_nomear_o_canal_que_virou_artista_em_vez_de_dizer_que_falta(self):
        resumo = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(resumo, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/Audioslave - Like a Stone.webm",
            "info_dict": {
                "id": "audioslave-1",
                "title": "Audioslave - Like a Stone (Official Video)",
                "uploader": "AudioslaveVEVO",
                "thumbnail": "https://exemplo.com/capa.jpg",
            },
        })

        assert resumo.metadata_pending_items[0].review_reasons == (
            "artista provisorio: canal AudioslaveVEVO",
        )

    def test_nao_deve_listar_pendencia_quando_artista_e_capa_estao_disponiveis(self):
        resumo = DownloadSummary(total_items=1)
        manager = DownloadManager(queue.Queue())
        hook = manager._make_progress_hook(resumo, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/Dancing Queen.webm",
            "info_dict": {
                "id": "abba-1",
                "title": "Dancing Queen",
                "artist": "ABBA",
                "thumbnail": "https://exemplo.com/capa.jpg",
            },
        })

        assert resumo.metadata_pending_items == []


class TestRelatoLimpoDoExtrator:
    """O que o usuário lê no resumo não é a saída crua do yt-dlp."""

    def _logger(self):
        resumo = DownloadSummary(total_items=1)
        return ReportingLogger(queue.Queue(), resumo), resumo

    def test_deve_remover_codigos_de_cor_da_mensagem_relatada(self):
        logger, resumo = self._logger()

        logger.error("\x1b[0;31mERROR:\x1b[0m unable to download video data: HTTP Error 403")

        assert resumo.failed_items == ["unable to download video data: HTTP Error 403"]

    def test_deve_tratar_como_repetida_a_falha_que_so_difere_na_cor(self):
        logger, resumo = self._logger()

        logger.error("\x1b[0;31mERROR:\x1b[0m Video unavailable")
        logger.error("ERROR: Video unavailable")

        assert resumo.failed_items == ["Video unavailable"]

    def test_deve_separar_aviso_do_extrator_da_falha_de_item(self):
        logger, resumo = self._logger()

        logger.warning("[youtube] No supported JavaScript runtime could be found.")
        logger.error("ERROR: unable to download video data: HTTP Error 403")

        assert resumo.failed_items == ["unable to download video data: HTTP Error 403"]
        assert resumo.extractor_notices == [
            "[youtube] No supported JavaScript runtime could be found."
        ]

    def test_deve_esquecer_avisos_quando_todos_os_itens_foram_baixados(self):
        resumo = DownloadSummary(
            total_items=1,
            downloaded_count=1,
            extractor_notices=["[youtube] No supported JavaScript runtime could be found."],
        )

        DownloadManager._reconcile_failure_reports(resumo)

        assert resumo.extractor_notices == []


class TestOpcoesDeSaidaDoExtrator:
    def test_deve_pedir_saida_sem_cor_para_o_resumo_nao_receber_escapes(self):
        manager = DownloadManager(queue.Queue())

        opts = manager._build_opts(Path("/tmp/destino"), "mp3", False, DownloadSummary())

        assert opts["no_color"] is True


class TestTamanhoDoResumo:
    """O resumo responde 'o que aconteceu?'; a revisão responde 'o que corrigir?'."""

    def _pendencias(self, quantidade):
        return [
            MetadataPendingItem(
                f"Faixa numero {n} com um titulo bem longo (Official Video)",
                f"/tmp/faixa{n}.mp3",
                ("artista provisorio: canal Algum Canal", "capa provisoria: miniatura do video"),
            )
            for n in range(quantidade)
        ]

    def test_deve_contar_a_pendencia_em_vez_de_listar_item_a_item(self):
        resumo = DownloadSummary(
            total_items=7, downloaded_count=7,
            metadata_pending_items=self._pendencias(7),
        )

        linhas = summary_lines(resumo)
        texto = "\n".join(linhas)

        assert "7 MP3 com metadata a confirmar" in texto
        assert "Faixa numero" not in texto, "o detalhe por item pertence à revisão"
        assert "revisao" in texto.lower(), "o resumo precisa dizer que a revisão vem em seguida"

    def test_deve_manter_o_resumo_curto_mesmo_em_playlist_grande(self):
        resumo = DownloadSummary(
            total_items=40, downloaded_count=40,
            metadata_pending_items=self._pendencias(40),
        )

        assert len(summary_lines(resumo)) <= 6

    def test_deve_limitar_a_lista_de_falhas_e_dizer_quantas_sobraram(self):
        resumo = DownloadSummary(
            total_items=10, downloaded_count=2,
            failed_items=[f"Video {n} indisponivel" for n in range(8)],
        )

        linhas = summary_lines(resumo)

        assert sum(1 for l in linhas if l.startswith("  - Video")) == FAILURES_SHOWN
        assert f"e mais {8 - FAILURES_SHOWN}" in "\n".join(linhas)

    def test_deve_listar_todas_as_falhas_quando_cabem(self):
        resumo = DownloadSummary(
            total_items=3, downloaded_count=1,
            failed_items=["Video privado", "Video removido"],
        )

        texto = "\n".join(summary_lines(resumo))

        assert "Video privado" in texto and "Video removido" in texto
        assert "e mais" not in texto
