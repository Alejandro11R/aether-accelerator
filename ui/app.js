/**
 * AETHER ACCELERATOR — FRONTEND CLIENT
 * Reactividad en tiempo real vía WebSocket + SVG Sparkline + Dynamic Segment Matrix
 */

document.addEventListener("DOMContentLoaded", () => {
  // Estado local
  let downloads = [];
  let selectedTaskId = null;
  let ws = null;
  const speedHistory = new Array(30).fill(0);

  // Elementos DOM
  const totalSpeedEl = document.getElementById("total-speed");
  const totalSpeedUnitEl = document.getElementById("total-speed-unit");
  const totalThreadsEl = document.getElementById("total-threads");
  const activeTasksLabelEl = document.getElementById("active-tasks-label");
  const downloadDirTextEl = document.getElementById("download-dir-text");
  const downloadsCountEl = document.getElementById("downloads-count");
  const downloadsListEl = document.getElementById("downloads-list");
  
  // Sparkline
  const sparklineLine = document.getElementById("sparkline-line");
  const sparklineArea = document.getElementById("sparkline-area");

  // Inspector
  const inspectorPanel = document.getElementById("inspector-panel");
  const inspFilename = document.getElementById("insp-filename");
  const inspUrl = document.getElementById("insp-url");
  const inspDownloadedSize = document.getElementById("insp-downloaded-size");
  const inspPercent = document.getElementById("insp-percent");
  const inspEta = document.getElementById("insp-eta");
  const inspProgressFill = document.getElementById("insp-progress-fill");
  const inspSegmentCount = document.getElementById("insp-segment-count");
  const inspSegmentsGrid = document.getElementById("insp-segments-grid");
  const inspPauseBtn = document.getElementById("insp-pause-btn");
  const inspDeleteBtn = document.getElementById("insp-delete-btn");

  // Modal
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

  let defaultDownloadDir = "";

  // --- 1. GESTOR DE TEMAS DIAL (IMPECCABLE DESIGN) ---
  const savedTheme = localStorage.getItem("aether-theme") || "obsidian";
  document.documentElement.setAttribute("data-theme", savedTheme);
  updateDialButtons(savedTheme);

  document.querySelectorAll(".dial-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const theme = btn.dataset.setTheme;
      document.documentElement.setAttribute("data-theme", theme);
      localStorage.setItem("aether-theme", theme);
      updateDialButtons(theme);
    });
  });

  function updateDialButtons(currentTheme) {
    document.querySelectorAll(".dial-btn").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.setTheme === currentTheme);
    });
  }

  // --- 2. WEBSOCKET CLIENT ---
  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
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
      console.warn("[Aether WS] Desconectado. Reintentando en 2s...");
      setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = (err) => {
      console.error("[Aether WS] Error:", err);
      ws.close();
    };
  }

  function handleWsMessage(msg) {
    if (msg.type === "init") {
      downloads = msg.downloads || [];
      if (msg.download_dir) {
        defaultDownloadDir = msg.download_dir;
        downloadDirTextEl.textContent = msg.download_dir;
        if (!inputDir.value) {
          inputDir.value = localStorage.getItem("aether-dir") || defaultDownloadDir;
        }
      }
      if (!selectedTaskId && downloads.length > 0) {
        selectedTaskId = downloads[0].id;
      }
      renderAll();
    } else if (msg.type === "task_added") {
      downloads.unshift(msg.task);
      selectedTaskId = msg.task.id;
      renderAll();
    } else if (msg.type === "task_updated" || msg.type === "task_progress") {
      const idx = downloads.findIndex(d => d.id === msg.task.id);
      if (idx !== -1) {
        downloads[idx] = msg.task;
      } else {
        downloads.unshift(msg.task);
      }
      updateMetrics();
      renderDownloadsList();
      if (selectedTaskId === msg.task.id) {
        renderInspector(msg.task);
      }
    } else if (msg.type === "task_removed") {
      downloads = downloads.filter(d => d.id !== msg.task_id);
      if (selectedTaskId === msg.task_id) {
        selectedTaskId = downloads.length > 0 ? downloads[0].id : null;
      }
      renderAll();
    }
  }

  // --- 3. FORMATTERS & HELPERS ---
  function jsonParse(str) {
    return JSON.parse(str);
  }

  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  }

  function formatSpeed(bytesPerSec) {
    if (!bytesPerSec || bytesPerSec <= 0) return { val: "0.0", unit: "KB/s" };
    const mb = bytesPerSec / (1024 * 1024);
    if (mb >= 1) {
      return { val: mb.toFixed(2), unit: "MB/s" };
    }
    const kb = bytesPerSec / 1024;
    return { val: kb.toFixed(1), unit: "KB/s" };
  }

  function formatEta(seconds) {
    if (seconds < 0 || seconds == null) return "Calculando...";
    if (seconds === 0) return "Completado";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    if (mins > 60) {
      const hours = Math.floor(mins / 60);
      return `ETA: ${hours}h ${mins % 60}m`;
    }
    return `ETA: ${mins}m ${secs}s`;
  }

  // --- 4. RENDERIZADO DE MÉTRICAS Y SPARKLINE ---
  function updateMetrics() {
    const activeTasks = downloads.filter(d => d.status === "downloading");
    const totalSpeed = activeTasks.reduce((acc, d) => acc + (d.speed_bps || 0), 0);
    const totalConnections = activeTasks.reduce((acc, d) => acc + (d.connections || 0), 0);

    const speedFmt = formatSpeed(totalSpeed);
    totalSpeedEl.textContent = speedFmt.val;
    totalSpeedUnitEl.textContent = speedFmt.unit;

    totalThreadsEl.textContent = totalConnections;
    activeTasksLabelEl.textContent = `${activeTasks.length} ${activeTasks.length === 1 ? 'descarga activa' : 'descargas activas'}`;
    downloadsCountEl.textContent = downloads.length;

    // Actualizar Sparkline
    speedHistory.shift();
    speedHistory.push(totalSpeed);
    drawSparkline();
  }

  function drawSparkline() {
    const max = Math.max(...speedHistory, 1024 * 1024); // Al menos 1MB escala
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

  // --- 5. RENDERIZADO DEL INSPECTOR DE SEGMENTOS (IDM VISUALIZER) ---
  function renderInspector(task) {
    if (!task) {
      inspectorPanel.style.display = "none";
      return;
    }

    inspectorPanel.style.display = "block";
    inspFilename.textContent = task.filename || "Descargando...";
    inspUrl.textContent = task.url;

    const downloadedStr = formatBytes(task.bytes_downloaded);
    const totalStr = task.total_size > 0 ? formatBytes(task.total_size) : "Desconocido";
    inspDownloadedSize.textContent = `${downloadedStr} / ${totalStr}`;
    inspPercent.textContent = `${task.progress}%`;
    inspProgressFill.style.width = `${task.progress}%`;
    inspEta.textContent = task.status === "completed" ? "¡Completado!" : formatEta(task.eta_seconds);

    inspSegmentCount.textContent = `${task.segments.length} hilos asignados (${task.supports_range ? "Paralelo Concurrente" : "Stream Lineal"})`;

    // Renderizar bloques de segmentos
    inspSegmentsGrid.innerHTML = "";
    task.segments.forEach(seg => {
      const block = document.createElement("div");
      block.className = `segment-block ${seg.status === 'downloading' ? 'active' : ''} ${seg.status === 'completed' ? 'completed' : ''}`;
      
      const speed = seg.speed_bps > 0 ? `${(seg.speed_bps / (1024 * 1024)).toFixed(2)} MB/s` : (seg.status === 'completed' ? '✓ Listo' : '0 KB/s');
      
      block.innerHTML = `
        <div class="segment-header font-mono">
          <span class="segment-id">Hilo #${seg.id + 1}</span>
          <span class="segment-speed">${speed}</span>
        </div>
        <div class="segment-bar">
          <div class="segment-fill" style="width: ${seg.progress}%;"></div>
        </div>
        <span class="segment-range font-mono">${formatBytes(seg.current_byte - seg.start_byte)} / ${formatBytes(seg.total)}</span>
      `;
      inspSegmentsGrid.appendChild(block);
    });

    // Botones de acción del inspector
    inspPauseBtn.onclick = () => togglePause(task.id, task.status);
    inspDeleteBtn.onclick = () => deleteTask(task.id);
  }

  // --- 6. RENDERIZADO DE LA COLA DE DESCARGAS ---
  function renderDownloadsList() {
    if (downloads.length === 0) {
      downloadsListEl.innerHTML = `
        <div class="empty-state glass-card">
          <div class="empty-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
          </div>
          <h3>No hay descargas activas</h3>
          <p>Pega un enlace para descargar a máxima velocidad saturando el ancho de banda con segmentación concurrente.</p>
        </div>
      `;
      return;
    }

    downloadsListEl.innerHTML = "";
    downloads.forEach(task => {
      const card = document.createElement("div");
      card.className = `download-item-card glass-card ${selectedTaskId === task.id ? 'selected' : ''}`;
      card.onclick = () => {
        selectedTaskId = task.id;
        renderAll();
      };

      const speedFmt = formatSpeed(task.speed_bps);
      const isSelected = selectedTaskId === task.id;

      card.innerHTML = `
        <div class="item-main">
          <div class="item-title-row">
            <span class="item-filename">${task.filename}</span>
            <span class="item-badge badge-${task.status}">${task.status}</span>
          </div>
          <div class="item-meta font-mono">
            <span>${formatBytes(task.bytes_downloaded)} / ${task.total_size > 0 ? formatBytes(task.total_size) : '?'}</span>
            <span>${task.speed_bps > 0 ? `${speedFmt.val} ${speedFmt.unit}` : ''}</span>
            <span>${task.progress}%</span>
            <span>${task.connections} hilos</span>
          </div>
          <div class="item-progress-track">
            <div class="item-progress-fill" style="width: ${task.progress}%;"></div>
          </div>
        </div>

        <div class="item-actions">
          <button class="btn-icon" title="Pausar / Reanudar" onclick="event.stopPropagation(); window.aetherTogglePause('${task.id}', '${task.status}')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              ${task.status === 'downloading' 
                ? '<rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect>'
                : '<polygon points="5 3 19 12 5 21 5 3"></polygon>'
              }
            </svg>
          </button>
          <button class="btn-icon btn-danger" title="Eliminar" onclick="event.stopPropagation(); window.aetherDelete('${task.id}')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      `;

      downloadsListEl.appendChild(card);
    });
  }

  function renderAll() {
    updateMetrics();
    renderDownloadsList();
    const current = downloads.find(d => d.id === selectedTaskId);
    renderInspector(current);
  }

  // --- 7. ACCIONES DE CONTROL ---
  window.aetherTogglePause = (taskId, status) => togglePause(taskId, status);
  window.aetherDelete = (taskId) => deleteTask(taskId);

  async function togglePause(taskId, status) {
    const action = status === "downloading" ? "pause" : "resume";
    try {
      await fetch(`/api/downloads/${taskId}/${action}`, { method: "POST" });
    } catch (e) {
      console.error(`Error al ${action} descarga`, e);
    }
  }

  async function deleteTask(taskId) {
    if (!confirm("¿Deseas eliminar esta descarga de la cola?")) return;
    try {
      await fetch(`/api/downloads/${taskId}`, { method: "DELETE" });
    } catch (e) {
      console.error("Error al eliminar descarga", e);
    }
  }

  // --- 8. MODAL NUEVA DESCARGA ---
  btnOpenModal.addEventListener("click", () => {
    modalNewDownload.classList.add("open");
    inputUrl.focus();
    if (!inputDir.value) {
      inputDir.value = localStorage.getItem("aether-dir") || defaultDownloadDir;
    }
    // Auto-detectar URL en portapapeles
    if (navigator.clipboard && navigator.clipboard.readText) {
      navigator.clipboard.readText().then(text => {
        if (text && (text.startsWith("http://") || text.startsWith("https://"))) {
          inputUrl.value = text;
        }
      }).catch(() => {});
    }
  });

  const closeModal = () => modalNewDownload.classList.remove("open");
  btnCloseModal.addEventListener("click", closeModal);
  btnCancelModal.addEventListener("click", closeModal);
  modalNewDownload.addEventListener("click", (e) => {
    if (e.target === modalNewDownload) closeModal();
  });

  btnPaste.addEventListener("click", async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) inputUrl.value = text;
    } catch (e) {
      alert("Por favor permite el acceso al portapapeles o pega con Ctrl+V");
    }
  });

  quickFolders.addEventListener("change", () => {
    if (quickFolders.value) {
      inputDir.value = quickFolders.value;
    }
  });

  formNewDownload.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = inputUrl.value.trim();
    if (!url) return;

    const threads = parseInt(selectThreads.value, 10);
    const speedLimit = parseInt(selectSpeedLimit.value, 10);
    const filename = inputFilename.value.trim();
    const customDir = inputDir.value.trim();

    if (customDir) {
      localStorage.setItem("aether-dir", customDir);
    }

    try {
      const resp = await fetch("/api/downloads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url,
          threads,
          filename,
          dir: customDir,
          speed_limit_bps: speedLimit
        })
      });
      const data = await resp.json();
      if (data.success) {
        closeModal();
        inputUrl.value = "";
        inputFilename.value = "";
      } else {
        alert(data.error || "No se pudo iniciar la descarga.");
      }
    } catch (err) {
      alert("Error al conectar con el servidor: " + err.message);
    }
  });

  // Hotkey Ctrl+N
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") {
      e.preventDefault();
      btnOpenModal.click();
    }
  });

  // Inicializar WebSocket
  connectWebSocket();
});
