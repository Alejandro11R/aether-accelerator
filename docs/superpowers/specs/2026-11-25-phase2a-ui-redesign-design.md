# Diseño Técnico — Fase 2A: Rediseño Visual Ligero y UX Incremental de la Web UI

**Fecha:** 2026-11-25  
**Proyecto:** `aether-accelerator` (`phase2a-ui-redesign`)  
**Estado:** Aprobado  
**Referencia visual:** Nota Nexo `b823421e` (consulta `openai/gpt-6.2-astra`) + Tarjeta Nexo `55549209`

---

## 1. Intención y Porqué

La auditoría inicial y la revisión visual con `openai/gpt-6.2-astra` detectaron que la Web UI actual:
1. Oculta la cola de descargas debajo de tres tarjetas grandes de métricas y un panel inspector superior (afectando especialmente ventanas estrechas/media pantalla en Hyprland).
2. Exige abrir el modal para pegar un enlace aunque el estado vacío invite a hacerlo, y oculta el control de límite en vivo (`POST /api/downloads/{id}/limit`), los mensajes de error (`task.error_message`), la opción `delete_file=true` al eliminar y rutas rápidas multiplataforma.
3. Reconstruye todo el DOM de la lista en cada evento `task_progress` (cada 500 ms), causando parpadeo, pérdida de foco y gasto innecesario; además mantiene 3 halos `blur(120px)` y animaciones infinitas innecesarias en reposo.

Esta Fase 2A rediseña la Web UI para que sea clara, rápida y visualmente distintiva usando **profundidad 3D ligera en CSS estático + SVG reactivo bajo demanda** (sin WebGL/Three.js), manteniendo el 100% del blindaje anti-XSS de Fase 1 y los 3 diales de tema (**Obsidian**, **Cyber**, **Frost**).

---

## 2. Goals, Non-Goals y Anti-Goals

### Goals
- Subir la cola de transferencias a primer plano bajo una **franja compacta de actividad** (velocidad global, descargas/hilos activos, sparkline SVG, estado WebSocket y botón «Abrir carpeta»).
- Ofrecer un **estado vacío accionable** con entrada directa de URL, botón pegar, descarga con Enter y opciones avanzadas plegables.
- Implementar **renderizado incremental** en `ui/app.js` preservando nodos DOM existentes por `task.id` durante `task_progress`/`task_updated`.
- Sustituir el inspector superior por **detalle expandible inline** por fila con la **banda segmentada continua** (`.segment-band`) como firma visual de Aether (diferenciada por tema y accesible sin depender solo del color), selector de **límite de velocidad en vivo** (`/limit`) y tabla de rangos de bytes bajo clic (`<details>`).
- Mostrar `task.error_message` en banner inline cuando `task.status === "error"`, reemplazar `confirm()`/`alert()` nativos por modal de confirmación Glassmorphism con checkbox `delete_file` y toasts `aria-live="polite"`, añadir cierre con `Escape` y atributos ARIA en modales.
- Exponer `platform` y `quick_folders` multiplataforma en `GET /api/system` y `/ws` `"init"`, y habilitar `POST /api/system/open-folder` protegido por `security_middleware`.
- Reducir carga gráfica: eliminar `.ambient-glow` `blur(120px)` y animaciones infinitas en reposo; aplicar degradados estáticos + relieve interior (`inset`), respetar `@media (prefers-reduced-motion: reduce)` y pausar redibujado del sparkline cuando `document.hidden === true`.

### Non-Goals (diferidos a Fases 2B–4)
- Cambios en la extensión MV3 (`extension/`) (Fase 2B).
- Persistencia de historial en base de datos, límite global de descargas concurrentes o cambios en `core/engine.py` / `aether-core/` (Fase 3).
- CI/CD, empaquetado `pyproject.toml`/`release.yml` y `README.md` (Fase 4).

### Anti-Goals
- Agregar librerías externas (Three.js, React, etc.) o dependencias Python nuevas.
- Introducir `innerHTML` con datos de usuario/tareas o manejadores inline `onclick=`.
- Romper compatibilidad con `/api/downloads`, `/api/system`, `/ws` ni con la extensión MV3 actual.

---

## 3. Restricciones Globales

- **Stack:** Python `>= 3.9`, `aiohttp>=3.9.0` únicamente, Vanilla HTML/CSS/JS sin bundler.
- **Portabilidad:** Linux (Wayland/X11), Windows y macOS.
- **Seguridad:** Cero `innerHTML` con datos de tareas, cero `shell=True` al abrir carpetas, validación estricta vía `core/security.py` y `security_middleware`.

---

## 4. Diseño Detallado

