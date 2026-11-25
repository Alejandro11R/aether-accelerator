# Phase 2A: Web UI Visual Redesign & Incremental UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the Aether Web UI with lightweight static-glass depth, an activity strip above the queue, an actionable empty state, incremental DOM rendering, inline expandable drawers featuring the continuous segmented band, live speed throttling, visible error banners, accessible delete-with-file modals, and cross-platform folder actions.

**Architecture:** `server/app.py` exposes `platform` and `quick_folders` metadata plus a security-hardened `POST /api/system/open-folder` endpoint. `ui/index.html` and `ui/style.css` replace heavy ambient blurs and infinite idle animations with compact, responsive static-glass surfaces across all 3 themes (`obsidian`, `cyber`, `oled`). `ui/app.js` switches to a `rowMap` incremental DOM updater preserving Phase 1's XSS-safe element construction and adds live throttling, inline segment bands, toasts, and visibility-aware sparkline updates.

**Tech Stack:** Python 3.9+, `aiohttp>=3.9.0`, Vanilla HTML5/CSS3/ES2020, `unittest`, Node.js `vm`

**Spec:** `docs/superpowers/specs/2026-11-25-phase2a-ui-redesign-design.md`

## Global Constraints

- **Runtime:** Python `>= 3.9`.
- **Production dependencies:** only `aiohttp>=3.9.0` (zero new Python or JS libraries).
- **Cross-platform portability:** Linux, Windows, and macOS (no `shell=True` subprocesses, platform-appropriate quick folders and folder openers).
- **Security & DOM XSS zero-regression:** preserve `security_middleware` protections (`Host`, `Origin`, `Sec-Fetch-Site`, `Content-Type: application/json`), `core/security.py` validation, and zero `innerHTML` with dynamic data / zero `alert()` / zero `confirm()`.
- **Themes & compatibility:** preserve `obsidian`, `cyber`, and `oled` theme dials and keep existing REST/WS payload fields intact.

## Review Focus

- `POST /api/system/open-folder` rejecting non-JSON `Content-Type`, external `Origin`, traversal/`~/.ssh`, and non-existent directories while safely invoking `folder_opener` without `shell=True` -> pinned in **Task 1** (`tests/test_server.py`).
- Frost (`oled`) theme segmented band visual distinction without relying solely on color, plus elimination of `blur(120px)` ambient halos and idle infinite animations -> pinned in **Task 2** (`ui/index.html`, `ui/style.css`) and **Task 4** (`tests/test_ui_security.py`).
- Incremental DOM node identity stability across multiple `task_progress` events, live `/limit` select not clobbered while focused, and zero XSS sinks in `error_message` or `quick_folders` -> pinned in **Task 3** (`ui/app.js`) and **Task 4** (`tests/test_ui_security.py`).
- `document.hidden === true` skipping sparkline SVG path redraws and resuming on `visibilitychange` -> pinned in **Task 3** and **Task 4**.
- Accessible delete modal sending `?delete_file=true|false`, `Escape` closing active modals, and zero native `alert()`/`confirm()` -> pinned in **Task 3** and **Task 4**.

---

## File Structure

- **Modify:** `server/app.py` — add `_get_platform_key`, `_get_quick_folders`, `open_folder` route + `folder_opener` hook in `create_app`, enforce `Content-Type: application/json` for `/api/system/open-folder` in `security_middleware`, include `platform` and `quick_folders` in `GET /api/system` and `/ws` `"init"`.
- **Modify:** `tests/test_server.py` — add TDD tests for `platform`/`quick_folders` and `/api/system/open-folder` security and behavior.
- **Modify:** `ui/index.html` — new compact header, `.activity-strip`, queue-first layout without top `#inspector-panel`, `#modal-new-download`, `#modal-delete-download`, and `#toast-container`.
- **Modify:** `ui/style.css` — lightweight static glass depth, responsive Hyprland half-screen rules, `.activity-strip`, inline empty-state quick form, `.row-details-drawer`, theme-distinct `.segment-band`, `.item-error-banner`, delete modal, toasts, and `prefers-reduced-motion`.
- **Modify:** `ui/app.js` — incremental `rowMap` renderer, inline empty-state form, dynamic `quick_folders`, expandable inline detail drawer with `.segment-band` + live `/limit` + byte-range `<details>`, error banners, delete modal, toasts, `Escape` key handler, `#btn-open-folder`, WS connection status indicator, and `document.hidden` sparkline pause.
- **Modify:** `tests/test_ui_security.py` — comprehensive static + Node.js `vm.runInNewContext` tests for XSS, incremental DOM reuse, live throttling, error banners, delete modal, `quick_folders`, `open-folder`, `Escape`, and `document.hidden`.

