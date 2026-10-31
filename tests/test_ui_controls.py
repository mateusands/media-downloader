"""
SDD — Especificação: acionamento dos controles principais

CONTRATO
  Os controles da janela principal disparam a ação que anunciam: "Baixar"
  chama `start_download`, "Abrir" cria e abre a pasta de destino, e o campo de
  link aceita colar sobre uma seleção trocando o trecho.

POR QUE EXISTE
  No CustomTkinter, um botão com feedback visual sobrescrevia o método interno
  que chama `command`, e "Baixar mídia" não fazia nada numa tela aparentemente
  normal. Na troca para Qt, o mesmo risco existe em qualquer widget próprio
  (cartão de formato, linha de opção): estilo não pode impedir a ação.
  Colar sobre a URL selecionada duplicava o link no Tk do X11; o `QLineEdit`
  troca a seleção nativamente, e o teste garante que continue assim.

REGRA DE NEGÓCIO
  Estilo visual adicional não pode impedir a ação solicitada pelo usuário.

---

SDD — Especificação: encerramento da revisão de metadata

CONTRATO
  `MetadataReview.resolve(item)` tira da tela a pendência que acabou de ser
  importada. Sem nenhuma pendência restante, o diálogo de revisão fecha sozinho.
  Quem é dono desse estado é `review.py`, não a janela principal.

POR QUE EXISTE
  Depois de escolher um resultado do catálogo, a janela de candidatos fechava
  mas a de revisão continuava aberta, oferecendo "Buscar metadata" para um
  arquivo já resolvido.

REGRA DE NEGÓCIO
  - Só a importação concluída (`metadata_applied`) resolve a linha.
  - Em playlist, resolver um item não pode fechar a revisão dos outros.

---

SDD — Especificação: fila de buscas no catálogo

CONTRATO
  `start_search(item)` enfileira; uma busca corre por vez; `search_finished`
  (no resultado ou no erro) libera a próxima.

POR QUE EXISTE
  Em playlist a pessoa clica em vários itens seguidos; recusar o segundo
  clique transformava isso em clicar, esperar, clicar de novo.

---

SDD — Especificação: teclado e estados de página

CONTRATO
  - Todo controle da página de download é alcançável com Tab e mostra foco:
    cartões de formato, chips de qualidade, chave do trecho, régua e chaves dos
    extras.
  - O histórico vazio explica e oferece o próximo passo ("Baixar algo"), que
    leva à página de download com o foco no campo de link.

POR QUE EXISTE
  Os cartões e as chaves são widgets desenhados aqui, não nativos: o Qt não dá
  foco nem anel de foco a eles sem pedir. E um estado vazio que só diz "nada
  ainda" deixa a pessoa sem saída (regra "nothing yet = explain + create" da
  skill app-shell do crewkit).

---

SDD — Especificação: a prévia é do link que está no campo

CONTRATO
  A janela só aplica o evento `preview` cuja URL é a que está no campo agora.

POR QUE EXISTE
  A prévia roda numa thread e pode chegar depois que a pessoa trocou o link:
  mostrar a miniatura e a duração do vídeo anterior faria a régua do trecho
  cortar com a duração errada.
"""

from collections import deque
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from media_downloader import window
from media_downloader.models import MediaPreview, MetadataPendingItem
from media_downloader.review import MetadataReview
from media_downloader.window import MediaDownloaderApp


@pytest.fixture
def app_window(qapp, tmp_path):
    janela = MediaDownloaderApp()
    janela._downloads_dir = tmp_path / "Downloads"
    janela._probe_timer.stop()
    yield janela
    janela._poll_timer.stop()
    janela.close()
    janela.deleteLater()


class TestAcionamentoDosControles:
    def test_deve_iniciar_o_download_quando_o_botao_baixar_e_clicado(self, app_window, monkeypatch):
        chamadas = []
        monkeypatch.setattr(app_window, "start_download", lambda: chamadas.append("baixar"))
        # O sinal foi ligado ao metodo original; religa para ver o clique chegar.
        app_window.download_btn.clicked.disconnect()
        app_window.download_btn.clicked.connect(app_window.start_download)

        QTest.mouseClick(app_window.download_btn, Qt.MouseButton.LeftButton)

        assert chamadas == ["baixar"]

    def test_clicar_no_cartao_mp4_deve_trocar_o_formato(self, app_window):
        QTest.mouseClick(app_window._mp4_card, Qt.MouseButton.LeftButton)

        assert app_window._format == "mp4"
        assert app_window._mp4_card.isSelected()
        assert not app_window._mp3_card.isSelected()

    def test_clicar_na_linha_da_opcao_deve_ligar_a_chave(self, app_window):
        app_window._set_format("mp4")

        QTest.mouseClick(app_window.subtitles_row, Qt.MouseButton.LeftButton)

        assert app_window.subtitles_row.isChecked()

    def test_link_invalido_deve_avisar_sem_iniciar_download(self, app_window):
        app_window.url_entry.setText("nao e link")

        app_window.start_download()

        assert app_window._thread is None
        assert "inválido" in app_window._url_hint.text()


