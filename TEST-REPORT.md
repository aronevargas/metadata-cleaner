# TEST-REPORT — Metadata Editor (aplicativo desktop Tkinter)

Data: 2026-11-16 · Testador independente (dono de `tests/` e deste relatório)
Ambiente: Linux 6.17 x86_64 (GNOME/Wayland, XWayland), Python 3.13 + Tk 8.6 (`.venv`), FFmpeg 7.1.1,
PyInstaller 6.22.3. Displays usados: `:0` (XWayland da sessão) e `:97` (Xwayland rootful, para capturas).


## Rodada Windows (2026-11-16, tarde): veredito **NÃO VALIDADO NO WINDOWS REAL**

**Ambiente disponível:** só Linux. Não há máquina Windows, VM, wine nem pwsh. O `docker` existe, mas sem permissão no socket, e de qualquer forma não roda contêiner Windows num host Linux. Nada pesado foi instalado e nenhum recurso remoto foi usado. **Nenhum teste foi executado em Windows.** Tudo abaixo é revisão estática, simulação no Linux ou teste preparado para rodar num Windows real.

### O que foi corrigido (achados da revisão → dono → verificação)
| Risco no Windows | Correção (dono) | Como foi verificado aqui |
|---|---|---|
| Console do ffmpeg/ffprobe "piscando" no `.exe --windowed`; stdin inválido sob pythonw | `app/proc.subprocess_kwargs()`: `CREATE_NO_WINDOW` + `stdin=DEVNULL` (backend) | `IS_WINDOWS` simulado; teste AST: **toda** chamada `subprocess.*` em `app/` usa o helper (zenity/kdialog só no ramo Linux) |
| cp1252: tag/nome UTF-8 no JSON do ffprobe ou no stderr do ffmpeg → `UnicodeDecodeError`, thread leitora morre e o pipe trava | `encoding="utf-8", errors="replace"` (backend) | job sob `LC_ALL=C PYTHONUTF8=0`; **com o código antigo** o mesmo cenário falha (`'ascii' codec can't decode byte 0xc3`); fake ffmpeg com 400 KB de bytes inválidos no stderr vira `MediaError` sem travar |
| Fallback sem hardlink (FAT32/exFAT/SMB) com `os.open` sem `O_BINARY` → LF→CRLF corrompe a cópia | `O_BINARY` (backend) | `os.link` forçado a falhar: cópia byte a byte idêntica, sem sobrescrever; checagem estática do `O_BINARY` |
| Temporário travado por antivírus → `PermissionError` no `finally` derruba a thread e o job fica "running" para sempre | `_remove_tmp` com retry; tolera `OSError` (backend) | `os.unlink` simulado com `PermissionError`: o job termina `done` |
| Comparação de caminhos sensível a maiúsculas; `C:/` vs `C:\`; UNC | `normcase`/`normpath`; `same_path`/`is_within` sem `relative_to` (backend) | regras checadas com `ntpath`: `C:\Users\Ana` = `c:/users/ANA/`; UNC; drives diferentes |
| Ocultos/sistema (`desktop.ini`) e *junctions* | `FILE_ATTRIBUTE_HIDDEN/SYSTEM`, `is_junction` (backend) | **só estático** (atributos não existem no Linux) |
| FFmpeg ao lado do exe | busca: `METAEDIT_*` → pasta do exe/`_internal` (`.`, `ffmpeg`, `ffmpeg\bin`, `bin`) → PATH (backend) | `sys.frozen`/`sys.executable` simulados nas 3 subpastas, pasta com espaço e "ç", PATH vazio |
| Erros invisíveis sob pythonw | `_fatal()` com `MessageBoxW` e `metadata-editor-debug.bat` (backend) | **só estático** |
| Janela borrada em 125–150%, `C:/` no campo, config em `~/.config`, roda do mouse | `app/desktop/platform.py`: DPI-aware, AppUserModelID, `display_path`, `%APPDATA%`, `<MouseWheel>` ±120, `ui_scale` (frontend) | `tests/win_sim_desktop.py` (do frontend, adotado): `ctypes.windll` falso, `ntpath`, `askdirectory` devolvendo `()` = cancelado, roda ±120: 12/12 |

### Revisão estática dos scripts Windows
- `metadata-editor.bat`: CRLF e ASCII, `cd /d "%~dp0"`, caminhos entre aspas, `pythonw` sem console, sem `--web`. Sem `.venv`, explica o que fazer e tenta `pyw -3`.
- `packaging/windows/install.ps1|.bat`, `build-exe.ps1|.bat`, `metadata-editor-debug.bat`: `$ErrorActionPreference="Stop"`, caminhos via `$PSScriptRoot`, `-LiteralPath`, nada baixado nem executado de fora (FFmpeg só via `-FfmpegDir`), `-ExecutionPolicy Bypass` só no processo. `.gitattributes` força CRLF em `.bat/.ps1`.
- `packaging/metadata-editor.spec`: Python válido (`compile`), `console=False`, exclui fastapi/uvicorn/app.main/app.picker.
- `.github/workflows/build-windows.yml`: **apenas `workflow_dispatch`**, `permissions: contents: read`, sem secrets nem release. Pytest completo é informativo. `build-exe.ps1 -Zip -RequireVideo` e `tests/test_windows_native.py` + `test_windows_compat.py` são **bloqueantes**. Os artefatos são zip, `selfcheck-windows.json` e junit. **Não foi disparado** e não houve push.
- **Não verificado:** a sintaxe real dos `.ps1` (não há pwsh aqui; foram feitas só heurísticas de aspas e chaves), o comportamento do `cmd.exe` e a criação de atalhos `.lnk` (COM `WScript.Shell`).

### Testes Windows preparados (`tests/test_windows_native.py`, 11 testes, **pulados no Linux**)
NTFS sem sobrescrita, com Unicode e espaços; a mesma pasta com outra caixa é recusada; barras `/` são aceitas; drive em minúscula e separador final; UNC via `\\localhost\C$` (se acessível); hardlink e fallback binário; cancelamento sem `ffmpeg.exe` órfão (`tasklist`) e sem temporário; `CREATE_NO_WINDOW` efetivo; `os.startfile`; `tk windowingsystem == win32` e `tk_chooseDirectory`; `metadata-editor.bat` abre `pythonw -m app` (via `Get-CimInstance`, porque o `wmic` foi removido); `.exe` com subsistema PE GUI (=2). O leitor PE foi validado no Linux com o `cli-64.exe` do setuptools e uma cópia alterada para GUI, conferidos com `file`.

### Roteiro de validação Windows (local, sem CI)
Numa máquina Windows 10/11 x64 com Python 3.13 (python.org, *tcl/tk* marcado) e `winget install Gyan.FFmpeg`:
```bat
packaging\windows\install.bat                                  & rem cria .venv, autoteste, atalhos
.venv\Scripts\python -m pip install -r requirements-dev.txt
packaging\windows\build-exe.bat -Zip -RequireVideo              & rem exe + self-check do exe
.venv\Scripts\python -m pytest -q -rs tests\test_windows_native.py tests\test_windows_compat.py
.venv\Scripts\python -m pytest -q tests                          & rem informativo (test_gui pula sem DISPLAY)
```
Checagem manual (não automatizada): (1) duplo clique em `metadata-editor.bat` e no atalho do Menu Iniciar, sem console; (2) *Escolher…* abre o diálogo nativo do Windows, em pasta com acento e em `\\servidor\pasta`; (3) processar JPG/PNG/MP4, cancelar um vídeo longo e confirmar no Gerenciador de Tarefas que o `ffmpeg.exe` sumiu; (4) *Abrir pasta de saída* abre o Explorer; (5) escala de 150%, nítida; (6) `dist\metadata-editor\metadata-editor.exe` copiado para outro PC sem Python. Alternativa: disparar **manualmente** o workflow `build-windows` (decisão do usuário, não dos agentes).

### Regressão Linux após as mudanças Windows
| Comando | Resultado |
|---|---|
| `METAEDIT_TEST_DISPLAY=:97 pytest -q tests` | `131 passed, 11 skipped in 40.17s` (os skips são os 11 só-Windows) |
| `tests/test_windows_compat.py` | `22 passed` |
| executável Linux rebuildado (13:42), cópia fora do repo, `env -i`: `drive_frozen.py --launch` | `{"ok": true}`: `1.jpg 2.png 3.mp4`, erro por arquivo, cancelamento sem ffmpeg órfão nem temporário |
| `metadata-editor --self-check` (congelado) | exit 0; imports, tk_window, images, ffmpeg e video ok |

---

# Rodada anterior (desktop Linux)

## Veredito: **PASS (aceite integrado)**

O fluxo principal agora é uma **janela nativa Tkinter/ttk**: `./metadata-editor` ou `python -m app`.
Os serviços rodam no mesmo processo (`app.services`). Não há servidor HTTP, porta aberta nem navegador.
Validei três caminhos: o código-fonte, o venv limpo e o **executável PyInstaller final** (build de 13:05).
O executável foi dirigido como um usuário faria, em processamento real de imagem e vídeo, cancelamento e erros.
A interface web antiga continua só como modo legado opcional (`--web`) e **não** foi usada como evidência do desktop.

## Como reproduzir

```sh
.venv/bin/pip install -r requirements-dev.txt

