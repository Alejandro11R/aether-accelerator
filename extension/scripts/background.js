/**
 * AETHER DOWNLOAD ACCELERATOR — BROWSER EXTENSION SERVICE WORKER
 * Intercepta descargas del navegador, gestiona el menú contextual y conecta con el motor Aether.
 */

const DEFAULT_CONFIG = {
  enabled: true,
  primaryPort: 8765,
  fallbackPort: 8769,
  threads: 8,
  minFileSizeMB: 0 // Interceptar todo por defecto
};

// Inicialización de menús contextuales y configuración
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(DEFAULT_CONFIG, (cfg) => {
    chrome.storage.local.set(cfg);
  });

  chrome.contextMenus.create({
    id: "aether-download-link",
    title: "⚡ Descargar enlace con Aether",
    contexts: ["link"]
  });

  chrome.contextMenus.create({
    id: "aether-download-media",
    title: "⚡ Descargar multimedia con Aether",
    contexts: ["video", "audio", "image"]
  });
});

// Manejo de clics en el menú contextual
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "aether-download-link" && info.linkUrl) {
    sendToAether(info.linkUrl);
  } else if (info.menuItemId === "aether-download-media" && info.srcUrl) {
    sendToAether(info.srcUrl);
  }
});

// Intercepción automática de descargas del navegador (Estilo IDM)
chrome.downloads.onCreated.addListener(async (downloadItem) => {
  const cfg = await getStoredConfig();
  if (!cfg.enabled) return;

  const url = downloadItem.url || downloadItem.finalUrl;
  if (!url || !url.startsWith("http")) return;

  // Evitar bucles con descargas internas de extensiones o blobs temporales
  if (url.startsWith("blob:") || url.startsWith("data:") || url.includes("127.0.0.1:") || url.includes("localhost:")) {
    return;
  }

  // Cancelar la descarga lenta nativa del navegador
  try {
    await chrome.downloads.cancel(downloadItem.id);
    await chrome.downloads.erase({ id: downloadItem.id });
  } catch (e) {
    console.warn("No se pudo cancelar la descarga nativa:", e);
  }

  // Transferir al acelerador Aether
  const filename = downloadItem.filename ? downloadItem.filename.split(/[\/\\]/).pop() : null;
  sendToAether(url, filename, cfg.threads);
});

// Comunicación con content scripts (Sniffer de vídeo)
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "DOWNLOAD_VIDEO" && request.url) {
    sendToAether(request.url, request.title ? `${request.title}.mp4` : null);
    sendResponse({ success: true });
  } else if (request.action === "CHECK_ENGINE_STATUS") {
    checkEngineStatus().then(status => sendResponse(status));
    return true; // Asíncrono
  }
});

/**
 * Envía la petición de descarga al motor local Aether probando puertos automáticamente.
 */
async function sendToAether(url, filename = null, threads = 8) {
  const cfg = await getStoredConfig();
  const ports = [cfg.primaryPort, cfg.fallbackPort].filter(Boolean);

  let success = false;
  let lastError = null;

  for (const port of ports) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/downloads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: url,
          threads: threads || cfg.threads,
          filename: filename
        })
      });

      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          success = true;
          notifyUser("Descarga iniciada en Aether", `${filename || 'Archivo'} acelerado con ${threads || cfg.threads} hilos.`);
          break;
        }
      }
    } catch (err) {
      lastError = err;
    }
  }

  if (!success) {
    notifyUser("Aether no responde", "Asegúrate de que el motor de Aether esté ejecutándose en 127.0.0.1.");
  }
}

async function checkEngineStatus() {
  const cfg = await getStoredConfig();
  const ports = [cfg.primaryPort, cfg.fallbackPort];

  for (const port of ports) {
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/api/system`);
      if (resp.ok) {
        const data = await resp.json();
        return { online: true, port: port, info: data };
      }
    } catch (e) {}
  }
  return { online: false };
}

function getStoredConfig() {
  return new Promise((resolve) => {
    chrome.storage.local.get(DEFAULT_CONFIG, (cfg) => resolve(cfg));
  });
}

function notifyUser(title, message) {
  // Notificación simple vía badge o consola
  chrome.action.setBadgeText({ text: "⚡" });
  chrome.action.setBadgeBackgroundColor({ color: "#00f2fe" });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 3000);
}
