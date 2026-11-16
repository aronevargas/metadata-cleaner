# Contrato de serviços — Metadata Editor Desktop (v2, FINAL)

> Decisão do coordenador: módulo de serviços **`app.services`**, interface **`app.desktop`**.

Fluxo padrão: aplicativo desktop Tkinter/ttk com janela própria. **Sem navegador e sem servidor HTTP.**
A interface chama diretamente o módulo Python `app.services`; o servidor FastAPI (`app/main.py`) fica
como legado opcional (`python -m app --web`) e usa o mesmo `app.services`.

## Divisão de arquivos
| Dono | Arquivos |
|---|---|
| backend | `app/services.py` (contrato abaixo), `app/__main__.py` (launcher), `app/media.py`, `app/jobs.py`, `app/paths.py`, `app/formats.py`, `app/main.py` + `app/picker.py` (legado web), `metadata-editor` (script de atalho), `packaging/` (atalho .desktop, PyInstaller), `requirements*.txt`, `README.md`, `docs/` |
| frontend | `app/desktop/` (pacote Tkinter: `__init__.py`, `ui.py`, `theme.py`, …). Ponto de entrada obrigatório: `app.desktop.main(argv: list[str] | None = None) -> int` (exportado em `app/desktop/__init__.py`) que cria `tk.Tk()`, monta a janela e roda `mainloop()`. |
| testador | `tests/`, `TEST-REPORT.md` |

`app/static/` (UI web) fica como legado; não é usado no fluxo desktop.

## Execução
- `.venv/bin/python -m app` → abre a janela desktop (chama `app.desktop.main`).
- `.venv/bin/python -m app --web [--port N] [--no-browser]` → legado HTTP em 127.0.0.1.
  (Compatibilidade: `python -m app --port N --no-browser` também inicia o legado web, para os testes atuais.)
- `./metadata-editor` → atalho na raiz do projeto (usa `.venv` se existir).

## Regras de threading
- Todas as funções de `app.services` são **não bloqueantes e thread-safe**, exceto `scan()`, que lê o disco
  (rápido; se quiser, chame-a numa thread e devolva o resultado à UI via `root.after`).
- O processamento roda numa thread de fundo do serviço. A GUI **não** recebe callbacks de outra thread:
  ela faz polling com `root.after(200, ...)` chamando `services.get_job(job_id)`. Nunca tocar em widgets
  fora da thread principal.
- Seleção de pasta: use `tkinter.filedialog.askdirectory` diretamente na GUI (não use `app.picker`).

## API (`from app import services`)
```python
services.ServiceError        # Exception; str(e) é mensagem em português pronta para exibir

services.health() -> dict
# {"version": "0.2.0",
#  "ffmpeg": {"available": bool, "path": str|None, "version": str|None, "error": str|None},
#  "formats": {"image": [".jpg", ...], "video": [".mp4", ...]}}

services.scan(input_dir: str, output_dir: str | None = None, recursive: bool = False) -> dict
# Mesmo formato do antigo POST /api/scan:
# {"input_dir", "output_dir", "files": [{"name","rel_path","size","kind","ext"}],
#  "skipped": [{"name","rel_path","reason"}], "counts": {"image","video","skipped"},
#  "next_index": int, "warnings": [str]}
# ServiceError: entrada inexistente/não é pasta, saída == entrada, entrada dentro da saída.

services.start_job(input_dir: str, output_dir: str, *, recursive: bool = False,
                  metadata_mode: str = "strip",            # "strip" | "replace"
                  metadata: dict | None = None,            # title, artist, comment, copyright, software
                  pixel_noise: bool = True, noise_strength: int = 1) -> str   # job_id
# ServiceError: validação de pastas, nenhum arquivo suportado, job ativo na mesma saída,
# noise_strength fora de 1..3, metadata_mode inválido. Cria a pasta de saída se necessário.
# Retorna imediatamente; o processamento roda numa thread de fundo do serviço.

services.get_job(job_id: str) -> dict
# {"job_id", "status": "queued"|"running"|"done"|"error"|"cancelled",
#  "total", "processed", "succeeded", "failed", "current": str|None,
#  "current_progress": float|None (0..1, vídeo), "input_dir", "output_dir", "error": str|None,
#  "started_at", "finished_at", "created_at",
#  "results": [{"source","kind","output": str|None,"ok": bool|None,"error": str|None,
#               "status": "pending"|"processing"|"done"|"error"|"skipped"}]}
# ServiceError se o job não existe.

services.cancel_job(job_id: str) -> str    # status atual; o job vira "cancelled" logo em seguida
services.is_finished(status: str) -> bool  # True para done/error/cancelled
services.wait_job(job_id: str, timeout: float | None = None) -> dict
                                          # bloqueia até terminar (testes/CLI; NÃO usar na GUI)
services.open_folder(path: str) -> None    # abre a pasta no gerenciador de arquivos (xdg-open/open/explorer)
services.shutdown(timeout: float = 5.0) -> None  # cancela jobs ativos; chame no WM_DELETE_WINDOW
```
Progresso/cancelamento: sem callbacks — o estado é lido por `get_job` (polling) e o cancelamento por
`cancel_job`, que também interrompe o ffmpeg em andamento.

## Garantias (inalteradas)
Originais só leitura; saída `1.ext, 2.ext…` sem sobrescrever (continua após maior número existente);
metadados removidos/substituídos; orientação/alfa/áudio/duração preservados; ruído verificado após
decodificação; FFmpeg ausente → erro por vídeo com instruções. Sem garantia contra reconhecimento perceptual.

## Portabilidade (Windows/Linux/macOS)
- Todo subprocesso em `app/` usa `app.proc.subprocess_kwargs()`: `stdin=DEVNULL`, UTF-8 com `errors="replace"`
  e, no Windows, `CREATE_NO_WINDOW` (sem console piscando no `.exe --windowed`/`pythonw`).
- FFmpeg: `METAEDIT_FFMPEG`/`METAEDIT_FFPROBE` → pasta do app (`app.media.bundled_dirs()`: diretório do
  executável congelado e `_internal`, ou raiz do projeto; subpastas `.`, `ffmpeg`, `ffmpeg/bin`, `bin`) → `PATH`.
- Caminhos comparados com `os.path.normcase` (Windows sem distinção de maiúsculas); ocultos/sistema e
  *junctions* ignorados na varredura; fallback sem hard link grava com `O_BINARY`; temporário removido com
  novas tentativas se travado (antivírus/indexador).
- `python -m app --self-check [REPORT.json]` (e `metadata-editor[.exe] --self-check REPORT.json`): autoteste
  não interativo usado pelos scripts de instalação/build e pelo workflow Windows.
