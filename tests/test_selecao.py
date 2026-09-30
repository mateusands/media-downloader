"""
SDD — Especificação: escolha dos itens da playlist antes de baixar

CONTRATO
  `PlaylistChoice` guarda o que está marcado no diálogo de escolha, sem conhecer
  widget: marcação inicial, marcar todos, desmarcar todos, só os novos, alternar
  um item, e o texto do botão que confirma.

POR QUE EXISTE
  Antes a playlist era tudo ou nada: para pegar três faixas de uma playlist de
  cem, baixava as cem. E rodar de novo uma playlist que ganhou duas faixas
  baixava todas outra vez. A escolha resolve os dois, e a marcação inicial é o
  que faz o caso comum ("só o que é novo") custar um clique.

REGRA DE NEGÓCIO
  - Começa marcado tudo que ainda não foi baixado.
  - Se tudo já foi baixado, começa sem nada marcado e o botão diz isso, em vez
    de fingir que há o que baixar.
  - Confirmar sem nenhum item marcado não é possível.
  - A ordem devolvida é a da playlist, não a dos cliques.
"""

from media_downloader.models import PlaylistEntry
from media_downloader.selection import PlaylistChoice


def _entradas(*baixados):
    return [
        PlaylistEntry(index=n, title=f"Faixa {n}", archive_id=f"youtube {n}",
                      duration=None, already_downloaded=n in baixados)
        for n in range(1, 6)
    ]


class TestMarcacaoInicial:
    def test_deve_marcar_so_o_que_ainda_nao_foi_baixado(self):
        escolha = PlaylistChoice(_entradas(2, 4))
        assert escolha.selected() == [1, 3, 5]

    def test_deve_comecar_vazia_quando_tudo_ja_foi_baixado(self):
        escolha = PlaylistChoice(_entradas(1, 2, 3, 4, 5))
        assert escolha.selected() == []
        assert escolha.can_confirm() is False
        assert escolha.confirm_label() == "Nada novo para baixar"


class TestAcoesDeMarcacao:
    def test_deve_marcar_todos_inclusive_os_ja_baixados(self):
        escolha = PlaylistChoice(_entradas(2))
        escolha.select_all()
        assert escolha.selected() == [1, 2, 3, 4, 5]

    def test_deve_desmarcar_todos(self):
        escolha = PlaylistChoice(_entradas())
        escolha.select_none()
        assert escolha.selected() == []
        assert escolha.confirm_label() == "Escolha ao menos um item"

    def test_deve_voltar_para_so_os_novos(self):
        escolha = PlaylistChoice(_entradas(2))
        escolha.select_all()
        escolha.select_new()
        assert escolha.selected() == [1, 3, 4, 5]

    def test_deve_alternar_um_item_e_devolver_na_ordem_da_playlist(self):
        escolha = PlaylistChoice(_entradas(1, 2, 3, 4, 5))
        escolha.toggle(4)
        escolha.toggle(1)
        assert escolha.selected() == [1, 4]

    def test_deve_contar_no_botao_o_que_sera_baixado(self):
        escolha = PlaylistChoice(_entradas(2))
        assert escolha.can_confirm() is True
        assert escolha.confirm_label() == "Baixar 4 de 5"
