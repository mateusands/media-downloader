"""
SDD — Especificação: régua do trecho (duas alças)

CONTRATO
  `RangeSlider` guarda início e fim em segundos sobre a duração do vídeo
  (`setMaximum`). `setValues(início, fim)` corrige o que vier fora da régua e
  publica `rangeChanged`. O mouse arrasta a alça mais próxima do clique; as
  setas movem 1 s, Shift+setas 10 s.

POR QUE EXISTE
  Digitar "1:02:03" funciona, mas arrastar é como a pessoa pensa "daqui até
  ali" — é o controle da extensão irmã e do Seal. Os campos de tempo e a régua
  mostram o mesmo trecho; a janela sincroniza os dois.

REGRA DE NEGÓCIO
  - As alças não se cruzam e ficam a pelo menos 1 s: trecho de zero segundos
    não é trecho.
  - Nada passa de zero nem da duração.
  - Duração nova (outro vídeo) volta a régua para o vídeo inteiro.
"""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from media_downloader.widgets import RangeSlider


@pytest.fixture
def regua(qapp):
    widget = RangeSlider()
    widget.resize(424, 56)   # trilha de 400 px entre as margens de 12
    widget.setMaximum(400)
    return widget


class TestValoresDaRegua:
    def test_duracao_nova_deve_cobrir_o_video_inteiro(self, regua):
        assert regua.values() == (0, 400)

    def test_nao_deve_passar_de_zero_nem_da_duracao(self, regua):
        regua.setValues(-30, 900)
        assert regua.values() == (0, 400)

    def test_alcas_nao_devem_se_cruzar(self, regua):
        regua.setValues(200, 100)
        inicio, fim = regua.values()
        assert fim - inicio >= RangeSlider.MIN_GAP

    def test_deve_avisar_quando_o_trecho_muda(self, regua):
        avisos = []
        regua.rangeChanged.connect(lambda a, b: avisos.append((a, b)))

        regua.setValues(60, 120)

        assert avisos == [(60, 120)]


class TestMouseNaRegua:
    def test_clique_perto_do_inicio_deve_mover_o_inicio(self, regua):
        QTest.mouseClick(regua, Qt.MouseButton.LeftButton, pos=QPoint(12 + 100, 20))
        assert regua.values() == (100, 400)

    def test_clique_perto_do_fim_deve_mover_o_fim(self, regua):
        QTest.mouseClick(regua, Qt.MouseButton.LeftButton, pos=QPoint(12 + 300, 20))
        assert regua.values() == (0, 300)


class TestTecladoNaRegua:
    def test_seta_deve_mover_um_segundo_e_shift_dez(self, regua):
        regua.setFocus()
        QTest.keyClick(regua, Qt.Key.Key_Right)
        QTest.keyClick(regua, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        assert regua.values()[0] == 11