---

### Task 1: Cross-Platform System Metadata & Safe `POST /api/system/open-folder` (`server/app.py`)

**Files:**
- Modify: `server/app.py:139-185,283-346`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `validate_output_dir` from `core/security.py`.
- Produces:
  - `create_app(download_dir: str, ui_dir: str, allowed_hosts: Optional[Set[str]] = None, folder_opener: Optional[Callable[[str], None]] = None) -> web.Application`
  - `GET /api/system` & `/ws` `"init"` payloads including `"platform": str` and `"quick_folders": List[Dict[str, str]]`
  - `POST /api/system/open-folder` (`{"dir"?: str}` -> `{"success": True, "path": str}`)

- [ ] **Step 1: Write the failing tests in `tests/test_server.py`**

Add tests covering:
- `GET /api/system` and `/ws` `"init"` include `"platform"` in `{"linux", "windows", "darwin"}` and non-empty `"quick_folders"` list where every item has `"label"` and `"path"`, while preserving `"total_speed_bps"`, `"active_tasks"`, `"total_tasks"`, `"download_dir"`, and `"downloads"`.
- `POST /api/system/open-folder` with `Content-Type: text/plain` returns `415`; with external `Origin: https://evil.com` returns `403`; with `dir: "~/.ssh"` or non-existent dir returns `400`.
- `POST /api/system/open-folder` with `{}` (valid JSON, `Origin: http://127.0.0.1:<port>`) invokes the injected `folder_opener(os.path.realpath(download_dir))` and returns `200` with `{"success": True, "path": ...}`; with a valid existing subdirectory inside `download_dir` invokes `folder_opener` with that resolved subdirectory path.

- [ ] **Step 2: Run server tests to verify failure**

Run: `.venv/bin/python -W default -m unittest tests/test_server.py -v`
Expected: FAIL (`404` on `/api/system/open-folder`, missing `"platform"`/`"quick_folders"`)

- [ ] **Step 3: Implement helpers, `folder_opener` injection, and `/api/system/open-folder` in `server/app.py`**

- Add `_get_platform_key() -> str` (`"windows"`, `"darwin"`, or `"linux"`).
- Add `_get_quick_folders(download_dir: str) -> List[Dict[str, str]]` building OS-aware `~/` quick routes plus `{"label": "Carpeta Aether", "path": download_dir}` (deduplicating normalized paths).
- Add `_open_folder_native(path: str) -> None` (`os.startfile(path)` on Windows, `subprocess.Popen(["open", path])` on macOS, `subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)` on Linux).
- Update `create_app(..., folder_opener: Optional[Callable[[str], None]] = None)`.
- Extend `security_middleware`'s `Content-Type == "application/json"` guard to include `request.path == "/api/system/open-folder"`.
- Add `"platform"` and `"quick_folders"` to `get_system_info` and `websocket_handler` `"init"` payload.
- Register `app.router.add_post("/api/system/open-folder", open_folder)`.

- [ ] **Step 4: Run full suite to verify pass**

