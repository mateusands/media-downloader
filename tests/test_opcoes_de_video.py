"""
SDD — Especificação: qualidade, legendas, capítulos e patrocínios

CONTRATO
  `MediaOptions` carrega o que a pessoa escolheu além do formato, e
  `DownloadManager._build_opts(..., options=...)` traduz cada escolha na opção
  ou no pós-processador do yt-dlp:
  - `quality`: "best" ou uma altura ("1080", "720", "480", "360") — teto, não
    exigência.
  - `subtitles`: baixa legendas em português e inglês (inclusive as automáticas)
    e as embute no MP4.
  - `chapters`: grava os capítulos da origem no MP4.
  - `skip_sponsors`: remove trechos de patrocínio marcados no SponsorBlock.
  As opções valem também para a playlist: `download` as guarda na listagem, e
  `download_selected` as usa no download de fato.

POR QUE EXISTE
  O MP4 era sempre "a melhor qualidade disponível" — num 4K de vinte minutos
  isso são gigabytes que a pessoa não pediu. Apps do mesmo tipo (Seal,
  Parabolic, Open Video Downloader, MeTube) oferecem resolução, legendas,
  capítulos e SponsorBlock; são as opções mais pedidas de um front-end do
  yt-dlp.

REGRA DE NEGÓCIO
  - Qualidade é teto: um vídeo que só existe em 720p, pedido em 1080p, baixa em
    720p em vez de falhar. Prefere MP4+M4A para não reencodar ao juntar.
  - Legendas e capítulos só existem no MP4; no MP3 são ignorados.
  - As legendas embutidas não sobram soltas na pasta como .vtt.
  - Arquivo de legenda passa pelo gancho de progresso como um download à parte
    (sem `id` no `info_dict`); contá-lo fazia um vídeo com legenda em PT e EN
    terminar como "3 item(s) baixado(s)".
  - SponsorBlock roda antes da conversão (`after_filter`), e o corte dos
    segmentos (`ModifyChapters`) antes de gravar metadados, senão os capítulos
    gravados apontam para tempos que não existem mais.
  - SponsorBlock não se combina com trecho: os segmentos vêm na linha do tempo
    do vídeo inteiro, e aplicados a um trecho cortariam o lugar errado.
"""

import queue
from pathlib import Path

import pytest

from media_downloader.downloader import DownloadManager, video_format
from media_downloader.models import DownloadSummary, MediaOptions


def _opts(file_format="mp4", playlist=False, include_metadata=False, **opcoes):
    return DownloadManager(queue.Queue())._build_opts(
        Path("/tmp/destino"), file_format, playlist, DownloadSummary(),
        include_metadata, options=MediaOptions(**opcoes),
    )


def _chaves(opts):
    return [pp["key"] for pp in opts.get("postprocessors", [])]


class TestQualidadeDoVideo:
    def test_melhor_qualidade_deve_manter_o_seletor_atual(self):
        assert _opts()["format"] == "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"

    @pytest.mark.parametrize("altura", ["1080", "720", "480", "360"])
    def test_deve_limitar_a_altura_escolhida(self, altura):
        seletor = _opts(quality=altura)["format"]
        alternativas = seletor.split("/")
        assert all(f"[height<={altura}]" in parte for parte in alternativas[:-1])

    def test_deve_preferir_mp4_com_m4a_para_nao_reencodar(self):
        assert video_format("720").startswith("bv*[height<=720][ext=mp4]+ba[ext=m4a]")

    def test_deve_cair_no_melhor_disponivel_em_vez_de_falhar(self):
        # A ultima alternativa nao tem filtro: um video sem nenhum formato abaixo
        # do teto ainda baixa.
        assert video_format("360").split("/")[-1] == "b"

    def test_qualidade_desconhecida_deve_valer_como_melhor(self):
        assert video_format("8k") == video_format("best")

    def test_qualidade_nao_deve_mudar_o_mp3(self):
        assert _opts(file_format="mp3", quality="360")["format"] == "bestaudio/best"


class TestLegendas:
    def test_deve_baixar_e_embutir_legendas_em_portugues_e_ingles(self):
        opts = _opts(subtitles=True)
        assert opts["writesubtitles"] is True
        assert opts["writeautomaticsub"] is True
        assert opts["subtitleslangs"] == ["pt.*", "en.*"]
        assert "FFmpegEmbedSubtitle" in _chaves(opts)

    def test_legenda_embutida_nao_deve_sobrar_solta_na_pasta(self):
        embed = next(pp for pp in _opts(subtitles=True)["postprocessors"]
                     if pp["key"] == "FFmpegEmbedSubtitle")
        assert embed["already_have_subtitle"] is False

    def test_sem_a_opcao_nao_deve_baixar_legenda(self):
        opts = _opts()
        assert "writesubtitles" not in opts
        assert "FFmpegEmbedSubtitle" not in _chaves(opts)

    def test_mp3_deve_ignorar_legendas(self):
        opts = _opts(file_format="mp3", subtitles=True)
        assert "writesubtitles" not in opts
        assert "FFmpegEmbedSubtitle" not in _chaves(opts)


