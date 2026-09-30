# AGENTS.md — Media Downloader

## Propósito do projeto

Aplicação desktop em Python com interface dark mode (CustomTkinter) para baixar vídeo ou áudio de plataformas
compatíveis com o yt-dlp. Aceita links HTTP/HTTPS e deixa a confirmação de suporte para o yt-dlp; preserva a
detecção explícita de playlists do YouTube para não baixar uma coleção inteira quando o usuário forneceu o link de
um único vídeo. Em playlist, lista os itens e deixa escolher o que baixar, lembrando o que já foi baixado antes.
Organiza os downloads em pastas por tipo, mostra progresso em tempo real, permite cancelar e fecha com um resumo
dos itens que falharam.

Projeto de portfólio.

---

## Fonte da verdade

**O estado real do sistema é o código.** Ele vive em `src/media_downloader/`, um módulo por camada
(ver abaixo). Leia o módulo relevante antes de mudar — não o pacote inteiro.

---

## Stack

- **Python 3.10+** (o ambiente local está em 3.14)
- **yt-dlp** — download e extração de metadados
- **mutagen** — leitura e escrita de tags ID3 e capa em MP3
- **Pillow** — redimensiona as capas exibidas na revisão de metadata. É importado no topo de `app.py`:
  sem ele o aplicativo **não abre**, mesmo que ninguém use a opção de metadata.
- **CustomTkinter** — widgets dark mode sobre Tkinter
- **FFmpeg** (binário do sistema) — obrigatório para MP3 e para juntar vídeo+áudio em MP4
- **pytest** — suíte da lógica pura (backend), sem abrir janela

### Estrutura

```
media-downloader/
├── requirements.txt        # dependências para rodar o app
├── requirements-dev.txt    # + pytest (desenvolvimento)
├── pytest.ini              # pythonpath=src, testpaths=tests
├── src/
│   ├── app.py              # ponto de entrada: só monta a janela e chama o mainloop
│   └── media_downloader/
│       ├── config.py       # caminhos, serviços externos e limites
│       ├── theme.py        # paleta e tipografia
│       ├── models.py       # dados imutáveis que atravessam a fila
│       ├── metadata.py     # catálogo do iTunes e tags do arquivo
│       ├── downloader.py   # ReportingLogger e DownloadManager
│       ├── widgets.py      # FormatCard e HoverButton
│       ├── review.py       # MetadataReview — diálogos de revisão de metadata
│       ├── selection.py    # PlaylistChoice (estado) e PlaylistSelection (diálogo)
│       └── window.py       # MediaDownloaderApp
├── assets/                 # ícone SVG mestre, PNG da UI e ICO para Windows
└── tests/
    ├── test_url.py              # validação e classificação de URL
    ├── test_caminhos.py         # raiz do repositório para assets e downloads
    ├── test_download_manager.py # interpretação do resultado do yt-dlp + relato de falhas
    ├── test_playlist.py         # histórico, numeração, escolha de itens, cancelamento, runtime JS
    ├── test_music_metadata.py   # sugestão, catálogo e importação de metadata escolhida
    ├── test_metadata_automatica.py # correspondência segura e aplicação automática
    ├── test_selecao.py          # estado da escolha de itens da playlist
    ├── test_contraste.py        # contraste AA da paleta
    └── test_ui_controls.py      # acionamento de controles e diálogos da revisão
```

**A dependência entre módulos só corre num sentido:** `window` e `widgets` importam do backend;
`downloader` e `metadata` nunca importam de `window`, `widgets` ou `theme`. Se você precisar do
contrário, a regra está na camada errada.

Os downloads vão para `Downloads/` na raiz do repo, em subpastas:

| Formato | Item único | Playlist |
|---|---|---|
| mp3 | `Downloads/audios_unicos/` | `Downloads/playlist_audio/` |
| mp4 | `Downloads/videos_unicos/` | `Downloads/playlist_video/` |

Em playlist, cada arquivo sai em `<pasta>/<nome da playlist>/`, e cada pasta de playlist tem um
`.historico-de-downloads.txt` (o `download_archive` do yt-dlp). O MP3 se chama pelo **nome da música**: o
título do catálogo quando aplicado, senão `song_title` do vídeo (sem artista, sem "(Official Video)"). Não
numere o nome — a pessoa pediu só o nome da música; a ordem da playlist vai na tag de faixa (`TRCK`).

---

## Arquitetura — a separação que dá valor a este projeto

