document.addEventListener("DOMContentLoaded", () => {
  const statusDot = document.getElementById("status-dot");
  const statusLabel = document.getElementById("status-label");
  const statusSub = document.getElementById("status-sub");
  const toggleIntercept = document.getElementById("toggle-intercept");
  const selectThreads = document.getElementById("select-threads");
  const btnRefresh = document.getElementById("btn-refresh");
  const btnOpenDashboard = document.getElementById("btn-open-dashboard");

  let activePort = 8765;

  // Cargar preferencias
  chrome.storage.local.get({ enabled: true, threads: 8, primaryPort: 8765 }, (cfg) => {
    toggleIntercept.checked = cfg.enabled;
    selectThreads.value = cfg.threads.toString();
    activePort = cfg.primaryPort;
    checkStatus();
  });

  // Guardar cambio en toggle
  toggleIntercept.addEventListener("change", () => {
    chrome.storage.local.set({ enabled: toggleIntercept.checked });
  });

  // Guardar cambio en hilos
  selectThreads.addEventListener("change", () => {
    chrome.storage.local.set({ threads: parseInt(selectThreads.value, 10) });
  });

  // Refrescar estado del motor
  btnRefresh.addEventListener("click", () => {
    statusDot.className = "status-dot";
    statusLabel.textContent = "Verificando...";
    checkStatus();
  });

  // Abrir panel en el navegador
  btnOpenDashboard.addEventListener("click", () => {
    chrome.tabs.create({ url: `http://127.0.0.1:${activePort}` });
  });

  function checkStatus() {
    chrome.runtime.sendMessage({ action: "CHECK_ENGINE_STATUS" }, (response) => {
      if (response && response.online) {
        activePort = response.port;
        statusDot.className = "status-dot online";
        statusLabel.textContent = "Motor Acelerador Activo";
        statusSub.textContent = `127.0.0.1:${response.port} (${response.info?.active_tasks || 0} activas)`;
      } else {
        statusDot.className = "status-dot offline";
        statusLabel.textContent = "Motor Fuera de Línea";
        statusSub.textContent = "Inicia Aether con ./aether";
      }
    });
  }
});
