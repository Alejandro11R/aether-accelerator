# Phase 1: Python Engine Stabilization & Local Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the Python download engine (`core/`) against data corruption, worker leaks, stalled retries, and throttling inaccuracy, while closing local Path Traversal, CSRF/DNS-rebinding, and DOM XSS vulnerabilities across `core/`, `server/`, and `ui/`.

**Architecture:** We add a dedicated path/filename validation module (`core/security.py`), harden `Segment` speed windowing and `RateLimiter` aggregate token-debt pacing, rewrite `DownloadTask`'s worker boundary checking, dynamic worker draining, atomic `.aether.json` persistence, ETag-checked resume, and single-stream resilience, and enforce loopback `Host`/`Origin`/`Sec-Fetch-Site`/`Content-Type` middleware on `server/app.py` plus safe DOM element construction in `ui/app.js`. Every behavioral fix is driven test-first via `unittest` (and Node.js `vm` verification for `ui/app.js`) before editing production code.

**Tech Stack:** Python 3.9+, `aiohttp>=3.9.0`, `unittest` (`IsolatedAsyncioTestCase`), Node.js `vm` (for browser DOM security test)

**Spec:** `docs/superpowers/specs/2026-11-25-phase1-engine-security-design.md`

## Global Constraints

- **Runtime:** Python `>= 3.9`.
- **Production dependencies:** only `aiohttp>=3.9.0` (zero new external packages).
- **Cross-platform portability:** Linux, Windows, and macOS (avoid POSIX-only assumptions without safe fallbacks).
- **API & payload compatibility:** keep existing structures for `/api/downloads`, `/api/system`, `/ws` events (`init`, `task_added`, `task_updated`, `task_progress`, `task_removed`), and all existing public keys in `DownloadTask.to_dict()` and `Segment.to_dict()`.
- **No regressions:** keep all existing tests passing and preserve all 3 UI themes.

## Review Focus

- Mixed-separator traversal (`sub\..\..\evil`), URL-encoded sequences in `Content-Disposition` (`%2e%2e%2f`), and symlink escapes inside `~/Downloads` -> pinned in **Task 1** (`tests/test_security.py`).
- `_dynamic_balancer` shrinking `segment.end_byte` mid-transfer while `_worker` holds a 64 KB chunk extending past `mid`, verifying zero overwrite of byte `mid + 1` -> pinned in **Task 3** (`tests/test_engine.py`).
- Resuming a paused task when the remote server's `ETag` or `Content-Length` changed while paused -> pinned in **Task 3** (`tests/test_engine.py`).
- Live `set_rate(0)` waking sleeping workers immediately and enforcing aggregate `rate_bps` across multiple concurrent workers -> pinned in **Task 2** (`tests/test_engine.py`).
- CSRF via `Content-Type: text/plain`, `Sec-Fetch-Site: cross-site`, DNS-rebinding `Host: evil.com:8765`, cross-origin WebSocket hijack, and XSS payload in `task.filename` -> pinned in **Task 5** (`tests/test_server.py`) and **Task 6** (`tests/test_ui_security.py`).

---

## File Structure

- **Create:** `core/security.py` — strict validation and sanitization for custom filenames, remote filenames, output directories, and containment checks.
- **Modify:** `core/segment.py` — monotonic time and windowed speed aggregation (`_window_bytes`) without changing `to_dict()` schema.
- **Modify:** `core/engine.py` — token-debt `RateLimiter`, chunk-boundary enforcement and `_write_chunk` (`os.pwrite`/Windows `_io_lock`), worker draining loop, atomic `.aether.json` persistence, `ETag` probe-on-resume, `MAX_RETRIES`/backoff, Range `200 OK` guard/inline fallback, and single-stream telemetry/error rules.
- **Modify:** `core/manager.py` — upfront validation of `output_dir`/`filename` in `add_download` and containment check before file deletion in `remove_download`.
- **Modify:** `server/app.py` — `security_middleware` (`Host`, `Origin`, `Sec-Fetch-Site`, `Content-Type`), safe integer parsing (`400`), `ValueError` handling (`400`), and `show_index=False` on `/static`.
- **Modify:** `ui/app.js` — `safeStatusClass` allowlist and `createElement`/`textContent`/`addEventListener` rendering replacing `innerHTML` and inline `onclick` handlers.
- **Create:** `tests/test_security.py`, `tests/test_server.py`, `tests/test_ui_security.py`.
- **Modify:** `tests/test_engine.py`.

---

### Task 1: Security Validation Module (`core/security.py`)

**Files:**
- Create: `core/security.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Consumes: Python standard library only (`os`, `re`, `typing`, `urllib.parse`).
- Produces:
  - `validate_custom_filename(filename: str) -> str`
  - `sanitize_remote_filename(filename: str, fallback: str = "download.bin") -> str`
  - `validate_output_dir(requested_dir: Optional[str], default_dir: str) -> str`
  - `ensure_within_directory(base_dir: str, target_path: str) -> str`

- [ ] **Step 1: Write the failing security unit tests**

Create `tests/test_security.py`:

```python
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.security import (
    ensure_within_directory,
    sanitize_remote_filename,
    validate_custom_filename,
    validate_output_dir,
)