Run: `.venv/bin/python -W default -m unittest discover -s tests -v`
Expected: PASS (0 warnings, 0 errors)

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_server.py
git commit -m "feat(server): add platform quick folders metadata and safe open-folder endpoint"
```

---

### Task 2: Lightweight Static-Glass HTML & CSS Redesign (`ui/index.html`, `ui/style.css`)

**Files:**
- Modify: `ui/index.html:1-275`
- Modify: `ui/style.css:1-925`

**Interfaces:**
- Consumes: Theme dial attributes `data-theme="obsidian|cyber|oled"`.
- Produces: Exact DOM IDs and CSS classes consumed by Task 3 (`ui/app.js`) and Task 4 (`tests/test_ui_security.py`):
  - Header & Theme Dial: `.dial-btn[data-set-theme]`, `#btn-open-modal`
  - Activity Strip: `.activity-strip`, `#live-indicator`, `#total-speed`, `#total-speed-unit`, `#active-tasks-label`, `#total-threads`, `#speed-sparkline`, `#sparkline-area`, `#sparkline-line`, `#btn-open-folder`, `#download-dir-text`
  - Queue Section: `#downloads-count`, `#downloads-list` (with `.download-item-card`, `.row-details-drawer`, `.segment-band`, `.segment-band-cell`, `.row-limit-select`, `.row-ranges-details`, `.item-error-banner`)
  - New Download Modal: `#modal-new-download`, `#btn-close-modal`, `#btn-cancel-modal`, `#form-new-download`, `#input-url`, `#btn-paste`, `#select-threads`, `#select-speed-limit`, `#input-filename`, `#input-dir`, `#quick-folders`
  - Delete Confirmation Modal: `#modal-delete-download`, `#delete-modal-filename`, `#checkbox-delete-file`, `#btn-close-delete-modal`, `#btn-cancel-delete-modal`, `#btn-confirm-delete`
  - Toasts: `#toast-container`

- [ ] **Step 1: Redesign `ui/index.html` with compact activity strip, queue-first layout, accessible modals, and toast container**

- Remove the 3 `<div class="ambient-glow">` elements, remove `.spin-slow` from the brand SVG, remove the 3-card `.metrics-grid` and top `#inspector-panel`.
- Add `<section class="activity-strip glass-card">` above `.downloads-section` containing speed, active tasks/threads, `#live-indicator`, `#speed-sparkline`, and `#btn-open-folder` with `#download-dir-text`.
- Update `<span class="badge-tag">DYNAMIC ACCELERATOR v1.1</span>`.
- Add `role="dialog"`, `aria-modal="true"`, `aria-labelledby="..."` to `#modal-new-download`, clear hardcoded Linux quick folder `<option>` items from `#quick-folders` (leaving only the placeholder option `<option value="">Rutas rápidas...</option>` to be populated dynamically by `app.js`), add `#modal-delete-download` (`#delete-modal-filename`, `#checkbox-delete-file`, `#btn-close-delete-modal`, `#btn-cancel-delete-modal`, `#btn-confirm-delete`), and add `<div class="toast-container" id="toast-container" aria-live="polite"></div>`.

- [ ] **Step 2: Redesign `ui/style.css` for lightweight static-glass 3D depth, segmented band themes, inline quick-form, and responsiveness**

- Remove `.ambient-glow`, `.glow-1/.glow-2/.glow-3`, `@keyframes spin`, and `@keyframes pulse-dot` infinite animations; replace body background with subtle static CSS radial gradients per `[data-theme]` and crisp `--inner-glow` bevels.
- Style `.activity-strip`, `#live-indicator` (`.connected` emerald, `.disconnected` amber/red), `.btn-folder`, `.quick-empty-card` / `.quick-form-row` / `.quick-advanced`, `.download-item-card` (compact row with stable `.item-actions` and `.btn-details`), `.item-error-banner`, `.row-details-drawer`, `.segment-band` + `.segment-band-cell` with distinct styles across `obsidian`, `cyber`, and `oled` (diagonal stripe pattern via `repeating-linear-gradient` + border markers in `oled` Frost so states remain distinct without relying only on color), `.row-ranges-details`, `#modal-delete-download`, `.toast-container`/`.toast`, and `@media (prefers-reduced-motion: reduce)` + `@media (max-width: 900px)` compact stacking.
- Ensure static informational containers (`.activity-strip`, `.glass-header`) do NOT elevate (`translateY`) on `:hover`.

- [ ] **Step 3: Verify existing test suite stays green and check HTML/CSS consistency**

Run: `.venv/bin/python -W default -m unittest discover -s tests -v`
Expected: PASS (all 45+ backend/security tests pass; inspect `ui/index.html` IDs match task interface list)

- [ ] **Step 4: Commit**

```bash
git add ui/index.html ui/style.css
git commit -m "feat(ui): redesign layout with lightweight static-glass depth and segmented band styles"
```

---

### Task 3: Incremental DOM Client, Live Throttling, Inline Detail Band & Modals (`ui/app.js`)

**Files:**
- Modify: `ui/app.js:1-581`

