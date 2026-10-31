"""
SDD — Especificação: velocidade e tempo restante no progresso

CONTRATO
  O evento `progress` que o gancho de progresso publica leva, além da
  porcentagem e da mensagem, `speed` (bytes/s) e `eta` (segundos) quando o
  yt-dlp os informa, e `None` quando não informa. `transfer_text(speed, eta)`
  transforma os dois no texto da barra ("4.2 MB/s · faltam 0:12").

POR QUE EXISTE
  Uma barra parada em 37% não diz se o download travou ou se a conexão está
  lenta. A velocidade e o tempo restante respondem isso — é o que todo app do
  mesmo tipo mostra ao lado da barra.

REGRA DE NEGÓCIO
  - O que o yt-dlp não informa não é inventado: sem velocidade, não há texto.
  - Corte de trecho via FFmpeg não informa total: a porcentagem fica em 0 e a
    velocidade ainda aparece se vier.
"""

import queue

from media_downloader.downloader import DownloadManager, transfer_text
from media_downloader.models import DownloadSummary


def _publicado(dados):
    fila = queue.Queue()
    gancho = DownloadManager(fila)._make_progress_hook(DownloadSummary(total_items=1))
    gancho(dados)
    return fila.get_nowait()


class TestVelocidadeNoProgresso:
    def test_deve_publicar_velocidade_e_tempo_restante(self):
        evento = _publicado({"status": "downloading", "downloaded_bytes": 50,
                             "total_bytes": 100, "speed": 2_000_000.0, "eta": 12,
                             "info_dict": {"title": "Clipe"}})
        assert evento["speed"] == 2_000_000.0
        assert evento["eta"] == 12
        assert evento["progress"] == 50

    def test_nao_deve_inventar_o_que_o_yt_dlp_nao_informou(self):
        evento = _publicado({"status": "downloading", "downloaded_bytes": 50,
                             "info_dict": {"title": "Clipe"}})
        assert evento["speed"] is None
        assert evento["eta"] is None


class TestTextoDaTransferencia:
    def test_deve_juntar_velocidade_e_tempo_restante(self):
        assert transfer_text(4.2 * 1024 * 1024, 12) == "4.2 MB/s · faltam 0:12"

    def test_deve_usar_kb_para_conexao_lenta(self):
        assert transfer_text(512_000, None) == "500 KB/s"

    def test_sem_velocidade_nao_deve_ter_texto(self):
        assert transfer_text(None, 30) == ""
