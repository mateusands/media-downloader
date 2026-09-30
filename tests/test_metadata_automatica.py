"""
SDD — Especificação: metadata aplicada sozinha quando a correspondência é segura

CONTRATO
  - `confident_match(item, candidatos)` devolve o candidato do catálogo que
    corresponde com segurança ao MP3 baixado, ou `None`.
  - `search_suggestion(item)` monta a busca a partir do título e, quando o
    título não traz artista, do artista que a própria origem publicou.
  - Com a aplicação automática ligada, `DownloadManager` busca e aplica ao fim
    do download o candidato seguro de cada pendência; só o que sobra vai para a
    revisão. O resumo conta o que foi aplicado sozinho.

POR QUE EXISTE
  Numa playlist, cada MP3 pendente exigia abrir a busca e escolher entre cinco
  resultados — mesmo quando o primeiro era obviamente a faixa certa. A pessoa
  passou a pedir que o caso óbvio se resolvesse sozinho e só o ambíguo chegasse
  até ela, como fazem beets e MusicBrainz Picard.

REGRA DE NEGÓCIO
  - "Seguro" exige as três coisas: artista, título e duração (até 5 s de
    diferença — o clipe oficial costuma ter alguns segundos a mais que a faixa
    do álbum). Duas de três é caso ambíguo e vai para a revisão.
  - Versão diferente nunca é a mesma faixa: "(Live at Wembley)", remix,
    acústica. O sufixo que se ignora é só o que não muda o áudio (remaster,
    participação especial).
  - Sem artista conhecido, nunca é seguro: título sozinho casa com regravação,
    cover e homônimo.
  - Diferença de caixa, acento, "The" no começo, "feat." e sufixo entre
    parênteses ("Remastered 2011") não desfaz a correspondência.
  - A aplicação automática é escolha explícita da pessoa, por download. Desligada,
    nada é gravado sem confirmação, como antes.
  - Falha do catálogo em um item não derruba os outros: o item fica pendente.
  - Cancelar interrompe a busca automática; o que sobrou continua pendente.
"""

import queue

from media_downloader.downloader import DownloadManager, summary_lines
from media_downloader.metadata import confident_match, search_suggestion
from media_downloader.models import (
    DownloadSummary,
    MetadataPendingItem,
    MusicMetadataCandidate,
    MusicSearchSuggestion,
)


def _item(title="Queen - Bohemian Rhapsody", duration=354.0, source_artist=None):
    return MetadataPendingItem(
        title=title, file_path=f"/tmp/{title}.mp3",
        review_reasons=("artista ausente",), duration=duration,
        source_artist=source_artist,
    )


def _candidato(title="Bohemian Rhapsody", artist="Queen", duration=355.0, track_id="1"):
    return MusicMetadataCandidate(
        track_id=track_id, title=title, artist=artist, album="A Night at the Opera",
        year="1975", artwork_url=None, duration_seconds=duration,
    )


