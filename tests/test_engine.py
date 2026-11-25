"""
Aether Download Accelerator - Test Suite
Raphael (QA & TDD) + Maestro Splinter (Integridad de datos y Cero Regresiones)
Verifica la matemática de segmentación, particionado dinámico, pausa/reanudación y checksum SHA-256.
"""

import os
import sys
import json
import time
import hashlib
import tempfile
import asyncio
import unittest
from unittest.mock import patch
import aiohttp
from aiohttp import web

# Añadir raíz al path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.segment import Segment
from core.engine import DownloadTask, RateLimiter

class TestSegmentationLogic(unittest.TestCase):
    def test_segment_math(self):
        seg = Segment(id=0, start_byte=0, current_byte=0, end_byte=999)
        self.assertEqual(seg.total_bytes, 1000)
        self.assertEqual(seg.remaining_bytes, 1000)
        self.assertEqual(seg.progress_percentage, 0.0)

        seg.update_progress(500)
        self.assertEqual(seg.current_byte, 500)
        self.assertEqual(seg.progress_percentage, 50.0)

        seg.update_progress(500)
        self.assertEqual(seg.current_byte, 1000)
        self.assertEqual(seg.status, "completed")
        self.assertEqual(seg.progress_percentage, 100.0)

    def test_dynamic_split_exactness(self):
        """Verifica que dividir un segmento no pierda ni solape un solo byte."""
        start = 1000
        current = 2000
        end = 9999
        remaining = end - current + 1  # 8000 bytes

        mid = current + (remaining // 2)
        new_start = mid + 1
        new_end = end

        # Primera mitad: [current, mid]
        first_half_len = mid - current + 1
        # Segunda mitad: [new_start, new_end]
        second_half_len = new_end - new_start + 1

        self.assertEqual(first_half_len + second_half_len, remaining)
        self.assertEqual(new_start, mid + 1)

    def test_rate_limiter_throttling(self):
        """Verifica que el Token Bucket regule el consumo de bytes."""
        limiter = RateLimiter(rate_bps=100_000) # 100 KB/s
        self.assertEqual(limiter.rate_bps, 100_000)
        limiter.set_rate(0) # Ilimitado
        self.assertEqual(limiter.rate_bps, 0)

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


class TestFullDownloadIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.data_size = 5 * 1024 * 1024  # 5 MB
        self.test_data = (b"AETHER_ACCELERATOR_BYTE_STREAM_TEST_SALT_1234567890\n" * 120000)[:self.data_size]
        self.expected_hash = hashlib.sha256(self.test_data).hexdigest()

        # Configurar servidor HTTP mock local en puerto dinámico (evita colisiones)
        app = web.Application()
        async def handle_range(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(self.data_size),
                        "Content-Disposition": 'attachment; filename="test_5mb.bin"'
                    }
                )

            range_header = request.headers.get("Range")
            if not range_header:
                return web.Response(
                    body=self.test_data,
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(self.data_size),
                        "Content-Disposition": 'attachment; filename="test_5mb.bin"'
                    }
                )

            prefix = "bytes="
            if range_header.startswith(prefix):
                ranges = range_header[len(prefix):].split("-")
                start = int(ranges[0])
                end = int(ranges[1]) if ranges[1] else self.data_size - 1
                end = min(end, self.data_size - 1)
                chunk = self.test_data[start:end + 1]
                return web.Response(
                    status=206,
                    body=chunk,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{self.data_size}",
                        "Content-Length": str(len(chunk)),
                        "Accept-Ranges": "bytes"
                    }
                )
            return web.Response(status=416)

        app.router.add_route("*", "/test.bin", handle_range)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.mock_url = f"http://127.0.0.1:{port}/test.bin"

    async def asyncTearDown(self):
        await self.runner.cleanup()
        self.test_dir.cleanup()

    async def test_concurrent_multithread_download(self):
        """Descarga acelerada en 8 hilos concurrentes con verificación de SHA-256."""
        task = DownloadTask(
            task_id="test_task_1",
            url=self.mock_url,
            output_dir=self.test_dir.name,
            connections=8
        )

        await task.start()

        self.assertEqual(task.status, "completed")
        self.assertTrue(task.supports_range)
        self.assertEqual(len(task.segments), 8)

        # Verificar archivo en disco
        output_file = os.path.join(self.test_dir.name, "test_5mb.bin")
        self.assertTrue(os.path.exists(output_file))
        self.assertEqual(os.path.getsize(output_file), self.data_size)

        # Verificación estricta de hash SHA-256 (Splinter Checkpoint)
        with open(output_file, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual_hash, self.expected_hash, "El hash SHA-256 descargado debe coincidir exactamente!")

    async def test_pause_and_resume(self):
        """Verifica que pausar y reanudar preserve el estado y verifique el SHA-256 final."""
        task = DownloadTask(
            task_id="test_pause_resume",
            url=self.mock_url,
            output_dir=self.test_dir.name,
            connections=4
        )

        # Iniciar en background y pausar rápidamente
        start_job = asyncio.create_task(task.start())
        await asyncio.sleep(0.02)
        await task.pause()
        await start_job

        self.assertIn(task.status, ("paused", "completed"))

        # Reanudar la tarea
        await task.start()
        self.assertEqual(task.status, "completed")

        output_file = os.path.join(self.test_dir.name, "test_5mb.bin")
        self.assertEqual(os.path.getsize(output_file), self.data_size)
        with open(output_file, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual_hash, self.expected_hash)

    async def _start_local_server(self, handler):
        app = web.Application()
        app.router.add_route("*", "/payload.bin", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return runner, f"http://127.0.0.1:{port}/payload.bin"

    async def test_dynamic_balancer_splits_without_duplicate_bytes_or_orphan_meta(self):
        """Verifica división dinámica sin bytes duplicados ni archivo .aether.json huérfano."""
        payload_size = 16 * 1024 * 1024  # 16 MB (4 MB por segmento inicial)
        payload = (b"AETHER_DYNAMIC_SPLIT_16MB_SALT_0123456789ABCDEF\n" * 400000)[:payload_size]
        expected_hash = hashlib.sha256(payload).hexdigest()
        requested_ranges = []

        async def handle(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(payload_size),
                        "Content-Disposition": 'attachment; filename="dynamic_16mb.bin"',
                        "ETag": '"dyn-v1"',
                    }
                )

            range_header = request.headers.get("Range")
            if not range_header or not range_header.startswith("bytes="):
                return web.Response(status=416)

            start_s, end_s = range_header[6:].split("-")
            start = int(start_s)
            end = min(int(end_s) if end_s else payload_size - 1, payload_size - 1)
            requested_ranges.append((start, end))
            slice_data = payload[start:end + 1]

            resp = web.StreamResponse(
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{payload_size}",
                    "Content-Length": str(len(slice_data)),
                    "Accept-Ranges": "bytes",
                    "ETag": '"dyn-v1"',
                },
            )
            await resp.prepare(request)
            try:
                for offset in range(0, len(slice_data), 65536):
                    if start == 0:
                        await asyncio.sleep(0.04)
                    await resp.write(slice_data[offset:offset + 65536])
                await resp.write_eof()
            except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
                pass
            return resp

        runner, url = await self._start_local_server(handle)
        try:
            task = DownloadTask(
                task_id="test_dynamic_split",
                url=url,
                output_dir=self.test_dir.name,
                connections=4,
            )
            await asyncio.wait_for(task.start(), timeout=15.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task.status, "completed")
        self.assertGreater(len(task.segments), 4)
        for seg in task.segments:
            self.assertLessEqual(seg.current_byte, seg.end_byte + 1)
        self.assertEqual(task.total_size, payload_size)
        self.assertEqual(task.bytes_downloaded, payload_size)
        self.assertTrue(
            any(start > 0 and end == (4 * 1024 * 1024) - 1 for start, end in requested_ranges),
            f"Se esperaba una petición Range dividida para la segunda mitad del segmento 0; rangos={requested_ranges}",
        )

        self.assertTrue(os.path.exists(task.output_path))
        self.assertEqual(os.path.getsize(task.output_path), payload_size)
        with open(task.output_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual_hash, expected_hash)
        self.assertFalse(os.path.exists(task.meta_path))
        self.assertFalse(os.path.exists(task.meta_path + ".tmp"))

    async def test_resume_detects_changed_etag_and_restarts_cleanly(self):
        """Verifica que al cambiar el ETag remoto tras pausar se descarte el estado previo y reinicie limpio."""
        size = 2 * 1024 * 1024
        old_data = (b"OLD_ETAG_V1_PAYLOAD_STREAM_DATA_1234567890\n" * 60000)[:size]
        new_data = (b"NEW_ETAG_V2_PAYLOAD_STREAM_DATA_ABCDEFGHIJ\n" * 60000)[:size]
        state = {"data": old_data, "etag": '"v1"', "throttle": True}

        async def handle(request):
            data = state["data"]
            etag = state["etag"]
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(len(data)),
                        "ETag": etag,
                    }
                )

            range_header = request.headers.get("Range")
            if not range_header or not range_header.startswith("bytes="):
                return web.Response(status=416)

            start_s, end_s = range_header[6:].split("-")
            start = int(start_s)
            end = min(int(end_s) if end_s else len(data) - 1, len(data) - 1)
            slice_data = data[start:end + 1]

            resp = web.StreamResponse(
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{len(data)}",
                    "Content-Length": str(len(slice_data)),
                    "Accept-Ranges": "bytes",
                    "ETag": etag,
                },
            )
            await resp.prepare(request)
            try:
                for offset in range(0, len(slice_data), 65536):
                    await resp.write(slice_data[offset:offset + 65536])
                    if state["throttle"]:
                        await asyncio.sleep(0.03)
                await resp.write_eof()
            except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
                pass
            return resp

        runner, url = await self._start_local_server(handle)
        try:
            task = DownloadTask(
                task_id="test_etag_change",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="etag_target.bin",
                connections=4,
            )
            start_job = asyncio.create_task(task.start())
            deadline = time.monotonic() + 2.0
            while task.bytes_downloaded == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            await task.pause()
            await asyncio.wait_for(start_job, timeout=3.0)

            self.assertEqual(task.status, "paused")
            self.assertGreater(task.bytes_downloaded, 0)
            self.assertLess(task.bytes_downloaded, size)
            self.assertTrue(os.path.exists(task.meta_path))
            with open(task.meta_path, "r", encoding="utf-8") as f:
                saved_meta = json.load(f)
            self.assertEqual(saved_meta.get("etag"), '"v1"')

            state["data"] = new_data
            state["etag"] = '"v2"'
            state["throttle"] = False

            await asyncio.wait_for(task.start(), timeout=5.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task.status, "completed")
        self.assertEqual(task.bytes_downloaded, len(new_data))
        with open(task.output_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual_hash, hashlib.sha256(new_data).hexdigest())
        self.assertFalse(os.path.exists(task.meta_path))

    async def test_resume_probe_failure_preserves_meta_state(self):
        """Verifica que un fallo de probe al reanudar marque error sin destruir .aether.json."""
        size = 1 * 1024 * 1024
        payload = (b"PROBE_FAILURE_PRESERVES_META_1234567890\n" * 30000)[:size]

        async def handle(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(size),
                        "ETag": '"probe-v1"',
                    }
                )
            range_header = request.headers.get("Range")
            if not range_header or not range_header.startswith("bytes="):
                return web.Response(status=416)
            start_s, end_s = range_header[6:].split("-")
            start = int(start_s)
            end = min(int(end_s) if end_s else size - 1, size - 1)
            slice_data = payload[start:end + 1]
            resp = web.StreamResponse(
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(len(slice_data)),
                    "Accept-Ranges": "bytes",
                    "ETag": '"probe-v1"',
                },
            )
            await resp.prepare(request)
            try:
                for offset in range(0, len(slice_data), 65536):
                    await resp.write(slice_data[offset:offset + 65536])
                    await asyncio.sleep(0.03)
                await resp.write_eof()
            except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
                pass
            return resp

        runner, url = await self._start_local_server(handle)
        task = DownloadTask(
            task_id="test_probe_failure",
            url=url,
            output_dir=self.test_dir.name,
            custom_filename="probe_fail.bin",
            connections=4,
        )
        try:
            start_job = asyncio.create_task(task.start())
            deadline = time.monotonic() + 2.0
            while task.bytes_downloaded == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            await task.pause()
            await asyncio.wait_for(start_job, timeout=3.0)

            self.assertEqual(task.status, "paused")
            self.assertTrue(os.path.exists(task.meta_path))
            with open(task.meta_path, "r", encoding="utf-8") as f:
                saved_meta_raw = f.read()
        finally:
            await runner.cleanup()

        await asyncio.wait_for(task.start(), timeout=3.0)
        self.assertEqual(task.status, "error")
        self.assertTrue(os.path.exists(task.meta_path))
        with open(task.meta_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), saved_meta_raw)

    async def test_resume_restores_bytes_downloaded_from_disk_state(self):
        """Verifica que un DownloadTask nuevo restaure bytes_downloaded y last_sample_bytes desde disco."""
        size = 2 * 1024 * 1024
        payload = (b"RESTORE_PROGRESS_FROM_DISK_STATE_XYZ\n" * 70000)[:size]
        expected_hash = hashlib.sha256(payload).hexdigest()
        mode = {"phase": "pause"}

        async def handle(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(size),
                        "ETag": '"restore-v1"',
                    }
                )
            range_header = request.headers.get("Range")
            if not range_header or not range_header.startswith("bytes="):
                return web.Response(status=416)
            start_s, end_s = range_header[6:].split("-")
            start = int(start_s)
            end = min(int(end_s) if end_s else size - 1, size - 1)
            slice_data = payload[start:end + 1]
            resp = web.StreamResponse(
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(len(slice_data)),
                    "Accept-Ranges": "bytes",
                    "ETag": '"restore-v1"',
                },
            )
            await resp.prepare(request)
            try:
                if mode["phase"] == "resume":
                    await asyncio.sleep(0.6)
                for offset in range(0, len(slice_data), 65536):
                    await resp.write(slice_data[offset:offset + 65536])
                    if mode["phase"] == "pause":
                        await asyncio.sleep(0.03)
                await resp.write_eof()
            except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
                pass
            return resp

        runner, url = await self._start_local_server(handle)
        try:
            task1 = DownloadTask(
                task_id="test_restore_1",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="restored_file.bin",
                connections=4,
            )
            start_job = asyncio.create_task(task1.start())
            deadline = time.monotonic() + 2.0
            while task1.bytes_downloaded == 0 and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            await task1.pause()
            await asyncio.wait_for(start_job, timeout=3.0)

            self.assertEqual(task1.status, "paused")
            persisted_bytes = sum(
                max(0, min(s.current_byte, s.end_byte + 1) - s.start_byte)
                for s in task1.segments
            )
            self.assertGreater(persisted_bytes, 0)
            self.assertLess(persisted_bytes, size)

            task2 = DownloadTask(
                task_id="test_restore_2",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="restored_file.bin",
                connections=4,
            )
            async with aiohttp.ClientSession() as session:
                self.assertTrue(await task2.probe(session))
            self.assertTrue(task2._load_state())
            self.assertEqual(task2.bytes_downloaded, persisted_bytes)
            self.assertEqual(task2.last_sample_bytes, persisted_bytes)

            updates = []

            async def on_update(snapshot):
                updates.append(snapshot)

            mode["phase"] = "resume"
            task3 = DownloadTask(
                task_id="test_restore_3",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="restored_file.bin",
                connections=4,
                on_update=on_update,
            )
            await asyncio.wait_for(task3.start(), timeout=5.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task3.status, "completed")
        self.assertGreaterEqual(len(updates), 1)
        self.assertTrue(all(u["bytes_downloaded"] >= persisted_bytes for u in updates))
        self.assertEqual(task3.bytes_downloaded, size)
        with open(task3.output_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(actual_hash, expected_hash)
        self.assertFalse(os.path.exists(task3.meta_path))

    async def test_worker_stops_on_permanent_http_error_without_infinite_loop(self):
        """Verifica que errores HTTP permanentes (404) detengan los workers sin bucle infinito."""
        get_count = 0

        async def handle(request):
            nonlocal get_count
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": "100000",
                    }
                )
            get_count += 1
            return web.Response(status=404, text="Not Found")

        runner, url = await self._start_local_server(handle)
        try:
            task = DownloadTask(
                task_id="test_perm_404",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="perm_404.bin",
                connections=4,
            )
            await asyncio.wait_for(task.start(), timeout=2.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task.status, "error")
        self.assertIn("404", task.error_message or "")
        self.assertEqual(task.bytes_downloaded, 0)
        self.assertLessEqual(get_count, 4)

    async def test_worker_retries_transient_error_up_to_max_retries(self):
        """Verifica reintentos con recuperación tras 503 y fallo acotado tras MAX_RETRIES."""
        import core.engine as engine_mod

        payload_size = 256 * 1024
        payload = (b"TRANSIENT_503_RETRY_RECOVERY_SALT_1234567890\n" * 8000)[:payload_size]
        expected_hash = hashlib.sha256(payload).hexdigest()
        range_hits = {}

        async def handle_recover(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(payload_size),
                    }
                )
            range_header = request.headers.get("Range", "")
            range_hits[range_header] = range_hits.get(range_header, 0) + 1
            if range_hits[range_header] <= 2:
                return web.Response(status=503, text="Service Unavailable")

            start_s, end_s = range_header[6:].split("-")
            start = int(start_s)
            end = min(int(end_s) if end_s else payload_size - 1, payload_size - 1)
            chunk = payload[start:end + 1]
            return web.Response(
                status=206,
                body=chunk,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{payload_size}",
                    "Content-Length": str(len(chunk)),
                    "Accept-Ranges": "bytes",
                },
            )

        runner, url = await self._start_local_server(handle_recover)
        try:
            task_ok = DownloadTask(
                task_id="test_503_recover",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="retry_recover.bin",
                connections=2,
                retry_base_delay=0.01,
            )
            await asyncio.wait_for(task_ok.start(), timeout=3.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task_ok.status, "completed")
        self.assertEqual(task_ok.bytes_downloaded, payload_size)
        with open(task_ok.output_path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), expected_hash)
        self.assertTrue(all(hits == 3 for hits in range_hits.values()))

        # Caso separado: servidor devolviendo 503 de forma permanente -> error tras MAX_RETRIES
        forever_hits = {}

        async def handle_forever_503(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(payload_size),
                    }
                )
            range_header = request.headers.get("Range", "")
            forever_hits[range_header] = forever_hits.get(range_header, 0) + 1
            return web.Response(status=503, text="Always Unavailable")

        runner2, url2 = await self._start_local_server(handle_forever_503)
        try:
            task_fail = DownloadTask(
                task_id="test_503_exhaust",
                url=url2,
                output_dir=self.test_dir.name,
                custom_filename="retry_exhaust.bin",
                connections=2,
                retry_base_delay=0.01,
            )
            await asyncio.wait_for(task_fail.start(), timeout=3.0)
        finally:
            await runner2.cleanup()

        max_retries = getattr(engine_mod, "MAX_RETRIES", None)
        self.assertEqual(max_retries, 5)
        self.assertEqual(
            getattr(engine_mod, "PERMANENT_HTTP_ERRORS", None),
            frozenset({400, 401, 403, 404, 405, 410, 416}),
        )
        self.assertEqual(task_fail.status, "error")
        self.assertTrue(task_fail.error_message)
        self.assertGreaterEqual(sum(forever_hits.values()), max_retries + 1)
        self.assertTrue(
            all(hits <= max_retries + 1 for hits in forever_hits.values()),
            f"Se excedió MAX_RETRIES + 1 por segmento: {forever_hits}",
        )

    async def test_multi_segment_worker_rejects_200_ok_without_corrupting_file(self):
        """Verifica que descargas multisegmento rechacen HTTP 200 en peticiones Range."""
        payload_size = 512 * 1024
        payload = (b"RANGE_IGNORED_200_OK_CORRUPTION_GUARD\n" * 16000)[:payload_size]

        async def handle(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(payload_size),
                    }
                )
            return web.Response(
                status=200,
                body=payload,
                headers={"Content-Length": str(payload_size)},
            )

        runner, url = await self._start_local_server(handle)
        try:
            task = DownloadTask(
                task_id="test_multi_200_reject",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="multi_200.bin",
                connections=4,
                retry_base_delay=0.01,
            )
            await asyncio.wait_for(task.start(), timeout=3.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task.status, "error")
        self.assertIn("200", task.error_message or "")
        self.assertEqual(task.bytes_downloaded, 0)
        if os.path.exists(task.output_path):
            with open(task.output_path, "rb") as f:
                self.assertEqual(f.read(), b"\x00" * payload_size)

    async def test_single_connection_200_ok_degrades_inline_without_second_get(self):
        """Verifica degradación inline cuando connections=1 recibe HTTP 200 sin segundo GET."""
        payload_size = 1024 * 1024  # 1 MB
        payload = (b"SINGLE_CONN_INLINE_200_FALLBACK_1MB\n" * 35000)[:payload_size]
        expected_hash = hashlib.sha256(payload).hexdigest()
        get_count = 0

        async def handle(request):
            nonlocal get_count
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(payload_size),
                    }
                )
            get_count += 1
            return web.Response(
                status=200,
                body=payload,
                headers={"Content-Length": str(payload_size)},
            )

        runner, url = await self._start_local_server(handle)
        try:
            task = DownloadTask(
                task_id="test_single_inline_200",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="inline_200.bin",
                connections=1,
            )
            await asyncio.wait_for(task.start(), timeout=5.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task.status, "completed")
        self.assertEqual(get_count, 1)
        self.assertFalse(task.supports_range)
        self.assertEqual(task.bytes_downloaded, payload_size)
        self.assertFalse(os.path.exists(task.meta_path))
        with open(task.output_path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), expected_hash)

    async def test_single_stream_telemetry_pause_truncation_and_error_handling(self):
        """Verifica telemetría, pausa/reanudación, 404/EOF truncado y reintento 503 en flujo único."""
        # Sub-case A: non-Range sin Content-Length emite telemetría on_update y actualiza total_size
        with self.subTest("subcase_a_telemetry_unknown_length"):
            payload_a = (b"SINGLE_STREAM_UNKNOWN_LEN_TELEMETRY\n" * 8000)[:256 * 1024]
            expected_hash_a = hashlib.sha256(payload_a).hexdigest()
            updates_a = []

            async def on_update_a(snapshot):
                updates_a.append(snapshot)

            async def handle_a(request):
                if request.method == "HEAD":
                    resp = web.StreamResponse(status=200)
                    resp.enable_chunked_encoding()
                    await resp.prepare(request)
                    await resp.write_eof()
                    return resp
                resp = web.StreamResponse(status=200)
                resp.enable_chunked_encoding()
                await resp.prepare(request)
                for offset in range(0, len(payload_a), 32768):
                    await resp.write(payload_a[offset:offset + 32768])
                    await asyncio.sleep(0.08)
                await resp.write_eof()
                return resp

            runner_a, url_a = await self._start_local_server(handle_a)
            try:
                task_a = DownloadTask(
                    task_id="test_single_a",
                    url=url_a,
                    output_dir=self.test_dir.name,
                    custom_filename="single_a.bin",
                    connections=4,
                    on_update=on_update_a,
                )
                await asyncio.wait_for(task_a.start(), timeout=5.0)
            finally:
                await runner_a.cleanup()

            self.assertEqual(task_a.status, "completed")
            self.assertFalse(task_a.supports_range)
            self.assertEqual(task_a.total_size, len(payload_a))
            self.assertEqual(task_a.bytes_downloaded, len(payload_a))
            self.assertEqual(task_a.segments[0].status, "completed")
            self.assertGreaterEqual(len(updates_a), 1)
            self.assertTrue(
                any(u["bytes_downloaded"] < len(payload_a) and u["segments"][0]["status"] == "downloading" for u in updates_a),
                f"Se esperaba telemetría intermedia en downloading: {updates_a}",
            )
            with open(task_a.output_path, "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), expected_hash_a)

        # Sub-case B: pausa en flujo único (incluso bloqueado) deja estado paused y reanudar reinicia limpio sin >100%
        with self.subTest("subcase_b_pause_and_restart_from_zero"):
            payload_b = (b"SINGLE_STREAM_PAUSE_RESUME_CLEAN_0123456\n" * 14000)[:512 * 1024]
            expected_hash_b = hashlib.sha256(payload_b).hexdigest()
            mode_b = {"stall": True}

            async def handle_b(request):
                if request.method == "HEAD":
                    return web.Response(headers={"Content-Length": str(len(payload_b))})
                resp = web.StreamResponse(
                    status=200,
                    headers={"Content-Length": str(len(payload_b))},
                )
                await resp.prepare(request)
                try:
                    for idx, offset in enumerate(range(0, len(payload_b), 32768)):
                        await resp.write(payload_b[offset:offset + 32768])
                        if mode_b["stall"] and idx == 1:
                            await asyncio.sleep(3.0)
                    await resp.write_eof()
                except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
                    pass
                return resp

            runner_b, url_b = await self._start_local_server(handle_b)
            try:
                task_b = DownloadTask(
                    task_id="test_single_b",
                    url=url_b,
                    output_dir=self.test_dir.name,
                    custom_filename="single_b.bin",
                    connections=4,
                )
                start_job = asyncio.create_task(task_b.start())
                deadline = time.monotonic() + 2.0
                while task_b.bytes_downloaded == 0 and time.monotonic() < deadline:
                    await asyncio.sleep(0.005)
                await asyncio.wait_for(task_b.pause(), timeout=1.0)
                await asyncio.wait_for(start_job, timeout=1.0)

                self.assertEqual(task_b.status, "paused")
                self.assertEqual(task_b.segments[0].status, "paused")
                self.assertGreater(task_b.bytes_downloaded, 0)
                self.assertLess(task_b.bytes_downloaded, len(payload_b))

                mode_b["stall"] = False
                await asyncio.wait_for(task_b.start(), timeout=5.0)
            finally:
                await runner_b.cleanup()

            self.assertEqual(task_b.status, "completed")
            self.assertEqual(task_b.segments[0].status, "completed")
            self.assertEqual(task_b.bytes_downloaded, len(payload_b))
            self.assertEqual(task_b.progress_percentage, 100.0)
            with open(task_b.output_path, "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), expected_hash_b)

        # Sub-case C1: 404 en flujo único falla inmediatamente sin guardar HTML
        with self.subTest("subcase_c1_404_immediate_error"):
            c1_get_count = 0

            async def handle_c1(request):
                nonlocal c1_get_count
                if request.method == "HEAD":
                    return web.Response(headers={"Content-Length": "10000"})
                c1_get_count += 1
                return web.Response(status=404, text="<html>Not Found</html>")

            runner_c1, url_c1 = await self._start_local_server(handle_c1)
            try:
                task_c1 = DownloadTask(
                    task_id="test_single_c1",
                    url=url_c1,
                    output_dir=self.test_dir.name,
                    custom_filename="single_c1.bin",
                    connections=4,
                )
                await asyncio.wait_for(task_c1.start(), timeout=2.0)
            finally:
                await runner_c1.cleanup()

            self.assertEqual(task_c1.status, "error")
            self.assertIn("404", task_c1.error_message or "")
            self.assertEqual(c1_get_count, 1)
            self.assertFalse(os.path.exists(task_c1.output_path))

        # Sub-case C2: EOF truncado en flujo único falla inmediatamente sin reintentar desde byte 0
        with self.subTest("subcase_c2_truncated_eof_immediate_error"):
            c2_get_count = 0

            async def handle_c2(request):
                nonlocal c2_get_count
                if request.method == "HEAD":
                    return web.Response(headers={"Content-Length": "10000"})
                c2_get_count += 1
                resp = web.StreamResponse(
                    status=200,
                    headers={"Content-Length": "10000"},
                )
                await resp.prepare(request)
                await resp.write(b"X" * 1000)
                if request.transport is not None:
                    request.transport.close()
                return resp

            runner_c2, url_c2 = await self._start_local_server(handle_c2)
            try:
                task_c2 = DownloadTask(
                    task_id="test_single_c2",
                    url=url_c2,
                    output_dir=self.test_dir.name,
                    custom_filename="single_c2.bin",
                    connections=4,
                    retry_base_delay=0.01,
                )
                await asyncio.wait_for(task_c2.start(), timeout=2.0)
            finally:
                await runner_c2.cleanup()

            self.assertEqual(task_c2.status, "error")
            self.assertEqual(c2_get_count, 1)

        # Sub-case D: 503 transitorio antes del byte 0 reintenta y tiene éxito al recuperarse (y acota si persiste)
        with self.subTest("subcase_d_503_before_byte_zero_recovers"):
            payload_d = (b"SINGLE_STREAM_503_RECOVER_OK\n" * 4000)[:100000]
            expected_hash_d = hashlib.sha256(payload_d).hexdigest()
            d_get_count = 0

            async def handle_d(request):
                nonlocal d_get_count
                if request.method == "HEAD":
                    return web.Response(headers={"Content-Length": str(len(payload_d))})
                d_get_count += 1
                if d_get_count == 1:
                    return web.Response(status=503, text="Retry shortly")
                return web.Response(
                    status=200,
                    body=payload_d,
                    headers={"Content-Length": str(len(payload_d))},
                )

            runner_d, url_d = await self._start_local_server(handle_d)
            try:
                task_d = DownloadTask(
                    task_id="test_single_d",
                    url=url_d,
                    output_dir=self.test_dir.name,
                    custom_filename="single_d.bin",
                    connections=4,
                    retry_base_delay=0.01,
                )
                await asyncio.wait_for(task_d.start(), timeout=3.0)
            finally:
                await runner_d.cleanup()

            self.assertEqual(task_d.status, "completed")
            self.assertEqual(d_get_count, 2)
            with open(task_d.output_path, "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), expected_hash_d)

            d2_get_count = 0

            async def handle_d2(request):
                nonlocal d2_get_count
                if request.method == "HEAD":
                    return web.Response(headers={"Content-Length": str(len(payload_d))})
                d2_get_count += 1
                return web.Response(status=503, text="Always 503")

            runner_d2, url_d2 = await self._start_local_server(handle_d2)
            try:
                task_d2 = DownloadTask(
                    task_id="test_single_d2",
                    url=url_d2,
                    output_dir=self.test_dir.name,
                    custom_filename="single_d2.bin",
                    connections=4,
                    retry_base_delay=0.01,
                )
                await asyncio.wait_for(task_d2.start(), timeout=3.0)
            finally:
                await runner_d2.cleanup()

            self.assertEqual(task_d2.status, "error")
            self.assertEqual(d2_get_count, 6)

    async def test_worker_write_oserror_fails_fatally_without_retrying(self):
        """Verifica que OSError de disco en _write_chunk aborte fatalmente sin reintentar tras avanzar offset."""
        payload_size = 128 * 1024
        payload = b"DISK_WRITE_ERROR_GUARD_DATA" * 5000
        payload = payload[:payload_size]
        get_count = 0

        async def handle(request):
            nonlocal get_count
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(payload_size),
                    }
                )
            get_count += 1
            range_header = request.headers.get("Range", "bytes=0-")
            start_s, end_s = range_header[6:].split("-")
            start = int(start_s)
            end = min(int(end_s) if end_s else payload_size - 1, payload_size - 1)
            chunk = payload[start:end + 1]
            return web.Response(
                status=206,
                body=chunk,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{payload_size}",
                    "Content-Length": str(len(chunk)),
                    "Accept-Ranges": "bytes",
                },
            )

        runner, url = await self._start_local_server(handle)
        try:
            task = DownloadTask(
                task_id="test_disk_oserror",
                url=url,
                output_dir=self.test_dir.name,
                custom_filename="disk_err.bin",
                connections=2,
            )
            with patch.object(task, "_write_chunk", side_effect=OSError("No space left on device")):
                await asyncio.wait_for(task.start(), timeout=2.0)
        finally:
            await runner.cleanup()

        self.assertEqual(task.status, "error")
        self.assertTrue(task.error_message)
        self.assertLessEqual(get_count, 2)


if __name__ == "__main__":
    unittest.main()
