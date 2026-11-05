/**
 * AETHER DOWNLOAD ACCELERATOR — CONTENT SCRIPT
 * Detector inteligente de vídeos y streams (IDM Video Grabber Style)
 */

(() => {
  const processedVideos = new WeakSet();

  function scanForVideos() {
    const videos = document.querySelectorAll("video");
    videos.forEach(attachGrabberToVideo);
  }

  function attachGrabberToVideo(video) {
    if (processedVideos.has(video)) return;
    processedVideos.add(video);

    // Obtener contenedor relativo más cercano
    let parent = video.parentElement;
    if (!parent) return;

    const parentStyle = window.getComputedStyle(parent);
    if (parentStyle.position === "static") {
      parent.style.position = "relative";
    }
    parent.classList.add("aether-video-wrapper");

    // Crear botón flotante
    const grabberBtn = document.createElement("button");
    grabberBtn.className = "aether-video-grabber-btn";
    grabberBtn.innerHTML = `
      <svg class="aether-grabber-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
      </svg>
      <span>Descargar con Aether</span>
    `;

    grabberBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();

      const videoUrl = getVideoSrc(video);
      if (!videoUrl) {
        alert("No se pudo detectar el enlace directo del stream en este reproductor.");
        return;
      }

      const pageTitle = document.title ? document.title.replace(/[\\/*?:"<>|]/g, "_").trim() : "video";

      chrome.runtime.sendMessage({
        action: "DOWNLOAD_VIDEO",
        url: videoUrl,
        title: pageTitle
      });

      grabberBtn.innerHTML = `<span>✔ Enviado a Aether!</span>`;
      setTimeout(() => {
        grabberBtn.innerHTML = `
          <svg class="aether-grabber-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
          </svg>
          <span>Descargar con Aether</span>
        `;
      }, 2500);
    });

    parent.appendChild(grabberBtn);
  }

  function getVideoSrc(video) {
    if (video.currentSrc && video.currentSrc.startsWith("http")) {
      return video.currentSrc;
    }
    if (video.src && video.src.startsWith("http")) {
      return video.src;
    }
    const source = video.querySelector("source[src]");
    if (source && source.src && source.src.startsWith("http")) {
      return source.src;
    }
    return null;
  }

  // Escaneo inicial
  scanForVideos();

  // Observador de mutaciones para reproductores cargados dinámicamente
  const observer = new MutationObserver(() => scanForVideos());
  observer.observe(document.body, { childList: true, subtree: true });
})();