class TestTecladoEEstadosDePagina:
    def test_deve_alcancar_pelo_tab_todos_os_controles_da_pagina(self, app_window):
        controles = [app_window._mp3_card, app_window._mp4_card, app_window.clip_switch,
                     app_window.clip_slider, app_window.subtitles_row.switch,
                     *app_window._quality_buttons.values()]

        sem_tab = [type(c).__name__ for c in controles
                   if not c.focusPolicy() & Qt.FocusPolicy.TabFocus]

        assert sem_tab == []

    def test_historico_vazio_deve_levar_a_pagina_de_download(self, app_window):
        app_window._show_page(1)

        app_window._history_empty_action.click()

        assert app_window._pages.currentIndex() == 0


class TestAberturaDaPastaDeDownloads:
    def test_deve_criar_e_abrir_a_pasta_com_xdg_open_no_linux(self, app_window, monkeypatch):
        chamadas = []
        monkeypatch.setattr(window.sys, "platform", "linux")
        monkeypatch.setattr(window.subprocess, "Popen", lambda argumentos: chamadas.append(argumentos))

        app_window.open_downloads_folder()

        assert app_window._downloads_dir.is_dir()
        assert chamadas == [["xdg-open", str(app_window._downloads_dir)]]


class TestColarNoCampoDeLink:
    def test_deve_substituir_o_trecho_selecionado_pelo_link_colado(self, app_window):
        app_window.url_entry.setText("https://youtu.be/AAA")
        app_window.url_entry.selectAll()
        QGuiApplication.clipboard().setText("https://youtu.be/BBB")

        app_window.url_entry.paste()

        assert app_window.url_entry.text() == "https://youtu.be/BBB"

    def test_botao_colar_deve_trocar_o_campo_inteiro(self, app_window):
        app_window.url_entry.setText("https://youtu.be/AAA")
        QGuiApplication.clipboard().setText("  https://youtu.be/BBB  ")

        app_window._paste_url()

        assert app_window.url_entry.text() == "https://youtu.be/BBB"


class TestPreviaDoLinkAtual:
    def _previa(self, url, duracao=300.0, playlist=False):
        return MediaPreview(url=url, title="Video", duration=None if playlist else duracao,
                            is_playlist=playlist, heights=(720, 360))

    def test_deve_ignorar_a_previa_de_um_link_que_ja_foi_trocado(self, app_window):
        app_window.url_entry.setText("https://youtu.be/novo")

        app_window._handle({"type": "preview", "url": "https://youtu.be/antigo",
                            "preview": self._previa("https://youtu.be/antigo")})

        assert app_window._preview is None

    def test_deve_usar_a_duracao_da_previa_na_regua_do_trecho(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)

        app_window._handle({"type": "preview", "url": url, "preview": self._previa(url, 245)})

        assert app_window.clip_slider.maximum() == 245

    def test_nao_deve_oferecer_qualidade_acima_da_que_o_video_tem(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)

        app_window._handle({"type": "preview", "url": url, "preview": self._previa(url)})

        assert not app_window._quality_buttons["1080"].isEnabled()
        assert app_window._quality_buttons["720"].isEnabled()

    def test_playlist_deve_desligar_o_trecho(self, app_window):
        url = "https://www.youtube.com/playlist?list=PL"
        app_window.url_entry.setText(url)
        app_window.clip_switch.setChecked(True)

        app_window._handle({"type": "preview", "url": url,
                            "preview": self._previa(url, playlist=True)})

        assert not app_window.clip_switch.isChecked()
        assert not app_window.clip_switch.isEnabled()

    def test_trecho_invalido_deve_bloquear_o_botao_baixar(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._previa(url, 300)})
        app_window.clip_switch.setChecked(True)

        app_window.clip_start.setText("2:00")
        app_window.clip_end.setText("1:00")
        app_window._times_edited("")

        assert not app_window.download_btn.isEnabled()
        assert "depois do início" in app_window._clip_error.text()

    def test_trecho_escolhido_deve_ir_nas_opcoes_do_download(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._previa(url, 300)})
        app_window.clip_switch.setChecked(True)
        app_window.clip_start.setText("1:00")
        app_window.clip_end.setText("1:30")

        opcoes = app_window._media_options()

        assert (opcoes.clip_start, opcoes.clip_end) == (60, 90)

    def test_fim_igual_a_duracao_deve_ir_ate_o_final(self, app_window):
        url = "https://youtu.be/atual"
        app_window.url_entry.setText(url)
        app_window._handle({"type": "preview", "url": url, "preview": self._previa(url, 300)})
        app_window.clip_switch.setChecked(True)
        app_window.clip_start.setText("1:00")
        app_window.clip_end.setText("5:00")

        assert app_window._media_options().clip_end is None


