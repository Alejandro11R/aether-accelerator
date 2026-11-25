# Diseño Técnico — Fase 1: Estabilización del Motor Python y Seguridad Local (P0)

**Fecha:** 2026-11-25  
**Proyecto:** `aether-accelerator` (`main`)  
**Estado:** Aprobado  
**Audiencia:** Implementación TDD sobre `core/`, `server/`, `ui/` y `tests/`

---

## 1. Intención y Porqué

La auditoría técnica del proyecto evidenció dos bloqueantes inmediatos en la aplicación actual:
1. **Corrupción y duplicación en descargas activas:** en descargas multihilo reales (evidenciado en `athenaos-live-x86_64.iso.aether.json`), los workers originales ignoran el recorte dinámico de `end_byte`, descargan bytes duplicados, quedan huérfanos frente a `asyncio.gather()` cuando se cierra `_file_handle`, recrean el archivo `.aether.json` al completar y carecen de límites de reintento, verificación `206` y telemetría en flujo único.
2. **Cadena de vulnerabilidades locales explotable desde el navegador:** falta de sanitización en `custom_filename`, permiso irrestricto en `dir`, ausencia de validación de origen/tipo de contenido en `127.0.0.1:8765` (CSRF / DNS-rebinding), listado de directorio en `/static` y DOM XSS en `ui/app.js`.

Esta Fase 1 estabiliza el motor actual y cierra los riesgos críticos de seguridad sin romper la Web UI ni la extensión MV3 existente.

---

## 2. Goals, Non-Goals y Anti-Goals

### Goals
- Garantizar descarga exacta byte-a-byte bajo segmentación dinámica (`_dynamic_balancer` + `_worker`) sin solapamientos ni workers huérfanos.
- Asegurar persistencia `.aether.json` atómica, limpieza real al completar, restauración íntegra de progreso (`bytes_downloaded`) y validación de `ETag` al reanudar.
- Proteger contra respuestas `200 OK` en descargas por rango, añadir reintentos acotados con backoff exponencial y habilitar telemetría/pausa correcta en descargas de flujo único (`_single_stream_download`).
- Corregir `RateLimiter` (sin dormir bajo lock y con reloj monotónico) y el cálculo de velocidad por segmento en `Segment`.
- Blindar la API local (`server/app.py`), la gestión de rutas (`core/security.py`) y el renderizado en `ui/app.js` contra Path Traversal, escritura arbitraria, CSRF local, DNS-rebinding y DOM XSS.

### Non-Goals (diferidos a Fases 2–4)
- Refactor de renderizado incremental, control UI para throttling en vivo (`/limit`), banner de `error_message` u opción `delete_file` en el modal (Fase 2).
- Cambios funcionales en la extensión de navegador MV3 (`extension/`) (Fase 2).
- Persistencia del historial de cola en base de datos y límite global de descargas concurrentes (Fase 3).
- Correcciones o integración de `aether-core` en Rust (Fase 3).
- Ajustes de CI/CD, empaquetado `pyproject.toml`/`release.yml` y actualización de `README.md` (Fase 4).