class TestCorrespondenciaSegura:
    def test_deve_aceitar_quando_artista_titulo_e_duracao_batem(self):
        candidato = _candidato()
        assert confident_match(_item(), [candidato]) == candidato

    def test_deve_recusar_quando_a_duracao_difere_mais_de_cinco_segundos(self):
        assert confident_match(_item(duration=354.0), [_candidato(duration=360.0)]) is None

    def test_deve_aceitar_os_segundos_a_mais_do_clipe_oficial(self):
        # Caso real: o clipe tem 359 s e a faixa do álbum 355,155 s.
        candidato = _candidato(duration=355.155)
        assert confident_match(_item(duration=359.0), [candidato]) == candidato

    def test_deve_recusar_a_versao_ao_vivo_mesmo_com_duracao_parecida(self):
        ao_vivo = _candidato(title="Bohemian Rhapsody (Live At Wembley Stadium / July 1986)")
        assert confident_match(_item(), [ao_vivo]) is None

    def test_deve_recusar_remix_e_versao_acustica(self):
        for titulo in ("Bohemian Rhapsody (Remix)", "Bohemian Rhapsody - Acoustic Version",
                       "Bohemian Rhapsody (ACAPELLA)", "Bohemian Rhapsody (A Capella)"):
            assert confident_match(_item(), [_candidato(title=titulo)]) is None, titulo

    def test_deve_recusar_quando_o_artista_e_outro(self):
        assert confident_match(_item(), [_candidato(artist="Panic! at the Disco")]) is None

    def test_deve_recusar_quando_o_titulo_e_outro(self):
        assert confident_match(_item(), [_candidato(title="Somebody to Love")]) is None

    def test_deve_recusar_quando_nao_se_sabe_o_artista(self):
        assert confident_match(_item(title="Bohemian Rhapsody"), [_candidato()]) is None

    def test_deve_recusar_quando_falta_a_duracao_de_um_dos_lados(self):
        assert confident_match(_item(duration=None), [_candidato()]) is None
        assert confident_match(_item(), [_candidato(duration=None)]) is None

    def test_deve_ignorar_caixa_acento_the_e_sufixo_entre_parenteses(self):
        item = _item(title="the beatles - Hey Jude", duration=431.0)
        candidato = _candidato(
            title="Hey Jude (Remastered 2015)", artist="The Beatles", duration=429.0)
        assert confident_match(item, [candidato]) == candidato

    def test_deve_ignorar_participacao_especial_no_titulo(self):
        item = _item(title="Mark Ronson - Uptown Funk ft. Bruno Mars", duration=270.0)
        candidato = _candidato(
            title="Uptown Funk (feat. Bruno Mars)", artist="Mark Ronson", duration=270.0)
        assert confident_match(item, [candidato]) == candidato

    def test_deve_aceitar_o_artista_principal_de_um_credito_conjunto(self):
        item = _item(title="Queen - Under Pressure", duration=248.0)
        candidato = _candidato(
            title="Under Pressure", artist="Queen & David Bowie", duration=248.0)
        assert confident_match(item, [candidato]) == candidato

    def test_deve_escolher_o_primeiro_seguro_na_ordem_do_catalogo(self):
        ao_vivo = _candidato(duration=380.0, track_id="ao-vivo")
        estudio = _candidato(track_id="estudio")
        coletanea = _candidato(track_id="coletanea")
        assert confident_match(_item(), [ao_vivo, estudio, coletanea]) == estudio

    def test_deve_usar_o_artista_publicado_pela_origem(self):
        item = _item(title="Bohemian Rhapsody", source_artist="Queen")
        candidato = _candidato()
        assert confident_match(item, [candidato]) == candidato


class TestSugestaoDeBuscaDaPendencia:
    def test_deve_reconhecer_o_travessao_como_separador_de_artista(self):
        # Caso real: o clipe oficial do Queen usa "–", não "-".
        for titulo in ("Queen – Bohemian Rhapsody", "Queen — Bohemian Rhapsody"):
            assert search_suggestion(_item(title=titulo)) == MusicSearchSuggestion(
                title="Bohemian Rhapsody", artist="Queen"), titulo

    def test_deve_usar_o_artista_do_titulo(self):
        assert search_suggestion(_item()) == MusicSearchSuggestion(
            title="Bohemian Rhapsody", artist="Queen")

    def test_deve_completar_com_o_artista_da_origem_quando_o_titulo_nao_traz(self):
        item = _item(title="Bohemian Rhapsody", source_artist="Queen")
        assert search_suggestion(item) == MusicSearchSuggestion(
            title="Bohemian Rhapsody", artist="Queen")

    def test_deve_manter_so_o_titulo_quando_ninguem_informa_artista(self):
        assert search_suggestion(_item(title="Bohemian Rhapsody")) == MusicSearchSuggestion(
            title="Bohemian Rhapsody")


class TestPendenciaGuardaOQueACorrespondenciaPrecisa:
    def test_deve_guardar_duracao_e_artista_da_origem(self):
        resumo = DownloadSummary(total_items=1)
        hook = DownloadManager(queue.Queue())._make_progress_hook(resumo, include_metadata=True)

        hook({
            "status": "finished",
            "filename": "/tmp/Bohemian Rhapsody.webm",
            "info_dict": {
                "id": "q1", "title": "Bohemian Rhapsody", "duration": 354,
                "artists": ["Queen"], "thumbnail": "https://exemplo.com/capa.jpg",
                "thumbnails": [{"url": "x", "width": 1280, "height": 720}],
            },
        })

        pendencia = resumo.metadata_pending_items[0]
        assert pendencia.duration == 354
        assert pendencia.source_artist == "Queen"

    def test_candidato_deve_trazer_a_duracao_do_catalogo(self):
        candidato = MusicMetadataCandidate.from_itunes({
            "trackId": 1, "trackName": "Bohemian Rhapsody", "artistName": "Queen",
            "trackTimeMillis": 354320,
        })
        assert candidato.duration_seconds == 354.32