# Suíte completa (precisa de um display X; usa $METAEDIT_TEST_DISPLAY ou $DISPLAY)
.venv/bin/python -m pytest -q tests                                   # -> 109 passed

# Com capturas de tela: no Wayland o XWayland da sessão recusa GetImage, então use um Xwayland rootful
Xwayland :97 -geometry 1280x860 &
METAEDIT_TEST_DISPLAY=:97 METAEDIT_SHOTS=/tmp/shots .venv/bin/python -m pytest -q tests/test_gui.py

# Executável: build + aceite dirigindo a janela real via Tk `send` (X11)
packaging/build-pyinstaller.sh
DISPLAY=:97 .venv/bin/python tests/drive_frozen.py --launch dist/metadata-editor/metadata-editor
```

## Resultados

| Comando | Resultado |
|---|---|
| `pytest -q tests` em `DISPLAY=:0` (antes dos ajustes finais de layout) | `106 passed in 46.01s` |
| `METAEDIT_TEST_DISPLAY=:97 pytest -q tests` (código final, `window.py` sha256 `403e2d67…`) | `109 passed in 38.22s` |
| venv limpo: `python3 -m venv X && X/bin/pip install -r requirements.txt` | `app.desktop`, PIL e numpy importam; **fastapi ausente** (o desktop não depende do web) |
| idem + `-r requirements-dev.txt` + `pytest -q tests` em `:97` | `109 passed in 37.84s` |
| `METAEDIT_TEST_DISPLAY=:97 METAEDIT_SHOTS=… pytest tests/test_gui.py` | `14 passed`; 6 capturas conferidas visualmente |
| `tests/drive_frozen.py --launch` no executável de 12:53 e no **final de 13:05** (cópia fora do repo, `env -i`, `cd /`) | `{"ok": true}` nas duas versões |
| executável final sem FFmpeg no `PATH` | aviso na janela e erro por vídeo; a imagem é processada (detalhes abaixo) |
| `./metadata-editor --no-restore` + `xwininfo -root -tree` | janela `"Editor de Metadados" ("metadata-editor" "Metadata-editor") 1120x760` |
| `ss -ltnup` para o PID do app (fonte e executável) | nenhum socket em escuta; o único socket aberto é a conexão X |
| `XDG_DATA_HOME=tmp packaging/install-linux-shortcut.sh` + `desktop-file-validate` | válido; só uma *hint* de que há mais de uma categoria principal |

## Cobertura

### Janela desktop real (`tests/test_gui.py`, 14 testes, Tk de verdade num display X)
- A janela abre visível e com título.
- **Fluxo completo pela GUI**: escolher pastas, listar e iniciar com JPG, PNG, MP4 e um JPG corrompido. As saídas são `1.mp4 2.jpg 3.png`, os originais ficam intactos (sha256) e a falha aparece por arquivo na tabela *Resultado*.
- O modo *substituir metadados* funciona pela GUI.
- Erros aparecem inline: pasta inexistente ou vazia, e iniciar sem listar mostra o banner "Algo deu errado" com o campo marcado.
- **Cancelamento** pela GUI: os arquivos restantes ficam "Não processado / cancelado", sem saída parcial nem temporário.
- **Fechar a janela durante um vídeo longo** não trava, encerra o ffmpeg e não deixa temporários.
- Os botões Listar, Iniciar e Cancelar ficam visíveis sem rolagem no tamanho padrão e em 820x560.
- **Launcher padrão** `python -m app`: abre a janela, não abre porta e não inicia navegador nem `xdg-open`.
- **Novos nesta rodada:**
  - `test_picker_zenity_selection_and_cancel`: chama o seletor real `native_ask_directory` sem o hook de injeção. Confere os argumentos do zenity (`--directory --title --filename`), o caminho com espaço e "ç", o fluxo pelo botão *Escolher…* e que cancelar mantém o valor anterior.
  - `test_picker_tk_fallback_real_dialog`: com `METAEDIT_TK_PICKER=1` abre o `tk_chooseDirectory` real e confirma a pasta pelo próprio diálogo. Tem failsafe para nunca travar.
  - `test_shell_launcher_creates_visible_window`: `./metadata-editor` cria a janela X `metadata-editor`, não carrega uvicorn, fastapi nem pydantic e não abre porta.
- Smoke manual do **zenity real**: o diálogo GTK abre (`zenity --file-selection --directory --title=Teste --filename=/tmp/`). Ao fechá-lo, a função retorna `''` e a janela continua respondendo.

Capturas conferidas (`gui-01-open`, `02-scan`, `03-done`, `04-error`, `05-cancel`): layout legível, contadores
(3 cópias, 1 falha), tabela de resultado com o motivo da falha, barra de progresso `4 / 4` e `2 / 13` no cancelamento,
chip "Vídeos: FFmpeg 7.1.1" e atalhos no rodapé.

### Executável PyInstaller (`tests/drive_frozen.py`, fora do pytest)
O script dirige a janela do binário pela API Tk `send` do X11. Ele preenche as pastas e clica em
*Listar arquivos*, *Iniciar transformação* e *Cancelar*, como um usuário faria. O binário rodou a partir de uma
**cópia fora do repositório**, com `env -i` (só DISPLAY, HOME temporário e `PATH=/usr/bin:/bin`) e `cd /`.
- Saídas `1.jpg 2.png 3.mp4` e progresso `100 / 100`. JPG sem EXIF, PNG sem chunks de texto, MP4 sem title/comment/artist/location. Hash diferente do original e originais intactos.
- O JPG corrompido aparece como "Falhou — Imagem corrompida ou formato não reconhecido."
- **Cancelamento** com MP4 de 40 s, feito durante o ffmpeg: o ffmpeg é encerrado, a tabela mostra "Não processado / cancelado" e não sobram temporários.
- **Autocontido**: `libpython3.13.so`, PIL e numpy carregam do `_internal/` da cópia. Nada vem do `.venv` nem de `/usr/lib/python`, e nenhum fastapi ou uvicorn é carregado.
- **Sem FFmpeg** (`PATH` sem ffmpeg): o chip mostra "Vídeos indisponíveis (sem FFmpeg)" com instruções de instalação. A imagem vira `1.jpg` e o vídeo falha com a mensagem de instalação. O FFmpeg **não** é embutido.
- Depois de um processamento, *Iniciar* fica desabilitado até listar de novo, por design: a numeração é recalculada. A UI explica isso em texto.

### Garantias anteriores preservadas (`tests/test_core.py`, 46 testes, `app.services` no mesmo processo)
São os mesmos testes funcionais de `test_api.py`, executados diretamente sobre `app.services`:
- Hash e pixels diferentes em JPG, PNG, JPEG pós-compressão e cores sólidas.
- Remoção de EXIF, GPS, XMP e textos PNG, e o modo replace.
- Orientação EXIF aplicada aos pixels, alfa preservado e modos L/P/LA/I;16/CMYK.
- Numeração sequencial que **nunca sobrescreve** e continua após o maior número existente.
- Saída igual à entrada ou aninhada é recusada; symlinks e ocultos são ignorados; Unicode e espaços funcionam.
- Erros por arquivo, progresso coerente e cancelamento.
- Vídeo: metadados removidos, áudio e duração preservados e rotação aplicada.
- Ausência de FFmpeg tratada.

### Modo web legado (`tests/test_api.py`, 49 testes)
Continua passando: escuta só em 127.0.0.1 e recusa Host e Origin estrangeiros e POST não-JSON. É opcional
(`python -m app --web`, `requirements-web.txt`) e está fora do fluxo principal.

## Falhas encontradas e retestes
1. **JPEG de cor sólida com pixels decodificados idênticos** (rodada web anterior). O backend corrigiu e os testes foram refeitos; continua coberto em `test_core.py`.
2. **Executável desatualizado**: o build de 12:53 era anterior ao `window.py` de 13:02, com os ajustes de layout do frontend. A pedido do testador, o backend gerou um novo build às 13:05, que foi revalidado e aprovado.
3. **Bug no meu teste** `test_picker_tk_fallback_real_dialog`: ele travava porque o 1º OK do `tk_chooseDirectory` só navega até a pasta. Foi corrigido com um 2º OK e um failsafe, e o diagnóstico foi confirmado pelo frontend. O app não tinha defeito.

## Limitações (honestas)
- **Plataformas**: testado apenas no Linux x86_64 (GNOME/Wayland via XWayland). **Windows e macOS não foram testados**, nem o `metadata-editor.bat` nem um build PyInstaller nessas plataformas. O executável Linux depende da glibc da máquina de build e roda via X11/XWayland, porque Tk não fala Wayland nativo.
- **Tkinter**: Linux precisa de `python3-tk` (Debian/Ubuntu) ou `python3-tkinter` (Fedora) para rodar pelo código-fonte. O executável já embute Tcl/Tk.
- **FFmpeg** é dependência externa do sistema, só para vídeos. Sem ele, imagens funcionam e vídeos falham com instruções.
- **Seletor de pastas**: o diálogo GTK do zenity foi aberto e fechado de verdade, mas **não** escolhi uma pasta clicando nele, porque não há xdotool aqui. A seleção foi validada com um zenity simulado e com o `tk_chooseDirectory` real. O caminho do kdialog não foi testado.
- No `:0` não há capturas de tela, porque o XWayland do GNOME recusa GetImage. As capturas vêm de um Xwayland rootful `:97`.
- O `drive_frozen.py` depende do `send` do Tk e dos nomes internos de widgets/variáveis (`PY_VAR0/1`). É uma ferramenta de aceite manual e não entra no pytest.
- Não há garantia contra reconhecimento perceptual (pHash, IA). A tag genérica `encoder=Lavc libx264` permanece no MP4. O perfil ICC é mantido. Vídeo é re-encodado (CRF 18).
- `.desktop`: o validador dá uma *hint* sobre múltiplas categorias principais, que pode causar entrada duplicada no menu. É cosmético e fica com o dono de `packaging/`.
