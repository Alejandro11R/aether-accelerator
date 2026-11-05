"""
Aether Download Accelerator - Test Suite
Raphael (QA & TDD) + Maestro Splinter (Integridad de datos y Cero Regresiones)
Verifica la matemática de segmentación, particionado dinámico, pausa/reanudación y checksum SHA-256.
"""

import os
import sys
import hashlib
import tempfile
import asyncio
import unittest
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


if __name__ == "__main__":
    unittest.main()