**Interfaces:**
- Consumes: DOM IDs from Task 2 (`ui/index.html`) and REST/WS contracts from Task 1 (`server/app.py`).
- Produces:
  - Preserved security helpers: `ALLOWED_STATUSES`, `safeStatusClass`, `safePercent`, `createSvgIcon`.
  - Incremental `rowMap` renderer, `#form-quick-download` inline submit/paste/advanced options, `expandedTaskIds` inline detail drawer with `.segment-band`, live `POST /api/downloads/{id}/limit` control, `.item-error-banner`, `#modal-delete-download` (`delete_file`), `#toast-container`, `Escape` modal close, dynamic `#quick-folders`, `#btn-open-folder` action, WS status indicator, and `document.hidden` sparkline gating.

- [ ] **Step 1: Refactor `ui/app.js` cleanly while preserving zero-innerHTML XSS safety**

Implement in `ui/app.js`:
1. Maintain `rowMap = new Map()`, `expandedTaskIds = new Set()`, `pendingDeleteTaskId = null`, `quickFoldersList = []`, `wsConnected = false`, and `pendingSparklineRedraw = false`.
2. Update theme dial buttons with `aria-pressed="true|false"` on initialization and click.
3. Update `updateLiveStatus(connected)` toggling `.connected`/`.disconnected` and `title` on `#live-indicator`.
4. Populate `#quick-folders` (and any quick-folder select in the inline empty state) safely from `msg.quick_folders` using `createElement("option")` + `.textContent` + `.value` (keeping placeholder `"Rutas rápidas..."`).
5. On `document.hidden === true`, skip executing `drawSparkline()` and mark `pendingSparklineRedraw = true`; listen to `document.addEventListener("visibilitychange", ...)` to redraw when `!document.hidden && pendingSparklineRedraw`.
6. Replace `renderDownloadsList()` + `renderInspector()` with incremental `syncDownloadsList()`:
   - When `downloads.length === 0`: clear `rowMap`, render `.quick-empty-card` containing `#form-quick-download` (`#quick-url`, `#btn-quick-paste`, `<details class="quick-advanced">` with `#quick-threads`, `#quick-speed-limit`, `#quick-filename`, `#quick-dir` synced with `defaultDownloadDir`/`localStorage`). Submitting posts to `POST /api/downloads` (`Content-Type: application/json`), disables submit while sending, and reports errors via `showToast(...)`.
   - When `downloads.length > 0`: remove any `.quick-empty-card`, remove deleted IDs from `rowMap` and DOM, reorder/insert missing rows using `createTaskRow(task)`, and update existing rows in place via `updateTaskRow(refs, task)` without destroying `<button>` or `<select>` elements.
   - Inside each row's `.row-details-drawer` (shown when `expandedTaskIds.has(task.id)`):
     - Render/update `.segment-band` with proportional `.segment-band-cell` bars (`flexGrow: Math.max(1, seg.total || 1)`), fill width `${safePercent(seg.progress)}%`, and accessible `title`/`aria-label`.
     - Render live speed limit `<select class="row-limit-select">` (0, 1MB/s, 5MB/s, 10MB/s, 20MB/s, plus custom value option if task has another limit); only sync `.value` when `document.activeElement !== refs.limitSelect`; on `change`, `POST /api/downloads/${encodeURIComponent(task.id)}/limit` with `{"limit_bps": val}` and toast feedback on error.
     - Render `<details class="row-ranges-details">` listing each segment's `#id`, `speed_bps`, `current-start / total`.
   - Show `.item-error-banner` (using `.textContent`) whenever `task.status === "error"` and `task.error_message` is non-empty; hide when not in error.
7. Wire `#btn-open-folder` click to `POST /api/system/open-folder` (`{"dir": defaultDownloadDir}`), showing a toast if it fails.
8. Wire `#modal-delete-download`: clicking delete on a row opens `#modal-delete-download` setting `#delete-modal-filename` via `.textContent`, resets `#checkbox-delete-file.checked = false`; confirming sends `DELETE /api/downloads/${encodeURIComponent(id)}?delete_file=${checked}`.
9. Wire `Escape` keydown to close `#modal-new-download` or `#modal-delete-download`, and replace every `alert()`/`confirm()` with `showToast(message, variant)`.

- [ ] **Step 2: Run existing tests to verify no static regressions**

