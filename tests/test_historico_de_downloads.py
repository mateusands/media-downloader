"""
SDD — Especificação: histórico dos arquivos baixados

CONTRATO
  - `history.py` guarda os últimos downloads em
    `$XDG_CONFIG_HOME/media-downloader/historico.json`:
    `append_history(arquivo, entradas)` acrescenta, `load_history(arquivo)` lê,
    do mais recente para o mais antigo.
  - `DownloadManager.set_history_path(arquivo)` liga o registro: ao fim de um
    download, cada arquivo que terminou entra no histórico, e o resumo leva os
    caminhos em `finished_files`.

POR QUE EXISTE
  Depois de baixar, a pessoa precisava abrir a pasta e procurar o arquivo, sem
  saber em qual das quatro subpastas ele caiu. O histórico (padrão do Seal e do
  MeTube) responde "onde está o que eu acabei de baixar?" com um clique, e
  sobrevive a fechar o app.

REGRA DE NEGÓCIO
  - Guarda título, caminho, formato e quando terminou. NÃO guarda a URL: link
    pode carregar parâmetro de sessão, e o AGENTS.md proíbe URL completa em
    saída persistente.
  - O caminho registrado é o final: depois da conversão, do nome da música
    (MP3) e do sufixo de trecho.
  - Tem limite: o arquivo não cresce para sempre; sai o mais antigo.
  - Histórico ilegível ou corrompido não impede o app de abrir: vale vazio.
  - Falha ao gravar o histórico não vira falha de download.
  - Sem `set_history_path`, nada é gravado (os testes e quem usa o backend sem
    tela não sujam a configuração da pessoa).
"""

import json
import queue
from pathlib import Path

from yt_dlp.postprocessor import MoveFilesAfterDownloadPP

from media_downloader.downloader import DownloadManager
from media_downloader.history import (
    HISTORY_LIMIT,
    HistoryEntry,
    append_history,
    load_history,
)


def _entrada(n, kind="mp3"):
    return HistoryEntry(
        title=f"Faixa {n}", path=f"/musicas/Faixa {n}.{kind}", kind=kind,
        finished_at=f"2026-10-30T10:{n:02d}:00",
    )


class TestRegistroDoHistorico:
    def test_sem_arquivo_deve_comecar_vazio(self, tmp_path):
        assert load_history(tmp_path / "nao-existe.json") == []

    def test_deve_devolver_do_mais_recente_para_o_mais_antigo(self, tmp_path):
        arquivo = tmp_path / "sub" / "historico.json"
        append_history(arquivo, [_entrada(1)])
        append_history(arquivo, [_entrada(2), _entrada(3)])

        assert [e.title for e in load_history(arquivo)] == ["Faixa 3", "Faixa 2", "Faixa 1"]

    def test_deve_descartar_o_mais_antigo_ao_passar_do_limite(self, tmp_path):
        arquivo = tmp_path / "historico.json"
        append_history(arquivo, [_entrada(n % 60) for n in range(HISTORY_LIMIT + 5)])

        historico = load_history(arquivo)
        assert len(historico) == HISTORY_LIMIT

    def test_arquivo_corrompido_deve_valer_como_vazio(self, tmp_path):
        arquivo = tmp_path / "historico.json"
        arquivo.write_text("{nao e json", encoding="utf-8")

        assert load_history(arquivo) == []

    def test_entrada_malformada_deve_ser_ignorada_sem_perder_as_outras(self, tmp_path):
        arquivo = tmp_path / "historico.json"
        append_history(arquivo, [_entrada(1)])
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        dados.append({"title": 42})
        arquivo.write_text(json.dumps(dados), encoding="utf-8")

        assert [e.title for e in load_history(arquivo)] == ["Faixa 1"]

    def test_nao_deve_gravar_url(self, tmp_path):
        arquivo = tmp_path / "historico.json"
        append_history(arquivo, [_entrada(1)])

        assert "http" not in arquivo.read_text(encoding="utf-8")


class _YoutubeDLFalso:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {"id": "v1", "title": "Clipe"}

    def download(self, urls):
        destino = Path(self.opts["outtmpl"]["default"]).parent
        final = destino / "Clipe.mp4"
        final.write_bytes(b"mp4")
        for hook in self.opts["progress_hooks"]:
            hook({"status": "finished", "filename": str(final),
                  "info_dict": {"id": "v1", "title": "Clipe"}})
        for hook in self.opts["postprocessor_hooks"]:
            hook({"status": "finished", "postprocessor": MoveFilesAfterDownloadPP.pp_key(),
                  "info_dict": {"title": "Clipe", "filepath": str(final)}})


class TestHistoricoDoDownload:
    def _baixar(self, monkeypatch, tmp_path, historico=None):
        monkeypatch.setattr("media_downloader.downloader.yt_dlp.YoutubeDL", _YoutubeDLFalso)
        monkeypatch.setattr(
            "media_downloader.downloader.DOWNLOAD_FOLDERS", {("mp4", False): tmp_path})
        fila = queue.Queue()
        manager = DownloadManager(fila)
        if historico is not None:
            manager.set_history_path(historico)
        manager.download("https://youtu.be/v1", "mp4")
        eventos = []
        while not fila.empty():
            eventos.append(fila.get_nowait())
        return eventos

    def test_deve_registrar_o_arquivo_que_terminou(self, monkeypatch, tmp_path):
        historico = tmp_path / "historico.json"

        self._baixar(monkeypatch, tmp_path, historico)

        entrada, = load_history(historico)
        assert entrada.title == "Clipe"
        assert entrada.path == str(tmp_path / "Clipe.mp4")
        assert entrada.kind == "mp4"

    def test_resumo_deve_levar_o_caminho_dos_arquivos(self, monkeypatch, tmp_path):
        eventos = self._baixar(monkeypatch, tmp_path, tmp_path / "historico.json")

        concluido = next(e for e in eventos if e["type"] == "done")
        assert concluido["summary"].finished_files == [str(tmp_path / "Clipe.mp4")]

    def test_sem_historico_ligado_nao_deve_gravar_nada(self, monkeypatch, tmp_path):
        self._baixar(monkeypatch, tmp_path)

        assert not list(tmp_path.glob("*.json"))

    def test_falha_ao_gravar_nao_deve_virar_falha_de_download(self, monkeypatch, tmp_path):
        bloqueio = tmp_path / "arquivo-comum"
        bloqueio.write_text("x", encoding="utf-8")

        eventos = self._baixar(monkeypatch, tmp_path, bloqueio / "historico.json")

        assert "done" in [e["type"] for e in eventos]
        assert "error" not in [e["type"] for e in eventos]