Cada camada é um módulo, e elas **não devem ser misturadas**:

### 1. Dados — `models.py`, `config.py`, `theme.py`
- `DownloadSummary` (dataclass) — contadores, itens que falharam, pendências de metadata, pasta de destino e modo playlist.
- `MetadataPendingItem` e `MusicMetadataCandidate` — dados imutáveis que atravessam a fila; nenhum campo
  de tag é inferido e gravado sem confirmação.

### 2. Backend — `downloader.py` e `metadata.py`
**Não conhece a UI.** Não importa widget, não chama `messagebox`, não toca em `root`. Comunica-se
exclusivamente por uma `queue.Queue`, publicando eventos com `_emit(tipo, **payload)`.

- `download(url, file_format, include_metadata, auto_metadata)` — item único: extrai info → baixa. Playlist:
  extrai a listagem rápida, marca o que está no histórico e publica `playlist_listed`; **não baixa**.
- `download_selected(indices)` — segunda etapa da playlist: esquece do histórico o que a pessoa pediu de novo e
  baixa só os índices escolhidos (`playlist_items`). `discard_listing()` quando ela desiste.
- `cancel()` — o progress hook levanta `DownloadCancelled` no próximo aviso; o fim chega como evento `cancelled`
  com o resumo parcial, nunca como `error`.
- `_base_opts()` — o que a listagem e o download precisam igual (runtime JS, componentes remotos). Opção que
  as duas etapas precisam entra aqui, não duplicada.
- `_build_opts(...)` — opções do yt-dlp (formato, template de saída, hooks)
- `_make_progress_hook(...)` — traduz o progresso do yt-dlp em evento na fila
- `confident_match(item, candidatos)` — o candidato que dispensa revisão: artista, título **e** duração (±5 s)
  batendo juntos; versão diferente (ao vivo, remix, acústica) nunca casa. `_auto_apply_metadata` usa ao fim do
  download quando a pessoa ligou a aplicação automática.
- `MusicMetadataService` — pesquisa candidatos na API de busca do iTunes e incorpora no MP3 apenas o
  candidato selecionado. Uma resposta já traz faixa, artista, álbum, ano e a arte, então não há segunda
  chamada para capa. `read_embedded` lê de volta o que já está gravado no arquivo — é a fonte da prévia
  mostrada na UI.
- `metadata_review_reasons(info)` — diz o que o yt-dlp realmente gravou (artista da origem, artista
  provisório vindo do canal, ou ausente); `metadata_review_detail` transforma isso na frase da UI.
- `ReportingLogger` — captura warning/error do yt-dlp e acumula em `summary.failed_items`; ao concluir,
  `_reconcile_failure_reports` remove diagnósticos técnicos quando todos os itens previstos baixaram.

### 3. UI — `window.py` e `widgets.py`
Consome a fila por **polling**: `self.root.after(100, self._poll)` reagenda a cada 100 ms, drenando a
`queue` com `get_nowait()` e despachando para `_handle(event)`.

> **Este é o padrão de concorrência do projeto: fila + polling.** Não é `root.after(0, callback)` chamado
> de dentro da thread — é a thread publicando na fila e a UI lendo sozinha. Ao adicionar comunicação
> thread→UI, **use a fila existente**, não invente um segundo mecanismo.

O download roda em `threading.Thread(target=self._manager.download, daemon=True)`, guardada em
`self._thread` para o guard de "download em andamento".

---

## Regras de desenvolvimento

- **`DownloadManager` nunca importa nem toca em widget.** Essa fronteira é o que torna a lógica testável
  isolada — é a melhor característica do projeto, não a quebre por conveniência.
- **Comunicação thread→UI só pela `queue`.** Evento novo = novo `type` tratado em `_handle`.
- **Um download por vez.** `start_download` já bloqueia se `self._thread.is_alive()`. Mantenha o guard — e toda
  thread de download sai de `_start_worker`, para o guard enxergar a segunda etapa da playlist também.
- **Metadata pendente não é falha.** `metadata_pending_items` precisa continuar separado de `failed_items`,
  sobretudo no resumo de playlists.
- **Inferência nunca é importação — salvo o que a pessoa autorizou.** `suggest_music_search` pode interpretar o
  título para formular a busca; grava-se no arquivo o `MusicMetadataCandidate` que a pessoa confirmou na revisão
  **ou** o que `confident_match` aprovou num download em que ela ligou "Aplicar sozinho". Desligada a opção, nada
  é gravado sem confirmação. Afrouxar o `confident_match` (tolerar dois de três critérios, casar só pelo título)
  é gravar palpite no arquivo dela — o ambíguo vai para a revisão.
