"""
SDD — Especificação: acionamento dos controles principais

CONTRATO
  Um botão visual do Media Downloader deve preservar o mecanismo de clique do
  CustomTkinter: ao liberar o mouse dentro de um botão habilitado, o comando
  associado precisa ser executado.

POR QUE EXISTE
  `HoverButton` adicionava feedback visual sobrescrevendo `_on_release`, um
  método interno de `CTkButton` responsável por chamar `command`. O resultado
  era uma tela aparentemente normal em que "Baixar mídia" e "Abrir pasta" não
  faziam nada — inclusive a pasta Downloads não era criada.

REGRA DE NEGÓCIO
  Estilo visual adicional não pode impedir a ação solicitada pelo usuário.

---

SDD — Especificação: encerramento da revisão de metadata

CONTRATO
  `MetadataReview.resolve(item)` tira da tela a pendência que acabou de ser
  importada. Sem nenhuma pendência restante, a janela de revisão fecha sozinha.
  Quem é dono desse estado é `review.py`, não a janela principal: os diálogos
  abrem e fecham sem ela saber.

POR QUE EXISTE
  Depois de escolher um resultado do catálogo, a janela de candidatos fechava
  mas a de revisão continuava aberta, oferecendo "Buscar metadata" para um
  arquivo já resolvido. O usuário tinha que fechar a janela na mão para
  descobrir que não faltava mais nada.

REGRA DE NEGÓCIO
  - Só a importação concluída (`metadata_applied`) resolve a linha; erro de
    importação a mantém, senão a pendência some sem ter sido atendida.
  - Em playlist, resolver um item não pode fechar a revisão dos outros.
  - A imagem da capa sai junto com a linha: guardá-la vaza memória por item
    que não está mais em cena.

---

SDD — Especificação: colar link sobre uma seleção

CONTRATO
  `_paste_over_selection()` atende ao `<<Paste>>` do campo de link: apaga o
  trecho selecionado antes de inserir, e devolve "break".

POR QUE EXISTE
  O binding de classe do Tk no X11 insere no ponto de inserção sem apagar a
  seleção. Selecionar a URL inteira e apertar Ctrl+V colava o novo link
  grudado no antigo (`.../AAAhttps://.../BBB`) em vez de substituí-lo.
  O "break" é parte do contrato: sem ele o binding de classe roda em seguida
  e insere o texto uma segunda vez.

REGRA DE NEGÓCIO
  - Sem seleção, colar insere no cursor, como em qualquer campo de texto.
  - Área de transferência vazia ou não textual deixa o campo intacto.
  - O botão "Colar" continua substituindo o campo inteiro: ele é atalho para
    "põe o link aqui", não uma edição pontual.
"""

from media_downloader import window
from media_downloader.models import MetadataPendingItem
from media_downloader.review import MetadataReview
from media_downloader.widgets import HoverButton
from media_downloader.window import MediaDownloaderApp


class TestAcionamentoDosControles:
    def test_deve_executar_o_comando_quando_o_botao_e_liberado_dentro_da_area(self):
        chamadas = []
        botao = object.__new__(HoverButton)
        botao._mouse_inside = True
        botao._state = "normal"
        botao._command = lambda: chamadas.append("executado")
        botao._on_leave = lambda: None
        botao._click_animation = lambda: None
        botao.after = lambda _ms, _callback: None

        botao._on_release()

        assert chamadas == ["executado"]


class TestAberturaDaPastaDeDownloads:
    def test_deve_criar_e_abrir_a_pasta_com_xdg_open_no_linux(self, tmp_path, monkeypatch):
        chamadas = []
        aplicativo = object.__new__(MediaDownloaderApp)
        destino = tmp_path / "Downloads"
        aplicativo._downloads_dir = destino
        monkeypatch.setattr(window.sys, "platform", "linux")
        monkeypatch.setattr(window.subprocess, "Popen", lambda argumentos: chamadas.append(argumentos))

        aplicativo.open_downloads_folder()

        assert destino.is_dir()
        assert chamadas == [["xdg-open", str(destino)]]