class TestEncerramentoDaRevisaoDeMetadata:
    def _revisao(self, qapp, pendentes):
        revisao = MetadataReview(QWidget(), SimpleNamespace())
        revisao._metadata_review_window = QWidget()
        revisao._metadata_review_window.show()
        revisao._metadata_review_rows = {
            item: (QWidget(), None, None, None) for item in pendentes
        }
        return revisao

    def test_deve_fechar_a_revisao_quando_a_ultima_pendencia_foi_importada(self, qapp):
        item = MetadataPendingItem("Paramore - All I Wanted", "/tmp/faixa.mp3",
                                   ("artista provisorio: canal Paramore",))
        revisao = self._revisao(qapp, [item])
        janela = revisao._metadata_review_window

        revisao.resolve(item)

        assert not janela.isVisible()
        assert revisao._metadata_review_rows == {}
        assert revisao._metadata_review_window is None

    def test_deve_manter_a_revisao_aberta_enquanto_houver_outra_pendencia(self, qapp):
        importado = MetadataPendingItem("Faixa A", "/tmp/a.mp3", ("artista ausente",))
        restante = MetadataPendingItem("Faixa B", "/tmp/b.mp3", ("artista ausente",))
        revisao = self._revisao(qapp, [importado, restante])
        janela = revisao._metadata_review_window

        revisao.resolve(importado)

        assert janela.isVisible()
        assert list(revisao._metadata_review_rows) == [restante]

    def test_deve_ignorar_item_que_nao_esta_na_revisao(self, qapp):
        item = MetadataPendingItem("Faixa A", "/tmp/a.mp3", ("artista ausente",))
        revisao = self._revisao(qapp, [item])

        revisao.resolve(MetadataPendingItem("Outra", "/tmp/outra.mp3", ("artista ausente",)))

        assert list(revisao._metadata_review_rows) == [item]


class TestFilaDeBuscaDeMetadata:
    def _revisao(self, qapp, pendentes):
        from PySide6.QtWidgets import QPushButton

        revisao = MetadataReview(QWidget(), SimpleNamespace(search_metadata="alvo"))
        revisao._metadata_review_rows = {
            item: (None, None, None, QPushButton("Buscar metadata")) for item in pendentes
        }
        revisao._search_queue = deque()
        revisao.iniciadas = []
        revisao._spawn = lambda alvo, *args: revisao.iniciadas.append(args[0])
        return revisao

    def _itens(self, quantidade):
        return [MetadataPendingItem(f"Faixa {n}", f"/tmp/{n}.mp3", ("artista ausente",))
                for n in range(quantidade)]

    def _botao(self, revisao, item):
        return revisao._metadata_review_rows[item][3]

    def test_deve_iniciar_a_primeira_busca_de_imediato(self, qapp):
        um, = self._itens(1)
        revisao = self._revisao(qapp, [um])

        revisao.start_search(um)

        assert revisao.iniciadas == [um]
        assert not self._botao(revisao, um).isEnabled()

    def test_deve_enfileirar_o_segundo_clique_em_vez_de_recusar(self, qapp):
        um, dois = self._itens(2)
        revisao = self._revisao(qapp, [um, dois])

        revisao.start_search(um)
        revisao.start_search(dois)

        assert revisao.iniciadas == [um], "só uma busca corre por vez"
        assert "fila" in self._botao(revisao, dois).text().lower()

    def test_deve_iniciar_a_proxima_quando_a_anterior_termina(self, qapp):
        um, dois = self._itens(2)
        revisao = self._revisao(qapp, [um, dois])
        revisao.start_search(um)
        revisao.start_search(dois)

        revisao.search_finished(um)

        assert revisao.iniciadas == [um, dois]
        assert self._botao(revisao, um).isEnabled()

    def test_deve_ignorar_clique_repetido_no_mesmo_item(self, qapp):
        um, = self._itens(1)
        revisao = self._revisao(qapp, [um])

        revisao.start_search(um)
        revisao.start_search(um)

        assert revisao.iniciadas == [um]
