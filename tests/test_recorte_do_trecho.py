"""
SDD — Especificação: baixar só um trecho do vídeo ou do áudio

CONTRATO
  `clip.py` é puro (sem yt-dlp, sem widget):
  - `parse_time(texto)` lê o que a pessoa digita ("90", "1:30", "1:02:03",
    "90,5", "1:30.5") e devolve segundos; vazio é `None` (sem limite); texto
    que não é tempo levanta `ValueError`.
  - `format_time(segundos)` é o inverso, para preencher os campos e a régua.
  - `clip_error(inicio, fim, duracao)` diz em português por que o trecho não
    vale, ou `None` quando vale.
  - `youtube_start_hint(url)` lê o `t=` de um link do YouTube ("t=90",
    "t=1m30s") para já abrir o corte no ponto que a pessoa compartilhou.
  - `clip_label(inicio, fim)` é o pedaço do nome do arquivo que identifica o
    trecho.
  `DownloadManager._build_opts(..., options=MediaOptions(clip_start, clip_end))`
  traduz o trecho em `download_ranges`, e liga `force_keyframes_at_cuts` só
  quando o trecho é curto.

POR QUE EXISTE
  A pessoa quer, de um vídeo de uma hora, só "de tal minuto a tal minuto".
  Baixar tudo e cortar no editor era o passo que ela queria eliminar. A extensão
  irmã (zumbue/video-extensao-download) já faz isso com o mesmo mecanismo do
  yt-dlp; daqui vem a regra dos 3 minutos:
  - Corte preciso (`force_keyframes_at_cuts`) reencoda o trecho. Num trecho
    curto custa segundos; num trecho longo é reencodar um filme, e o
    cancelamento não alcança o FFmpeg no meio disso.
  - Sem corte preciso, o yt-dlp corta no keyframe mais próximo — o trecho pode
    começar alguns segundos antes. Aceitável num trecho longo.

REGRA DE NEGÓCIO
  - Início e fim vazios = arquivo inteiro, sem `download_ranges`.
  - Início sem fim vai até o final do vídeo e nunca reencoda (seria o vídeo
    inteiro).
  - O fim precisa ser depois do início; nenhum dos dois passa da duração
    conhecida. Duração desconhecida não bloqueia: o yt-dlp corta o que houver.
  - O trecho não vale em playlist: cada item tem duração própria, e "do minuto
    2 ao 3" de cinquenta músicas não é o que alguém quer.
  - O arquivo do trecho tem nome próprio. Sem isso, o `final_ext` faz o yt-dlp
    achar que o MP3 inteiro já baixado é o trecho pedido e não baixar nada — e
    um trecho baixado depois sobrescreveria o arquivo inteiro.
"""

import queue
from pathlib import Path

import pytest

from media_downloader.clip import (
    PRECISE_CUT_SECONDS,
    clip_error,
    clip_label,
    format_time,
    parse_time,
    youtube_start_hint,
)
from media_downloader.downloader import DownloadManager
from media_downloader.models import DownloadSummary, MediaOptions


class TestLeituraDoTempoDigitado:
    @pytest.mark.parametrize("texto, segundos", [
        ("90", 90.0),
        ("1:30", 90.0),
        ("01:30", 90.0),
        ("1:02:03", 3723.0),
        ("90,5", 90.5),
        ("1:30.5", 90.5),
        ("  2:00  ", 120.0),
    ])
    def test_deve_entender_os_formatos_que_a_pessoa_digita(self, texto, segundos):
        assert parse_time(texto) == segundos

    def test_deve_tratar_campo_vazio_como_sem_limite(self):
        assert parse_time("") is None
        assert parse_time("   ") is None

    @pytest.mark.parametrize("texto", ["abc", "1:75", "-5", "1::2", "1:2:3:4"])
    def test_deve_recusar_o_que_nao_e_tempo(self, texto):
        with pytest.raises(ValueError):
            parse_time(texto)


class TestEscritaDoTempo:
    @pytest.mark.parametrize("segundos, texto", [
        (0, "0:00"),
        (90, "1:30"),
        (3723, "1:02:03"),
        (90.5, "1:30.5"),
    ])
    def test_deve_escrever_no_formato_do_relogio(self, segundos, texto):
        assert format_time(segundos) == texto

    def test_deve_ler_de_volta_o_que_escreveu(self):
        assert parse_time(format_time(4321.5)) == 4321.5


class TestValidacaoDoTrecho:
    def test_trecho_dentro_do_video_deve_valer(self):
        assert clip_error(60, 120, 300) is None

    def test_deve_recusar_fim_antes_do_inicio(self):
        assert clip_error(120, 60, 300) == "O fim precisa ser depois do início."

    def test_deve_recusar_fim_igual_ao_inicio(self):
        assert clip_error(60, 60, 300) == "O fim precisa ser depois do início."

    def test_deve_recusar_inicio_depois_da_duracao(self):
        assert clip_error(400, None, 300) == "O início passa da duração do vídeo."

    def test_deve_recusar_fim_depois_da_duracao(self):
        assert clip_error(10, 400, 300) == "O fim passa da duração do vídeo."

    def test_nao_deve_bloquear_quando_a_duracao_e_desconhecida(self):
        assert clip_error(10, 4000, None) is None


