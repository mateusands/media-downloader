"""
SDD — Especificação: prévia do link antes de baixar

CONTRATO
  - `preview_from_info(url, info)` (puro, em `preview.py`) resume o que o
    yt-dlp extraiu num `MediaPreview`: título, canal, duração, se é playlist e
    quantos itens tem, as alturas de vídeo disponíveis e o início sugerido pelo
    `t=` do link.
  - `available_heights(info)` lista as alturas que têm vídeo de fato, da maior
    para a menor.
  - `DownloadManager.probe(url)` roda numa thread, extrai sem baixar, busca a
    miniatura e publica `preview` (com o `MediaPreview`) ou `preview_failed`.
    Os dois carregam a URL: a tela descarta a prévia de um link que a pessoa
    já trocou.

POR QUE EXISTE
  Colar o link e apertar "Baixar" às cegas era descobrir só no fim que o link
  era outro vídeo, ou uma playlist de 200 itens. A prévia (padrão do Seal e do
  Open Video Downloader) mostra o que vai ser baixado; e é dela que o corte de
  trecho tira a duração para a régua, e a escolha de qualidade tira o que
  existe — oferecer 1080p num vídeo de 480p é prometer o que não vem.

REGRA DE NEGÓCIO
  - Prévia que falha não é erro de download: não publica `error`, não trava a
    tela, e o botão "Baixar" continua funcionando (é o yt-dlp quem decide).
  - Miniatura que não carrega não derruba a prévia: ela vem sem imagem.
  - Formato só de áudio (sem `vcodec` ou `vcodec == "none"`) não conta como
    altura disponível.
  - A URL completa não vai para lugar persistente nenhum — só volta na fila.
"""

import queue

from media_downloader.downloader import DownloadManager
from media_downloader.models import MediaPreview
from media_downloader.preview import available_heights, preview_from_info


def _video(**extra):
    return {
        "id": "abc",
        "title": "Paramore - Misery Business",
        "uploader": "Paramore",
        "duration": 211,
        "thumbnail": "https://i.ytimg.com/vi/abc/maxresdefault.jpg",
        "formats": [
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "height": None},
            {"format_id": "137", "vcodec": "avc1", "height": 1080},
            {"format_id": "136", "vcodec": "avc1", "height": 720},
            {"format_id": "398", "vcodec": "av01", "height": 720},
            {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "height": 360},
        ],
        **extra,
    }


class TestResumoDoLink:
    def test_deve_resumir_titulo_canal_e_duracao(self):
        previa = preview_from_info("https://youtu.be/abc", _video())
        assert previa.title == "Paramore - Misery Business"
        assert previa.uploader == "Paramore"
        assert previa.duration == 211
        assert previa.is_playlist is False

    def test_deve_reconhecer_playlist_e_contar_os_itens(self):
        info = {"_type": "playlist", "title": "Rock 2000", "uploader": "Alguem",
                "entries": [{"id": "a"}, None, {"id": "b"}]}
        previa = preview_from_info("https://www.youtube.com/playlist?list=PL", info)
        assert previa.is_playlist is True
        assert previa.item_count == 2
        assert previa.duration is None

    def test_deve_sugerir_o_inicio_do_link_compartilhado(self):
        previa = preview_from_info("https://youtu.be/abc?t=95", _video())
        assert previa.start_hint == 95

    def test_deve_aceitar_link_sem_canal_nem_duracao(self):
        previa = preview_from_info("https://exemplo.com/v", {"title": "Clipe"})
        assert previa.uploader is None
        assert previa.duration is None
        assert previa.heights == ()

    def test_deve_usar_o_canal_quando_nao_ha_uploader(self):
        previa = preview_from_info("https://youtu.be/abc", _video(uploader=None, channel="Canal"))
        assert previa.uploader == "Canal"


class TestAlturasDisponiveis:
    def test_deve_listar_as_alturas_com_video_da_maior_para_a_menor(self):
        assert available_heights(_video()) == (1080, 720, 360)

    def test_formato_so_de_audio_nao_conta(self):
        info = {"formats": [{"vcodec": "none", "height": 1080}]}
        assert available_heights(info) == ()

    def test_sem_formatos_nao_deve_inventar_altura(self):
        assert available_heights({}) == ()


class _YoutubeDLFalso:
    info: dict | None = None
    erro: Exception | None = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if _YoutubeDLFalso.erro:
            raise _YoutubeDLFalso.erro
        return _YoutubeDLFalso.info


def _eventos(fila):
    eventos = []
    while not fila.empty():
        eventos.append(fila.get_nowait())
    return eventos


class TestConsultaDaPrevia:
    def _manager(self, monkeypatch, info=None, erro=None, miniatura=b"jpg"):
        _YoutubeDLFalso.info = info
        _YoutubeDLFalso.erro = erro
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _YoutubeDLFalso)

        def baixar_miniatura(_url):
            if isinstance(miniatura, Exception):
                raise miniatura
            return miniatura

        monkeypatch.setattr("media_downloader.downloader.fetch_thumbnail", baixar_miniatura)
        fila = queue.Queue()
        return DownloadManager(fila), fila

    def test_deve_publicar_a_previa_com_a_miniatura(self, monkeypatch):
        manager, fila = self._manager(monkeypatch, info=_video())

        manager.probe("https://youtu.be/abc")

        evento, = _eventos(fila)
        assert evento["type"] == "preview"
        assert evento["url"] == "https://youtu.be/abc"
        assert isinstance(evento["preview"], MediaPreview)
        assert evento["preview"].thumbnail == b"jpg"

    def test_miniatura_que_falha_nao_deve_derrubar_a_previa(self, monkeypatch):
        manager, fila = self._manager(monkeypatch, info=_video(), miniatura=OSError("timeout"))

        manager.probe("https://youtu.be/abc")

        evento, = _eventos(fila)
        assert evento["type"] == "preview"
        assert evento["preview"].thumbnail is None

    def test_falha_na_extracao_deve_avisar_sem_virar_erro_de_download(self, monkeypatch):
        manager, fila = self._manager(monkeypatch, erro=RuntimeError("Video unavailable"))

        manager.probe("https://youtu.be/abc")

        evento, = _eventos(fila)
        assert evento["type"] == "preview_failed"
        assert evento["url"] == "https://youtu.be/abc"
        assert "Video unavailable" in evento["message"]

    def test_extracao_vazia_deve_avisar_sem_virar_erro(self, monkeypatch):
        manager, fila = self._manager(monkeypatch, info=None)

        manager.probe("https://youtu.be/abc")

        assert [e["type"] for e in _eventos(fila)] == ["preview_failed"]

    def test_deve_listar_playlist_sem_resolver_cada_item(self, monkeypatch):
        manager, _ = self._manager(monkeypatch, info=_video())
        opcoes = []
        original = _YoutubeDLFalso.__init__
        monkeypatch.setattr(
            _YoutubeDLFalso, "__init__",
            lambda self, opts: (opcoes.append(opts), original(self, opts))[1])

        manager.probe("https://www.youtube.com/playlist?list=PLx")

        assert opcoes[0]["extract_flat"] == "in_playlist"
        assert opcoes[0]["noplaylist"] is False