- **Não chame de ausente o que está gravado.** O `FFmpegMetadata` do yt-dlp cai para o nome do canal quando
  a origem não publica `artist` — o MP3 sai com artista provisório (`AudioslaveVEVO`), não sem artista.
  A revisão nomeia o canal; dizer "faltam: artista" era falso e foi o que originou `metadata_review_reasons`.
- **Capa da miniatura é provisória, como o artista do canal.** A miniatura do YouTube é 16:9 — um quadro
  do vídeo — e o `EmbedThumbnail` grava ela como está. Tratar "existe miniatura" como "capa resolvida"
  fechava a revisão e deixava o MP3 com um PNG 1280x720 no lugar da arte. `_cover_reason` mede a
  proporção declarada; sem dimensões, não afirma nada.
- **A prévia mostra o arquivo, não a suposição.** O que aparece no diálogo de revisão vem de
  `read_embedded`, lido pelo backend numa thread e publicado na fila — a thread gráfica não abre arquivo.
- **`ignoreerrors: True`** faz o yt-dlp continuar a playlist quando um item falha — por isso existe
  `failed_items` no resumo. Não "conserte" isso para abortar tudo no primeiro erro.
- **Item já baixado aparece na escolha, desmarcado — nunca escondido.** Se a pessoa marcar, `forget_archived`
  tira o item do histórico antes do download; sem isso o yt-dlp pula em silêncio o que ela pediu.
- **Renomear vem depois de gravar a tag.** `_apply_candidate` grava e só então renomeia para o título do
  catálogo; falha ao renomear não desfaz a importação. Todo caminho que aplica candidato passa por ele.
- **Não logue a URL completa** em nada persistente; pode conter parâmetros de sessão.
- **Não adicione opção de yt-dlp que contorne restrição de acesso.** A ferramenta baixa o que o usuário já
  pode acessar.

---

## Regra inegociável: SDD + BDD + TDD

Nenhum código de produção é escrito sem spec (SDD) → comportamento (BDD) → teste vermelho (TDD).
Sem exceções, mesmo em mudança pequena. Os três andam juntos, **nessa ordem**, no mesmo arquivo de teste.

### 1. SDD — a spec vem primeiro, e mora no topo do arquivo de teste

Cabeçalho explicando **qual é o contrato**, **por que existe** (o bug ou a decisão que o originou) e
**o que é regra de negócio**. É a spec que sobrevive ao esquecimento; o teste sozinho não explica o porquê.

### 2. BDD — descreva COMPORTAMENTO, não implementação

`class Test<CenárioDeNegócio>` → `def test_deve_<resultado>_quando_<condição>`, em português, na
linguagem da operação (download, playlist, item que falhou). Se o nome do teste cita variável interna,
você está testando implementação — reescreva.

### 3. TDD — ciclo obrigatório

**Red** (escreva o teste, rode, **tem que falhar**) → **Green** (mínimo para passar) → **Refactor**.

### O que testar (por prioridade)

| Prioridade | Alvo | Exemplos |
|---|---|---|
| 🔴 Alta | Helpers puros do backend | `_valid_url`, `_url_is_playlist`, `_is_playlist_result`, `_count_items` |
| 🔴 Alta | Tradução de eventos | `ReportingLogger` (dedup de falha, remoção de prefixo) |
| 🟡 Média | Montagem de opções | `_build_opts` — formato, `outtmpl`, `noplaylist` |
| 🟢 Baixa | UI | jsdom não existe aqui; comportamento de widget valida-se à mão |

**Mocks só para o externo** (yt-dlp, rede, FFmpeg). O `DownloadManager` já é testável sem mock porque
não conhece a UI — **essa fronteira é o que sustenta a suíte**, por isso ela é inegociável.

