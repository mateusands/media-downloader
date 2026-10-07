"""
SDD — Especificação: a pessoa escolhe onde os downloads são salvos

CONTRATO
  - `load_settings(arquivo)` / `save_settings(arquivo, settings)` guardam a
    escolha entre uma execução e outra; `downloads_dir` é a pasta base.
  - `DownloadManager.set_downloads_dir(base)` troca a base; as quatro subpastas
    (`audios_unicos`, `videos_unicos`, `playlist_audio`, `playlist_video`)
    continuam as mesmas, agora dentro da base escolhida.

POR QUE EXISTE
  Os downloads caíam sempre em `Downloads/` dentro do repositório. A biblioteca
  da pessoa mora em outro disco (`/mnt/hdd/musicas`), e mover arquivo à mão a
  cada download era o passo que ela queria eliminar.

REGRA DE NEGÓCIO
  - Sem escolha salva, vale `Downloads/` na raiz do repositório, como antes.
  - Arquivo de configuração ausente, corrompido ou com valor de tipo errado não
    impede o app de abrir: volta para o padrão.
  - A organização por subpasta é mantida: o histórico de playlist depende dela.
"""

import queue

from media_downloader.config import BASE_DOWNLOADS_DIR
from media_downloader.downloader import DownloadManager
from media_downloader.settings import Settings, load_settings, save_settings


class TestEscolhaGuardada:
    def test_sem_escolha_salva_deve_usar_a_pasta_padrao(self, tmp_path):
        assert load_settings(tmp_path / "nao-existe.json").downloads_dir == BASE_DOWNLOADS_DIR

    def test_deve_lembrar_a_pasta_escolhida(self, tmp_path):
        arquivo = tmp_path / "sub" / "config.json"
        save_settings(arquivo, Settings(downloads_dir=tmp_path / "Musicas"))

        assert load_settings(arquivo).downloads_dir == tmp_path / "Musicas"

    def test_arquivo_corrompido_deve_voltar_ao_padrao(self, tmp_path):
        arquivo = tmp_path / "config.json"
        for conteudo in ("{nao e json", '{"downloads_dir": 42}', "[]"):
            arquivo.write_text(conteudo)
            assert load_settings(arquivo).downloads_dir == BASE_DOWNLOADS_DIR, conteudo


class TestDownloadNaPastaEscolhida:
    def test_deve_criar_as_subpastas_dentro_da_pasta_escolhida(self, tmp_path):
        manager = DownloadManager(queue.Queue())
        manager.set_downloads_dir(tmp_path)

        assert manager._ensure_output_dir("mp3", False) == tmp_path / "audios_unicos"
        assert manager._ensure_output_dir("mp4", True) == tmp_path / "playlist_video"
        assert (tmp_path / "audios_unicos").is_dir()
