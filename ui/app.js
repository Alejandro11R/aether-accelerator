/**
 * AETHER ACCELERATOR — FRONTEND CLIENT (Phase 2A)
 * Renderizado incremental DOM, throttling en vivo, banda segmentada inline y modales accesibles.
 */

document.addEventListener("DOMContentLoaded", () => {
  // --- 0. CONSTANTES Y ESTADO LOCAL ---
  const ALLOWED_THEMES = new Set(["obsidian", "cyber", "oled"]);
  const ALLOWED_STATUSES = new Set([
    "initializing",
    "downloading",
    "paused",
    "completed",
    "error",
  ]);
  const ALLOWED_TOAST_VARIANTS = new Set(["info", "success", "warning", "error"]);

  const THREAD_OPTIONS = [
    { value: "4", label: "4 Hilos (Ligero)" },
    { value: "8", label: "8 Hilos (Recomendado)", selected: true },
    { value: "16", label: "16 Hilos (Ultra Rápido)" },
    { value: "24", label: "24 Hilos (Fibra 1Gbps+)" },
    { value: "32", label: "32 Hilos (Máxima Saturación)" },
  ];

  const SPEED_LIMIT_OPTIONS = [
    { value: "0", label: "⚡ Ilimitado (Máxima red)", selected: true },
    { value: "1048576", label: "1 MB/s (Bajo consumo)" },
    { value: "5242880", label: "5 MB/s (Para jugar/navegar)" },
    { value: "10485760", label: "10 MB/s (Streaming fluido)" },
    { value: "20971520", label: "20 MB/s (Rápido)" },
  ];

  const ROW_LIMIT_OPTIONS = [
    { value: "0", label: "⚡ Ilimitado", selected: true },
    { value: "1048576", label: "1 MB/s" },
    { value: "5242880", label: "5 MB/s" },
    { value: "10485760", label: "10 MB/s" },
    { value: "20971520", label: "20 MB/s" },
  ];
  const ROW_LIMIT_PRESET_VALUES = new Set(ROW_LIMIT_OPTIONS.map((o) => o.value));

  let downloads = [];
  const rowMap = new Map();
  const expandedTaskIds = new Set();
  let pendingDeleteTaskId = null;
  let quickFoldersList = [];
  let wsConnected = false;
  let pendingSparklineRedraw = false;
  let rowSeq = 0;

  let ws = null;
  let reconnectTimer = null;
  let defaultDownloadDir = "";
  let emptyStateCard = null;
  let quickInlineRefs = null;
  let lastFocusedBeforeModal = null;
  const speedHistory = new Array(30).fill(0);

  // --- ELEMENTOS DOM ---
  const liveIndicatorEl = document.getElementById("live-indicator");
  const totalSpeedEl = document.getElementById("total-speed");
  const totalSpeedUnitEl = document.getElementById("total-speed-unit");
  const totalThreadsEl = document.getElementById("total-threads");
  const activeTasksLabelEl = document.getElementById("active-tasks-label");
  const downloadDirTextEl = document.getElementById("download-dir-text");
  const btnOpenFolder = document.getElementById("btn-open-folder");
  const downloadsCountEl = document.getElementById("downloads-count");
  const downloadsListEl = document.getElementById("downloads-list");
  const toastContainerEl = document.getElementById("toast-container");

  // Sparkline
  const sparklineLine = document.getElementById("sparkline-line");
  const sparklineArea = document.getElementById("sparkline-area");

  // Modal Nueva Descarga
  const modalNewDownload = document.getElementById("modal-new-download");
  const btnOpenModal = document.getElementById("btn-open-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const btnCancelModal = document.getElementById("btn-cancel-modal");
  const formNewDownload = document.getElementById("form-new-download");
  const inputUrl = document.getElementById("input-url");
  const inputFilename = document.getElementById("input-filename");
  const inputDir = document.getElementById("input-dir");
  const quickFolders = document.getElementById("quick-folders");
  const selectThreads = document.getElementById("select-threads");
  const selectSpeedLimit = document.getElementById("select-speed-limit");
  const btnPaste = document.getElementById("btn-paste");

  // Modal Confirmación de Eliminación
  const modalDeleteDownload = document.getElementById("modal-delete-download");
  const btnCloseDeleteModal = document.getElementById("btn-close-delete-modal");
  const btnCancelDeleteModal = document.getElementById("btn-cancel-delete-modal");
  const deleteModalFilenameEl = document.getElementById("delete-modal-filename");
  const checkboxDeleteFileEl = document.getElementById("checkbox-delete-file");
  const btnConfirmDelete = document.getElementById("btn-confirm-delete");

  // --- 1. GESTOR DE TEMAS DIAL ---
  const rawSavedTheme = localStorage.getItem("aether-theme") || "obsidian";
  const savedTheme = ALLOWED_THEMES.has(rawSavedTheme) ? rawSavedTheme : "obsidian";
  document.documentElement.setAttribute("data-theme", savedTheme);
  updateDialButtons(savedTheme);

  document.querySelectorAll(".dial-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rawTheme = btn.dataset ? btn.dataset.setTheme : "obsidian";
      const theme = ALLOWED_THEMES.has(rawTheme) ? rawTheme : "obsidian";
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("aether-theme", theme);
      updateDialButtons(theme);
    });
  });

  function updateDialButtons(currentTheme) {
    document.querySelectorAll(".dial-btn").forEach((btn) => {
      const isActive = btn.dataset && btn.dataset.setTheme === currentTheme;
      btn.classList.toggle("active", Boolean(isActive));
      btn.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
  }

  // --- 2. SEGURIDAD & HELPERS DOM ---
  function safeStatusClass(status) {
    return ALLOWED_STATUSES.has(status) ? status : "initializing";
  }

  function safePercent(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return 0;
    return Math.max(0, Math.min(100, num));
  }

  function createSvgIcon(width, height, fill, stroke, strokeWidth, childrenSpecs) {
    const svgNs = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNs, "svg");
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", fill);
    if (stroke) svg.setAttribute("stroke", stroke);
    if (strokeWidth != null) svg.setAttribute("stroke-width", String(strokeWidth));
    svg.setAttribute("aria-hidden", "true");
    childrenSpecs.forEach(([tag, attrs]) => {
      const child = document.createElementNS(svgNs, tag);
      Object.entries(attrs).forEach(([key, val]) => {
        child.setAttribute(key, String(val));
      });
      svg.appendChild(child);
    });
    return svg;
  }

  function removeNode(parent, child) {
    if (!child) return;
    if (typeof child.remove === "function") {
      child.remove();
    } else if (parent && typeof parent.removeChild === "function") {
      parent.removeChild(child);
    } else if (parent && Array.isArray(parent.children)) {
      parent.children = parent.children.filter((node) => node !== child);
    }
  }

  function placeChildAt(parent, child, index) {
    if (!parent || !child) return;
    const currentAtIndex = parent.children[index];
    if (currentAtIndex === child) return;
    if (typeof parent.insertBefore === "function") {
      parent.insertBefore(child, currentAtIndex || null);
      return;
    }
    if (Array.isArray(parent.children)) {
      const existingIdx = parent.children.indexOf(child);
      if (existingIdx !== -1) {
        parent.children.splice(existingIdx, 1);
      }
      parent.children.splice(Math.min(index, parent.children.length), 0, child);
      return;
    }
    parent.appendChild(child);
  }

  function setElementVisible(el, visible) {
    if (!el) return;
    if (visible) {
      el.hidden = false;
      if (typeof el.removeAttribute === "function") {
        el.removeAttribute("hidden");
      }
      el.style.display = "";
    } else {
      el.hidden = true;
      el.setAttribute("hidden", "");
      el.style.display = "none";
    }
  }

  function populateSelectOptions(selectEl, options) {
    selectEl.innerHTML = "";
    options.forEach((opt) => {
      const option = document.createElement("option");
      option.value = opt.value;
      option.textContent = opt.label;
      if (opt.selected) {
        option.selected = true;
        selectEl.value = opt.value;
      }
      selectEl.appendChild(option);
    });
  }

  function upsertDownloadTask(task) {
    if (!task || !task.id) return false;
    const idx = downloads.findIndex((d) => d.id === task.id);
    if (idx !== -1) {
      downloads[idx] = task;
    } else {
      downloads.unshift(task);
    }
    return true;
  }

  async function apiRequest(url, options, fallbackError) {
    const resp = await fetch(url, options);
    const data = await resp.json().catch(() => ({}));
    const isHttpOk = resp && (resp.ok === undefined || resp.ok);
    if (!isHttpOk || !data || !data.success) {
      return { ok: false, error: (data && data.error) || fallbackError, data };
    }
    return { ok: true, error: null, data };
  }

  function wireClipboardPaste(buttonEl, targetInput, focusAfter = false) {
    if (!buttonEl || !targetInput) return;
    buttonEl.addEventListener("click", async () => {
      try {
        if (!navigator.clipboard || !navigator.clipboard.readText) {
          showToast("Portapapeles no disponible. Pega manualmente con Ctrl+V.", "warning");
          return;
        }
        const text = await navigator.clipboard.readText();
        if (text) {
          targetInput.value = text.trim();
          if (focusAfter && typeof targetInput.focus === "function") {
            targetInput.focus();
          }
        }
      } catch (_) {
        showToast("Por favor permite el acceso al portapapeles o pega con Ctrl+V.", "warning");
      }
    });
  }

  function wireQuickFolderSelect(selectEl, targetInput) {
    if (!selectEl || !targetInput) return;
    selectEl.addEventListener("change", () => {
      if (selectEl.value) {
        targetInput.value = selectEl.value;
      }
    });
  }

  function jsonParse(str) {
    return JSON.parse(str);
  }

  function formatBytes(bytes) {
    const num = Number(bytes);
    if (!Number.isFinite(num) || num <= 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(sizes.length - 1, Math.floor(Math.log(num) / Math.log(k)));
    return parseFloat((num / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  }

  function formatSpeed(bytesPerSec) {
    const num = Number(bytesPerSec);
    if (!Number.isFinite(num) || num <= 0) return { val: "0.0", unit: "KB/s" };
    const mb = num / (1024 * 1024);
    if (mb >= 1) {
      return { val: mb.toFixed(2), unit: "MB/s" };
    }
    const kb = num / 1024;
    return { val: kb.toFixed(1), unit: "KB/s" };
  }

  function formatEta(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) {
      return "Calculando...";
    }
    const secNum = Math.floor(Number(seconds));
    if (secNum === 0) return "Completado";
    const mins = Math.floor(secNum / 60);
    const secs = secNum % 60;
    if (mins >= 60) {
      const hours = Math.floor(mins / 60);
      return `ETA: ${hours}h ${mins % 60}m`;
    }
    return `ETA: ${mins}m ${secs}s`;
  }

  function formatTaskEtaText(task, safeStatus) {
    if (safeStatus === "completed") return "Completado";
    if (safeStatus === "paused") return "Pausado";
    if (safeStatus === "error") return "Error";
    return formatEta(task.eta_seconds);
  }

  // --- 3. NOTIFICACIONES TOAST ACCESIBLES ---
  function showToast(message, variant = "info", durationMs = 4500) {
    if (!toastContainerEl) return;
    const safeVariant = ALLOWED_TOAST_VARIANTS.has(variant) ? variant : "info";
    const toast = document.createElement("div");
    toast.className = safeVariant === "info" ? "toast" : `toast toast-${safeVariant}`;
    toast.setAttribute("role", safeVariant === "error" ? "alert" : "status");
    toast.textContent = message == null ? "" : String(message);

    toast.addEventListener("click", () => {
      removeNode(toastContainerEl, toast);
    });

    toastContainerEl.appendChild(toast);

    while (toastContainerEl.children.length > 5) {
      removeNode(toastContainerEl, toastContainerEl.children[0]);
    }

    if (durationMs > 0 && typeof setTimeout === "function") {
      setTimeout(() => {
        removeNode(toastContainerEl, toast);
      }, durationMs);
    }
  }

  // --- 4. ESTADO EN VIVO WEBSOCKET & SPARKLINE ---
  function updateLiveStatus(connected) {
    wsConnected = Boolean(connected);
    if (!liveIndicatorEl) return;
    liveIndicatorEl.classList.toggle("connected", wsConnected);
    liveIndicatorEl.classList.toggle("disconnected", !wsConnected);
    const titleText = wsConnected
      ? "Conectado en tiempo real con el motor Aether"
      : "Desconectado del motor. Reintentando...";
    liveIndicatorEl.title = titleText;
    liveIndicatorEl.setAttribute("title", titleText);
  }

  function populateQuickFoldersSelect(selectEl, folders) {
    if (!selectEl) return;
    const previousValue = selectEl.value || "";
    selectEl.innerHTML = "";

    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Rutas rápidas...";
    selectEl.appendChild(placeholder);

    if (!Array.isArray(folders)) return;

    folders.forEach((folder) => {
      if (!folder || typeof folder !== "object") return;
      const path = typeof folder.path === "string" ? folder.path : "";
      const label = typeof folder.label === "string" && folder.label ? folder.label : path;
      if (!path) return;

      const option = document.createElement("option");
      option.value = path;
      option.textContent = label;
      selectEl.appendChild(option);
    });

    if (previousValue && folders.some((f) => f && f.path === previousValue)) {
      selectEl.value = previousValue;
    } else {
      selectEl.value = "";
    }
  }

  function refreshQuickFolderSelects() {
    populateQuickFoldersSelect(quickFolders, quickFoldersList);
    if (quickInlineRefs && quickInlineRefs.quickFoldersSelect) {
      populateQuickFoldersSelect(quickInlineRefs.quickFoldersSelect, quickFoldersList);
    }
  }

  function connectWebSocket() {
    if (reconnectTimer && typeof clearTimeout === "function") {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      updateLiveStatus(true);
      console.log("[Aether WS] Conectado al motor.");
    };

    ws.onmessage = (event) => {
      try {
        const msg = jsonParse(event.data);
        handleWsMessage(msg);
      } catch (e) {
        console.error("Error al procesar mensaje WS", e);
      }
    };

    ws.onclose = () => {
      updateLiveStatus(false);
      console.warn("[Aether WS] Desconectado. Reintentando en 2s...");
      if (!reconnectTimer && typeof setTimeout === "function") {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connectWebSocket();
        }, 2000);
      }
    };

    ws.onerror = (err) => {
      updateLiveStatus(false);
      console.error("[Aether WS] Error:", err);
      try {
        ws.close();
      } catch (_) {}
    };
  }

  function handleWsMessage(msg) {
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "init") {
      downloads = Array.isArray(msg.downloads) ? msg.downloads : [];
      if (typeof msg.download_dir === "string" && msg.download_dir) {
        defaultDownloadDir = msg.download_dir;
        if (downloadDirTextEl) {
          downloadDirTextEl.textContent = msg.download_dir;
        }
        const preferredDir = localStorage.getItem("aether-dir") || defaultDownloadDir;
        if (inputDir && !inputDir.value) {
          inputDir.value = preferredDir;
        }
        if (quickInlineRefs && quickInlineRefs.dirInput && !quickInlineRefs.dirInput.value) {
          quickInlineRefs.dirInput.value = preferredDir;
        }
      }
      if (Array.isArray(msg.quick_folders)) {
        quickFoldersList = msg.quick_folders;
        refreshQuickFolderSelects();
      }
      renderAll();
    } else if (msg.type === "task_added" || msg.type === "task_updated" || msg.type === "task_progress") {
      if (upsertDownloadTask(msg.task)) {
        renderAll();
      }
    } else if (msg.type === "task_removed") {
      downloads = downloads.filter((d) => d.id !== msg.task_id);
      expandedTaskIds.delete(msg.task_id);
      if (pendingDeleteTaskId === msg.task_id) {
        closeDeleteModal();
      }
      renderAll();
    } else if (msg.type === "error") {
      showToast(msg.message || msg.error || "Error del servidor.", "error");
    }
  }

  function updateMetrics() {
    const activeTasks = downloads.filter((d) => d.status === "downloading");
    const totalSpeed = activeTasks.reduce((acc, d) => acc + (Number(d.speed_bps) || 0), 0);
    const totalConnections = activeTasks.reduce((acc, d) => acc + (Number(d.connections) || 0), 0);

    const speedFmt = formatSpeed(totalSpeed);
    if (totalSpeedEl) totalSpeedEl.textContent = speedFmt.val;
    if (totalSpeedUnitEl) totalSpeedUnitEl.textContent = speedFmt.unit;
    if (totalThreadsEl) totalThreadsEl.textContent = String(totalConnections);
    if (activeTasksLabelEl) {
      activeTasksLabelEl.textContent = `${activeTasks.length} ${activeTasks.length === 1 ? "descarga activa" : "descargas activas"}`;
    }
    if (downloadsCountEl) {
      downloadsCountEl.textContent = String(downloads.length);
    }

    speedHistory.shift();
    speedHistory.push(totalSpeed);

    if (document.hidden) {
      pendingSparklineRedraw = true;
      return;
    }
    drawSparkline();
  }

  function drawSparkline() {
    if (document.hidden) {
      pendingSparklineRedraw = true;
      return;
    }
    pendingSparklineRedraw = false;
    if (!sparklineLine || !sparklineArea) return;

    const max = Math.max(...speedHistory, 1024 * 1024);
    const width = 300;
    const height = 50;
    const step = width / (speedHistory.length - 1);

    const points = speedHistory.map((val, idx) => {
      const x = idx * step;
      const y = height - (val / max) * (height - 6) - 3;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });

    const linePath = `M ${points.join(" L ")}`;
    const areaPath = `M 0,${height} L ${points.join(" L ")} L ${width},${height} Z`;

    sparklineLine.setAttribute("d", linePath);
    sparklineArea.setAttribute("d", areaPath);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && pendingSparklineRedraw) {
      drawSparkline();
    }
  });

  // --- 5. ENVÍO DE DESCARGAS (COMPARTIDO MODAL / INLINE) ---
  async function submitDownloadRequest({
    url,
    threads,
    speedLimit,
    filename,
    customDir,
    submitBtn,
    onSuccess,
  }) {
    const trimmedUrl = (url || "").trim();
    if (!trimmedUrl) return;

    const parsedThreads = parseInt(threads, 10);
    const parsedLimit = parseInt(speedLimit, 10);
    const cleanFilename = (filename || "").trim();
    const cleanDir = (customDir || "").trim();

    if (cleanDir) {
      localStorage.setItem("aether-dir", cleanDir);
      if (inputDir) inputDir.value = cleanDir;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
      const result = await apiRequest(
        "/api/downloads",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: trimmedUrl,
            threads: Number.isFinite(parsedThreads) ? parsedThreads : 8,
            filename: cleanFilename,
            dir: cleanDir,
            speed_limit_bps: Number.isFinite(parsedLimit) && parsedLimit >= 0 ? parsedLimit : 0,
          }),
        },
        "No se pudo iniciar la descarga."
      );
      if (result.ok && result.data && result.data.success) {
        if (typeof onSuccess === "function") onSuccess(result.data);
      } else {
        showToast(result.error || "No se pudo iniciar la descarga.", "error");
      }
    } catch (err) {
      showToast("Error al conectar con el servidor: " + err.message, "error");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  // --- 6. ESTADO VACÍO ACCIONABLE (#form-quick-download) ---
  function createQuickEmptyCard() {
    const card = document.createElement("div");
    card.className = "quick-empty-card glass-card";

    const header = document.createElement("div");
    header.className = "quick-empty-header";

    const headerText = document.createElement("div");
    const heading = document.createElement("h3");
    heading.textContent = "No hay descargas activas";
    const desc = document.createElement("p");
    desc.textContent =
      "Pega un enlace directo para acelerar tu descarga con segmentación concurrente.";
    headerText.appendChild(heading);
    headerText.appendChild(desc);
    header.appendChild(headerText);

    const form = document.createElement("form");
    form.id = "form-quick-download";
    form.className = "quick-form";

    const mainRow = document.createElement("div");
    mainRow.className = "quick-form-row";

    const urlInput = document.createElement("input");
    urlInput.type = "url";
    urlInput.id = "quick-url";
    urlInput.placeholder = "https://servidor.com/archivo-pesado.iso";
    urlInput.required = true;
    urlInput.setAttribute("required", "");
    urlInput.setAttribute("autocomplete", "off");

    const pasteBtn = document.createElement("button");
    pasteBtn.type = "button";
    pasteBtn.id = "btn-quick-paste";
    pasteBtn.className = "btn-secondary";
    pasteBtn.title = "Pegar del portapapeles";
    pasteBtn.textContent = "Pegar";

    const submitBtn = document.createElement("button");
    submitBtn.type = "submit";
    submitBtn.className = "btn-primary glow-button";
    submitBtn.appendChild(
      createSvgIcon(16, 16, "none", "currentColor", 2, [
        ["polygon", { points: "13 2 3 14 12 14 11 22 21 10 12 10 13 2" }],
      ])
    );
    const submitLabel = document.createElement("span");
    submitLabel.textContent = "Descargar";
    submitBtn.appendChild(submitLabel);

    mainRow.appendChild(urlInput);
    mainRow.appendChild(pasteBtn);
    mainRow.appendChild(submitBtn);

    const advancedDetails = document.createElement("details");
    advancedDetails.className = "quick-advanced";

    const advancedSummary = document.createElement("summary");
    advancedSummary.textContent = "Opciones avanzadas (hilos, límite y carpeta)";
    advancedDetails.appendChild(advancedSummary);

    const advancedGrid = document.createElement("div");
    advancedGrid.className = "quick-advanced-grid";

    // Hilos
    const threadsGroup = document.createElement("div");
    threadsGroup.className = "form-group";
    const threadsLabel = document.createElement("label");
    threadsLabel.setAttribute("for", "quick-threads");
    threadsLabel.textContent = "Hilos Paralelos";
    const threadsSelect = document.createElement("select");
    threadsSelect.id = "quick-threads";
    threadsSelect.className = "custom-select";
    populateSelectOptions(threadsSelect, THREAD_OPTIONS);
    threadsGroup.appendChild(threadsLabel);
    threadsGroup.appendChild(threadsSelect);

    // Límite
    const limitGroup = document.createElement("div");
    limitGroup.className = "form-group";
    const limitLabel = document.createElement("label");
    limitLabel.setAttribute("for", "quick-speed-limit");
    limitLabel.textContent = "Límite de Ancho de Banda";
    const limitSelect = document.createElement("select");
    limitSelect.id = "quick-speed-limit";
    limitSelect.className = "custom-select";
    populateSelectOptions(limitSelect, SPEED_LIMIT_OPTIONS);
    limitGroup.appendChild(limitLabel);
    limitGroup.appendChild(limitSelect);

    // Nombre
    const filenameGroup = document.createElement("div");
    filenameGroup.className = "form-group";
    const filenameLabel = document.createElement("label");
    filenameLabel.setAttribute("for", "quick-filename");
    filenameLabel.textContent = "Nombre Personalizado (Opcional)";
    const filenameInput = document.createElement("input");
    filenameInput.type = "text";
    filenameInput.id = "quick-filename";
    filenameInput.placeholder = "Detectar automáticamente";
    filenameGroup.appendChild(filenameLabel);
    filenameGroup.appendChild(filenameInput);

    // Carpeta
    const dirGroup = document.createElement("div");
    dirGroup.className = "form-group";
    const dirLabel = document.createElement("label");
    dirLabel.setAttribute("for", "quick-dir");
    dirLabel.textContent = "Carpeta de Destino";
    const dirRow = document.createElement("div");
    dirRow.className = "input-with-action";
    const dirInput = document.createElement("input");
    dirInput.type = "text";
    dirInput.id = "quick-dir";
    dirInput.placeholder = "Carpeta de destino predeterminada";
    dirInput.value = localStorage.getItem("aether-dir") || defaultDownloadDir || "";

    const quickFoldersInline = document.createElement("select");
    quickFoldersInline.id = "quick-folders-inline";
    quickFoldersInline.className = "custom-select quick-folders-select";
    quickFoldersInline.setAttribute("aria-label", "Rutas rápidas");
    populateQuickFoldersSelect(quickFoldersInline, quickFoldersList);

    dirRow.appendChild(dirInput);
    dirRow.appendChild(quickFoldersInline);
    dirGroup.appendChild(dirLabel);
    dirGroup.appendChild(dirRow);

    advancedGrid.appendChild(threadsGroup);
    advancedGrid.appendChild(limitGroup);
    advancedGrid.appendChild(filenameGroup);
    advancedGrid.appendChild(dirGroup);
    advancedDetails.appendChild(advancedGrid);

    form.appendChild(mainRow);
    form.appendChild(advancedDetails);

    card.appendChild(header);
    card.appendChild(form);

    // Listeners inline
    wireClipboardPaste(pasteBtn, urlInput, true);
    wireQuickFolderSelect(quickFoldersInline, dirInput);

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      await submitDownloadRequest({
        url: urlInput.value,
        threads: threadsSelect.value,
        speedLimit: limitSelect.value,
        filename: filenameInput.value,
        customDir: dirInput.value,
        submitBtn,
        onSuccess: () => {
          urlInput.value = "";
          filenameInput.value = "";
        },
      });
    });

    quickInlineRefs = {
      card,
      form,
      urlInput,
      threadsSelect,
      limitSelect,
      filenameInput,
      dirInput,
      quickFoldersSelect: quickFoldersInline,
      submitBtn,
    };

    return card;
  }

  // --- 7. RENDERIZADO INCREMENTAL DE FILAS & DRAWER INLINE ---
  function syncLimitSelectOptions(refs, taskLimit) {
    const limitStr = String(taskLimit);
    const hasCustom = !ROW_LIMIT_PRESET_VALUES.has(limitStr);

    if (hasCustom) {
      const fmt = formatSpeed(taskLimit);
      const customLabel = `${fmt.val} ${fmt.unit} (Actual)`;
      if (!refs.customLimitOption) {
        const opt = document.createElement("option");
        refs.customLimitOption = opt;
        refs.limitSelect.appendChild(opt);
      }
      refs.customLimitOption.value = limitStr;
      refs.customLimitOption.textContent = customLabel;
    } else if (refs.customLimitOption) {
      removeNode(refs.limitSelect, refs.customLimitOption);
      refs.customLimitOption = null;
    }

    refs.limitSelect.value = limitStr;
  }

  function updateSegmentBand(refs, task) {
    const segments = Array.isArray(task.segments) ? task.segments : [];
    refs.segmentMeta.textContent = `${segments.length} ${segments.length === 1 ? "hilo" : "hilos"} · ${
      task.supports_range ? "Paralelo Concurrente" : "Stream Lineal"
    }`;

    if (segments.length === 0) {
      setElementVisible(refs.segmentBand, false);
      if (refs.segmentCells.length > 0) {
        refs.segmentBand.innerHTML = "";
        refs.segmentCells = [];
      }
    } else {
      setElementVisible(refs.segmentBand, true);
      if (refs.segmentCells.length !== segments.length) {
        refs.segmentBand.innerHTML = "";
        refs.segmentCells = [];
        for (let i = 0; i < segments.length; i += 1) {
          const cell = document.createElement("div");
          cell.className = "segment-band-cell";
          const fill = document.createElement("div");
          fill.className = "segment-band-fill";
          cell.appendChild(fill);
          refs.segmentBand.appendChild(cell);
          refs.segmentCells.push({ cell, fill });
        }
      }

      segments.forEach((seg, idx) => {
        const { cell, fill } = refs.segmentCells[idx];
        const segStatus = safeStatusClass(seg.status);
        const segProgress = safePercent(seg.progress);
        const segTotal = Math.max(1, Number(seg.total) || 1);
        const statusSuffix = segStatus === "initializing" ? "" : ` ${segStatus}`;

        cell.className = `segment-band-cell${statusSuffix}`;
        cell.style.flexGrow = String(segTotal);
        fill.style.width = `${segProgress}%`;

        const segTitle = `Hilo #${Number(seg.id) + 1}: ${segProgress}% (${segStatus})`;
        cell.title = segTitle;
        cell.setAttribute("title", segTitle);
        cell.setAttribute("aria-label", segTitle);
      });
    }

    // Lista colapsable de rangos de bytes
    refs.rangesSummary.textContent = `Rangos de bytes por hilo (${segments.length})`;
    if (refs.rangeItems.length !== segments.length) {
      refs.rangesList.innerHTML = "";
      refs.rangeItems = [];
      for (let i = 0; i < segments.length; i += 1) {
        const item = document.createElement("div");
        item.className = "row-range-item";
        const left = document.createElement("span");
        const right = document.createElement("span");
        item.appendChild(left);
        item.appendChild(right);
        refs.rangesList.appendChild(item);
        refs.rangeItems.push({ item, left, right });
      }
    }

    segments.forEach((seg, idx) => {
      const { left, right } = refs.rangeItems[idx];
      const segStatus = safeStatusClass(seg.status);
      const speedFmt = formatSpeed(seg.speed_bps);
      const speedText =
        Number(seg.speed_bps) > 0
          ? `${speedFmt.val} ${speedFmt.unit}`
          : segStatus === "completed"
          ? "Listo"
          : "0 KB/s";
      const downloaded = Math.max(
        0,
        (Number(seg.current_byte) || 0) - (Number(seg.start_byte) || 0)
      );
      left.textContent = `#${Number(seg.id) + 1} · ${speedText}`;
      right.textContent = `${formatBytes(downloaded)} / ${formatBytes(seg.total)}`;
    });
  }

  function createTaskRow(task) {
    const taskId = String(task.id || "");

    const card = document.createElement("div");
    card.className = "download-item-card glass-card";
    card.dataset.taskId = taskId;

    // Resumen superior
    const summary = document.createElement("div");
    summary.className = "item-summary";

    const itemMain = document.createElement("div");
    itemMain.className = "item-main";

    const titleRow = document.createElement("div");
    titleRow.className = "item-title-row";

    const filenameEl = document.createElement("span");
    filenameEl.className = "item-filename";

    const badgeEl = document.createElement("span");
    badgeEl.className = "item-badge";

    titleRow.appendChild(filenameEl);
    titleRow.appendChild(badgeEl);

    const metaEl = document.createElement("div");
    metaEl.className = "item-meta font-mono";

    const sizeSpan = document.createElement("span");
    const speedSpan = document.createElement("span");
    const progressSpan = document.createElement("span");
    const etaSpan = document.createElement("span");
    const connSpan = document.createElement("span");

    metaEl.appendChild(sizeSpan);
    metaEl.appendChild(speedSpan);
    metaEl.appendChild(progressSpan);
    metaEl.appendChild(etaSpan);
    metaEl.appendChild(connSpan);

    const trackEl = document.createElement("div");
    trackEl.className = "item-progress-track";

    const fillEl = document.createElement("div");
    fillEl.className = "item-progress-fill";
    trackEl.appendChild(fillEl);

    itemMain.appendChild(titleRow);
    itemMain.appendChild(metaEl);
    itemMain.appendChild(trackEl);

    // Acciones
    const actionsEl = document.createElement("div");
    actionsEl.className = "item-actions";

    const pauseBtn = document.createElement("button");
    pauseBtn.type = "button";
    pauseBtn.className = "btn-icon";

    const detailsBtn = document.createElement("button");
    detailsBtn.type = "button";
    detailsBtn.className = "btn-details";
    detailsBtn.setAttribute("aria-expanded", "false");
    detailsBtn.textContent = "Detalles ▾";

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn-icon btn-danger";
    deleteBtn.title = "Eliminar descarga";
    deleteBtn.setAttribute("aria-label", "Eliminar descarga");
    deleteBtn.appendChild(
      createSvgIcon(14, 14, "none", "currentColor", 2, [
        ["line", { x1: 18, y1: 6, x2: 6, y2: 18 }],
        ["line", { x1: 6, y1: 6, x2: 18, y2: 18 }],
      ])
    );

    actionsEl.appendChild(pauseBtn);
    actionsEl.appendChild(detailsBtn);
    actionsEl.appendChild(deleteBtn);

    summary.appendChild(itemMain);
    summary.appendChild(actionsEl);

    // Banner de error inline
    const errorBanner = document.createElement("div");
    errorBanner.className = "item-error-banner";
    errorBanner.setAttribute("role", "alert");
    setElementVisible(errorBanner, false);

    // Drawer inline expandible
    const drawer = document.createElement("div");
    drawer.className = "row-details-drawer";
    setElementVisible(drawer, false);

    const drawerToolbar = document.createElement("div");
    drawerToolbar.className = "row-drawer-toolbar";

    const drawerUrl = document.createElement("span");
    drawerUrl.className = "row-drawer-url font-mono";

    const limitGroup = document.createElement("div");
    limitGroup.className = "row-limit-group";
    const limitLabel = document.createElement("label");
    rowSeq += 1;
    const limitSelectId = `row-limit-${rowSeq}`;
    limitLabel.setAttribute("for", limitSelectId);
    limitLabel.textContent = "Límite:";

    const limitSelect = document.createElement("select");
    limitSelect.id = limitSelectId;
    limitSelect.className = "row-limit-select";
    limitSelect.setAttribute("aria-label", "Límite de velocidad en vivo");
    populateSelectOptions(limitSelect, ROW_LIMIT_OPTIONS);

    limitGroup.appendChild(limitLabel);
    limitGroup.appendChild(limitSelect);

    drawerToolbar.appendChild(drawerUrl);
    drawerToolbar.appendChild(limitGroup);

    const bandHeader = document.createElement("div");
    bandHeader.className = "segment-band-header";
    const bandTitle = document.createElement("span");
    bandTitle.className = "segment-band-title";
    bandTitle.textContent = "Banda Segmentada";
    const segmentMeta = document.createElement("span");
    segmentMeta.className = "font-mono";
    bandHeader.appendChild(bandTitle);
    bandHeader.appendChild(segmentMeta);

    const segmentBand = document.createElement("div");
    segmentBand.className = "segment-band";
    segmentBand.setAttribute("role", "group");
    segmentBand.setAttribute("aria-label", "Progreso de segmentos paralelos");

    const rangesDetails = document.createElement("details");
    rangesDetails.className = "row-ranges-details";
    const rangesSummary = document.createElement("summary");
    rangesSummary.textContent = "Rangos de bytes por hilo (0)";
    const rangesList = document.createElement("div");
    rangesList.className = "row-ranges-list font-mono";
    rangesDetails.appendChild(rangesSummary);
    rangesDetails.appendChild(rangesList);

    drawer.appendChild(drawerToolbar);
    drawer.appendChild(bandHeader);
    drawer.appendChild(segmentBand);
    drawer.appendChild(rangesDetails);

    card.appendChild(summary);
    card.appendChild(errorBanner);
    card.appendChild(drawer);

    const refs = {
      taskId,
      currentStatus: safeStatusClass(task.status),
      lastFilename: task.filename || "",
      lastPauseIsDownloading: null,
      lastTask: task,
      card,
      filenameEl,
      badgeEl,
      sizeSpan,
      speedSpan,
      progressSpan,
      etaSpan,
      connSpan,
      fillEl,
      pauseBtn,
      detailsBtn,
      deleteBtn,
      errorBanner,
      drawer,
      drawerUrl,
      limitSelect,
      customLimitOption: null,
      segmentMeta,
      segmentBand,
      segmentCells: [],
      rangesDetails,
      rangesSummary,
      rangesList,
      rangeItems: [],
      drawerInitialized: false,
    };

    pauseBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      togglePause(refs.taskId, refs.currentStatus);
    });

    detailsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (expandedTaskIds.has(refs.taskId)) {
        expandedTaskIds.delete(refs.taskId);
      } else {
        expandedTaskIds.add(refs.taskId);
      }
      const latestTask = downloads.find((d) => d.id === refs.taskId) || refs.lastTask;
      if (latestTask) {
        updateTaskRow(refs, latestTask);
      }
    });

    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openDeleteModal(refs.taskId, refs.lastFilename);
    });

    limitSelect.addEventListener("change", async () => {
      const parsedVal = parseInt(limitSelect.value, 10);
      const limitBps = Number.isFinite(parsedVal) && parsedVal >= 0 ? parsedVal : 0;
      try {
        const result = await apiRequest(
          `/api/downloads/${encodeURIComponent(refs.taskId)}/limit`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ limit_bps: limitBps }),
          },
          "No se pudo actualizar el límite de velocidad."
        );
        if (!result.ok) {
          showToast(result.error, "error");
          syncLimitSelectOptions(
            refs,
            Math.max(0, Number(refs.lastTask && refs.lastTask.speed_limit_bps) || 0)
          );
        } else {
          const speedFmt = formatSpeed(limitBps);
          showToast(
            limitBps === 0
              ? "Límite de velocidad eliminado (Ilimitado)."
              : `Límite actualizado a ${speedFmt.val} ${speedFmt.unit}.`,
            "success"
          );
        }
      } catch (err) {
        showToast("Error al actualizar límite: " + err.message, "error");
      }
    });

    updateTaskRow(refs, task);
    return refs;
  }

  function updateTaskRow(refs, task) {
    const safeStatus = safeStatusClass(task.status);
    const taskProgress = safePercent(task.progress);
    const speedFmt = formatSpeed(task.speed_bps);
    const isExpanded = expandedTaskIds.has(refs.taskId);
    const isDownloading = safeStatus === "downloading";

    refs.currentStatus = safeStatus;
    refs.lastFilename = task.filename || "";
    refs.lastTask = task;

    refs.card.classList.toggle("expanded", isExpanded);

    refs.filenameEl.textContent = task.filename || "Descargando...";
    refs.badgeEl.className = `item-badge badge-${safeStatus}`;
    refs.badgeEl.textContent = task.status || safeStatus;

    refs.sizeSpan.textContent = `${formatBytes(task.bytes_downloaded)} / ${
      Number(task.total_size) > 0 ? formatBytes(task.total_size) : "?"
    }`;
    refs.speedSpan.textContent =
      Number(task.speed_bps) > 0 ? `${speedFmt.val} ${speedFmt.unit}` : "";
    refs.progressSpan.textContent = `${taskProgress}%`;
    refs.etaSpan.textContent = formatTaskEtaText(task, safeStatus);
    refs.connSpan.textContent = `${Number(task.connections) || 0} hilos`;

    refs.fillEl.style.width = `${taskProgress}%`;

    if (refs.lastPauseIsDownloading !== isDownloading) {
      refs.pauseBtn.innerHTML = "";
      refs.pauseBtn.appendChild(
        isDownloading
          ? createSvgIcon(14, 14, "currentColor", null, null, [
              ["rect", { x: 6, y: 4, width: 4, height: 16 }],
              ["rect", { x: 14, y: 4, width: 4, height: 16 }],
            ])
          : createSvgIcon(14, 14, "currentColor", null, null, [
              ["polygon", { points: "5 3 19 12 5 21 5 3" }],
            ])
      );
      const pauseLabel = isDownloading ? "Pausar descarga" : "Reanudar descarga";
      refs.pauseBtn.title = pauseLabel;
      refs.pauseBtn.setAttribute("aria-label", pauseLabel);
      refs.lastPauseIsDownloading = isDownloading;
    }
    refs.pauseBtn.disabled = safeStatus === "completed";

    refs.detailsBtn.classList.toggle("expanded", isExpanded);
    refs.detailsBtn.setAttribute("aria-expanded", isExpanded ? "true" : "false");
    refs.detailsBtn.textContent = isExpanded ? "Ocultar ▴" : "Detalles ▾";

    const hasErrorBanner =
      safeStatus === "error" &&
      typeof task.error_message === "string" &&
      task.error_message.trim().length > 0;
    if (hasErrorBanner) {
      refs.errorBanner.textContent = task.error_message;
      setElementVisible(refs.errorBanner, true);
    } else {
      refs.errorBanner.textContent = "";
      setElementVisible(refs.errorBanner, false);
    }

    setElementVisible(refs.drawer, isExpanded);
    refs.drawerUrl.textContent = task.url || "";

    const taskLimit = Math.max(0, Number(task.speed_limit_bps) || 0);
    if (document.activeElement !== refs.limitSelect) {
      syncLimitSelectOptions(refs, taskLimit);
    }

    if (isExpanded || !refs.drawerInitialized) {
      updateSegmentBand(refs, task);
      refs.drawerInitialized = true;
    }
  }

  function syncDownloadsList() {
    if (!downloadsListEl) return;

    if (downloads.length === 0) {
      rowMap.clear();
      if (!emptyStateCard) {
        downloadsListEl.innerHTML = "";
        emptyStateCard = createQuickEmptyCard();
        downloadsListEl.appendChild(emptyStateCard);
      }
      return;
    }

    if (rowMap.size === 0) {
      downloadsListEl.innerHTML = "";
      emptyStateCard = null;
      quickInlineRefs = null;
    } else if (emptyStateCard) {
      removeNode(downloadsListEl, emptyStateCard);
      emptyStateCard = null;
      quickInlineRefs = null;
    }

    const activeIds = new Set(downloads.map((d) => String(d.id || "")));
    for (const [taskId, refs] of rowMap.entries()) {
      if (!activeIds.has(taskId)) {
        removeNode(downloadsListEl, refs.card);
        rowMap.delete(taskId);
      }
    }

    downloads.forEach((task, index) => {
      const taskId = String(task.id || "");
      let refs = rowMap.get(taskId);
      if (!refs) {
        refs = createTaskRow(task);
        rowMap.set(taskId, refs);
      } else {
        updateTaskRow(refs, task);
      }
      placeChildAt(downloadsListEl, refs.card, index);
    });
  }

  function renderAll() {
    updateMetrics();
    syncDownloadsList();
  }

  // --- 8. ACCIONES DE CONTROL ---
  async function togglePause(taskId, status) {
    const action = status === "downloading" ? "pause" : "resume";
    const fallbackError = `No se pudo ${action === "pause" ? "pausar" : "reanudar"} la descarga.`;
    try {
      const result = await apiRequest(
        `/api/downloads/${encodeURIComponent(taskId)}/${action}`,
        { method: "POST" },
        fallbackError
      );
      if (!result.ok) {
        showToast(result.error, "error");
      }
    } catch (e) {
      console.error(`Error al ${action} descarga`, e);
      showToast("Error de conexión con el servidor.", "error");
    }
  }

  // --- 9. ABRIR CARPETA DE DESCARGAS ---
  if (btnOpenFolder) {
    btnOpenFolder.addEventListener("click", async () => {
      try {
        const result = await apiRequest(
          "/api/system/open-folder",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ dir: defaultDownloadDir }),
          },
          "No se pudo abrir la carpeta de descargas."
        );
        if (!result.ok) {
          showToast(result.error, "error");
        }
      } catch (err) {
        showToast("Error al abrir la carpeta: " + err.message, "error");
      }
    });
  }

  function restoreModalFocus() {
    if (
      lastFocusedBeforeModal &&
      lastFocusedBeforeModal.isConnected !== false &&
      typeof lastFocusedBeforeModal.focus === "function"
    ) {
      lastFocusedBeforeModal.focus();
    }
    lastFocusedBeforeModal = null;
  }

  // --- 10. MODAL CONFIRMACIÓN DE ELIMINACIÓN ---
  function openDeleteModal(taskId, filename) {
    pendingDeleteTaskId = taskId;
    if (deleteModalFilenameEl) {
      deleteModalFilenameEl.textContent = filename || taskId;
    }
    if (checkboxDeleteFileEl) {
      checkboxDeleteFileEl.checked = false;
    }
    if (modalDeleteDownload) {
      lastFocusedBeforeModal = document.activeElement || null;
      modalDeleteDownload.classList.add("open");
    }
    if (btnCancelDeleteModal && typeof btnCancelDeleteModal.focus === "function") {
      btnCancelDeleteModal.focus();
    }
  }

  function closeDeleteModal() {
    if (modalDeleteDownload) {
      modalDeleteDownload.classList.remove("open");
    }
    pendingDeleteTaskId = null;
    if (checkboxDeleteFileEl) {
      checkboxDeleteFileEl.checked = false;
    }
    restoreModalFocus();
  }

  if (btnCloseDeleteModal) {
    btnCloseDeleteModal.addEventListener("click", closeDeleteModal);
  }
  if (btnCancelDeleteModal) {
    btnCancelDeleteModal.addEventListener("click", closeDeleteModal);
  }
  if (modalDeleteDownload) {
    modalDeleteDownload.addEventListener("click", (e) => {
      if (e.target === modalDeleteDownload) closeDeleteModal();
    });
  }

  if (btnConfirmDelete) {
    btnConfirmDelete.addEventListener("click", async () => {
      if (!pendingDeleteTaskId) return;
      const taskId = pendingDeleteTaskId;
      const deleteFile = Boolean(checkboxDeleteFileEl && checkboxDeleteFileEl.checked);
      btnConfirmDelete.disabled = true;
      try {
        const result = await apiRequest(
          `/api/downloads/${encodeURIComponent(taskId)}?delete_file=${
            deleteFile ? "true" : "false"
          }`,
          { method: "DELETE" },
          "No se pudo eliminar la descarga."
        );
        if (!result.ok) {
          showToast(result.error, "error");
        } else {
          closeDeleteModal();
        }
      } catch (err) {
        showToast("Error al eliminar la descarga: " + err.message, "error");
      } finally {
        btnConfirmDelete.disabled = false;
      }
    });
  }

  // --- 11. MODAL NUEVA DESCARGA ---
  function closeNewModal() {
    if (modalNewDownload) {
      modalNewDownload.classList.remove("open");
    }
    restoreModalFocus();
  }

  if (btnOpenModal) {
    btnOpenModal.addEventListener("click", () => {
      lastFocusedBeforeModal = document.activeElement || null;
      if (modalNewDownload) {
        modalNewDownload.classList.add("open");
      }
      if (inputUrl && typeof inputUrl.focus === "function") {
        inputUrl.focus();
      }
      if (inputDir && !inputDir.value) {
        inputDir.value = localStorage.getItem("aether-dir") || defaultDownloadDir;
      }
      if (navigator.clipboard && navigator.clipboard.readText && inputUrl) {
        navigator.clipboard
          .readText()
          .then((text) => {
            const trimmed = text ? text.trim() : "";
            if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
              inputUrl.value = trimmed;
            }
          })
          .catch(() => {});
      }
    });
  }

  if (btnCloseModal) btnCloseModal.addEventListener("click", closeNewModal);
  if (btnCancelModal) btnCancelModal.addEventListener("click", closeNewModal);
  if (modalNewDownload) {
    modalNewDownload.addEventListener("click", (e) => {
      if (e.target === modalNewDownload) closeNewModal();
    });
  }

  wireClipboardPaste(btnPaste, inputUrl, false);
  wireQuickFolderSelect(quickFolders, inputDir);

  if (formNewDownload) {
    formNewDownload.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submitBtn =
        typeof formNewDownload.querySelector === "function"
          ? formNewDownload.querySelector('button[type="submit"]')
          : null;
      await submitDownloadRequest({
        url: inputUrl ? inputUrl.value : "",
        threads: selectThreads ? selectThreads.value : "8",
        speedLimit: selectSpeedLimit ? selectSpeedLimit.value : "0",
        filename: inputFilename ? inputFilename.value : "",
        customDir: inputDir ? inputDir.value : "",
        submitBtn,
        onSuccess: () => {
          closeNewModal();
          if (inputUrl) inputUrl.value = "";
          if (inputFilename) inputFilename.value = "";
        },
      });
    });
  }

  // Hotkeys: Escape para cerrar modales, Ctrl/Cmd+N para abrir nueva descarga
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (modalDeleteDownload && modalDeleteDownload.classList.contains("open")) {
        e.preventDefault();
        closeDeleteModal();
        return;
      }
      if (modalNewDownload && modalNewDownload.classList.contains("open")) {
        e.preventDefault();
        closeNewModal();
        return;
      }
    }
    if ((e.ctrlKey || e.metaKey) && e.key && e.key.toLowerCase() === "n") {
      e.preventDefault();
      if (btnOpenModal) btnOpenModal.click();
    }
  });

  // Inicializar estado visual y WebSocket
  updateLiveStatus(false);
  syncDownloadsList();
  connectWebSocket();
});