class _CatalogoFalso:
    def __init__(self, resultados, falha_em=()):
        self._resultados = resultados
        self._falha_em = falha_em
        self.aplicados = []

    def search(self, suggestion):
        if suggestion.title in self._falha_em:
            raise OSError("catalogo fora do ar")
        return self._resultados.get(suggestion.title, [])

    def apply_to_mp3(self, path, candidate):
        self.aplicados.append((str(path), candidate))
        return True


class TestAplicacaoAutomaticaAoFimDoDownload:
    def _resumo(self, *itens):
        return DownloadSummary(
            downloaded_count=len(itens), total_items=len(itens),
            metadata_pending_items=list(itens))

    def test_deve_aplicar_o_seguro_e_deixar_o_ambiguo_para_a_revisao(self):
        seguro = _item()
        ambiguo = _item(title="Queen - Somebody to Love", duration=296.0)
        catalogo = _CatalogoFalso({
            "Bohemian Rhapsody": [_candidato()],
            "Somebody to Love": [_candidato(title="Somebody to Love", duration=330.0)],
        })
        resumo = self._resumo(seguro, ambiguo)

        DownloadManager(queue.Queue(), catalogo)._auto_apply_metadata(resumo)

        assert [c.title for _, c in catalogo.aplicados] == ["Bohemian Rhapsody"]
        assert resumo.metadata_pending_items == [ambiguo]
        assert resumo.metadata_auto_applied == ["Queen — Bohemian Rhapsody"]

    def test_deve_manter_pendente_o_item_cuja_busca_falhou_e_seguir(self):
        falha = _item(title="Queen - Somebody to Love", duration=296.0)
        seguro = _item()
        catalogo = _CatalogoFalso(
            {"Bohemian Rhapsody": [_candidato()]}, falha_em=("Somebody to Love",))
        resumo = self._resumo(falha, seguro)

        DownloadManager(queue.Queue(), catalogo)._auto_apply_metadata(resumo)

        assert resumo.metadata_pending_items == [falha]
        assert resumo.failed_items == []

    def test_deve_parar_quando_o_download_foi_cancelado(self):
        catalogo = _CatalogoFalso({"Bohemian Rhapsody": [_candidato()]})
        resumo = self._resumo(_item())
        manager = DownloadManager(queue.Queue(), catalogo)
        manager.cancel()

        manager._auto_apply_metadata(resumo)

        assert catalogo.aplicados == []
        assert len(resumo.metadata_pending_items) == 1

    def test_deve_contar_no_resumo_o_que_foi_aplicado_sozinho(self):
        linhas = summary_lines(DownloadSummary(
            downloaded_count=2, metadata_auto_applied=["Queen — Bohemian Rhapsody"]))
        assert "OK  1 MP3 com metadata aplicada automaticamente" in linhas

    def test_nao_deve_falar_de_aplicacao_automatica_quando_nada_foi_aplicado(self):
        linhas = summary_lines(DownloadSummary(downloaded_count=2))
        assert not any("automaticamente" in linha for linha in linhas)


class TestEscolhaDaPessoa:
    """Desligada a aplicação automática, nada é gravado sem confirmação."""

    def _manager(self, monkeypatch, tmp_path, catalogo):
        class _YoutubeDLFalso:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def extract_info(self, url, download=False):
                return {"id": "q1", "title": "Queen - Bohemian Rhapsody"}

            def download(self, urls):
                for hook in self.opts["progress_hooks"]:
                    hook({"status": "finished", "filename": "/tmp/q.webm",
                          "info_dict": {"id": "q1", "title": "Queen - Bohemian Rhapsody",
                                        "duration": 354}})

        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _YoutubeDLFalso)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp3", False): tmp_path})
        return DownloadManager(queue.Queue(), catalogo)

    def test_nao_deve_gravar_nada_quando_a_pessoa_nao_pediu(self, monkeypatch, tmp_path):
        catalogo = _CatalogoFalso({"Bohemian Rhapsody": [_candidato()]})
        manager = self._manager(monkeypatch, tmp_path, catalogo)

        manager.download("https://www.youtube.com/watch?v=q1", "mp3",
                         include_metadata=True, auto_metadata=False)

        assert catalogo.aplicados == []

    def test_deve_aplicar_quando_a_pessoa_pediu(self, monkeypatch, tmp_path):
        catalogo = _CatalogoFalso({"Bohemian Rhapsody": [_candidato()]})
        manager = self._manager(monkeypatch, tmp_path, catalogo)

        manager.download("https://www.youtube.com/watch?v=q1", "mp3",
                         include_metadata=True, auto_metadata=True)

        assert len(catalogo.aplicados) == 1