⚠️ **Cuidado com mock que esconde bug:** se o comportamento só existe quando o yt-dlp é mockado, isole a
regra numa função pura e teste ela direto.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest                      # suíte completa
pytest -k playlist          # um recorte
```

Depois de verde, **exercite de verdade** — baixar um vídeo é o que a suíte não cobre.

---

### Convenção de commits

Conventional Commits: `feat: adiciona escolha de qualidade`, `fix: trata playlist privada`,
`test: cobre contagem de itens com entradas nulas`, `refactor: extrai construção de opts`.

---

## Armadilhas ativas

1. **FFmpeg é obrigatório e a falha é confusa.** MP3 usa o postprocessor `FFmpegExtractAudio`; MP4 usa
   `merge_output_format` (o yt-dlp baixa vídeo e áudio separados e junta). Com a opção de metadata ligada
   entram também `FFmpegMetadata` e `EmbedThumbnail`, com `writethumbnail` — tudo depende do mesmo binário.
   Sem FFmpeg no PATH, o download parece progredir e falha na conversão, com mensagem que não diz
   "instale o ffmpeg".

2. **yt-dlp quebra sozinho.** O YouTube muda, e a versão instalada para de funcionar sem nada ter mudado
   no código. Erro de extração é **primeiro** suspeito de versão velha: `pip install -U yt-dlp`.

3. **`"remote_components": ["ejs:github"]`** em `_base_opts` faz o yt-dlp buscar componentes remotos —
   depende de rede além do YouTube e pode falhar em ambiente restrito.

4. **Sem `final_ext`, o MP3 que já existe é baixado de novo.** O yt-dlp procura o arquivo pela extensão de
   download (`.webm`), que a conversão apagou. `final_ext` e o histórico são as duas defesas; o histórico só vale
   em playlist.

5. **Runtime de JavaScript.** Desde o yt-dlp 2025.11 o YouTube exige um, e só o deno é ligado por padrão.
   `js_runtimes_for` escolhe deno → node → bun entre os instalados; os scripts vêm do extra `yt-dlp[default]`
   (`yt-dlp-ejs`). Sem runtime, o YouTube entrega poucos formatos ou nenhum — `./run.sh --verificar` mostra qual
   foi escolhido.

6. **Template de saída com `%(title)s`** — títulos com `/`, emoji ou nome muito longo geram problema de
   nome de arquivo, variando por sistema de arquivos.

7. **URL aceita não é URL suportada.** `_valid_url` só exige HTTP/HTTPS com host; a confirmação de suporte,
   disponibilidade e acesso pertence ao yt-dlp. Não liste plataformas como garantia: a tela mostra exemplos e
   aponta para a lista oficial atualizada.

8. **Os caminhos são relativos ao arquivo que os declara, não ao diretório de trabalho.**
   `config.py` sobe **três** níveis (`config.py` → `media_downloader` → `src` → raiz) para achar
   `Downloads/` e `assets/`. Essa armadilha já disparou: ao dividir `app.py` em módulos, a contagem
   antiga passou a parar em `src/`, o ícone sumiu e a janela não abriu — **com a suíte verde**, porque
   nenhum teste olhava para caminho. Hoje `tests/test_caminhos.py` cobre isso; mover `config.py` de
   lugar exige refazer a conta.

9. **O tamanho da capa do iTunes está na URL, e pedir mais não cria pixel.** A API devolve
   `artworkUrl100` (miniatura de 100 px); trocar o trecho `100x100bb` pelo tamanho desejado é o que dá a
   arte utilizável — `ITUNES_ARTWORK_SIZE` (600). Pedir `1200x1200` é aceito e devolve **600×600 real**.
   A extensão nem sempre é `.jpg`, por isso `_ARTWORK_SIZE_SUFFIX` preserva a que veio em vez de fixar
   uma no `replace`.

10. **O catálogo do iTunes é comercial.** O que não está à venda na loja — bootleg, lançamento fora das
   plataformas, gravação rara — simplesmente não aparece, e a busca volta vazia sem erro. É o preço de
   ter capa quase sempre disponível: a fonte é vitrine, não acervo.

11. **`wraplength` maior que a coluna corta a frase.** O Tk não encolhe a linha para caber: o texto é
   clipado no meio, sem reticências. Vale nos **dois** diálogos: o `minsize` da revisão acompanha o
   `wraplength` da linha de motivos, e o dos resultados acompanha o do título do candidato — que precisa
   de quebra porque o iTunes devolve nome de faixa longo. Mexer em um exige mexer no outro.

---

## Regras gerais

- **O código é a fonte da verdade.** Se algo aqui parecer inconsistente com o código, o código vence —
  e atualize este arquivo.
- Decisão técnica não-óbvia deve ser documentada (no commit e/ou aqui).
- **Não commite nem faça push sem ordem explícita.**
