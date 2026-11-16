"use strict";

(() => {
  const $ = (sel) => document.querySelector(sel);
  const el = {
    chips: $("#status-chips"),
    errorBanner: $("#error-banner"),
    errorText: $("#error-text"),
    errorClose: $("#error-close"),
    pickerNote: $("#picker-note"),
    foldersForm: $("#folders-form"),
    inputDir: $("#input-dir"),
    outputDir: $("#output-dir"),
    pickInput: $("#pick-input"),
    pickOutput: $("#pick-output"),
    recursive: $("#recursive"),
    scanBtn: $("#scan-btn"),
    fileCounts: $("#file-counts"),
    scanWarnings: $("#scan-warnings"),
    emptyState: $("#empty-state"),
    emptyTitle: $("#empty-title"),
    emptyText: $("#empty-text"),
    fileListWrap: $("#file-list-wrap"),
    fileListBody: $("#file-list tbody"),
    numberingHelp: $("#numbering-help"),
    skippedWrap: $("#skipped-wrap"),
    skippedSummary: $("#skipped-summary"),
    skippedList: $("#skipped-list"),
    metaFields: $("#meta-fields"),
    pixelNoise: $("#pixel-noise"),
    strength: $("#noise-strength"),
    strengthOut: $("#noise-strength-out"),
    strengthField: $("#strength-field"),
    startBtn: $("#start-btn"),
    startHelp: $("#start-help"),
    cancelBtn: $("#cancel-btn"),
    progress: $("#progress"),
    progressLabel: $("#progress-label"),
    progressCount: $("#progress-count"),
    progressBar: $("#progress-bar"),
    progressCurrent: $("#progress-current"),
    results: $("#results"),
    resultSummary: $("#result-summary"),
    resultBody: $("#result-table tbody"),
  };

  const STRENGTH_LABELS = { 1: "sutil", 2: "moderada", 3: "maior" };
  const STATUS_LABELS = {
    pending: "Aguardando",
    processing: "Processando…",
    done: "Concluído",
    error: "Falhou",
    skipped: "Não processado",
  };
  const POLL_MS = 600;
  const STORAGE_KEY = "metaedit.folders.v1";

  const state = {
    health: null,
    pickerSupported: false,
    scan: null,        // última resposta de /api/scan
    scanKey: null,     // pastas/opções usadas no último scan
    job: null,
    jobId: null,
    pollTimer: null,
    running: false,
  };

  // ---------- utilidades ----------
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  function detailToMessage(detail, status) {
    if (!detail) return `Erro inesperado do servidor (HTTP ${status}).`;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d) => {
        const field = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        return field ? `${field}: ${d.msg}` : d.msg;
      }).join("; ");
    }
    try { return JSON.stringify(detail); } catch { return String(detail); }
  }

  async function api(path, { method = "GET", body } = {}) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
        cache: "no-store",
      });
    } catch {
      throw new ApiError("Não foi possível conectar ao programa. Verifique se o servidor local ainda está em execução.", 0);
    }
    let data = null;
    const text = await res.text();
    if (text) { try { data = JSON.parse(text); } catch { data = null; } }
    if (!res.ok) {
      throw new ApiError(detailToMessage(data && data.detail, res.status), res.status);
    }
    return data;
  }

  function formatSize(bytes) {
    if (typeof bytes !== "number" || !isFinite(bytes)) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0; let n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toLocaleString("pt-BR", { maximumFractionDigits: i === 0 ? 0 : 1 })} ${units[i]}`;
  }

  function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    for (const c of children) {
      if (c == null) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function showError(message) {
    el.errorText.textContent = message;
    el.errorBanner.hidden = false;
    el.errorBanner.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function clearError() { el.errorBanner.hidden = true; el.errorText.textContent = ""; }

  function setBusy(btn, busy) {
    btn.classList.toggle("is-busy", busy);
    btn.disabled = busy;
    if (busy) btn.setAttribute("aria-busy", "true"); else btn.removeAttribute("aria-busy");
  }

  function loadSaved() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (saved) {
        if (saved.input && !el.inputDir.value) el.inputDir.value = saved.input;
        if (saved.output && !el.outputDir.value) el.outputDir.value = saved.output;
      }
    } catch { /* armazenamento indisponível */ }
  }
  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ input: el.inputDir.value.trim(), output: el.outputDir.value.trim() }));
    } catch { /* ignore */ }
  }

  // ---------- estado do sistema ----------
  function renderChips() {
    el.chips.textContent = "";
    const hl = state.health;
    if (!hl) {
      el.chips.append(h("li", { class: "chip chip-err", text: "Servidor indisponível" }));
      return;
    }
    el.chips.append(h("li", { class: "chip chip-ok", text: "Servidor local ativo" }));
    const ff = hl.ffmpeg || {};
    el.chips.append(h("li", {
      class: `chip ${ff.available ? "chip-ok" : "chip-warn"}`,
      text: ff.available ? "Vídeos: FFmpeg disponível" : "Vídeos indisponíveis (sem FFmpeg)",
      title: ff.available ? (ff.path || "") : (ff.error || "FFmpeg não encontrado"),
    }));
  }

  async function loadHealth() {
    try {
      state.health = await api("/api/health");
      state.pickerSupported = !!(state.health.picker && state.health.picker.supported);
    } catch (e) {
      state.health = null;
      state.pickerSupported = false;
      showError(e.message);
    }
    renderChips();
    applyPickerSupport();
  }

  function applyPickerSupport() {
    for (const btn of [el.pickInput, el.pickOutput]) btn.hidden = !state.pickerSupported;
    el.pickerNote.hidden = state.pickerSupported || !state.health;
  }

  // ---------- seletor de pastas ----------
  async function pickFolder(btn) {
    const input = document.getElementById(btn.dataset.target);
    clearError();
    setBusy(btn, true);
    try {
      const body = { title: btn.dataset.title };
      const current = input.value.trim();
      if (current) body.initial = current;
      const res = await api("/api/dialog/folder", { method: "POST", body });
      if (!res.supported) {
        state.pickerSupported = false;
        applyPickerSupport();
        input.focus();
        return;
      }
      if (res.path) {
        input.value = res.path;
        input.removeAttribute("aria-invalid");
        save();
        invalidateScan();
      }
    } catch (e) {
      showError(`Não foi possível abrir o seletor de pastas: ${e.message} Você pode digitar o caminho manualmente.`);
      el.pickerNote.hidden = false;
    } finally {
      setBusy(btn, false);
      input.focus();
    }
  }

  // ---------- listagem ----------
  function currentScanKey() {
    return JSON.stringify([el.inputDir.value.trim(), el.outputDir.value.trim(), el.recursive.checked]);
  }

  function invalidateScan() {
    if (state.running) return;
    if (state.scan && state.scanKey !== currentScanKey()) {
      state.scan = null;
      state.scanKey = null;
      renderFiles();
      el.emptyTitle.textContent = "Pastas alteradas";
      el.emptyText.innerHTML = "Clique em <strong>Listar arquivos</strong> novamente para atualizar a lista.";
    }
    updateStartState();
  }

  function validateFolders({ requireOutput }) {
    const inp = el.inputDir.value.trim();
    const out = el.outputDir.value.trim();
    el.inputDir.removeAttribute("aria-invalid");
    el.outputDir.removeAttribute("aria-invalid");
    if (!inp) {
      el.inputDir.setAttribute("aria-invalid", "true");
      el.inputDir.focus();
      return "Informe a pasta de entrada.";
    }
    if (requireOutput && !out) {
      el.outputDir.setAttribute("aria-invalid", "true");
      el.outputDir.focus();
      return "Informe a pasta de saída.";
    }
    if (out && out.replace(/\/+$/, "") === inp.replace(/\/+$/, "")) {
      el.outputDir.setAttribute("aria-invalid", "true");
      el.outputDir.focus();
      return "A pasta de saída deve ser diferente da pasta de entrada.";
    }
    return null;
  }

  async function doScan(ev) {
    if (ev) ev.preventDefault();
    if (state.running) return;
    clearError();
    const problem = validateFolders({ requireOutput: false });
    if (problem) { showError(problem); return; }
    save();
    setBusy(el.scanBtn, true);
    const key = currentScanKey();
    try {
      const body = { input_dir: el.inputDir.value.trim(), recursive: el.recursive.checked };
      const out = el.outputDir.value.trim();
      if (out) body.output_dir = out;
      const res = await api("/api/scan", { method: "POST", body });
      state.scan = res;
      state.scanKey = key;
      // mostra o caminho normalizado pelo servidor
      if (res.input_dir) el.inputDir.value = res.input_dir;
      if (res.output_dir) el.outputDir.value = res.output_dir;
      state.scanKey = currentScanKey();
      save();
    } catch (e) {
      state.scan = null;
      state.scanKey = null;
      if (e.status === 400 || e.status === 422) el.inputDir.setAttribute("aria-invalid", "true");
      showError(e.message);
      el.emptyTitle.textContent = "Não foi possível listar a pasta";
      el.emptyText.textContent = "Corrija o problema indicado acima e tente novamente.";
    } finally {
      setBusy(el.scanBtn, false);
      renderFiles();
      updateStartState();
    }
  }

  function renderFiles() {
    const scan = state.scan;
    el.fileListBody.textContent = "";
    el.skippedList.textContent = "";
    el.scanWarnings.textContent = "";

    if (!scan) {
      el.fileListWrap.hidden = true;
      el.numberingHelp.hidden = true;
      el.skippedWrap.hidden = true;
      el.scanWarnings.hidden = true;
      el.emptyState.hidden = false;
      el.fileCounts.textContent = "";
      return;
    }

    const files = scan.files || [];
    const skipped = scan.skipped || [];
    const warnings = scan.warnings || [];
    const counts = scan.counts || {};
    const start = Number.isInteger(scan.next_index) ? scan.next_index : 1;

    el.fileCounts.textContent = files.length
      ? `${plural(counts.image ?? 0, "imagem", "imagens")} · ${plural(counts.video ?? 0, "vídeo", "vídeos")}`
        + (skipped.length ? ` · ${plural(skipped.length, "ignorado", "ignorados")}` : "")
      : "";

    if (warnings.length) {
      const ul = h("ul");
      for (const w of warnings) ul.append(h("li", { text: w }));
      el.scanWarnings.append(h("strong", { text: "Atenção" }), ul);
      el.scanWarnings.hidden = false;
    } else {
      el.scanWarnings.hidden = true;
    }

    if (!files.length) {
      el.fileListWrap.hidden = true;
      el.numberingHelp.hidden = true;
      el.emptyState.hidden = false;
      el.emptyTitle.textContent = "Nenhuma imagem ou vídeo compatível";
      const fmts = state.health && state.health.formats
        ? [...(state.health.formats.image || []), ...(state.health.formats.video || [])].join(" ")
        : "";
      el.emptyText.textContent = skipped.length
        ? `A pasta tem ${plural(skipped.length, "arquivo", "arquivos")}, mas nenhum em formato suportado.${fmts ? " Formatos aceitos: " + fmts : ""}`
        : `A pasta está vazia.${el.recursive.checked ? "" : " Se os arquivos estiverem em subpastas, marque “Incluir subpastas”."}`;
    } else {
      el.emptyState.hidden = true;
      el.fileListWrap.hidden = false;
      el.numberingHelp.hidden = false;
      const frag = document.createDocumentFragment();
      files.forEach((f, i) => {
        const ext = (f.ext || "").toLowerCase();
        frag.append(h("tr", {},
          h("td", { class: "num", text: i + 1 }),
          h("td", { class: "name", text: f.rel_path || f.name, title: f.rel_path || f.name }),
          h("td", {}, h("span", { class: `tag tag-${f.kind === "video" ? "video" : "image"}`, text: f.kind === "video" ? "Vídeo" : "Imagem" })),
          h("td", { class: "num hide-sm", text: formatSize(f.size) }),
          h("td", { class: "out", text: `${start + i}${ext}` }),
        ));
      });
      el.fileListBody.append(frag);
    }

    if (skipped.length) {
      el.skippedSummary.textContent = `${plural(skipped.length, "arquivo ignorado", "arquivos ignorados")} (ver motivos)`;
      const frag = document.createDocumentFragment();
      for (const s of skipped) frag.append(h("li", {}, h("span", { class: "mono", text: s.rel_path || s.name }), ` — ${s.reason}`));
      el.skippedList.append(frag);
      el.skippedWrap.hidden = false;
    } else {
      el.skippedWrap.hidden = true;
    }
  }

  function updateStartState() {
    if (state.running) {
      el.startBtn.disabled = true;
      el.startHelp.textContent = "Transformação em andamento…";
      return;
    }
    const hasFiles = !!(state.scan && state.scan.files && state.scan.files.length && state.scanKey === currentScanKey());
    const hasOut = !!el.outputDir.value.trim();
    el.startBtn.disabled = !(hasFiles && hasOut);
    if (!state.scan) el.startHelp.textContent = "Liste os arquivos primeiro.";
    else if (!hasFiles) el.startHelp.textContent = "Não há arquivos compatíveis para processar.";
    else if (!hasOut) el.startHelp.textContent = "Informe a pasta de saída.";
    else el.startHelp.textContent = `${plural(state.scan.files.length, "cópia será criada", "cópias serão criadas")} em ${el.outputDir.value.trim()}`;
  }

  // ---------- opções ----------
  function metadataMode() {
    const r = document.querySelector('input[name="metadata_mode"]:checked');
    return r ? r.value : "strip";
  }
  function syncOptions() {
    el.metaFields.hidden = metadataMode() !== "replace";
    const v = Number(el.strength.value);
    el.strengthOut.textContent = `${v} · ${STRENGTH_LABELS[v] || ""}`;
    el.strength.setAttribute("aria-valuetext", `${v}, ${STRENGTH_LABELS[v] || ""}`);
    el.strength.disabled = !el.pixelNoise.checked;
    el.strengthField.classList.toggle("is-disabled", !el.pixelNoise.checked);
  }
  function collectMetadata() {
    const meta = {};
    document.querySelectorAll("[data-meta]").forEach((inp) => { meta[inp.dataset.meta] = inp.value.trim(); });
    return meta;
  }

  // ---------- execução ----------
  function setRunning(running) {
    state.running = running;
    el.cancelBtn.hidden = !running;
    el.cancelBtn.disabled = false;
    for (const node of [el.inputDir, el.outputDir, el.recursive, el.scanBtn, el.pickInput, el.pickOutput]) node.disabled = running;
    document.querySelectorAll("#options-form input").forEach((n) => { n.disabled = running; });
    if (!running) syncOptions();
    updateStartState();
  }

  async function startJob() {
    clearError();
    const problem = validateFolders({ requireOutput: true });
    if (problem) { showError(problem); return; }
    if (state.scanKey !== currentScanKey()) { showError("As pastas mudaram desde a última listagem. Clique em “Listar arquivos” novamente."); return; }
    const mode = metadataMode();
    const body = {
      input_dir: el.inputDir.value.trim(),
      output_dir: el.outputDir.value.trim(),
      recursive: el.recursive.checked,
      metadata_mode: mode,
      pixel_noise: el.pixelNoise.checked,
      noise_strength: Number(el.strength.value),
    };
    if (mode === "replace") body.metadata = collectMetadata();

    setBusy(el.startBtn, true);
    el.results.hidden = true;
    try {
      const res = await api("/api/jobs", { method: "POST", body });
      state.jobId = res.job_id;
      state.job = { status: res.status || "queued", total: res.total || 0, processed: 0, results: [] };
      setBusy(el.startBtn, false);
      setRunning(true);
      el.progress.hidden = false;
      renderProgress();
      el.cancelBtn.focus();
      poll();
    } catch (e) {
      setBusy(el.startBtn, false);
      showError(e.message);
      updateStartState();
    }
  }

  async function poll() {
    clearTimeout(state.pollTimer);
    if (!state.jobId) return;
    try {
      state.job = await api(`/api/jobs/${encodeURIComponent(state.jobId)}`);
    } catch (e) {
      if (e.status === 404) {
        finishJob();
        showError("O processamento não foi encontrado no servidor (talvez o programa tenha sido reiniciado). Verifique a pasta de saída e tente novamente.");
        return;
      }
      if (e.status === 0) {
        // falha de conexão: tenta de novo um pouco depois, avisando
        el.progressCurrent.textContent = "Sem resposta do servidor local; tentando novamente…";
        state.pollTimer = setTimeout(poll, POLL_MS * 3);
        return;
      }
      showError(e.message);
      state.pollTimer = setTimeout(poll, POLL_MS * 3);
      return;
    }
    renderProgress();
    if (["done", "error", "cancelled"].includes(state.job.status)) {
      finishJob();
    } else {
      state.pollTimer = setTimeout(poll, POLL_MS);
    }
  }

  function renderProgress() {
    const job = state.job || {};
    const total = job.total || 0;
    const processed = job.processed || 0;
    const partial = job.status === "running" && typeof job.current_progress === "number"
      ? Math.min(Math.max(job.current_progress, 0), 1) : 0;
    const pct = total ? Math.min(100, Math.round(((processed + partial) / total) * 100)) : 0;
    el.progressBar.style.width = `${job.status === "done" ? 100 : pct}%`;
    el.progressBar.setAttribute("aria-valuenow", String(job.status === "done" ? 100 : pct));
    el.progressBar.setAttribute("aria-valuetext", `${processed} de ${total} arquivos`);
    el.progressCount.textContent = `${processed} / ${total}`;
    el.progress.classList.toggle("is-done", job.status === "done" && !job.failed);
    el.progress.classList.toggle("is-error", job.status === "error");
    const labels = {
      queued: "Na fila…",
      running: `Processando… ${pct}%`,
      done: job.failed ? "Concluído com falhas" : "Concluído",
      error: "Interrompido por erro",
      cancelled: "Cancelado",
    };
    el.progressLabel.textContent = labels[job.status] || "Processando…";
    el.progressCurrent.textContent = job.status === "running" && job.current ? `Arquivo atual: ${job.current}` : "";
  }

  function finishJob() {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
    setRunning(false);
    const job = state.job;
    if (job) renderResults(job);
    // a próxima numeração mudou: força nova listagem antes de outra execução
    state.scanKey = null;
    updateStartState();
    if (state.scan) el.startHelp.textContent = "Para processar novamente, clique em “Listar arquivos” para atualizar a numeração.";
  }

  function renderResults(job) {
    el.resultSummary.textContent = "";
    el.resultBody.textContent = "";
    const results = job.results || [];
    const ok = job.succeeded ?? results.filter((r) => r.status === "done").length;
    const failed = job.failed ?? results.filter((r) => r.status === "error").length;
    const notDone = results.filter((r) => r.status === "skipped" || r.status === "pending").length;

    if (job.status === "error") {
      showError(`O processamento foi interrompido: ${job.error || "erro desconhecido."}`);
    }

    const stats = h("div", { class: "stats" },
      h("div", { class: "stat stat-ok" }, h("b", { text: ok }), h("span", { text: ok === 1 ? "cópia criada" : "cópias criadas" })),
      h("div", { class: "stat stat-err" }, h("b", { text: failed }), h("span", { text: failed === 1 ? "falha" : "falhas" })),
      h("div", { class: "stat" }, h("b", { text: notDone }), h("span", { text: "não processados" })),
    );
    const headline = job.status === "cancelled" ? "Processamento cancelado."
      : job.status === "error" ? "Processamento interrompido."
      : failed ? "Processamento concluído, mas alguns arquivos falharam (veja os motivos abaixo)."
      : "Processamento concluído com sucesso.";
    el.resultSummary.append(
      h("p", { class: "out-path", text: headline }),
      stats,
      h("p", { class: "out-path muted" }, "Pasta de saída: ", h("code", { text: job.output_dir || el.outputDir.value.trim() })),
    );

    const frag = document.createDocumentFragment();
    for (const r of results) {
      const st = r.status || (r.ok ? "done" : r.ok === false ? "error" : "pending");
      const stCell = h("td", {}, h("span", { class: `st st-${st}`, text: STATUS_LABELS[st] || st }));
      if (r.error) stCell.append(h("span", { class: "err-msg", text: r.error }));
      frag.append(h("tr", {},
        h("td", { class: "name", text: r.source }),
        h("td", { class: "out", text: r.output || "—" }),
        stCell,
      ));
    }
    el.resultBody.append(frag);
    el.results.hidden = false;
    el.results.focus({ preventScroll: true });
    el.results.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function cancelJob() {
    if (!state.jobId) return;
    el.cancelBtn.disabled = true;
    el.progressLabel.textContent = "Cancelando… (o arquivo atual será concluído ou abortado)";
    try {
      await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, { method: "POST", body: {} });
    } catch (e) {
      showError(`Não foi possível cancelar: ${e.message}`);
      el.cancelBtn.disabled = false;
    }
    poll();
  }

  // ---------- eventos ----------
  el.errorClose.addEventListener("click", clearError);
  el.foldersForm.addEventListener("submit", doScan);
  el.pickInput.addEventListener("click", () => pickFolder(el.pickInput));
  el.pickOutput.addEventListener("click", () => pickFolder(el.pickOutput));
  for (const inp of [el.inputDir, el.outputDir]) {
    inp.addEventListener("input", () => { inp.removeAttribute("aria-invalid"); invalidateScan(); });
    inp.addEventListener("change", save);
  }
  el.recursive.addEventListener("change", invalidateScan);
  document.querySelectorAll('input[name="metadata_mode"]').forEach((r) => r.addEventListener("change", syncOptions));
  el.pixelNoise.addEventListener("change", syncOptions);
  el.strength.addEventListener("input", syncOptions);
  el.startBtn.addEventListener("click", startJob);
  el.cancelBtn.addEventListener("click", cancelJob);
  window.addEventListener("beforeunload", (e) => {
    if (state.running) { e.preventDefault(); e.returnValue = ""; }
  });

  loadSaved();
  syncOptions();
  updateStartState();
  loadHealth();
})();