### Anti-Goals
- Romper los contratos JSON/WebSocket existentes (Control Splinter #1).
- Introducir fricción de autenticación manual o tokens que rompan la extensión MV3 actual al enviar descargas a `http://127.0.0.1:8765/api/downloads`.
- Modificar los 3 temas Glassmorphism (`Obsidian`, `Cyber`, `Frost`) ni el diseño visual base.
- Escribir código de producción sin prueba TDD fallida previa.

---

## 3. Restricciones Globales

- **Runtime:** Python `>= 3.9`.
- **Dependencias de producción:** únicamente `aiohttp>=3.9.0` (cero dependencias nuevas).
- **Portabilidad:** Linux, Windows y macOS.
- **Compatibilidad de contratos:** conservar la estructura de respuestas en `/api/downloads`, `/api/system` y los eventos WebSocket (`init`, `task_added`, `task_updated`, `task_progress`, `task_removed`), manteniendo intactas las claves públicas de `DownloadTask.to_dict()` y `Segment.to_dict()`.

---

## 4. Enfoques Evaluados

- **Opción A (Elegida por Alejandro):** Consolidar el motor Python (`core/` + `server/`) como núcleo principal estable mediante TDD y dejar `aether-core` (Rust) como CLI opcional para la Fase 3.
  - *Ventajas:* resuelve de inmediato los bugs de producción con mínimo riesgo y 100% de compatibilidad con la Web UI y la extensión actuales.
  - *Trade-off:* mantiene el techo de rendimiento de Python/`aiohttp` (suficiente para conexiones domésticas y fibra).
- **Opción B (Descartada):** Migrar inmediatamente el backend de descargas a `aether-core` (Rust).
  - *Razón de descarte:* multiplica el alcance y retrasa la corrección de integridad y seguridad de la app en uso.

---

## 5. Diseño Detallado

> Las decisiones marcadas como **Decisión propuesta por el agente** cubren detalles de arquitectura no fijados previamente y quedan sujetas a revisión de Alejandro junto con este documento.

### 5.1 Motor de Descarga (`core/engine.py`)

#### a) Límites estrictos en `_worker` y `_dynamic_balancer`
1. En `_worker`, dentro de un bloque sincronizado con `self._lock` **antes** de escribir cada `chunk` leído:
   - Calcular `remaining = segment.end_byte - segment.current_byte + 1`.
   - Si `remaining <= 0`, marcar `segment.status = "completed"` y salir inmediatamente del stream HTTP (cerrando la respuesta para abortar bytes excedentes del rango antiguo).
   - Si `len(chunk) > remaining`, recortar `chunk = chunk[:remaining]`.
   - Capturar `offset = segment.current_byte`, actualizar `segment.update_progress(len(chunk))` y `self.bytes_downloaded += len(chunk)`.
2. `_dynamic_balancer` realiza la división (`largest.end_byte = mid`, creación de `new_seg` y `self._workers.append(task)`) bajo el mismo `self._lock`, garantizando que ningún worker lea o escriba más allá del `mid` actualizado.

#### b) Escritura en disco sin `flush()` por paquete *(Decisión propuesta por el agente)*
- Extraer método `_write_chunk(offset: int, data: bytes)` en `DownloadTask`:
  - **POSIX (`hasattr(os, "pwrite")`):** escribir con `os.pwrite(self._file_handle.fileno(), data, offset)` (atómico por offset, sin requerir `seek` ni retener `self._lock` durante la llamada de I/O).
  - **Windows / fallback:** proteger `seek(offset)` + `write(data)` con un lock dedicado de I/O (`self._io_lock`) para no bloquear `_dynamic_balancer` ni otros cálculos de estado.
- Eliminar `self._file_handle.flush()` por cada paquete de 64 KB; ejecutar `flush()` explícitamente solo en `pause()` y en `finally:` antes de cerrar `_file_handle`.

#### c) Espera completa de workers dinámicos y activación para 1+ conexiones Range
- En `start()`, usar `if self.supports_range and self.total_size > 0:` (en lugar de `len(self.segments) > 1`) para que descargas con `connections=1` sobre servidores con `Accept-Ranges` también usen `_worker` (habilitando pausa/reanudación y evitando abrir el mismo archivo dos veces).
- Reemplazar el único `await asyncio.gather(*self._workers, return_exceptions=True)` por un bucle de drenado que espere iterativamente a cualquier `asyncio.Task` aún pendiente en `self._workers` mientras sigan agregándose workers dinámicos.
- Antes de concluir `start()`, cancelar `balancer` y esperar su cancelación (`await asyncio.gather(balancer, return_exceptions=True)`), realizando una última pasada sobre cualquier worker pendiente en `self._workers` creado justo antes de cancelar `balancer`. Solo después cancelar `speed_sampler` y entrar al `finally:` que cierra `self._file_handle`.

#### d) Rechazo de `200 OK` en descargas segmentadas *(Decisión propuesta por el agente)*
- Cuando `self.supports_range` es verdadero y `_worker` obtiene `resp.status == 200` (el servidor ignoró `Range`):
  - Prohibido escribir en `segment.current_byte` desplazado.
  - Si `len(self.segments) == 1` y `segment.current_byte == 0`, degradar limpiamente transmitiendo **inline** la respuesta `200 OK` ya abierta desde el byte `0` (truncando a longitud `0`, fijando `self.supports_range = False` y eliminando `meta_path` si existe) para no lanzar un segundo `GET` ni romper URLs firmadas de un solo uso.
  - En cualquier otro caso (múltiples segmentos o reanudación parcial), marcar error permanente de rango (`"El servidor ignoró Range y devolvió HTTP 200"`), activar `self._stop_event.set()`, fijar `self.status = "error"` y detener los demás workers.

#### e) Reintentos acotados y backoff exponencial *(Decisión propuesta por el agente)*
- Definir `MAX_RETRIES = 5` por segmento y códigos HTTP permanentes `PERMANENT_HTTP_ERRORS = {400, 401, 403, 404, 405, 410, 416}`.
- Errores transitorios (red, timeout, `408`, `429`, `5xx`) incrementan el contador de reintentos del segmento y esperan `min(0.5 * (2 ** (attempt - 1)), 8.0)` segundos antes de reintentar (reseteando el contador si el segmento progresa bytes nuevos).
- Códigos en `PERMANENT_HTTP_ERRORS` o superar `MAX_RETRIES` abortan la tarea: fijan `segment.status = "error"`, `self.error_message` descriptivo, `self.status = "error"` y activan `self._stop_event.set()` para detener el resto de workers sin bucles infinitos.

#### f) Persistencia atómica y limpieza de `.aether.json`
- `_save_state()` escribirá primero a `f"{self.meta_path}.tmp"` con `flush()` y luego reemplazará atómicamente mediante `os.replace(tmp_path, self.meta_path)`.
- En `start()`, el bloque `finally:` cerrará `self._file_handle` y solo invocará `self._save_state()` cuando `self.status != "completed"`. Si `self.status == "completed"`, garantizar que `self.meta_path` (y cualquier `.tmp`) no exista en disco.

#### g) Reanudación íntegra (`probe` en reanudación + `_load_state`) *(Decisión propuesta por el agente)*
- En `start()`, ejecutar `await self.probe(session)` en cada arranque o reanudación (no solo cuando `self.total_size == 0`) para obtener `ETag` y tamaño remoto actualizados, preservando el `self.filename` ya resuelto cuando no se especificó `custom_filename` (mediante un flag interno `_filename_resolved`) para no alterar la ruta de `.aether.json` si el nombre vino de fallback `download_<timestamp>.bin`. Si `probe()` falla en una reanudación, fijar `self.status = "error"` y salir **antes** de modificar o borrar `.aether.json`.
- `_load_state()` verificará coincidencia de `total_size`, `url` y, cuando tanto el estado guardado como la cabecera remota actual contengan `etag` no vacío, exigirá igualdad exacta de `etag` (si no coincide, descartar estado, reiniciar `bytes_downloaded = 0` / `last_sample_bytes = 0` y recrear la descarga desde cero).
- Al restaurar `self.segments`, calcular:
  - `self.bytes_downloaded = sum(max(0, min(s.current_byte, s.end_byte + 1) - s.start_byte) for s in self.segments)`
  - `self.last_sample_bytes = self.bytes_downloaded`

#### h) Soporte completo para `_single_stream_download`
- En `start()`, no abrir `self._file_handle` en `"r+b"` antes de ramificar; abrir `"r+b"` únicamente en la rama con `Range` y abrir `"wb"` dentro de la rama de flujo único (evitando doble apertura del archivo en Windows).
- Iniciar `speed_sampler = asyncio.create_task(self._speed_sampler())` también para descargas lineales y cancelarlo en un `try/finally` interno.
- Si es una descarga nueva o reinicio lineal sin `Range`, reiniciar `seg.current_byte = 0`, `seg.bytes_downloaded = 0`, `self.bytes_downloaded = 0`, `self.last_sample_bytes = 0` antes de abrir el archivo para evitar contadores acumulados > 100%.
- Validar explícitamente `resp.status == 200` (en caso de `PERMANENT_HTTP_ERRORS` u otro código distinto de `200`, marcar `self.status = "error"` con `self.error_message` sin escribir cuerpo HTML de error como descarga válida).
- Si se detiene por `_stop_event` o `asyncio.CancelledError`, dejar `seg.status = "paused"` (no `"completed"`).
- Si finaliza por EOF natural sin `_stop_event`: verificar integridad de longitud cuando `self.total_size > 0` (`self.bytes_downloaded == self.total_size`); si está truncado (`self.bytes_downloaded < self.total_size`), marcar error (`"Descarga truncada antes de completar Content-Length"`). Si está completo (o `self.total_size == 0`), marcar `seg.status = "completed"`; si `self.total_size == 0`, actualizar `self.total_size = self.bytes_downloaded` y `seg.end_byte = max(0, self.bytes_downloaded - 1)`.
- **Política de errores y reintentos en `_single_stream_download` (Aprobada):**
  - Antes de recibir el primer byte (`bytes_downloaded == 0`), reintentar errores transitorios de conexión/timeout o HTTP transitorios (`408`, `429`, `5xx`) hasta `MAX_RETRIES = 5` con backoff exponencial (`min(retry_base_delay * (2 ** (attempt - 1)), 8.0)`).
  - Ante códigos en `PERMANENT_HTTP_ERRORS` o cualquier estado distinto de `200`, fallar de inmediato (`status = "error"`).
  - Ante caída de red o EOF truncado (`total_size > 0` y `bytes_downloaded < total_size`) después de haber descargado bytes (`bytes_downloaded > 0`), fallar de inmediato (`status = "error"`) sin re-descargar automáticamente desde el byte 0.

---

### 5.2 `RateLimiter` (`core/engine.py`) y `Segment` (`core/segment.py`)

- **`RateLimiter` (throttling agregado por reserva de deuda de tokens — Decisión propuesta por el agente):**
  - Sustituir `time.time()` por `time.monotonic()` y mantener un contador de época `_rate_epoch` que se incrementa en `set_rate()`.
  - Dentro de `async with self._lock:`:
    - Si `self.rate_bps <= 0`, retornar de inmediato.
    - Calcular `now = time.monotonic()`, `elapsed = max(0.0, now - self.last_check)`, `self.last_check = now`.
    - Recargar `self.allowance = min(float(self.rate_bps), self.allowance + elapsed * self.rate_bps)`.
    - Descontar mediante reserva de deuda: `self.allowance -= amount` (permitiendo saldo negativo para escalonar a los `N` workers concurrentes y evitar que todos despierten al mismo tiempo excediendo `rate_bps`).
    - Si `self.allowance < 0`: calcular `wait_time = (-self.allowance) / self.rate_bps` y capturar `epoch = self._rate_epoch`.
  - Ejecutar `await asyncio.sleep(...)` **fuera de `self._lock`** en tramos cortos (`min(remaining_wait, 0.2)`), abortando el sueño si `self.rate_bps <= 0` o `self._rate_epoch != epoch` (permitiendo quitar o ajustar el límite en vivo sin bloquear al resto de workers).
- **`Segment.update_progress`:**
  - En `__post_init__` (sin declararlo como campo de `@dataclass`, para que `dataclasses.asdict()` conserve exactamente el esquema JSON actual), inicializar `self._window_bytes = 0` y usar `time.monotonic()`.
  - Acumular `self._window_bytes += new_bytes` en cada actualización.
  - Cuando `delta = now - self.last_update_time >= 0.2`: calcular `instant_speed = self._window_bytes / delta`, actualizar `self.speed_bps` (asignación directa si era `0.0`, luego `self.speed_bps * 0.7 + instant_speed * 0.3`), actualizar `self.last_update_time = now` y reiniciar `self._window_bytes = 0`.

---

### 5.3 Seguridad de Rutas y Archivos (`core/security.py` — módulo nuevo) *(Decisión propuesta por el agente)*

1. **`validate_custom_filename(filename: str) -> str`:**
   - Recortar espacios extremos; si queda vacío, lanzar `ValueError`.
   - Rechazar si es ruta absoluta (`os.path.isabs`), si contiene `/` o `\`, si contiene bytes nulos (`\x00`) o caracteres de control (`ord(c) < 32`), si es `.` o `..` o empieza por `.` (impide sobrescribir `.bashrc`, `.profile`, `.htaccess`, etc.), si contiene `[\\/*?:"<>|]`, o si el nombre base (sin extensión, mayúsculas) coincide con nombres reservados de Windows (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`).
2. **`sanitize_remote_filename(filename: str, fallback: str) -> str`:**
   - Para nombres inferidos desde `Content-Disposition` o URL: extraer `os.path.basename` normalizando `\` a `/`, reemplazar caracteres inválidos/control por `_`, eliminar puntos/espacios iniciales (`lstrip(". ")`), prefijar `_` si coincide con reservado Windows, y devolver `fallback` si queda vacío o resulta `.` / `..`.
3. **`validate_output_dir(requested_dir: Optional[str], default_dir: str) -> str`:**
   - Si `requested_dir` es `None` o cadena vacía tras `strip()`, devolver `os.path.realpath(default_dir)`.
   - Resolver `default_real = os.path.realpath(default_dir)` y `target_real = os.path.realpath(os.path.abspath(os.path.expanduser(requested_dir)))`.
   - Permitir si:
     - `target_real == default_real` o es subdirectorio de `default_real` sin ningún segmento relativo que comience con `.`, **o**
     - `target_real` está estrictamente dentro de `home_real = os.path.realpath(os.path.expanduser("~"))` (`target_real != home_real`) y **ningún** segmento del camino relativo respecto a `home_real` comienza con `.` (bloquea `~/.ssh`, `~/.config`, `~/.local`, `~/.gnupg`, etc.).
   - En cualquier otro caso, lanzar `ValueError("Directorio de destino no permitido")`.
4. **`ensure_within_directory(base_dir: str, target_path: str) -> str`:**
   - Resolver ambos con `os.path.realpath` y exigir `os.path.commonpath([base_real, target_real]) == base_real`; si no coincide, lanzar `ValueError`. Aplicarse tanto en `DownloadTask` como antes de borrar archivos en `QueueManager.remove_download`.

*(Nota: `DownloadTask.__init__` aplicará `validate_custom_filename` si recibe `custom_filename`, y `QueueManager.add_download` aplicará `validate_output_dir`, evitando que llamadas directas al motor salten las validaciones).*

---

### 5.4 Endurecimiento del Servidor Local (`server/app.py`) *(Decisión propuesta por el agente)*

Middleware `security_middleware` aplicado a toda la app `aiohttp`:
1. **Protección contra DNS-rebinding (`Host`):**
   - Extraer hostname de `request.headers.get("Host", "")` (soportando IPv6 `[::1]:8765` y `host:port`).
   - Permitir únicamente `{"127.0.0.1", "localhost", "::1"}` (y opcionalmente el `allowed_host` pasado al crear la app si en pruebas o CLI se usa otro bind loopback). Si no coincide, devolver `403 Forbidden`.
2. **Protección anti-CSRF en `/api/*` y `/ws` sin fricción para la extensión MV3:**
   - No añadir cabeceras CORS abiertas (`Access-Control-Allow-Origin`).
   - Si viene cabecera `Origin`: validar que el esquema sea `http`/`https` con hostname en `{"127.0.0.1", "localhost", "::1"}`, **o** esquema en `{"chrome-extension", "moz-extension"}`. Cualquier otro origen (web externa) recibe `403 Forbidden`.
   - Si viene cabecera `Sec-Fetch-Site` con valor `cross-site` (insensible a mayúsculas) y el `Origin` no corresponde a `chrome-extension` / `moz-extension`, devolver `403 Forbidden`.
3. **Validación estricta de `Content-Type` en endpoints JSON (`POST /api/downloads`, `POST /api/downloads/{id}/limit`):**
   - Exigir `request.content_type == "application/json"`; si no coincide (ej. `text/plain` o formularios HTML cross-origin sin preflight), devolver `415 Unsupported Media Type`.
4. **Validación limpia de entradas REST:**
   - En `add_download` y `set_limit`, convertir `threads` (rango `1..32` o validar entero) y `speed_limit_bps` / `limit_bps` (`>= 0`) dentro de `try/except (ValueError, TypeError)` devolviendo `400 Bad Request`.
   - Capturar `ValueError` de `validate_output_dir` / `validate_custom_filename` y responder `400 Bad Request`.
5. **Desactivar listado de estáticos:**
   - Cambiar `app.router.add_static("/static", ui_dir, show_index=False)`.

---

### 5.5 Eliminación de DOM XSS (`ui/app.js`)

- Añadir helper `safeStatusClass(status)` restringido a la lista blanca `new Set(["initializing", "downloading", "paused", "completed", "error"])` (fallback `"initializing"`).
- Reescribir `renderDownloadsList()` y `renderInspector()` construyendo todos los elementos con `document.createElement(...)`, asignando textos dinámicos (`task.filename`, `task.url`, `task.status`, tamaños, IDs) exclusivamente con `.textContent` y enlazando eventos de pausa/borrado con `addEventListener("click", ...)` (eliminando `innerHTML` con interpolación de datos de tareas y `onclick="..."` inline).
- Eliminar `window.aetherTogglePause` y `window.aetherDelete` del ámbito global al dejar de usar manejadores inline.

---

## 6. Decisiones Delegadas al Implementador (What's Left to the Builder)

- Organización modular de los nuevos tests en `tests/` (p. ej. mantener `tests/test_engine.py` y añadir `tests/test_security.py` / `tests/test_server.py` ejecutables vía `python -m unittest discover -s tests`).
- Diseño de los servidores HTTP mock locales para simular `200 OK` frente a `Range`, caídas transitorias de red, respuestas `404`/`403`, cambios de `ETag` y archivos grandes para probar balanceo dinámico real y throttling.
- Refactorización interna menor de utilidades SVG/DOM en `ui/app.js` mientras conserve los selectores CSS e IDs actuales.