class TestLegendaNaoEItem:
    def _contagem(self, *avisos):
        resumo = DownloadSummary(total_items=1)
        gancho = DownloadManager(queue.Queue())._make_progress_hook(resumo)
        for aviso in avisos:
            gancho(aviso)
        return resumo.downloaded_count

    def test_legendas_baixadas_nao_devem_contar_como_itens(self):
        video = {"status": "finished", "filename": "/v/Clipe.mp4",
                 "info_dict": {"id": "v1", "title": "Clipe"}}
        legenda_pt = {"status": "finished", "filename": "/v/Clipe.pt.vtt",
                      "info_dict": {"ext": "vtt", "url": "https://x/pt"}}
        legenda_en = {"status": "finished", "filename": "/v/Clipe.en.vtt",
                      "info_dict": {"ext": "vtt", "url": "https://x/en"}}

        assert self._contagem(legenda_pt, legenda_en, video) == 1


class TestCapitulos:
    def test_deve_gravar_os_capitulos_no_mp4(self):
        meta = next(pp for pp in _opts(chapters=True)["postprocessors"]
                    if pp["key"] == "FFmpegMetadata")
        assert meta["add_chapters"] is True

    def test_sem_a_opcao_nao_deve_mexer_no_mp4(self):
        assert "FFmpegMetadata" not in _chaves(_opts())

    def test_mp3_deve_ignorar_capitulos(self):
        assert "FFmpegMetadata" not in _chaves(_opts(file_format="mp3", chapters=True))


class TestPatrocinios:
    def test_deve_marcar_e_remover_os_segmentos_de_patrocinio(self):
        opts = _opts(skip_sponsors=True)
        sponsor = next(pp for pp in opts["postprocessors"] if pp["key"] == "SponsorBlock")
        corte = next(pp for pp in opts["postprocessors"] if pp["key"] == "ModifyChapters")
        assert sponsor["when"] == "after_filter"
        assert "sponsor" in sponsor["categories"]
        assert set(corte["remove_sponsor_segments"]) == set(sponsor["categories"])

    def test_deve_remover_os_segmentos_antes_de_gravar_os_metadados(self):
        chaves = _chaves(_opts(file_format="mp3", include_metadata=True, skip_sponsors=True))
        assert chaves.index("ModifyChapters") < chaves.index("FFmpegMetadata")

    def test_deve_converter_para_mp3_depois_de_consultar_o_sponsorblock(self):
        chaves = _chaves(_opts(file_format="mp3", skip_sponsors=True))
        assert chaves.index("SponsorBlock") < chaves.index("FFmpegExtractAudio")

    def test_nao_deve_combinar_com_trecho(self):
        opts = _opts(skip_sponsors=True, clip_start=60, clip_end=120)
        assert "SponsorBlock" not in _chaves(opts)
        assert "ModifyChapters" not in _chaves(opts)


class TestOpcoesNaPlaylist:
    @pytest.fixture
    def falso(self, monkeypatch, tmp_path):
        class _YoutubeDLFalso:
            chamadas: list = []
            info: dict = {}

            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=False):
                return _YoutubeDLFalso.info

            def download(self, urls):
                _YoutubeDLFalso.chamadas.append(self.opts)

        _YoutubeDLFalso.chamadas = []
        _YoutubeDLFalso.info = {"_type": "playlist", "entries": [
            {"id": "a", "ie_key": "Youtube", "title": "A"},
            {"id": "b", "ie_key": "Youtube", "title": "B"},
        ]}
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _YoutubeDLFalso)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp4", True): tmp_path})
        return _YoutubeDLFalso

    def test_deve_usar_na_playlist_a_qualidade_escolhida_antes_da_listagem(self, falso):
        manager = DownloadManager(queue.Queue())
        manager.download("https://www.youtube.com/playlist?list=PLx", "mp4",
                         options=MediaOptions(quality="720", subtitles=True))

        manager.download_selected([1, 2])

        assert "[height<=720]" in falso.chamadas[0]["format"]
        assert falso.chamadas[0]["writesubtitles"] is True
