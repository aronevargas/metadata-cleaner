# Contrato da API HTTP — Metadata Editor (LEGADO, opcional)

> O aplicativo padrão é desktop (Tkinter), ver `docs/services.md`. Este servidor só roda com
> `.venv/bin/python -m app --web [--port N] [--no-browser]` (requer `requirements-web.txt`) e usa `app.services`.

Servidor: FastAPI em `http://127.0.0.1:8765` (somente loopback).
UI estática: arquivos em `app/static/` (dono: frontend), servidos em `GET /` (StaticFiles html=True montado após `/api`).

## Regras gerais
- Todas as rotas JSON ficam sob `/api`. Corpo de POST **deve** ser `Content-Type: application/json` (senão 415).
- Erros: HTTP 4xx/5xx com `{"detail": "mensagem em português"}` (422 de validação do FastAPI tem `detail` em lista).
- Segurança: só aceita `Host` igual a `127.0.0.1:<porta>` ou `localhost:<porta>` (senão 403); se houver header `Origin`, ele precisa ser `http://127.0.0.1:<porta>` ou `http://localhost:<porta>` (senão 403). Sem CORS.
- Caminhos são absolutos ou com `~`; são normalizados (`realpath`).

## GET /api/health
```json
{"ok": true, "version": "0.1.0",
 "ffmpeg": {"available": true, "path": "/usr/bin/ffmpeg", "version": "7.1.1", "error": null},
 "picker": {"supported": true, "method": "zenity"},
 "formats": {"image": [".jpg",".jpeg",".png",".webp",".bmp",".tif",".tiff"],
             "video": [".mp4",".mov",".m4v",".mkv",".webm"]}}
```
Se ffmpeg faltar: `available:false`, `error:"FFmpeg não encontrado..."` (imagens continuam funcionando; vídeos falham por arquivo com essa mensagem).
Env `METAEDIT_FFMPEG` / `METAEDIT_FFPROBE` sobrescrevem o caminho (útil em testes para simular ausência).

## POST /api/dialog/folder
Req: `{"title": "Escolha a pasta de entrada", "initial": "/home/user"}` (ambos opcionais)
Resp: `{"path": "/abs/path" | null, "supported": true|false, "cancelled": bool}`
- `supported:false` → sem seletor nativo; UI deve usar campo de texto.
- `path:null, cancelled:true` → usuário cancelou.

## POST /api/scan
Req: `{"input_dir": "/abs/in", "output_dir": "/abs/out" (opcional), "recursive": false}`
Resp 200:
```json
{"input_dir": "/abs/in", "output_dir": "/abs/out",
 "files": [{"name": "a.jpg", "rel_path": "a.jpg", "size": 1234, "kind": "image", "ext": ".jpg"}],
 "skipped": [{"name": "nota.txt", "rel_path": "nota.txt", "reason": "formato não suportado"}],
 "counts": {"image": 1, "video": 0, "skipped": 1},
 "next_index": 1,
 "warnings": ["FFmpeg não encontrado: vídeos não poderão ser processados."]}
```
- Ordem de `files` = ordem de numeração na saída (ordenação natural por `rel_path`).
- `next_index`: primeiro número livre na saída (maior nome numérico existente + 1; 1 se vazia/inexistente).
- Arquivos dentro da pasta de saída, ocultos (`.x`) e symlinks são ignorados (aparecem em `skipped`).
- 400: pasta de entrada inexistente / não é diretório; saída igual à entrada.

## POST /api/jobs  → 202
Req:
```json
{"input_dir": "/abs/in", "output_dir": "/abs/out", "recursive": false,
 "metadata_mode": "strip",
 "metadata": {"title": "", "artist": "", "comment": "", "copyright": "", "software": ""},
 "pixel_noise": true, "noise_strength": 1}
```
Só `input_dir` e `output_dir` são obrigatórios. Defaults: `recursive:false`, `metadata_mode:"strip"`
(`"strip"` remove tudo; `"replace"` remove tudo e grava somente os campos não vazios de `metadata`),
`pixel_noise:true`, `noise_strength:1` (1–3).
Resp: `{"job_id": "abc123", "status": "queued", "total": 3}`
- Pasta de saída é criada se não existir. 400: entrada inválida, saída == entrada, entrada dentro da saída, nenhum arquivo suportado.
- 409: já existe job rodando com a mesma pasta de saída.

## GET /api/jobs/{job_id}
```json
{"job_id": "abc123", "status": "queued|running|done|error|cancelled",
 "total": 3, "processed": 2, "succeeded": 1, "failed": 1,
 "current": "b.mp4" ,
 "results": [
   {"source": "a.jpg", "output": "1.jpg", "ok": true, "error": null, "status": "done"},
   {"source": "b.mp4", "output": null, "ok": false, "error": "FFmpeg não encontrado...", "status": "error"},
   {"source": "c.png", "output": null, "ok": null, "error": null, "status": "pending"}],
 "output_dir": "/abs/out", "error": null,
 "started_at": 1700000000.0, "finished_at": null}
```
- `results` tem uma entrada por arquivo desde o início (`status`: `pending|processing|done|error|skipped`).
- `status:"done"` mesmo com falhas por arquivo (ver `failed`). `status:"error"` só para falha geral (`error` preenchido).
- `output` = nome do arquivo na pasta de saída (`1.jpg`, `2.png`, `3.mp4`). Números só são consumidos por sucessos.
- 404 se job não existe.

## POST /api/jobs/{job_id}/cancel
Resp: `{"job_id": "...", "status": "cancelled"|<status atual se já terminou>}`. Arquivo em andamento termina/aborta; restantes ficam `skipped`.

## GET /api/jobs
Lista resumida: `{"jobs": [{"job_id","status","total","processed","output_dir"}]}`

## Garantias / limites
- Originais nunca são abertos para escrita. Saída escrita em temp na pasta de saída e publicada com `link` exclusivo (nunca sobrescreve; se nome existe, pula para o próximo número).
- Extensão de saída = extensão original em minúsculas (`.JPEG` → `.jpeg`).
- Orientação EXIF é aplicada aos pixels antes de remover EXIF; transparência (PNG/WebP/TIFF) preservada; vídeo: áudio copiado sem re-encode, duração preservada, rotação aplicada aos pixels.
- Hash binário diferente NÃO garante que sistemas de reconhecimento perceptual tratem a mídia como diferente.
