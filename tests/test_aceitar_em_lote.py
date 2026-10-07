"""
SDD — Especificação: aplicar a sugestão do catálogo em todos os itens da revisão

CONTRATO
  - `suggested_match(item, candidatos)` devolve o candidato que o app sugere:
    o de `confident_match` quando existe; senão, o primeiro do catálogo em que
    artista **e** título batem, sem olhar a duração. Sem isso, `None`.
  - `DownloadManager.apply_suggested(itens)` busca e aplica a sugestão de cada
    item, publicando `metadata_bulk_applied` por item resolvido,
    `metadata_bulk_progress` a cada passo e `metadata_bulk_done` no fim.

POR QUE EXISTE
  Uma playlist de 136 faixas terminou com 75 itens na revisão — quase todos com
  artista e título certos e só a capa provisória do vídeo. Confirmar um a um
  (buscar, esperar, importar) levava a tarde inteira. A pessoa pediu um botão
  para aceitar o padrão do app de uma vez.

REGRA DE NEGÓCIO
  - O botão é a autorização: o `confident_match` (aplicação sem perguntar)
    continua exigindo os três critérios. O critério relaxado só existe aqui,
    porque a pessoa viu a lista e clicou.
  - Relaxa só a duração — é ela que separa clipe (com abertura) de faixa.
    Artista e título continuam obrigatórios, e versão diferente (ao vivo,
    remix, acústica) nunca casa com a de estúdio.
  - Sem artista conhecido, nada é aplicado: título sozinho casa com cover.
  - O que não tem sugestão fica na revisão; falha do catálogo num item não
    para os outros. Parar interrompe entre um item e outro.
"""

import queue

from media_downloader.downloader import DownloadManager
from media_downloader.metadata import suggested_match
from media_downloader.models import MetadataPendingItem, MusicMetadataCandidate


def _item(title="Feel Good Drag", artist="Anberlin", duration=230.0):
    return MetadataPendingItem(
        title=title, file_path=f"/tmp/{title}.mp3",
        review_reasons=("capa provisoria: miniatura do video",),
        duration=duration, source_artist=artist,
    )


def _candidato(title="Feel Good Drag", artist="Anberlin", duration=201.0, track_id="1"):
    return MusicMetadataCandidate(
        track_id=track_id, title=title, artist=artist, album="New Surrender",
        year="2008", artwork_url=None, duration_seconds=duration,
    )


class TestSugestaoDoApp:
    def test_deve_aceitar_artista_e_titulo_mesmo_com_duracao_diferente(self):
        candidato = _candidato(duration=201.0)
        assert suggested_match(_item(duration=230.0), [candidato]) == candidato

    def test_deve_preferir_o_que_tambem_bate_a_duracao(self):
        longe = _candidato(duration=260.0, track_id="longe")
        perto = _candidato(duration=231.0, track_id="perto")
        assert suggested_match(_item(), [longe, perto]) == perto

    def test_nao_deve_trocar_estudio_por_ao_vivo(self):
        assert suggested_match(_item(), [_candidato(title="Feel Good Drag (Live)")]) is None

    def test_nao_deve_aplicar_sem_artista_conhecido(self):
        assert suggested_match(_item(artist=None), [_candidato()]) is None

    def test_nao_deve_aplicar_quando_o_artista_e_outro(self):
        assert suggested_match(_item(), [_candidato(artist="Outra Banda")]) is None


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
        self.aplicados.append(candidate.title)
        return True


def _eventos(fila):
    eventos = []
    while not fila.empty():
        eventos.append(fila.get_nowait())
    return eventos


class TestAplicarEmLote:
    def test_deve_aplicar_o_que_tem_sugestao_e_manter_o_resto(self):
        com = _item()
        sem = _item(title="Re-Education", artist="Rise Against")
        falha = _item(title="Falling On", artist="Finger Eleven")
        catalogo = _CatalogoFalso(
            {"Feel Good Drag": [_candidato()]}, falha_em=("Falling On",))
        fila = queue.Queue()

        DownloadManager(fila, catalogo).apply_suggested([com, sem, falha])

        eventos = _eventos(fila)
        assert catalogo.aplicados == ["Feel Good Drag"]
        assert [e["pending_item"] for e in eventos
                if e["type"] == "metadata_bulk_applied"] == [com]
        fim = eventos[-1]
        assert fim["type"] == "metadata_bulk_done"
        assert (fim["applied"], fim["kept"]) == (1, 2)

    def test_deve_parar_entre_um_item_e_outro_quando_pedido(self):
        catalogo = _CatalogoFalso({"Feel Good Drag": [_candidato()]})
        fila = queue.Queue()
        manager = DownloadManager(fila, catalogo)
        busca_original = catalogo.search

        def buscar_e_pedir_para_parar(suggestion):
            manager.stop_bulk()
            return busca_original(suggestion)

        catalogo.search = buscar_e_pedir_para_parar

        manager.apply_suggested([_item(), _item(title="Outra")])

        assert catalogo.aplicados == ["Feel Good Drag"]
        assert _eventos(fila)[-1]["kept"] == 1