### 4.1 Backend Multiplataforma y Apertura Segura de Carpetas (`server/app.py`)
- **`_get_platform_key() -> str`:** mapea `platform.system().lower()` a `"linux"`, `"windows"` o `"darwin"`.
- **`_get_quick_folders(download_dir: str) -> List[Dict[str, str]]`:** devuelve lista de `{"label": ..., "path": ...}` adaptada al SO actual (p. ej. `~/Descargas` vs `~/Downloads`, `~/Vídeos` vs `~/Videos`/`~/Movies`, `~/Documentos` vs `~/Documents`, `~/Escritorio` vs `~/Desktop`, siempre asegurando incluir la carpeta configurada `download_dir` primero con etiqueta `"Carpeta Aether"`).
- **`GET /api/system` y `/ws` (`"type": "init"`):** añaden `"platform"` y `"quick_folders"` conservando intactas todas las claves existentes.
- **`POST /api/system/open-folder`:**
  - Protegido en `security_middleware` exigiendo `Content-Type: application/json` (estado `415` si no coincide), además de `Host`/`Origin`/`Sec-Fetch-Site`.
  - Acepta `{}` o `{"dir": "..."}`. Valida `dir` mediante `validate_output_dir(..., manager.download_dir)` y verifica `os.path.isdir(target_dir)` (devuelve `400` si falla).
  - Invoca `folder_opener(target_dir)` inyectable en `create_app(..., folder_opener: Optional[Callable[[str], None]] = None)` cuyo default ejecuta `os.startfile` (Windows), `subprocess.Popen(["open", path])` (macOS) o `subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)` (Linux). Devuelve `{"success": True, "path": target_dir}` o `500` si falla el lanzador del SO.

### 4.2 Maquetación, Profundidad Ligera y 3 Diales (`ui/index.html`, `ui/style.css`)
- **Fondo y relieve sin carga:** eliminar los 3 `<div class="ambient-glow">` y `.spin-slow`/pulsos infinitos en reposo; sustituirlos por fondo radial CSS estático por tema y bordes/biseles interiores (`--inner-glow`, `--shadow-glass`). Añadir regla `@media (prefers-reduced-motion: reduce)`.
- **Franja de actividad `.activity-strip`:** barra compacta sobre la cola con velocidad actual (`#total-speed`, `#total-speed-unit`), resumen (`#active-tasks-label`, `#total-threads`), `#live-indicator` (verde fijo al estar conectado, ámbar/rojo al desconectarse), SVG `#speed-sparkline` (`#sparkline-line`, `#sparkline-area`) y botón `#btn-open-folder` con `#download-dir-text`.
- **Estado vacío accionable (`#empty-state-template` / generado por DOM):** formulario `#form-quick-download` con input `#quick-url`, botón `#btn-quick-paste`, botón submit `⚡ Descargar`, ruta visible y `<details class="quick-advanced">` plegable (`#quick-threads`, `#quick-speed-limit`, `#quick-filename`, `#quick-dir`).
- **Cola y detalle expandible inline:** cada `.download-item-card` muestra nombre, badge de estado, tamaño/velocidad/porcentaje/ETA legible, barra de progreso y botones `Pausar/Reanudar`, `Detalles ▾` y `Eliminar` en posición fija. Al expandir, muestra `.row-details-drawer`:
  - **Banda segmentada continua (`.segment-band`):** segmentos proporcionales a `seg.total / task.total_size` con ancho mínimo legible, relleno por progreso y estilo diferenciado por tema (Obsidian: cian sobre grafito; Cyber: rosa con borde cian; Frost: blanco con trama diagonal gris para distinguir estado sin depender solo del color).
  - Control **Límite en vivo** (`<select class="row-limit-select">`) conectado a `POST /api/downloads/{id}/limit`.
  - Desplegable `<details class="row-ranges-details">` con detalle de rangos de bytes por hilo.
  - Banner `.item-error-banner` visible cuando `task.status === "error"` y `task.error_message` está presente.
- **Modales accesibles y toasts:** `#modal-new-download` y `#modal-delete-download` (con `#delete-modal-filename`, `#checkbox-delete-file`, `#btn-confirm-delete`, `#btn-cancel-delete-modal`, `#btn-close-delete-modal`) con `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, más `#toast-container` (`aria-live="polite"`).

### 4.3 Cliente Reactivo Incremental (`ui/app.js`)
- Conservar íntegramente `ALLOWED_STATUSES`, `safeStatusClass`, `safePercent`, `createSvgIcon`, `.textContent` y `encodeURIComponent`.
- **Renderizado incremental (`rowMap`):** mantener `Map<taskId, RowRefs>` para actualizar solo texto, atributos `style.width`, badges, estado de botones, banner de error y banda segmentada cuando llegan `task_progress`/`task_updated` sin destruir nodos DOM ni robar foco del `<select>` de límite en vivo (`document.activeElement !== refs.limitSelect`).
- **Gestión de estado y optimización:**
  - `expandedTaskIds = new Set()` para preservar filas expandidas entre actualizaciones.
  - Si `document.hidden === true`, no recalcular ni mutar atributos `d` del `#speed-sparkline`; redibujar al volver visible (`visibilitychange`).
  - Llenar `#quick-folders` dinámicamente desde `msg.quick_folders`.
  - Cerrar cualquier modal abierto con `Escape` y restaurar foco o limpiar estado pendiente.
  - Reemplazar todos los `alert()`/`confirm()` por `showToast(message, variant)` y `#modal-delete-download`.