class TestSecurityValidation(unittest.TestCase):
    def test_validate_custom_filename_accepts_safe_names(self):
        self.assertEqual(validate_custom_filename("  ubuntu-24.04.iso  "), "ubuntu-24.04.iso")
        self.assertEqual(validate_custom_filename("archivo_v1.2.tar.gz"), "archivo_v1.2.tar.gz")

    def test_validate_custom_filename_rejects_traversal_hidden_and_reserved(self):
        bad_names = [
            "",
            "   ",
            ".bashrc",
            "..",
            "../evil.sh",
            r"sub\..\..\evil.sh",
            "/etc/passwd",
            "C:\\Windows\\system32\\cmd.exe",
            "bad\x00name.bin",
            "CON",
            "nul.txt",
            "com1",
            "LPT9.log",
            "bad:name?.iso",
        ]
        for name in bad_names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_custom_filename(name)

    def test_sanitize_remote_filename_cleans_paths_encoded_traversal_and_reserved(self):
        self.assertEqual(sanitize_remote_filename("../../%2e%2e/etc/passwd"), "passwd")
        self.assertEqual(sanitize_remote_filename(r"folder\..\..\\.bashrc"), "bashrc")
        self.assertEqual(sanitize_remote_filename("CON.txt"), "_CON.txt")
        self.assertEqual(sanitize_remote_filename("..."), "download.bin")
        self.assertEqual(sanitize_remote_filename("bad<script>?.iso"), "bad_script__.iso")

    def test_validate_output_dir_policies_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp_home, tempfile.TemporaryDirectory() as outside_dir:
            default_dir = os.path.join(tmp_home, "Downloads", "Aether")
            os.makedirs(default_dir, exist_ok=True)
            safe_sub = os.path.join(tmp_home, "Videos")
            os.makedirs(safe_sub, exist_ok=True)
            hidden_dir = os.path.join(tmp_home, ".ssh")
            os.makedirs(hidden_dir, exist_ok=True)
            nested_hidden = os.path.join(tmp_home, "Downloads", ".hidden")
            os.makedirs(nested_hidden, exist_ok=True)

            symlink_escape = os.path.join(tmp_home, "Downloads", "escape_link")
            os.symlink(outside_dir, symlink_escape)

            with patch("os.path.expanduser", side_effect=lambda p: tmp_home if p == "~" else p.replace("~/", tmp_home + "/", 1)):
                self.assertEqual(validate_output_dir(None, default_dir), os.path.realpath(default_dir))
                self.assertEqual(validate_output_dir("", default_dir), os.path.realpath(default_dir))
                self.assertEqual(validate_output_dir("~/Videos", default_dir), os.path.realpath(safe_sub))

                for forbidden in [tmp_home, "~", hidden_dir, nested_hidden, outside_dir, symlink_escape]:
                    with self.subTest(forbidden=forbidden):
                        with self.assertRaises(ValueError):
                            validate_output_dir(forbidden, default_dir)

    def test_ensure_within_directory_blocks_escapes(self):
        with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as outside_dir:
            inside = os.path.join(base_dir, "file.bin")
            self.assertEqual(ensure_within_directory(base_dir, inside), os.path.realpath(inside))
            with self.assertRaises(ValueError):
                ensure_within_directory(base_dir, os.path.join(base_dir, "..", "evil.bin"))
            with self.assertRaises(ValueError):
                ensure_within_directory(base_dir, os.path.join(outside_dir, "evil.bin"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests/test_security.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.security'`

- [ ] **Step 3: Implement `core/security.py`**

Create `core/security.py` implementing:
- `WINDOWS_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})`
- `validate_custom_filename(filename: str) -> str`: strip outer whitespace, reject empty, leading `.`, `/` or `\` or `os.path.isabs`, `..`, control chars (`ord(c) < 32`), `[\\/*?:"<>|]`, Windows drive prefixes (`^[A-Za-z]:`), and reserved Windows stem names (case-insensitive `stem = cleaned.split(".")[0].upper()`).
- `sanitize_remote_filename(filename: str, fallback: str = "download.bin") -> str`: URL-unquote iteratively (e.g. `unquote(filename)`), replace `\` with `/`, take last non-empty path component, replace `[\\/*?:"<>|]` and control chars with `_`, strip leading `.` and whitespace (`lstrip(". ")`), strip trailing dots/spaces (`rstrip(". ")`), if empty or `.`/`..` use `fallback`, if stem in `WINDOWS_RESERVED_NAMES` prefix `_`.
- `validate_output_dir(requested_dir: Optional[str], default_dir: str) -> str`: resolve `default_real = os.path.realpath(os.path.abspath(os.path.expanduser(default_dir)))`. If `requested_dir` is `None` or empty after `.strip()`, return `default_real`. Resolve `target_real = os.path.realpath(os.path.abspath(os.path.expanduser(requested_dir.strip())))` and `home_real = os.path.realpath(os.path.expanduser("~"))`. Helper `_has_hidden_component(base, target)` checks `os.path.relpath(target, base)` parts (reject if any part starts with `.`). Allow if `(target_real == default_real or (os.path.commonpath([default_real, target_real]) == default_real and not _has_hidden_component(default_real, target_real)))` OR `(target_real != home_real and os.path.commonpath([home_real, target_real]) == home_real and not _has_hidden_component(home_real, target_real))`. Otherwise raise `ValueError("Directorio de destino no permitido")`.
- `ensure_within_directory(base_dir: str, target_path: str) -> str`: resolve `base_real = os.path.realpath(base_dir)` and `target_real = os.path.realpath(target_path)`; verify `os.path.commonpath([base_real, target_real]) == base_real`, else raise `ValueError`.

- [ ] **Step 4: Run security tests and full test suite to verify pass**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS (`TestSecurityValidation` tests + existing 5 tests all pass)

- [ ] **Step 5: Commit**

```bash
git add core/security.py tests/test_security.py
git commit -m "feat(security): add strict filename and directory validation module"
```

---

### Task 2: Windowed `Segment` Speed & Aggregate `RateLimiter` (`core/segment.py`, `core/engine.py`)

**Files:**
- Modify: `core/segment.py:1-59`
- Modify: `core/engine.py:21-50`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: Python `time.monotonic`, `asyncio`.
- Produces:
  - `Segment.__post_init__() -> None` (initializes `self._window_bytes = 0`, excludes it from `to_dict()` keys)
  - `Segment.update_progress(new_bytes: int) -> None`
  - `RateLimiter.set_rate(rate_bps: int) -> None` (increments `_rate_epoch`, sets `last_check = time.monotonic()`)
  - `async def RateLimiter.consume(amount: int) -> None` (aggregate token-debt pacing sleeping outside `self._lock`)

- [ ] **Step 1: Write the failing unit tests for `Segment` windowed speed and concurrent `RateLimiter`**

In `tests/test_engine.py`, add tests to `TestSegmentationLogic` (and a new `TestRateLimiterConcurrency(unittest.IsolatedAsyncioTestCase)`):

```python
    def test_segment_windowed_speed_and_to_dict_schema(self):
        seg = Segment(id=0, start_byte=0, current_byte=0, end_byte=999_999)
        expected_keys = set(seg.to_dict().keys())
        fake_clock = [100.0]
        with patch("core.segment.time.monotonic", side_effect=lambda: fake_clock[0]):
            seg.update_progress(65_536)  # t = 100.0 -> initializes window
            fake_clock[0] = 100.1
            seg.update_progress(65_536)  # < 0.2s window -> accumulates
            fake_clock[0] = 100.25
            seg.update_progress(65_536)  # 0.25s elapsed, 131_072 bytes in window
        self.assertAlmostEqual(seg.speed_bps, 131_072 / 0.25, delta=1.0)
        self.assertEqual(set(seg.to_dict().keys()), expected_keys)
        self.assertNotIn("_window_bytes", seg.to_dict())
```

And in `TestRateLimiterConcurrency`:

```python
class TestRateLimiterConcurrency(unittest.IsolatedAsyncioTestCase):
    async def test_aggregate_rate_limit_across_concurrent_workers(self):
        # 200 KB/s limit across 4 concurrent workers each consuming 5 x 16 KB = 320 KB total
        # First ~200 KB uses initial 1s allowance, remaining 120 KB takes ~0.6s -> elapsed >= 0.45s
        limiter = RateLimiter(rate_bps=200_000)
        chunk = 16_000

        async def worker():
            for _ in range(5):
                await limiter.consume(chunk)

        start = time.monotonic()
        await asyncio.gather(*(worker() for _ in range(4)))
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 0.45)

    async def test_live_set_rate_zero_wakes_sleeping_workers_quickly(self):
        limiter = RateLimiter(rate_bps=10_000)
        await limiter.consume(10_000)  # empty bucket
        sleeper = asyncio.create_task(limiter.consume(50_000))  # would sleep ~5s
        await asyncio.sleep(0.05)
        limiter.set_rate(0)
        start = time.monotonic()
        await asyncio.wait_for(sleeper, timeout=0.6)
        self.assertLess(time.monotonic() - start, 0.4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m unittest tests/test_engine.py -v`
Expected: FAIL (on `test_segment_windowed_speed_and_to_dict_schema`, `test_aggregate_rate_limit_across_concurrent_workers`, and `test_live_set_rate_zero_wakes_sleeping_workers_quickly`)

- [ ] **Step 3: Implement `Segment` windowing (`core/segment.py`) and aggregate `RateLimiter` (`core/engine.py`)**

- In `core/segment.py`:
  - Add `__post_init__(self)`: `self._window_bytes = 0`.
  - In `update_progress(self, new_bytes: int)`: use `now = time.monotonic()`, increment `self.bytes_downloaded += new_bytes` and `self.current_byte += new_bytes`. If `self.last_update_time == 0`: set `self.last_update_time = now` (starting the measurement window with `self._window_bytes = 0`). Otherwise increment `self._window_bytes += new_bytes` and, if `delta = now - self.last_update_time >= 0.2`, compute `instant_speed = self._window_bytes / delta`, set `self.speed_bps = instant_speed if self.speed_bps == 0.0 else (self.speed_bps * 0.7) + (instant_speed * 0.3)`, set `self.last_update_time = now`, and reset `self._window_bytes = 0`.
- In `core/engine.py`:
  - In `RateLimiter.__init__`: use `self.last_check = time.monotonic()`, `self._rate_epoch = 0`.
  - In `RateLimiter.set_rate(rate_bps: int)`: `self.rate_bps = max(0, rate_bps)`, `self.allowance = float(self.rate_bps)`, `self.last_check = time.monotonic()`, `self._rate_epoch += 1`.
  - In `RateLimiter.consume(amount: int)`:
    - Under `async with self._lock:`: if `self.rate_bps <= 0`, return; compute `now = time.monotonic()`, `elapsed = max(0.0, now - self.last_check)`, `self.last_check = now`, `self.allowance = min(float(self.rate_bps), self.allowance + elapsed * self.rate_bps)`, `self.allowance -= amount`, `wait_time = (-self.allowance) / self.rate_bps if self.allowance < 0 else 0.0`, `epoch = self._rate_epoch`.
    - Outside `self._lock`: while `wait_time > 0`: if `self.rate_bps <= 0 or self._rate_epoch != epoch: break`; `slice_s = min(wait_time, 0.2)`; `await asyncio.sleep(slice_s)`; `wait_time -= slice_s`.

- [ ] **Step 4: Run tests and full suite to verify pass**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/segment.py core/engine.py tests/test_engine.py
git commit -m "fix(engine): use windowed segment speed and aggregate token-debt rate limiter"
```

---

### Task 3: Dynamic Balancer Integrity, Worker Draining, Atomic Persistence & ETag Resume (`core/engine.py`)

**Files:**
- Modify: `core/engine.py:51-386,430-456`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `validate_custom_filename`, `sanitize_remote_filename`, `ensure_within_directory` from `core/security.py`; `RateLimiter`, `Segment`.
- Produces:
  - `DownloadTask.__init__(..., custom_filename: Optional[str] = None, ...)` (validates `custom_filename` via `validate_custom_filename`, verifies `ensure_within_directory(output_dir, output_path)`, sets `self._filename_resolved`, `self._io_lock = asyncio.Lock()`)
  - `DownloadTask._write_chunk(offset: int, data: bytes) -> None`
  - `DownloadTask._save_state() -> None` (atomic `.tmp` + `os.replace`)
  - `DownloadTask._load_state() -> bool` (`ETag` validation + `bytes_downloaded` / `last_sample_bytes` restoration)

- [ ] **Step 1: Write the failing tests for dynamic balancer exactness, worker draining, `.aether.json` cleanup, and ETag resume**

Add integration tests to `TestFullDownloadIntegration` in `tests/test_engine.py`:
1. `test_dynamic_balancer_splits_without_duplicate_bytes_or_orphan_meta`:
   - Serve an 8 MB payload over a mock server handler that throttles segment starting at offset `0` (`await asyncio.sleep(0.02)` per 64 KB chunk) so other workers finish early and trigger `_dynamic_balancer` (`MIN_SPLIT_CHUNK = 2MB`) while the slow worker is mid-chunk.
   - Track byte ranges requested/served; run `DownloadTask(..., connections=4)` to completion.
   - Assert `task.status == "completed"`, `len(task.segments) > 4`, every segment satisfies `seg.current_byte <= seg.end_byte + 1`, `task.bytes_downloaded == task.total_size == 8 * 1024 * 1024`, downloaded file SHA-256 matches `expected_hash`, and `not os.path.exists(task.meta_path)`.
2. `test_resume_detects_changed_etag_and_restarts_cleanly`:
   - Start a download with `ETag: '"v1"'`, pause after receiving part of the data, verify `.aether.json` exists and contains `etag: '"v1"'`.
   - Switch mock server payload to new content (`new_data`) with `ETag: '"v2"'`.
   - Resume (`await task.start()`): verify `task.status == "completed"`, file SHA-256 matches `hashlib.sha256(new_data).hexdigest()`, and `.aether.json` is deleted on completion.
3. `test_resume_probe_failure_preserves_meta_state`:
   - Pause a valid task so `.aether.json` exists on disk.
   - Stop the mock server (`await self.runner.cleanup()` or return `500`), call `await task.start()`.
   - Assert `task.status == "error"` and `os.path.exists(task.meta_path)` is still `True`.
4. `test_resume_restores_bytes_downloaded_from_disk_state`:
   - Create a fresh `DownloadTask` pointing to the same URL and filename after pausing an earlier `DownloadTask`; check after `_load_state()` (or via progress callback / completed check) that `bytes_downloaded` starts from the persisted progress rather than `0`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m unittest tests/test_engine.py -v`
Expected: FAIL (dynamic balancer writes duplicate bytes and leaves `.aether.json` behind; changed `ETag` corrupts resume; `probe` failure behavior fails)

- [ ] **Step 3: Implement engine fixes in `core/engine.py`**

Implement in `core/engine.py`:
- Import `validate_custom_filename`, `sanitize_remote_filename`, `ensure_within_directory` from `core.security`.
- In `DownloadTask.__init__`:
  - If `custom_filename is not None`: `self.custom_filename = validate_custom_filename(custom_filename)`, `self.filename = self.custom_filename`, `self._filename_resolved = True`. Else `self.custom_filename = None`, `self.filename = "download.bin"`, `self._filename_resolved = False`.
  - Validate `self.output_path = ensure_within_directory(self.output_dir, os.path.join(self.output_dir, self.filename))` and `self.meta_path = self.output_path + ".aether.json"`.
  - Initialize `self._io_lock = asyncio.Lock()`.
- In `_parse_headers(resp)`:
  - Resolve `self.filename` only `if not self.custom_filename and not self._filename_resolved:`, pass parsed name through `sanitize_remote_filename(..., fallback=f"download_{int(time.time())}.bin")`, update `self.output_path = ensure_within_directory(...)`, `self.meta_path`, and set `self._filename_resolved = True`.
  - Unconditionally update size from response headers: check `Content-Range` first (`match = re.search(r'/(\d+)', cr)` -> `self.total_size = int(match.group(1)); self.supports_range = True`), `elif cl and cl.isdigit(): self.total_size = int(cl)`, `else: self.total_size = 0`.
  - Update `self.supports_range` and `self.etag = resp.headers.get("ETag")`.
- Add `_write_chunk(self, offset: int, data: bytes)`:
  - If `hasattr(os, "pwrite")`: call `os.pwrite(self._file_handle.fileno(), data, offset)`.
  - Else: `async with self._io_lock: self._file_handle.seek(offset); self._file_handle.write(data)`.
- In `_worker`:
  - Before writing each `chunk`, acquire `async with self._lock:`: compute `remaining = segment.end_byte - segment.current_byte + 1`; if `remaining <= 0`, set `segment.status = "completed"` and break (leaving the `async with session.get(...)` block cleanly); if `len(chunk) > remaining`, slice `chunk = chunk[:remaining]`; capture `offset = segment.current_byte`, `chunk_len = len(chunk)`, call `segment.update_progress(chunk_len)`, `self.bytes_downloaded += chunk_len`.
  - Write outside `self._lock` via `await self._write_chunk(offset, chunk)`.
- In `start()`:
  - Always call `success = await self.probe(session)`; if `not success`: set `self.status = "error"` and `return` BEFORE opening files or entering `finally`.
  - In Range path (`if self.supports_range and self.total_size > 0:`): check `self._load_state()`; if false, reset `self.bytes_downloaded = 0`, `self.last_sample_bytes = 0`, call `self._init_segments()`, pre-allocate file with `truncate(self.total_size)`. Open `self._file_handle = open(self.output_path, "r+b")`.
  - Drain all workers (including dynamically appended ones):
    ```python
    while True:
        pending = [w for w in self._workers if not w.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    balancer.cancel()
    await asyncio.gather(balancer, return_exceptions=True)
    stragglers = [w for w in self._workers if not w.done()]
    if stragglers:
        await asyncio.gather(*stragglers, return_exceptions=True)
    speed_sampler.cancel()
    await asyncio.gather(speed_sampler, return_exceptions=True)
    ```
  - In `finally:` of `start()`: flush & close `self._file_handle` if open; if `self.status == "completed"`, remove `self.meta_path` and `self.meta_path + ".tmp"` if they exist; else call `self._save_state()`.
- In `_save_state()`: write JSON to `f"{self.meta_path}.tmp"`, `f.flush()`, then `os.replace(tmp_path, self.meta_path)`.
- In `_load_state()`: require `state.get("total_size") == self.total_size` and `state.get("url") == self.url`; if `state.get("etag")` and `self.etag` are both truthy and `state.get("etag") != self.etag`, return `False`. Restore `self.segments`, then set `self.bytes_downloaded = sum(max(0, min(s.current_byte, s.end_byte + 1) - s.start_byte) for s in self.segments)` and `self.last_sample_bytes = self.bytes_downloaded`.
- In `pause()`: flush `self._file_handle` if open, wait/cancel workers as needed, set `status = "paused"`, `speed_bps = 0.0`, and call `self._save_state()`.

- [ ] **Step 4: Run full test suite to verify pass**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/engine.py tests/test_engine.py
git commit -m "fix(engine): prevent dynamic segment overlap, drain dynamic workers, and enforce atomic ETag resume"
```

---

### Task 4: Bounded Retries, Range `200 OK` Handling & Single-Stream Resilience (`core/engine.py`)

**Files:**
- Modify: `core/engine.py:18-20,52-92,273-406`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `DownloadTask` lifecycle from Task 3.
- Produces:
  - Constants `MAX_RETRIES = 5`, `PERMANENT_HTTP_ERRORS = frozenset({400, 401, 403, 404, 405, 410, 416})` in `core/engine.py`
  - Optional parameter `retry_base_delay: float = 0.5` in `DownloadTask.__init__`

- [ ] **Step 1: Write the failing tests for bounded retries, `200 OK` Range rejection/inline fallback, and single-stream behavior**

Add tests in `tests/test_engine.py`:
1. `test_worker_stops_on_permanent_http_error_without_infinite_loop`:
   - Mock server responds `200` to `HEAD` (with `Accept-Ranges: bytes`, `Content-Length: 100000`) and `404` to `GET`.
   - Assert `await asyncio.wait_for(task.start(), timeout=2.0)` finishes with `task.status == "error"` and `task.error_message` mentioning `404`.
2. `test_worker_retries_transient_error_up_to_max_retries`:
   - Mock server responds `503` on first 2 range GETs then succeeds, AND a separate test case returning `503` forever with `retry_base_delay=0.01` asserts `task.status == "error"` after `MAX_RETRIES` attempts (counting server GET hits <= `MAX_RETRIES + 1` per segment).
3. `test_multi_segment_worker_rejects_200_ok_without_corrupting_file`:
   - Mock server returns `Accept-Ranges: bytes` on `HEAD` but ignores `Range` on `GET` and returns `200 OK` with full body.
   - Run `DownloadTask(..., connections=4, retry_base_delay=0.01)` and assert `task.status == "error"` and `"200"` in `(task.error_message or "")`.
4. `test_single_connection_200_ok_degrades_inline_without_second_get`:
   - Mock server returns `Accept-Ranges: bytes` on `HEAD`, counts `GET` calls, and returns `200 OK` with full 1 MB payload on the first `GET`.
   - Run `DownloadTask(..., connections=1)` and assert `task.status == "completed"`, `get_count == 1`, `task.supports_range is False`, and SHA-256 matches `1 MB` payload.
5. `test_single_stream_telemetry_pause_truncation_and_error_handling`:
   - Sub-case A: non-Range stream (`Accept-Ranges` omitted) emits `on_update` telemetry, matches SHA-256 on completion, and works when `Content-Length` is omitted (`total_size == 0` -> updated to downloaded length on completion).
   - Sub-case B: pausing non-Range stream sets `task.status == "paused"` and segment `status == "paused"` (NOT `"completed"`), and resuming restarts cleanly from byte 0 without exceeding 100%.
   - Sub-case C: non-Range stream returning `404` fails immediately with `status == "error"`; truncated EOF (`Content-Length: 10000` sending only `1000` bytes then closing) fails immediately with `status == "error"` without retrying from byte 0.
   - Sub-case D: transient `503` before byte 0 on non-Range stream retries up to `MAX_RETRIES` with `retry_base_delay=0.01` and succeeds when server recovers on attempt 2.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m unittest tests/test_engine.py -v`
Expected: FAIL (infinite loop/timeout, 404 HTML saved as valid file, `get_count` > 1, paused single-stream marked `"completed"`)

- [ ] **Step 3: Implement bounded retries, `200 OK` Range guard/inline fallback, and single-stream resilience in `core/engine.py`**

- Define `MAX_RETRIES = 5` and `PERMANENT_HTTP_ERRORS = frozenset({400, 401, 403, 404, 405, 410, 416})`.
- Add `retry_base_delay: float = 0.5` to `DownloadTask.__init__`.
- In `start()`:
  - Branch using `if self.supports_range and self.total_size > 0:` for the Range worker path (opening `self._file_handle = open(self.output_path, "r+b")` strictly inside this branch).
  - In the `else:` branch, run `speed_sampler = asyncio.create_task(self._speed_sampler())`, reset single-stream state cleanly (`self._init_segments()`, `self.bytes_downloaded = 0`, `self.last_sample_bytes = 0`), track `single_task = asyncio.create_task(self._single_stream_download(session))` inside `self._workers = [single_task]` so `pause()` cancels stalled streams immediately, await `single_task`, and cancel/await `speed_sampler` in `try/finally`.
  - Check completion carefully: for Range path `all_done = all(s.current_byte > s.end_byte for s in self.segments)`; for single-stream `all_done = len(self.segments) == 1 and self.segments[0].status == "completed"`. Only mark `self.status = "completed"` when `all_done and not self._stop_event.is_set() and self.status != "error"`.
- In `_worker`:
  - Track `retries = 0`.
  - If `resp.status == 200`:
    - If `len(self.segments) == 1 and segment.current_byte == 0`: consume `resp.content.read(BUFFER_SIZE)` inline into `offset = 0` (truncating file to 0 under lock, setting `self.supports_range = False`, updating `segment` and `self.bytes_downloaded`, removing `meta_path` if present) and break on completion (`segment.status = "completed"`).
    - Else: set `segment.status = "error"`, `self.error_message = "El servidor ignoró Range y devolvió HTTP 200"`, `self.status = "error"`, `self._stop_event.set()`, and break.
  - If `resp.status in PERMANENT_HTTP_ERRORS`: set `segment.status = "error"`, `self.error_message = f"Error HTTP permanente {resp.status}"`, `self.status = "error"`, `self._stop_event.set()`, and break.
  - If `resp.status != 206` or transient network/timeout error: increment `retries`, reset `retries = 0` whenever a chunk of `> 0` bytes is read; if `retries > MAX_RETRIES`: set `segment.status = "error"`, `self.error_message`, `self.status = "error"`, `self._stop_event.set()`, break; otherwise `await asyncio.sleep(min(self.retry_base_delay * (2 ** (retries - 1)), 8.0))`.
- In `_single_stream_download`:
  - Retry loop up to `MAX_RETRIES` **only** while `self.bytes_downloaded == 0`: validate `resp.status == 200` (if `resp.status in PERMANENT_HTTP_ERRORS` or `resp.status != 200` after retries exhausted -> set `seg.status = "error"`, `self.status = "error"`, `self.error_message`, return).
  - Open `self._file_handle = open(self.output_path, "wb")` only after `resp.status == 200` is confirmed, stream chunks, consume `self.rate_limiter`, update `seg` & `self.bytes_downloaded`.
  - On `asyncio.CancelledError` or `self._stop_event.is_set()`: set `seg.status = "paused"` and return.
  - On stream exception after `self.bytes_downloaded > 0` or truncated EOF (`self.total_size > 0 and self.bytes_downloaded < self.total_size`): set `seg.status = "error"`, `self.status = "error"`, `self.error_message`, return (no retry from byte 0).
  - On valid EOF (`not self._stop_event.is_set()`): if `self.total_size == 0`, set `self.total_size = self.bytes_downloaded` and `seg.end_byte = max(0, self.bytes_downloaded - 1)`; set `seg.status = "completed"`.

- [ ] **Step 4: Run full test suite to verify pass**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/engine.py tests/test_engine.py
git commit -m "fix(engine): bound retries, guard Range 200 responses, and harden single-stream downloads"
```

---

### Task 5: Local Server Security Hardening & Safe `QueueManager` (`core/manager.py`, `server/app.py`)

**Files:**
- Modify: `core/manager.py:35-118`
- Modify: `server/app.py:12-143`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `validate_custom_filename`, `validate_output_dir`, `ensure_within_directory` from `core/security.py`.
- Produces:
  - `QueueManager.add_download(url, threads=8, filename=None, output_dir=None, speed_limit_bps=0) -> DownloadTask` (validates `output_dir` and `filename` upfront)
  - `create_app(download_dir: str, ui_dir: str, allowed_hosts: Optional[Set[str]] = None) -> web.Application`

- [ ] **Step 1: Write the failing server & manager security integration tests**

Create `tests/test_server.py` using `aiohttp.test_utils.AioHTTPTestCase` / `TestClient` + `TestServer`:
- Verify `GET /static/` returns `403`/`404` (directory listing disabled), while `GET /static/app.js` returns `200`.
- Verify DNS-rebinding rejection: request with `Host: evil.example.com:8765` returns `403`.
- Verify CSRF protection on `/api/downloads`, `/api/downloads/{id}/pause`, `/api/downloads/{id}/limit`, `/api/downloads/{id}`, and `/ws`:
  - `POST /api/downloads` with `Origin: https://evil.com` -> `403`.
  - `POST /api/downloads` with `Sec-Fetch-Site: cross-site` and no extension `Origin` -> `403`.
  - `POST /api/downloads` with `Content-Type: text/plain` (JSON body) -> `415`.
  - `GET /ws` with `Origin: https://evil.com` -> `403`.
  - `POST /api/downloads` with `Origin: chrome-extension://abcdef123456` and `Sec-Fetch-Site: cross-site` and `Content-Type: application/json` -> allowed (`200`), and `Origin: http://127.0.0.1:<port>` -> allowed (`200`).
- Verify input validation:
  - `POST /api/downloads` with `filename: "../../evil.sh"` or `filename: ".bashrc"` -> `400`.
  - `POST /api/downloads` with `dir: "/tmp/outside_home"` or `dir: "~/.ssh"` -> `400`.
  - `POST /api/downloads` with `threads: "not-an-int"` or `speed_limit_bps: "abc"` or `POST /api/downloads/{id}/limit` with `limit_bps: "bad"` -> `400` (not `500`).
- Verify `QueueManager.remove_download(..., delete_file=True)` removes normal files inside `output_dir` and refuses path escapes via `ensure_within_directory`.

- [ ] **Step 2: Run server tests to verify they fail**

Run: `.venv/bin/python -m unittest tests/test_server.py -v`
Expected: FAIL (`Host: evil.example.com` returns 200, `Origin: https://evil.com` returns 200, `text/plain` returns 200, `../../evil.sh` returns 200, `threads: "bad"` raises 500, `/static/` returns directory index)

- [ ] **Step 3: Implement `QueueManager` validation (`core/manager.py`) and `security_middleware` (`server/app.py`)**

- In `core/manager.py`:
  - Import `validate_custom_filename`, `validate_output_dir`, `ensure_within_directory`.
  - In `add_download`: compute `target_dir = validate_output_dir(output_dir, self.download_dir)` and `safe_filename = validate_custom_filename(filename) if filename else None` **before** `os.makedirs(target_dir, exist_ok=True)`.
  - In `remove_download(..., delete_file=True)`: verify `ensure_within_directory(task.output_dir, path)` before `os.remove(path)`.
- In `server/app.py`:
  - Update signature: `def create_app(download_dir: str, ui_dir: str, allowed_hosts: Optional[Set[str]] = None) -> web.Application:`.
  - Add `@web.middleware async def security_middleware(request, handler):`:
    1. Parse hostname from `request.headers.get("Host", "")` (strip port, strip IPv6 `[...]`), reject with `403` if not in `{"127.0.0.1", "localhost", "::1"} | (allowed_hosts or set())`.
    2. If `request.path.startswith("/api/") or request.path == "/ws"`:
       - Check `Origin`: if present, parse via `urllib.parse.urlparse`; allow `scheme in {"http", "https"}` when `hostname in {"127.0.0.1", "localhost", "::1"} | (allowed_hosts or set())`, OR `scheme in {"chrome-extension", "moz-extension"}`. Otherwise return `403`.
       - Check `Sec-Fetch-Site`: if `.lower() == "cross-site"` and `origin_scheme not in {"chrome-extension", "moz-extension"}`, return `403`.
    3. For `POST` on `/api/downloads` and `/api/downloads/{id}/limit`: require `request.content_type == "application/json"`, else return `415`.
  - In `add_download`: parse `threads = int(body.get("threads", 8))` and `speed_limit = int(body.get("speed_limit_bps", 0))` inside `try/except (ValueError, TypeError)` (rejecting `threads < 1 or threads > 32`, `speed_limit < 0`, or booleans with `400`); catch `ValueError` from `manager.add_download` and return `400`.
  - In `set_limit`: parse `limit_bps = int(body.get("limit_bps", 0))` (rejecting `< 0`, booleans, or non-ints with `400`).
  - Set `app.router.add_static("/static", ui_dir, show_index=False)`.

- [ ] **Step 4: Run server tests and full test suite to verify pass**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/manager.py server/app.py tests/test_server.py
git commit -m "fix(security): harden local server against CSRF, DNS rebinding, and path traversal"
```

---

### Task 6: Eliminate DOM XSS in Web UI (`ui/app.js`)

**Files:**
- Modify: `ui/app.js:220-353`
- Test: `tests/test_ui_security.py`

**Interfaces:**
- Consumes: Task JSON payloads (`id`, `filename`, `url`, `status`, `bytes_downloaded`, `total_size`, `progress`, `speed_bps`, `eta_seconds`, `connections`, `supports_range`, `segments`).
- Produces:
  - Safe DOM construction inside `ui/app.js` using `ALLOWED_STATUSES`, `safeStatusClass(status)`, `document.createElement`, `document.createElementNS`, `.textContent`, and `addEventListener`.

- [ ] **Step 1: Write the failing DOM XSS verification test**

Create `tests/test_ui_security.py`:
- Static check asserting `ui/app.js` no longer contains `window.aetherTogglePause`, `window.aetherDelete`, or `onclick=`.
- Functional check (using `node` via `subprocess.run` when `shutil.which("node")` is available) that evaluates `ui/app.js` inside `vm.runInNewContext` with a lightweight mock DOM (`createElement`, `createElementNS`, `innerHTML` getter/setter trap that throws on any non-empty dynamic task write, `textContent` tracking, `addEventListener`), fires `DOMContentLoaded`, triggers `ws.onmessage` with an `"init"` payload containing:
  ```js
  {
    id: "abc');alert(1);//",
    filename: "<img src=x onerror=alert(1)>'\"",
    url: "https://example.com/<svg/onload=alert(1)>",
    status: "downloading\" onclick=\"alert(1)",
    bytes_downloaded: 50,
    total_size: 100,
    progress: 50,
    speed_bps: 1024,
    eta_seconds: 1,
    connections: 1,
    supports_range: true,
    segments: [{ id: 0, start_byte: 0, current_byte: 50, end_byte: 99, total: 100, progress: 50, speed_bps: 1024, status: "downloading\" onload=\"alert(1)" }]
  }
  ```
  Assert:
  - Zero dynamic `innerHTML` assignments occurred during task/inspector rendering (except clearing containers with `""` or rendering the static empty state).
  - The malicious `filename` and `url` appear strictly in `.textContent` and never create `<img>`/`<svg>` elements or `onclick` attributes.
  - The forged `status` badge class resolves strictly to `badge-initializing` (allowlist fallback) instead of injecting spaces/quotes into `className`.

- [ ] **Step 2: Run UI security test to verify it fails**

Run: `.venv/bin/python -m unittest tests/test_ui_security.py -v`
Expected: FAIL (`window.aetherTogglePause` / `onclick=` found and `innerHTML` trap triggered by `renderDownloadsList`)

- [ ] **Step 3: Refactor `renderInspector` and `renderDownloadsList` in `ui/app.js`**

- Add `const ALLOWED_STATUSES = new Set(["initializing", "downloading", "paused", "completed", "error"]);` and `function safeStatusClass(status) { return ALLOWED_STATUSES.has(status) ? status : "initializing"; }`.
- Add a tiny helper `createSvgIcon(width, height, fill, stroke, strokeWidth, childrenSpecs)` using `document.createElementNS("http://www.w3.org/2000/svg", ...)` for pause/play/delete icons.
- Rewrite `renderInspector(task)` segment block creation with `document.createElement` + `.textContent` and `safeStatusClass(seg.status)` (`seg.status === "downloading" ? "active" : seg.status === "completed" ? "completed" : ""`), removing `block.innerHTML`.
- Rewrite `renderDownloadsList()` card creation with `document.createElement` + `.textContent`, `badge-${safeStatusClass(task.status)}`, and `btn.addEventListener("click", (e) => { e.stopPropagation(); ... })`, removing `card.innerHTML`, inline `onclick`, `window.aetherTogglePause`, and `window.aetherDelete`.

- [ ] **Step 4: Run UI security tests and full project test suite to verify pass**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS (all Python unit, security, server, engine, and UI DOM XSS tests pass with zero warnings/errors)

- [ ] **Step 5: Commit**

```bash
git add ui/app.js tests/test_ui_security.py
git commit -m "fix(ui): eliminate DOM XSS by replacing innerHTML task rendering with safe DOM nodes"
```