Run: `.venv/bin/python -W default -m unittest tests/test_ui_security.py tests/test_server.py -v`
Expected: PASS (note: existing `test_ui_security.py` must continue passing; if `test_ui_security.py` referenced any removed legacy helper/ID we inspect and align in Task 4, wait — verify NOW so `test_ui_security.py` keeps passing or verify what `test_ui_security.py` checks!).

*(Self-note for implementer: check `tests/test_ui_security.py` lines 50-441 so `ui/app.js` preserves expected IDs used in Phase 1 test or document any selector updates cleanly in Task 4!)*

- [ ] **Step 3: Commit**

```bash
git add ui/app.js
git commit -m "feat(ui): implement incremental DOM rendering, live throttling, inline segment band, and accessible modals"
```

---

### Task 4: Comprehensive UI Behavioral, Performance & Security Verification (`tests/test_ui_security.py`)

**Files:**
- Modify: `tests/test_ui_security.py`

**Interfaces:**
- Consumes: `ui/index.html`, `ui/style.css`, and `ui/app.js`.
- Produces: Complete static + Node.js `vm.runInNewContext` test suite covering all Phase 2A behaviors and Phase 1 XSS invariants.

- [ ] **Step 1: Write/update static and Node.js `vm` tests in `tests/test_ui_security.py`**

Cover all items (with `timeout=15` on `subprocess.run`):
1. Static checks on `ui/index.html`, `ui/style.css`, `ui/app.js`:
   - Zero `inline onclick=`, zero `window.aetherTogglePause`/`window.aetherDelete`, zero `alert(`/confirm(`, zero `.ambient-glow` or `@keyframes spin`/`pulse-dot`, presence of `@media (prefers-reduced-motion: reduce)`, presence of `role="dialog"`/`aria-modal="true"` on both modals, and zero hardcoded `~/Descargas` in HTML `<option>` tags.
2. Functional Node.js `vm.runInNewContext` tests (executing real `ui/app.js` parsed against elements extracted/mirrored from `ui/index.html`):
   - **DOM XSS invariance:** malicious `"init"` payload (`filename`, `url`, `status`, `error_message`, `quick_folders`) never writes dynamic `innerHTML`, renders via `.textContent`, clamps `safePercent` (`NaN`, `-5`, `150`, `"50%;background:red"` -> `0`/`100`), normalizes `status` to `"initializing"`, and percent-encodes `task.id` in `fetchCalls` URLs.
   - **Incremental DOM identity:** emitting `"init"` followed by 3 `"task_progress"` WebSocket updates preserves the exact same `.download-item-card` element object reference in `#downloads-list` (`cardAfter === cardBefore`) while updating progress width and speed text.
   - **Inline detail drawer + `.segment-band` + live `/limit`:** clicking `.btn-details` expands `.row-details-drawer` containing `.segment-band-cell` nodes; changing `.row-limit-select` sends `POST /api/downloads/<id>/limit` with `Content-Type: application/json` and `{"limit_bps": ...}`; holding focus on `.row-limit-select` prevents `task_progress` from overwriting its selected value.
   - **Error banner + delete modal + `Escape` + quick-download + `open-folder` + `document.hidden`:**
     - `status: "error"` displays `.item-error-banner` with `error_message` via `.textContent`.
     - Clicking delete opens `#modal-delete-download`, checking `#checkbox-delete-file` and clicking `#btn-confirm-delete` triggers `DELETE /api/downloads/<id>?delete_file=true`; pressing `Escape` closes open modals.
     - Empty-state `#form-quick-download` submits `POST /api/downloads`; `#btn-open-folder` submits `POST /api/system/open-folder`; `#quick-folders` options come from `msg.quick_folders`.
     - With `document.hidden = true`, `task_progress` updates do not rewrite `#sparkline-line` `d` attribute until `document.hidden = false` + `visibilitychange` fires.

- [ ] **Step 2: Run full project test suite**

Run: `.venv/bin/python -W default -m unittest discover -s tests -v`
Expected: PASS (all tests across `test_engine.py`, `test_security.py`, `test_server.py`, `test_ui_security.py` pass with 0 warnings, 0 errors, 0 skips)

- [ ] **Step 3: Commit**

```bash
git add tests/test_ui_security.py
git commit -m "test(ui): verify incremental DOM rendering, live controls, and XSS invariants"
```