class TestEncerramentoDaRevisaoDeMetadata:
    """A revisão existe para resolver pendências; item resolvido não fica em cena."""

    class _WidgetFalso:
        def __init__(self):
            self.destruido = False

        def winfo_exists(self):
            return not self.destruido

        def destroy(self):
            self.destruido = True

    def _revisao(self, pendentes):
        aplicativo = object.__new__(MetadataReview)
        aplicativo._metadata_review_rows = {
            item: (self._WidgetFalso(), self._WidgetFalso(), self._WidgetFalso(),
                   self._WidgetFalso())
            for item in pendentes
        }
        aplicativo._metadata_embedded_images = {item: object() for item in pendentes}
        aplicativo._metadata_review_window = self._WidgetFalso()
        return aplicativo

    def test_deve_fechar_a_revisao_quando_a_ultima_pendencia_foi_importada(self):
        item = MetadataPendingItem("Paramore - All I Wanted", "/tmp/faixa.mp3", ("artista provisorio: canal Paramore",))
        aplicativo = self._revisao([item])
        janela = aplicativo._metadata_review_window

        aplicativo.resolve(item)

        assert janela.destruido is True
        assert aplicativo._metadata_review_rows == {}
        assert aplicativo._metadata_embedded_images == {}

    def test_deve_manter_a_revisao_aberta_enquanto_houver_outra_pendencia(self):
        importado = MetadataPendingItem("Faixa A", "/tmp/a.mp3", ("artista ausente",))
        restante = MetadataPendingItem("Faixa B", "/tmp/b.mp3", ("artista ausente",))
        aplicativo = self._revisao([importado, restante])
        janela = aplicativo._metadata_review_window
        linha_importada = aplicativo._metadata_review_rows[importado][0]

        aplicativo.resolve(importado)

        assert linha_importada.destruido is True
        assert janela.destruido is False
        assert list(aplicativo._metadata_review_rows) == [restante]

    def test_deve_ignorar_item_que_nao_esta_na_revisao(self):
        item = MetadataPendingItem("Faixa A", "/tmp/a.mp3", ("artista ausente",))
        aplicativo = self._revisao([item])
        aplicativo._metadata_review_window = None

        aplicativo.resolve(
            MetadataPendingItem("Outra", "/tmp/outra.mp3", ("artista ausente",)))

        assert list(aplicativo._metadata_review_rows) == [item]


class TestColarNoCampoDeLink:
    """Colar sobre uma selecao troca o trecho — o Tk no X11 nao faz isso sozinho."""

    class _EntradaFalsa:
        def __init__(self, texto="", selecao=None):
            self.texto = texto
            self.selecao = selecao
            self.cursor = len(texto)

        def select_present(self):
            return self.selecao is not None

        def delete(self, _primeiro, _ultimo=None):
            inicio, fim = self.selecao
            self.texto = self.texto[:inicio] + self.texto[fim:]
            self.cursor = inicio
            self.selecao = None

        def insert(self, _indice, texto):
            self.texto = self.texto[:self.cursor] + texto + self.texto[self.cursor:]
            self.cursor += len(texto)

    class _RaizFalsa:
        def __init__(self, area_de_transferencia):
            self._conteudo = area_de_transferencia

        def clipboard_get(self):
            if self._conteudo is None:
                raise window.tk.TclError("selection doesn't exist")
            return self._conteudo

    def _aplicativo(self, entrada, area_de_transferencia):
        aplicativo = object.__new__(MediaDownloaderApp)
        aplicativo.url_entry = entrada
        aplicativo.root = self._RaizFalsa(area_de_transferencia)
        return aplicativo

    def test_deve_substituir_o_trecho_selecionado_pelo_link_colado(self):
        entrada = self._EntradaFalsa("https://youtu.be/AAA", selecao=(0, 20))
        aplicativo = self._aplicativo(entrada, "https://youtu.be/BBB")

        resultado = aplicativo._paste_over_selection()

        assert entrada.texto == "https://youtu.be/BBB"
        assert resultado == "break"

    def test_deve_inserir_no_cursor_quando_nao_ha_selecao(self):
        entrada = self._EntradaFalsa("https://youtu.be/", selecao=None)
        aplicativo = self._aplicativo(entrada, "AAA")

        aplicativo._paste_over_selection()

        assert entrada.texto == "https://youtu.be/AAA"

    def test_deve_manter_o_campo_intacto_quando_a_area_de_transferencia_esta_vazia(self):
        entrada = self._EntradaFalsa("https://youtu.be/AAA", selecao=(0, 20))
        aplicativo = self._aplicativo(entrada, None)

        aplicativo._paste_over_selection()

        assert entrada.texto == "https://youtu.be/AAA"


