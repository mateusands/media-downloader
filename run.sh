#!/usr/bin/env bash
# Executa o Media Downloader no Linux.
#
#   ./run.sh              executa o aplicativo
#   ./run.sh --atualizar  atualiza o yt-dlp antes de executar
#   ./run.sh --verificar  so checa o ambiente e sai, sem abrir a janela
#   ./run.sh --instalar-atalho  cria o atalho "mediadownloader" no menu de aplicativos
#   ./run.sh --remover-atalho   remove esse atalho
#   ./run.sh --ajuda      mostra esta ajuda
#
# Resolve tudo a partir da propria localizacao, entao funciona de qualquer
# diretorio. Na primeira vez cria o ambiente virtual e instala as dependencias.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$RAIZ/.venv"
PY="$VENV/bin/python"

erro()   { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
aviso()  { printf '\033[0;33m%s\033[0m\n' "$*" >&2; }
passo()  { printf '\033[0;36m%s\033[0m\n' "$*"; }
ok()     { printf '\033[0;32m%s\033[0m\n' "$*"; }

atualizar=0
verificar=0
acao_atalho=""
for arg in "${@:-}"; do
    case "$arg" in
        ""|-)                 ;;
        -u|--atualizar)       atualizar=1 ;;
        -v|--verificar)       verificar=1 ;;
        --instalar-atalho)    acao_atalho=instalar ;;
        --remover-atalho)     acao_atalho=remover ;;
        -h|--ajuda)           sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^#\s\?//'; exit 0 ;;
        *)                    erro "Opcao desconhecida: $arg"; erro "Use --ajuda."; exit 2 ;;
    esac
done

# ── Atalho no menu de aplicativos ────────────────────────────────────────────
# O .desktop e gerado aqui, e nao versionado, porque Exec e Icon exigem caminho
# absoluto e ele muda de maquina para maquina.
if [[ -n "$acao_atalho" ]]; then
    destino="${XDG_DATA_HOME:-$HOME/.local/share}/applications/mediadownloader.desktop"
    if [[ "$acao_atalho" == remover ]]; then
        rm -f "$destino"
        ok "Atalho removido."
    else
        mkdir -p "$(dirname "$destino")"
        cat > "$destino" <<DESKTOP
[Desktop Entry]
Type=Application
Name=mediadownloader
Comment=Baixa musicas e videos do YouTube e de outras plataformas
Exec="$RAIZ/run.sh"
Icon=$RAIZ/assets/media-downloader-icon.png
Path=$RAIZ
Terminal=false
Categories=AudioVideo;Audio;Video;
DESKTOP
        chmod +x "$destino"
        command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$destino")" 2>/dev/null || true
        ok "Atalho criado: $destino"
    fi
    exit 0
fi

# ── Ambiente virtual ─────────────────────────────────────────────────────────
# O venv traz o proprio pip, o que resolve de uma vez os dois tropecos das
# distribuicoes recentes: pip ausente no Python do sistema e o bloqueio do
# PEP 668 (`externally-managed-environment`).
instalar=0
if [[ ! -x "$PY" ]]; then
    command -v python3 >/dev/null || { erro "python3 nao encontrado no PATH."; exit 1; }
    passo "Criando o ambiente virtual em .venv ..."
    python3 -m venv "$VENV"
    instalar=1
fi

# yt_dlp_ejs entra na checagem porque venvs antigos tem o yt-dlp sem o extra
# [default], e sem ele o YouTube nao resolve a assinatura dos formatos.
if ! "$PY" -c 'import customtkinter, yt_dlp, yt_dlp_ejs, mutagen, PIL' 2>/dev/null; then
    instalar=1
fi

if (( instalar )); then
    passo "Instalando as dependencias ..."
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r "$RAIZ/requirements.txt"
fi

if (( atualizar )); then
    # Erro de extracao e, primeiro de tudo, suspeita de yt-dlp velho: as
    # plataformas mudam e a versao instalada para de funcionar sozinha.
    passo "Atualizando o yt-dlp ..."
    "$PY" -m pip install --quiet --upgrade yt-dlp
fi

# ── Requisitos de sistema ────────────────────────────────────────────────────
# Tk nao vem junto do Python em varias distribuicoes; sem ele nao ha janela.
if ! "$PY" -c 'import tkinter' 2>/dev/null; then
    erro "Falta o Tk — o aplicativo nao abre sem ele."
    erro "  Arch/CachyOS : sudo pacman -S tk"
    erro "  Debian/Ubuntu: sudo apt install python3-tk"
    erro "  Fedora       : sudo dnf install python3-tkinter"
    exit 1
fi

# FFmpeg e aviso, nao bloqueio: a janela abre e a falha so aparece na conversao,
# com uma mensagem do yt-dlp que nao diz "instale o ffmpeg".
if ! command -v ffmpeg >/dev/null; then
    aviso "AVISO: FFmpeg nao esta no PATH."
    aviso "       O download vai parecer progredir e falhar na conversao para MP3/MP4."
    aviso "       Arch/CachyOS: sudo pacman -S ffmpeg"
fi

if (( verificar )); then
    ok "Ambiente pronto."
    printf '  python  : %s\n' "$("$PY" --version)"
    printf '  yt-dlp  : %s\n' "$("$PY" -c 'import yt_dlp; print(yt_dlp.version.__version__)')"
    printf '  ffmpeg  : %s\n' "$(command -v ffmpeg >/dev/null && ffmpeg -version | head -1 | cut -d' ' -f3 || echo 'ausente')"
    printf '  js      : %s\n' "$(PYTHONPATH="$RAIZ/src" "$PY" -c 'import shutil; from media_downloader.downloader import js_runtimes_for; r = js_runtimes_for(shutil.which); n = next(iter(r)); print(n, r[n].get("path", "(nao instalado: YouTube com poucos formatos)"))')"
    printf '  destino : %s\n' "$RAIZ/Downloads"
    exit 0
fi

exec "$PY" "$RAIZ/src/app.py"
