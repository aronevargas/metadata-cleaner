# Metadata Editor

Aplicativo **desktop** (janela própria, roda só no seu computador — sem navegador e sem servidor) que lê
uma pasta de imagens/vídeos, remove ou substitui os metadados, altera discretamente alguns pixels e grava
cópias numeradas (`1.jpg`, `2.png`, `3.mp4`, …) numa pasta de saída. **Os originais nunca são modificados.**

## Requisitos
- Python 3.10+ com **Tkinter** (testado com Python 3.13 / Tk 8.6 no Linux).
  - Debian/Ubuntu: `sudo apt install python3-tk python3-venv` · Fedora: `sudo dnf install python3-tkinter`
  - Windows/macOS: o Python de python.org já inclui Tkinter (no Windows mantenha marcado *tcl/tk and IDLE*;
    ou `winget install Python.Python.3.13`).
- **FFmpeg + ffprobe** (só para vídeos). Procurados nesta ordem: `METAEDIT_FFMPEG`/`METAEDIT_FFPROBE`; pasta
  ao lado do aplicativo (`ffmpeg.exe`, `ffmpeg\`, `ffmpeg\bin\` ou `bin\` junto do executável, ou na raiz do
  projeto quando roda do código-fonte); `PATH`. Instalação: Ubuntu/Debian `sudo apt install ffmpeg`; macOS
  `brew install ffmpeg`; Windows `winget install Gyan.FFmpeg` (abra um terminal novo depois) ou copie
  `ffmpeg.exe` + `ffprobe.exe` para `ffmpeg\bin\`. Sem FFmpeg as imagens funcionam e cada vídeo falha com uma
  mensagem explicando como instalar.
- Opcional (Linux): `zenity` (GTK) ou `kdialog` para o seletor de pasta nativo; sem eles é usado o do Tkinter
  (`METAEDIT_TK_PICKER=1` força o do Tkinter).

## Instalação e execução — Linux / macOS
```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./metadata-editor              # atalho; usa .venv automaticamente (equivale a .venv/bin/python -m app)
packaging/install-linux-shortcut.sh   # opcional: atalho no menu de aplicativos (~/.local/share/applications)
```
O atalho de menu aponta para esta pasta; se movê-la, rode o script de novo.

## Instalação e execução — Windows
1. Instale o Python 3.10+ (python.org ou `winget install Python.Python.3.13`) e, para vídeos, o FFmpeg.
2. Dê duplo clique em **`packaging\windows\install.bat`** (ou, no PowerShell:
   `powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\install.ps1 -Shortcut`).
   Ele cria `.venv`, instala as dependências, roda um autoteste (janela Tk, imagens, FFmpeg) e cria atalhos
   *Metadata Editor* no Menu Iniciar e na Área de Trabalho.
3. Abra pelo atalho ou com duplo clique em **`metadata-editor.bat`** (usa `pythonw`, sem console; erros de
   inicialização aparecem numa caixa de mensagem). Para ver logs/tracebacks:
   `packaging\windows\metadata-editor-debug.bat`.

> **Status no Windows:** o código foi revisado e ajustado para Windows (subprocessos sem janela de console,
> UTF-8 na saída do FFmpeg, caminhos sem distinção de maiúsculas, arquivos ocultos/de sistema e *junctions*
> ignorados, gravação binária no fallback sem hard link, temporário removido mesmo com arquivo travado), mas
> **ainda não foi executado num Windows real por este projeto**: os scripts `.ps1`/`.bat` e o `.exe` não
> foram testados. O workflow `.github/workflows/build-windows.yml` existe para fazer essa validação.

A janela lembra as últimas pastas usadas (Linux: `~/.config/metadata-editor/`; Windows: `%APPDATA%\metadata-editor\`);
`--no-restore` desativa.

### Autoteste sem interface
```sh
.venv/bin/python -m app --self-check relatorio.json     # Windows: .venv\Scripts\python -m app --self-check relatorio.json
```
Grava um JSON com o resultado (importações, abertura de janela Tk, processamento de imagens em pasta com
acentos/espaços, FFmpeg e um vídeo curto) e sai com código 0 se o essencial passou. Funciona também no executável.

## Uso
1. Escolha a pasta de entrada e a de saída (botão *Procurar…* ou digite o caminho). A saída é criada se não existir.
2. Liste os arquivos para ver os suportados e os ignorados.
3. Escolha *remover* metadados ou *substituir* (remove tudo e grava título/autor/comentário/copyright/software
   informados), a intensidade do ruído (1–3) e inicie. Progresso e erros aparecem por arquivo; é possível cancelar.

## Formatos
- Imagens: JPG/JPEG, PNG, WebP, BMP, TIFF (animações/multipágina não suportadas).
- Vídeos: MP4, MOV, M4V, MKV (H.264) e WebM (VP9).

## O que é feito
- **Imagens**: orientação EXIF aplicada aos pixels; imagem nova criada a partir dos pixels (sem EXIF/XMP/IPTC/
  GPS/textos PNG); ±1–3 níveis em ~3–10% dos pixels visíveis; transparência intacta; perfil ICC mantido.
  A saída é decodificada e comparada ao original: se a compressão (JPEG/WebP) anular a alteração, ela é
  reforçada (blocos 8×8 deslocados poucos níveis); se ainda assim não diferir, o arquivo é marcado como erro.
- **Vídeos**: re-encode (CRF 18) com ruído temporal leve; áudio copiado sem re-encode quando o contêiner
  permite; metadados globais/de faixa/capítulos removidos; rotação aplicada aos pixels.

## Garantias de arquivo
- Originais só são lidos. A saída é gravada em temporário e publicada com link exclusivo: **nunca sobrescreve**;
  a numeração continua após o maior número já existente na saída.
- Saída igual à entrada, ou entrada dentro da saída, é recusada; saída dentro da entrada é ignorada na varredura.
  Arquivos ocultos e links simbólicos são ignorados.

## Gerar executável (opcional)
O PyInstaller **não faz compilação cruzada**: o executável de cada sistema precisa ser gerado nesse sistema.
As duas plataformas usam o mesmo `packaging/metadata-editor.spec` e o PyInstaller fixado em `requirements-build.txt`.

| Plataforma | Comando | Resultado | Status |
|---|---|---|---|
| Linux x86_64 | `packaging/build-pyinstaller.sh` | `dist/metadata-editor/metadata-editor` | gerado e testado (`--self-check` OK) |
| Windows x64 | `packaging\windows\build-exe.bat` ou `build-exe.ps1 [-FfmpegDir C:\ffmpeg\bin] [-Zip]` | `dist\metadata-editor\metadata-editor.exe` (+ zip opcional) | scripts prontos, **não executados** |
| macOS | `packaging/build-pyinstaller.sh` | `dist/metadata-editor/` | não testado |

- `build-exe.ps1` usa/cria `.venv`, gera a pasta, roda o `--self-check` do `.exe` gerado
  (`dist\selfcheck-windows.json`) e, com `-Zip`, cria `dist\metadata-editor-windows-x64.zip` com SHA256.
- **FFmpeg não é baixado por nenhum script.** Com `-FfmpegDir` você indica a pasta de um FFmpeg que já tem
  (ex.: build do gyan.dev ou BtbN, origens indicadas em ffmpeg.org); `ffmpeg.exe`, `ffprobe.exe`, DLLs e textos
  de licença vão para `dist\metadata-editor\ffmpeg\`, onde o app os encontra. Ao redistribuir, respeite a
  licença (GPL/LGPL) desse FFmpeg. Sem isso, o `.exe` usa o FFmpeg do `PATH`.
- O executável Linux depende da glibc da máquina onde foi gerado.

### Build Windows via CI (manual)
`.github/workflows/build-windows.yml` roda num runner `windows-latest` do GitHub **apenas quando disparado
manualmente** (*Actions → build-windows → Run workflow*; não roda em push/PR): instala Python 3.13 e FFmpeg
(Chocolatey, só no runner), executa `install.ps1 -Dev` (autoteste do código-fonte), a suíte de testes
(informativa), `build-exe.ps1 -Zip -RequireVideo` (autoteste do `.exe`, vídeo obrigatório), os testes
`tests/test_windows_native.py` + `tests/test_windows_compat.py` (**bloqueantes**) e publica zip, JSON e relatórios
junit como artefato.
Requer o repositório no GitHub; ainda não foi executado.

## Modo web (legado, opcional)
A versão anterior com interface no navegador continua disponível:
```sh
.venv/bin/pip install -r requirements-web.txt
.venv/bin/python -m app --web [--port 8765] [--no-browser]   # escuta só em 127.0.0.1
```

## Desenvolvimento e testes
```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
```
Variáveis: `METAEDIT_FFMPEG` / `METAEDIT_FFPROBE` (caminho dos binários; caminho inexistente simula FFmpeg
ausente), `METAEDIT_PORT` e `METAEDIT_NO_PICKER=1` (modo web). Contratos: `docs/services.md` (desktop),
`docs/api.md` (web legado).

## Limitações (importante)
- **Não há garantia de "enganar" reconhecimento perceptual.** O hash do arquivo e os pixels mudam, mas sistemas
  que comparam conteúdo visual (hash perceptual, fingerprint de vídeo, IA) normalmente continuam reconhecendo a
  mesma imagem/vídeo. A alteração é propositalmente discreta.
- Vídeos são re-encodados (pequena perda de qualidade, tempo de CPU). O nome genérico do encoder
  (`Lavc libx264`) permanece no MP4.
- Imagens 16 bits são convertidas para 8 bits; CMYK vira RGB.
- Windows: caminhos com mais de 260 caracteres só funcionam com *LongPathsEnabled* ativado no sistema;
  pastas de rede (UNC `\\servidor\pasta`) devem funcionar mas não foram testadas.