class TestFilaDeBuscaDeMetadata:
    """Em playlist a pessoa clica em vários; recusar o segundo clique é ruim."""

    class _BotaoFalso:
        def __init__(self):
            self.texto = "Buscar metadata"
            self.estado = "normal"
            self.destruido = False

        def winfo_exists(self):
            return not self.destruido

        def configure(self, **kwargs):
            self.texto = kwargs.get("text", self.texto)
            self.estado = kwargs.get("state", self.estado)

        def destroy(self):
            self.destruido = True

    def _revisao(self, pendentes):
        from collections import deque
        from types import SimpleNamespace

        revisao = object.__new__(MetadataReview)
        # O alvo nunca e chamado: `_spawn` esta trocado. So precisa existir,
        # como existiria o gerente de verdade.
        revisao._manager = SimpleNamespace(search_metadata="alvo")
        revisao._metadata_review_rows = {
            item: (None, None, None, self._BotaoFalso()) for item in pendentes
        }
        revisao._search_queue = deque()
        revisao._searching = None
        revisao.iniciadas = []
        revisao._spawn = lambda alvo, *args: revisao.iniciadas.append(args[0])
        return revisao

    def _itens(self, quantidade):
        return [
            MetadataPendingItem(f"Faixa {n}", f"/tmp/{n}.mp3", ("artista ausente",))
            for n in range(quantidade)
        ]

    def _botao(self, revisao, item):
        return revisao._metadata_review_rows[item][3]

    def test_deve_iniciar_a_primeira_busca_de_imediato(self):
        um, = self._itens(1)
        revisao = self._revisao([um])

        revisao.start_search(um)

        assert revisao.iniciadas == [um]
        assert self._botao(revisao, um).estado == "disabled"

    def test_deve_enfileirar_o_segundo_clique_em_vez_de_recusar(self):
        um, dois = self._itens(2)
        revisao = self._revisao([um, dois])

        revisao.start_search(um)
        revisao.start_search(dois)

        assert revisao.iniciadas == [um], "só uma busca corre por vez"
        assert "fila" in self._botao(revisao, dois).texto.lower()

    def test_deve_iniciar_a_proxima_quando_a_anterior_termina(self):
        um, dois = self._itens(2)
        revisao = self._revisao([um, dois])
        revisao.start_search(um)
        revisao.start_search(dois)

        revisao.search_finished(um)

        assert revisao.iniciadas == [um, dois]
        assert self._botao(revisao, um).estado == "normal"

    def test_deve_ignorar_clique_repetido_no_mesmo_item(self):
        um, = self._itens(1)
        revisao = self._revisao([um])

        revisao.start_search(um)
        revisao.start_search(um)

        assert revisao.iniciadas == [um]

    def test_deve_seguir_a_fila_quando_a_busca_anterior_falha(self):
        um, dois = self._itens(2)
        revisao = self._revisao([um, dois])
        revisao.start_search(um)
        revisao.start_search(dois)

        revisao.search_finished(um)   # o erro chega pelo mesmo caminho

        assert revisao.iniciadas == [um, dois]