class TestInicioSugeridoPeloLink:
    @pytest.mark.parametrize("url, segundos", [
        ("https://www.youtube.com/watch?v=abc&t=90", 90.0),
        ("https://youtu.be/abc?t=90s", 90.0),
        ("https://www.youtube.com/watch?v=abc&t=1m30s", 90.0),
        ("https://www.youtube.com/watch?v=abc&t=1h2m3s", 3723.0),
    ])
    def test_deve_usar_o_tempo_do_link_compartilhado(self, url, segundos):
        assert youtube_start_hint(url) == segundos

    def test_sem_tempo_no_link_nao_deve_sugerir_inicio(self):
        assert youtube_start_hint("https://www.youtube.com/watch?v=abc") is None

    def test_tempo_ilegivel_nao_deve_sugerir_inicio(self):
        assert youtube_start_hint("https://www.youtube.com/watch?v=abc&t=xyz") is None


class TestNomeDoTrecho:
    def test_deve_identificar_inicio_e_fim_no_nome(self):
        assert clip_label(90, 150) == "trecho 1m30s-2m30s"

    def test_deve_dizer_fim_quando_vai_ate_o_final(self):
        assert clip_label(90, None) == "trecho 1m30s-fim"

    def test_deve_incluir_horas_em_video_longo(self):
        assert clip_label(3723, 3800) == "trecho 1h02m03s-1h03m20s"

    def test_nome_nao_deve_ter_caractere_proibido_em_arquivo(self):
        assert not set(clip_label(90.5, 150.25)) & set('/\\:*?"<>|%')


def _opts(file_format="mp4", playlist=False, **opcoes):
    return DownloadManager(queue.Queue())._build_opts(
        Path("/tmp/destino"), file_format, playlist, DownloadSummary(),
        options=MediaOptions(**opcoes),
    )


def _secoes(opts):
    return list(opts["download_ranges"]({"duration": 3600}, None))


class TestOpcoesDoTrecho:
    def test_sem_trecho_deve_baixar_o_arquivo_inteiro(self):
        opts = _opts()
        assert "download_ranges" not in opts
        assert "force_keyframes_at_cuts" not in opts

    def test_deve_pedir_ao_yt_dlp_so_o_trecho_escolhido(self):
        opts = _opts(clip_start=60, clip_end=120)
        assert _secoes(opts) == [{"start_time": 60, "end_time": 120}]

    def test_trecho_curto_deve_ter_corte_preciso(self):
        opts = _opts(clip_start=60, clip_end=60 + PRECISE_CUT_SECONDS)
        assert opts["force_keyframes_at_cuts"] is True

    def test_trecho_longo_nao_deve_reencodar(self):
        opts = _opts(clip_start=0, clip_end=PRECISE_CUT_SECONDS + 1)
        assert "force_keyframes_at_cuts" not in opts

    def test_inicio_sem_fim_deve_ir_ate_o_final_sem_reencodar(self):
        opts = _opts(clip_start=60)
        assert _secoes(opts)[0]["start_time"] == 60
        assert _secoes(opts)[0]["end_time"] == float("inf")
        assert "force_keyframes_at_cuts" not in opts

    def test_fim_sem_inicio_deve_comecar_do_zero(self):
        opts = _opts(clip_end=30)
        assert _secoes(opts) == [{"start_time": 0, "end_time": 30}]
        assert opts["force_keyframes_at_cuts"] is True

    def test_deve_valer_tambem_para_mp3(self):
        opts = _opts(file_format="mp3", clip_start=60, clip_end=120)
        assert _secoes(opts) == [{"start_time": 60, "end_time": 120}]

    def test_deve_dar_ao_trecho_um_nome_que_nao_colide_com_o_arquivo_inteiro(self):
        inteiro = _opts()["outtmpl"]["default"]
        trecho = _opts(clip_start=60, clip_end=120)["outtmpl"]["default"]
        assert trecho != inteiro
        assert trecho.endswith("%(title)s (trecho 1m00s-2m00s).%(ext)s")

    def test_nao_deve_cortar_itens_de_playlist(self):
        opts = _opts(playlist=True, clip_start=60, clip_end=120)
        assert "download_ranges" not in opts
        assert "trecho" not in opts["outtmpl"]["default"]


class TestNomeDoMp3DoTrecho:
    def test_mp3_do_trecho_deve_manter_o_sufixo_ao_ganhar_o_nome_da_musica(self, tmp_path):
        inteiro = tmp_path / "Every Breath You Take.mp3"
        inteiro.write_bytes(b"inteiro")
        baixado = tmp_path / "The Police - Every Breath You Take (trecho 1m00s-2m00s).mp3"
        baixado.write_bytes(b"trecho")

        finais = DownloadManager(queue.Queue())._rename_downloads(
            DownloadSummary(),
            [("The Police - Every Breath You Take (Official Video)", baixado)],
            clip_label(60, 120),
        )

        assert finais[0][1].name == "Every Breath You Take (trecho 1m00s-2m00s).mp3"
        assert inteiro.read_bytes() == b"inteiro"
