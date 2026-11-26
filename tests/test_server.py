"""
Aether Download Accelerator - Server & QueueManager Security Tests
Verifica endurecimiento contra DNS-rebinding, CSRF, Content-Type inválido,
validación de entradas REST, listado /static y confinamiento en QueueManager.
"""

import os
import sys
import asyncio
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import aiohttp
from aiohttp import web, WSMsgType, WSCloseCode
from aiohttp.test_utils import AioHTTPTestCase

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import server.app as server_app
from core.manager import QueueManager
from core.security import validate_output_dir
from server.app import MANAGER_KEY, WEBSOCKETS_KEY, create_app


def _make_fake_expanduser(home_dir: str):
    def _fake(path: str) -> str:
        if path == "~":
            return home_dir
        if path.startswith("~/") or path.startswith("~\\"):
            return os.path.join(home_dir, path[2:])
        return path

    return _fake


class TestServerSecurity(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        self.temp_home = tempfile.TemporaryDirectory()
        self.outside_dir = tempfile.TemporaryDirectory()
        self.download_dir = os.path.join(self.temp_home.name, "Downloads", "Aether")
        os.makedirs(self.download_dir, exist_ok=True)
        self.ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ui"))

        self._expanduser_patch = patch(
            "os.path.expanduser",
            side_effect=_make_fake_expanduser(self.temp_home.name),
        )
        self._expanduser_patch.start()

        # Servidor HTTP mock local para descargas válidas durante los tests
        self.mock_payload = b"AETHER_SERVER_SECURITY_MOCK_PAYLOAD_" * 128
        mock_app = web.Application()

        async def handle_mock_file(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Length": str(len(self.mock_payload)),
                        "ETag": '"mock-etag-v1"',
                    }
                )
            range_header = request.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                start_s, end_s = range_header[6:].split("-", 1)
                start = int(start_s)
                end = min(int(end_s) if end_s else len(self.mock_payload) - 1, len(self.mock_payload) - 1)
                chunk = self.mock_payload[start:end + 1]
                return web.Response(
                    status=206,
                    body=chunk,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{len(self.mock_payload)}",
                        "Content-Length": str(len(chunk)),
                        "Accept-Ranges": "bytes",
                        "ETag": '"mock-etag-v1"',
                    },
                )
            return web.Response(
                status=200,
                body=self.mock_payload,
                headers={
                    "Content-Length": str(len(self.mock_payload)),
                    "Accept-Ranges": "bytes",
                    "ETag": '"mock-etag-v1"',
                },
            )

        mock_app.router.add_route("*", "/test.bin", handle_mock_file)
        self.mock_runner = web.AppRunner(mock_app)
        await self.mock_runner.setup()
        site = web.TCPSite(self.mock_runner, "127.0.0.1", 0)
        await site.start()
        mock_port = site._server.sockets[0].getsockname()[1]
        self.mock_url = f"http://127.0.0.1:{mock_port}/test.bin"

        return create_app(
            download_dir=self.download_dir,
            ui_dir=self.ui_dir,
            folder_opener=lambda path: self.fail(
                f"Unexpected folder_opener call in TestServerSecurity: {path}"
            ),
        )

    async def tearDownAsync(self) -> None:
        manager = self.app.get(MANAGER_KEY)
        if manager is not None:
            for task_id in list(manager.tasks.keys()):
                try:
                    await manager.remove_download(task_id, delete_file=True)
                except Exception:
                    pass
            await manager.shutdown()
        await asyncio.sleep(0)
        await super().tearDownAsync()
        await self.mock_runner.cleanup()
        self._expanduser_patch.stop()
        self.outside_dir.cleanup()
        self.temp_home.cleanup()

    async def _create_valid_task(self) -> str:
        resp = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url, "threads": 2, "filename": "valid_task.bin"},
            headers={"Origin": f"http://127.0.0.1:{self.server.port}"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        return data["task"]["id"]

    async def test_static_directory_listing_disabled_and_asset_served(self):
        resp_dir = await self.client.get("/static/")
        self.assertIn(resp_dir.status, (403, 404))

        resp_js = await self.client.get("/static/app.js")
        self.assertEqual(resp_js.status, 200)
        text = await resp_js.text()
        self.assertIn("connectWebSocket", text)

        resp_index = await self.client.get("/")
        self.assertEqual(resp_index.status, 200)

    async def test_dns_rebinding_host_header_validation(self):
        for bad_host in [
            "evil.example.com:8765",
            "evil.example.com",
            "evil.com@127.0.0.1:8765",
            "127.0.0.1:notaport",
            "[::1]extra",
            "",
        ]:
            with self.subTest(bad_host=bad_host):
                resp = await self.client.get("/api/downloads", headers={"Host": bad_host})
                self.assertEqual(resp.status, 403)

        resp_static = await self.client.get("/static/app.js", headers={"Host": "evil.example.com:8765"})
        self.assertEqual(resp_static.status, 403)

        resp_dup_host = await self.client.get(
            "/api/downloads",
            headers=[
                ("Host", f"127.0.0.1:{self.server.port}"),
                ("Host", "evil.example.com:8765"),
            ],
        )
        self.assertEqual(resp_dup_host.status, 403)

        for good_host in [
            f"127.0.0.1:{self.server.port}",
            f"localhost:{self.server.port}",
            f"[::1]:{self.server.port}",
            "LOCALHOST:8765",
        ]:
            with self.subTest(good_host=good_host):
                resp = await self.client.get("/api/downloads", headers={"Host": good_host})
                self.assertEqual(resp.status, 200)

    async def test_csrf_rejects_external_origin_and_cross_site_requests(self):
        task_id = await self._create_valid_task()

        evil_headers = {"Origin": "https://evil.com"}
        resp = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url},
            headers=evil_headers,
        )
        self.assertEqual(resp.status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

        resp_pause = await self.client.post(f"/api/downloads/{task_id}/pause", headers=evil_headers)
        self.assertEqual(resp_pause.status, 403)

        resp_resume = await self.client.post(f"/api/downloads/{task_id}/resume", headers=evil_headers)
        self.assertEqual(resp_resume.status, 403)

        resp_limit = await self.client.post(
            f"/api/downloads/{task_id}/limit",
            json={"limit_bps": 1000},
            headers=evil_headers,
        )
        self.assertEqual(resp_limit.status, 403)

        resp_delete = await self.client.delete(f"/api/downloads/{task_id}", headers=evil_headers)
        self.assertEqual(resp_delete.status, 403)

        # Sec-Fetch-Site: cross-site without extension Origin
        cross_site_headers = {"Sec-Fetch-Site": "cross-site"}
        resp_cs_post = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url},
            headers=cross_site_headers,
        )
        self.assertEqual(resp_cs_post.status, 403)

        resp_cs_spoof_origin = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url},
            headers={
                "Origin": f"http://127.0.0.1:{self.server.port}",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        self.assertEqual(resp_cs_spoof_origin.status, 403)

        resp_cs_pause = await self.client.post(f"/api/downloads/{task_id}/pause", headers=cross_site_headers)
        self.assertEqual(resp_cs_pause.status, 403)

        resp_cs_limit = await self.client.post(
            f"/api/downloads/{task_id}/limit",
            json={"limit_bps": 1000},
            headers=cross_site_headers,
        )
        self.assertEqual(resp_cs_limit.status, 403)

        resp_cs_delete = await self.client.delete(f"/api/downloads/{task_id}", headers=cross_site_headers)
        self.assertEqual(resp_cs_delete.status, 403)

        resp_sys_evil = await self.client.get("/api/system", headers=evil_headers)
        self.assertEqual(resp_sys_evil.status, 403)

        resp_sys_cs = await self.client.get("/api/system", headers=cross_site_headers)
        self.assertEqual(resp_sys_cs.status, 403)

        resp_open_evil = await self.client.post(
            "/api/system/open-folder",
            json={},
            headers=evil_headers,
        )
        self.assertEqual(resp_open_evil.status, 403)

        resp_open_cs = await self.client.post(
            "/api/system/open-folder",
            json={},
            headers=cross_site_headers,
        )
        self.assertEqual(resp_open_cs.status, 403)

        resp_dup_origin = await self.client.get(
            "/api/system",
            headers=[
                ("Origin", f"http://127.0.0.1:{self.server.port}"),
                ("Origin", "https://evil.com"),
            ],
        )
        self.assertEqual(resp_dup_origin.status, 403)

        # Malformed or null Origin headers must fail closed with 403 (never 500)
        for bad_origin in ["null", "", "http://[::1:8765", "http://evil.com@127.0.0.1:8765"]:
            with self.subTest(bad_origin=bad_origin):
                resp_bad = await self.client.post(
                    "/api/downloads",
                    json={"url": self.mock_url},
                    headers={"Origin": bad_origin},
                )
                self.assertEqual(resp_bad.status, 403)

    async def test_content_type_enforced_on_json_post_endpoints_only(self):
        task_id = await self._create_valid_task()

        resp_dl = await self.client.post(
            "/api/downloads",
            data='{"url": "' + self.mock_url + '"}',
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(resp_dl.status, 415)

        resp_limit = await self.client.post(
            f"/api/downloads/{task_id}/limit",
            data='{"limit_bps": 1024}',
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(resp_limit.status, 415)

        resp_open_folder = await self.client.post(
            "/api/system/open-folder",
            data="{}",
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(resp_open_folder.status, 415)

        # UI pause/resume send POST without body or Content-Type header
        resp_pause = await self.client.post(
            f"/api/downloads/{task_id}/pause",
            headers={"Origin": f"http://127.0.0.1:{self.server.port}"},
        )
        self.assertEqual(resp_pause.status, 200)

    async def test_websocket_origin_and_sec_fetch_site_protection(self):
        with self.assertRaises(aiohttp.WSServerHandshakeError) as ctx_evil:
            async with self.client.ws_connect("/ws", headers={"Origin": "https://evil.com"}):
                pass
        self.assertEqual(ctx_evil.exception.status, 403)

        with self.assertRaises(aiohttp.WSServerHandshakeError) as ctx_cs:
            async with self.client.ws_connect("/ws", headers={"Sec-Fetch-Site": "cross-site"}):
                pass
        self.assertEqual(ctx_cs.exception.status, 403)

        async with self.client.ws_connect(
            "/ws",
            headers={"Origin": f"http://127.0.0.1:{self.server.port}"},
        ) as ws:
            init_msg = await ws.receive_json(timeout=2.0)
            self.assertEqual(init_msg["type"], "init")

        async with self.client.ws_connect(
            "/ws",
            headers={
                "Origin": "chrome-extension://abcdef123456",
                "Sec-Fetch-Site": "cross-site",
            },
        ) as ws_ext:
            init_msg_ext = await ws_ext.receive_json(timeout=2.0)
            self.assertEqual(init_msg_ext["type"], "init")

    async def test_allows_browser_extensions_and_loopback_origins(self):
        resp_sys_ext = await self.client.get(
            "/api/system",
            headers={
                "Origin": "chrome-extension://abcdef123456",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        self.assertEqual(resp_sys_ext.status, 200)

        # Chrome MV3 extension payload (includes filename: null)
        resp_chrome = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url, "threads": 4, "filename": None},
            headers={
                "Origin": "chrome-extension://abcdef123456",
                "Sec-Fetch-Site": "cross-site",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(resp_chrome.status, 200)

        # Firefox extension origin
        resp_moz = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url, "threads": 2, "filename": "moz.bin"},
            headers={
                "Origin": "moz-extension://12345678-1234-1234-1234-1234567890ab",
                "Sec-Fetch-Site": "cross-site",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(resp_moz.status, 200)

        # Local UI loopback origins
        for origin in [
            f"http://127.0.0.1:{self.server.port}",
            f"http://localhost:{self.server.port}",
            f"http://[::1]:{self.server.port}",
        ]:
            with self.subTest(origin=origin):
                resp_loop = await self.client.post(
                    "/api/downloads",
                    json={"url": self.mock_url, "threads": 2, "filename": ""},
                    headers={"Origin": origin, "Content-Type": "application/json"},
                )
                self.assertEqual(resp_loop.status, 200)

    async def test_input_validation_rejects_bad_filenames_dirs_and_numeric_types(self):
        task_id = await self._create_valid_task()

        for bad_filename in ["../../evil.sh", ".bashrc", "CON.txt"]:
            with self.subTest(bad_filename=bad_filename):
                resp = await self.client.post(
                    "/api/downloads",
                    json={"url": self.mock_url, "filename": bad_filename},
                )
                self.assertEqual(resp.status, 400)

        outside_target = os.path.join(self.outside_dir.name, "outside_home")
        for bad_dir in [outside_target, "~/.ssh", self.temp_home.name]:
            with self.subTest(bad_dir=bad_dir):
                resp = await self.client.post(
                    "/api/downloads",
                    json={"url": self.mock_url, "dir": bad_dir},
                )
                self.assertEqual(resp.status, 400)
        self.assertFalse(os.path.exists(outside_target))

        for bad_threads in ["not-an-int", True, False, 3.5, "3.5", 0, 33, None]:
            with self.subTest(bad_threads=bad_threads):
                resp = await self.client.post(
                    "/api/downloads",
                    json={"url": self.mock_url, "threads": bad_threads},
                )
                self.assertEqual(resp.status, 400)

        for bad_speed in ["abc", True, False, 1.5, "1.5", -1, None]:
            with self.subTest(bad_speed=bad_speed):
                resp = await self.client.post(
                    "/api/downloads",
                    json={"url": self.mock_url, "speed_limit_bps": bad_speed},
                )
                self.assertEqual(resp.status, 400)

                resp_limit = await self.client.post(
                    f"/api/downloads/{task_id}/limit",
                    json={"limit_bps": bad_speed},
                )
                self.assertEqual(resp_limit.status, 400)

        resp_ok_limit = await self.client.post(
            f"/api/downloads/{task_id}/limit",
            json={"limit_bps": 50_000},
        )
        self.assertEqual(resp_ok_limit.status, 200)

        resp_list_body = await self.client.post(
            "/api/downloads",
            json=["not", "a", "dict"],
        )
        self.assertEqual(resp_list_body.status, 400)

        resp_bad_json = await self.client.post(
            "/api/downloads",
            data="{invalid-json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp_bad_json.status, 400)

        for bad_url in ["", "ftp://example.com/file.bin", "http://", "http://[::1/file.bin", 123, None]:
            with self.subTest(bad_url=bad_url):
                resp_url = await self.client.post(
                    "/api/downloads",
                    json={"url": bad_url},
                )
                self.assertEqual(resp_url.status, 400)

        resp_bad_fname_type = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url, "filename": 123},
        )
        self.assertEqual(resp_bad_fname_type.status, 400)

        resp_bad_dir_type = await self.client.post(
            "/api/downloads",
            json={"url": self.mock_url, "dir": ["bad"]},
        )
        self.assertEqual(resp_bad_dir_type.status, 400)

        # Tampered output_path escape rejected cleanly as HTTP 400
        outside_file = os.path.join(self.outside_dir.name, "do_not_delete.txt")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("protected")
        self.app[MANAGER_KEY].tasks[task_id].output_path = outside_file
        resp_escape_del = await self.client.delete(f"/api/downloads/{task_id}?delete_file=true")
        self.assertEqual(resp_escape_del.status, 400)
        self.assertTrue(os.path.exists(outside_file))

    async def test_system_info_and_ws_init_include_platform_and_quick_folders(self):
        loopback_origin = f"http://127.0.0.1:{self.server.port}"
        resp_sys = await self.client.get("/api/system", headers={"Origin": loopback_origin})
        self.assertEqual(resp_sys.status, 200)
        sys_data = await resp_sys.json()

        for expected_key in ("total_speed_bps", "active_tasks", "total_tasks", "download_dir"):
            self.assertIn(expected_key, sys_data)
        self.assertEqual(sys_data["download_dir"], self.download_dir)
        self.assertIn(sys_data.get("platform"), {"linux", "windows", "darwin"})

        quick_folders = sys_data.get("quick_folders")
        self.assertIsInstance(quick_folders, list)
        self.assertGreater(len(quick_folders), 0)
        self.assertEqual(
            quick_folders[0],
            {"label": "Carpeta Aether", "path": self.download_dir},
        )
        for item in quick_folders:
            self.assertIsInstance(item, dict)
            self.assertIsInstance(item.get("label"), str)
            self.assertTrue(item["label"])
            self.assertIsInstance(item.get("path"), str)
            self.assertTrue(item["path"])
            validate_output_dir(item["path"], self.download_dir)

        async with self.client.ws_connect("/ws", headers={"Origin": loopback_origin}) as ws:
            init_msg = await ws.receive_json(timeout=2.0)

        self.assertEqual(init_msg.get("type"), "init")
        self.assertIsInstance(init_msg.get("downloads"), list)
        self.assertEqual(init_msg.get("download_dir"), self.download_dir)
        self.assertEqual(init_msg.get("platform"), sys_data["platform"])
        self.assertEqual(init_msg.get("quick_folders"), quick_folders)


class TestCustomAllowedHosts(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        self.temp_dir = tempfile.TemporaryDirectory()
        ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ui"))
        return create_app(
            download_dir=self.temp_dir.name,
            ui_dir=ui_dir,
            allowed_hosts={"aether.local"},
        )

    async def tearDownAsync(self) -> None:
        await super().tearDownAsync()
        self.temp_dir.cleanup()

    async def test_custom_allowed_hosts_accepted(self):
        resp_allowed = await self.client.get(
            "/api/downloads",
            headers={
                "Host": "aether.local:8765",
                "Origin": "http://aether.local:8765",
            },
        )
        self.assertEqual(resp_allowed.status, 200)

        resp_rejected = await self.client.get(
            "/api/downloads",
            headers={"Host": "evil.example.com:8765"},
        )
        self.assertEqual(resp_rejected.status, 403)


class TestQueueManagerSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_home = tempfile.TemporaryDirectory()
        self.outside_dir = tempfile.TemporaryDirectory()
        self.download_dir = os.path.join(self.temp_home.name, "Downloads", "Aether")
        os.makedirs(self.download_dir, exist_ok=True)
        self._expanduser_patch = patch(
            "os.path.expanduser",
            side_effect=_make_fake_expanduser(self.temp_home.name),
        )
        self._expanduser_patch.start()
        self.manager = QueueManager(download_dir=self.download_dir)

    async def asyncTearDown(self):
        for task_id in list(self.manager.tasks.keys()):
            try:
                await self.manager.remove_download(task_id, delete_file=False)
            except Exception:
                pass
        await asyncio.sleep(0)
        self._expanduser_patch.stop()
        self.outside_dir.cleanup()
        self.temp_home.cleanup()

    async def test_add_download_validates_before_creating_directories(self):
        forbidden_dir = os.path.join(self.outside_dir.name, "should_not_exist")
        with self.assertRaises(ValueError):
            await self.manager.add_download(
                url="http://127.0.0.1:9/file.bin",
                output_dir=forbidden_dir,
            )
        self.assertFalse(os.path.exists(forbidden_dir))

        with self.assertRaises(ValueError):
            await self.manager.add_download(
                url="http://127.0.0.1:9/file.bin",
                output_dir="~/.ssh",
            )

        for bad_name in ["../../evil.sh", ".bashrc", "CON.txt"]:
            with self.subTest(bad_name=bad_name):
                with self.assertRaises(ValueError):
                    await self.manager.add_download(
                        url="http://127.0.0.1:9/file.bin",
                        filename=bad_name,
                    )

    async def test_remove_download_deletes_contained_files_and_refuses_escapes(self):
        # 1. Valid file removal inside output_dir
        mock_app = web.Application()
        payload = b"QUEUE_MANAGER_SAFE_DELETE_TEST" * 16

        async def handle_file(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={"Accept-Ranges": "bytes", "Content-Length": str(len(payload))}
                )
            return web.Response(status=200, body=payload, headers={"Content-Length": str(len(payload))})

        mock_app.router.add_route("*", "/item.bin", handle_file)
        runner = web.AppRunner(mock_app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            task = await self.manager.add_download(
                url=f"http://127.0.0.1:{port}/item.bin",
                threads=1,
                filename="contained.bin",
            )
            for _ in range(100):
                if task.status == "completed":
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(task.status, "completed")
            self.assertTrue(os.path.exists(task.output_path))

            # Simulate sidecar metadata file inside output_dir
            with open(task.meta_path, "w", encoding="utf-8") as f:
                f.write("{}")

            removed = await self.manager.remove_download(task.task_id, delete_file=True)
            self.assertTrue(removed)
            self.assertFalse(os.path.exists(task.output_path))
            self.assertFalse(os.path.exists(task.meta_path))

            # 2. Direct path escape outside output_dir must be refused
            outside_target = os.path.join(self.outside_dir.name, "precious.txt")
            with open(outside_target, "w", encoding="utf-8") as f:
                f.write("do not delete")

            escaped_task = await self.manager.add_download(
                url=f"http://127.0.0.1:{port}/item.bin",
                threads=1,
                filename="escape_target.bin",
            )
            await escaped_task.pause()
            escaped_task.output_path = outside_target

            with self.assertRaises(ValueError):
                await self.manager.remove_download(escaped_task.task_id, delete_file=True)
            self.assertTrue(os.path.exists(outside_target))
        finally:
            await runner.cleanup()

    async def test_remove_download_refuses_symlink_escape(self):
        outside_target = os.path.join(self.outside_dir.name, "precious_symlink.txt")
        with open(outside_target, "w", encoding="utf-8") as f:
            f.write("do not delete")

        symlink_path = os.path.join(os.path.realpath(self.download_dir), "escape_link.bin")
        try:
            os.symlink(outside_target, symlink_path)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks require elevated privileges on this OS")

        mock_app = web.Application()
        payload = b"QUEUE_MANAGER_SYMLINK_ESCAPE_TEST" * 16

        async def handle_file(request):
            if request.method == "HEAD":
                return web.Response(
                    headers={"Accept-Ranges": "bytes", "Content-Length": str(len(payload))}
                )
            return web.Response(status=200, body=payload, headers={"Content-Length": str(len(payload))})

        mock_app.router.add_route("*", "/item.bin", handle_file)
        runner = web.AppRunner(mock_app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]

        try:
            escaped_task = await self.manager.add_download(
                url=f"http://127.0.0.1:{port}/item.bin",
                threads=1,
                filename="escape_target.bin",
            )
            await escaped_task.pause()
            escaped_task.output_path = symlink_path

            with self.assertRaises(ValueError):
                await self.manager.remove_download(escaped_task.task_id, delete_file=True)
            self.assertTrue(os.path.exists(outside_target))
        finally:
            await runner.cleanup()


class _OrderedWebSocketSet(set):
    """Set determinista para asegurar que un WebSocket roto se cierre antes que uno sano."""

    def __init__(self, items=()):
        super().__init__(items)
        self._order = list(items)

    def add(self, item):
        if item not in self:
            self._order.append(item)
        super().add(item)

    def discard(self, item):
        super().discard(item)
        if item in self._order:
            self._order.remove(item)

    def __iter__(self):
        return iter(self._order)


class _BrokenWebSocket:
    async def close(self, **kwargs):
        raise ConnectionResetError("broken socket")


class TestServerShutdown(unittest.IsolatedAsyncioTestCase):
    async def test_on_shutdown_closes_healthy_websockets_when_earlier_socket_raises(self):
        with tempfile.TemporaryDirectory() as download_dir:
            ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ui"))
            app = create_app(download_dir=download_dir, ui_dir=ui_dir)
            broken_ws = _BrokenWebSocket()
            app[WEBSOCKETS_KEY] = _OrderedWebSocketSet([broken_ws])

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]

            try:
                async with aiohttp.ClientSession() as session:
                    ws = await session.ws_connect(
                        f"http://127.0.0.1:{port}/ws",
                        headers={"Origin": f"http://127.0.0.1:{port}"},
                    )
                    try:
                        init_msg = await ws.receive_json(timeout=2.0)
                        self.assertEqual(init_msg["type"], "init")

                        shutdown_task = asyncio.create_task(runner.shutdown())
                        try:
                            _, close_msg = await asyncio.wait_for(
                                asyncio.gather(shutdown_task, ws.receive()),
                                timeout=2.0,
                            )
                        finally:
                            if not shutdown_task.done():
                                shutdown_task.cancel()
                                await asyncio.gather(shutdown_task, return_exceptions=True)

                        self.assertEqual(close_msg.type, WSMsgType.CLOSE)
                        self.assertEqual(close_msg.data, WSCloseCode.GOING_AWAY)
                        self.assertEqual(close_msg.extra, "Server shutdown")
                    finally:
                        await ws.close()
            finally:
                app[WEBSOCKETS_KEY].discard(broken_ws)
                await runner.cleanup()


class TestSystemOpenFolder(AioHTTPTestCase):
    async def get_application(self) -> web.Application:
        self.temp_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_home.cleanup)
        self.outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside_dir.cleanup)
        self.download_dir = os.path.join(self.temp_home.name, "Downloads", "Aether")
        os.makedirs(self.download_dir, exist_ok=True)
        self.ui_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ui"))

        self._expanduser_patch = patch(
            "os.path.expanduser",
            side_effect=_make_fake_expanduser(self.temp_home.name),
        )
        self._expanduser_patch.start()
        self.addCleanup(self._expanduser_patch.stop)

        self.opened_folders = []
        self.opener_error = None

        def _fake_opener(path: str) -> None:
            if self.opener_error is not None:
                raise self.opener_error
            self.opened_folders.append(path)

        return create_app(
            download_dir=self.download_dir,
            ui_dir=self.ui_dir,
            folder_opener=_fake_opener,
        )

    async def tearDownAsync(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            manager = app.get(MANAGER_KEY)
            if manager is not None:
                await manager.shutdown()
        await asyncio.sleep(0)
        await super().tearDownAsync()

    async def test_open_folder_rejects_security_and_invalid_directories(self):
        loopback_origin = f"http://127.0.0.1:{self.server.port}"

        resp_text = await self.client.post(
            "/api/system/open-folder",
            data="{}",
            headers={"Origin": loopback_origin, "Content-Type": "text/plain"},
        )
        self.assertEqual(resp_text.status, 415)

        resp_evil_origin = await self.client.post(
            "/api/system/open-folder",
            json={},
            headers={"Origin": "https://evil.com"},
        )
        self.assertEqual(resp_evil_origin.status, 403)

        resp_cross_site = await self.client.post(
            "/api/system/open-folder",
            json={},
            headers={"Origin": loopback_origin, "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(resp_cross_site.status, 403)

        resp_bad_json = await self.client.post(
            "/api/system/open-folder",
            data="{bad-json",
            headers={"Origin": loopback_origin, "Content-Type": "application/json"},
        )
        self.assertEqual(resp_bad_json.status, 400)

        resp_list_body = await self.client.post(
            "/api/system/open-folder",
            json=["not-a-dict"],
            headers={"Origin": loopback_origin},
        )
        self.assertEqual(resp_list_body.status, 400)

        resp_non_str_dir = await self.client.post(
            "/api/system/open-folder",
            json={"dir": 123},
            headers={"Origin": loopback_origin},
        )
        self.assertEqual(resp_non_str_dir.status, 400)

        regular_file = os.path.join(self.download_dir, "not_a_dir.txt")
        with open(regular_file, "w", encoding="utf-8") as fh:
            fh.write("file")

        non_existent = os.path.join(self.download_dir, "missing_subdir")
        os.makedirs(os.path.join(self.temp_home.name, ".ssh"), exist_ok=True)

        for bad_dir in [
            "~/.ssh",
            self.temp_home.name,
            self.outside_dir.name,
            non_existent,
            regular_file,
        ]:
            with self.subTest(bad_dir=bad_dir):
                resp_bad = await self.client.post(
                    "/api/system/open-folder",
                    json={"dir": bad_dir},
                    headers={"Origin": loopback_origin},
                )
                self.assertEqual(resp_bad.status, 400)

        symlink_dir = os.path.join(self.download_dir, "escape_dir_link")
        try:
            os.symlink(self.outside_dir.name, symlink_dir)
        except (OSError, NotImplementedError):
            symlink_dir = None

        if symlink_dir is not None:
            resp_sym = await self.client.post(
                "/api/system/open-folder",
                json={"dir": symlink_dir},
                headers={"Origin": loopback_origin},
            )
            self.assertEqual(resp_sym.status, 400)

        self.assertEqual(self.opened_folders, [])

    async def test_open_folder_invokes_injected_opener_for_default_and_valid_subdirs(self):
        loopback_origin = f"http://127.0.0.1:{self.server.port}"

        resp_default = await self.client.post(
            "/api/system/open-folder",
            json={},
            headers={"Origin": loopback_origin},
        )
        self.assertEqual(resp_default.status, 200)
        data_default = await resp_default.json()
        expected_default = os.path.realpath(self.download_dir)
        self.assertEqual(data_default, {"success": True, "path": expected_default})

        for default_payload in ({"dir": None}, {"dir": "   "}):
            with self.subTest(default_payload=default_payload):
                resp_fallback = await self.client.post(
                    "/api/system/open-folder",
                    json=default_payload,
                    headers={"Origin": loopback_origin},
                )
                self.assertEqual(resp_fallback.status, 200)
                self.assertEqual(
                    await resp_fallback.json(),
                    {"success": True, "path": expected_default},
                )

        subdir = os.path.join(self.download_dir, "nested", "sub")
        os.makedirs(subdir, exist_ok=True)
        resp_subdir = await self.client.post(
            "/api/system/open-folder",
            json={"dir": subdir},
            headers={"Origin": loopback_origin},
        )
        self.assertEqual(resp_subdir.status, 200)
        data_subdir = await resp_subdir.json()
        expected_subdir = os.path.realpath(subdir)
        self.assertEqual(data_subdir, {"success": True, "path": expected_subdir})

        resp_quick = await self.client.post(
            "/api/system/open-folder",
            json={"dir": "~/Downloads"},
            headers={"Origin": loopback_origin},
        )
        self.assertEqual(resp_quick.status, 200)
        data_quick = await resp_quick.json()
        expected_quick = os.path.realpath(os.path.join(self.temp_home.name, "Downloads"))
        self.assertEqual(data_quick, {"success": True, "path": expected_quick})

        self.assertEqual(
            self.opened_folders,
            [
                expected_default,
                expected_default,
                expected_default,
                expected_subdir,
                expected_quick,
            ],
        )

    async def test_open_folder_returns_500_when_opener_fails(self):
        self.opener_error = OSError("desktop file manager failed")
        resp = await self.client.post(
            "/api/system/open-folder",
            json={},
            headers={"Origin": f"http://127.0.0.1:{self.server.port}"},
        )
        self.assertEqual(resp.status, 500)
        data = await resp.json()
        self.assertIn("error", data)


class TestCrossPlatformSystemHelpers(unittest.TestCase):
    def test_get_platform_key_normalizes_os_names(self):
        for raw_system, expected in [
            ("Linux", "linux"),
            ("Windows", "windows"),
            ("Darwin", "darwin"),
        ]:
            with self.subTest(raw_system=raw_system):
                with patch("server.app.platform.system", return_value=raw_system):
                    self.assertEqual(server_app._get_platform_key(), expected)

    def test_get_quick_folders_os_variants_dedup_and_security(self):
        with tempfile.TemporaryDirectory() as tmp_home:
            download_dir = os.path.join(tmp_home, "Downloads", "Aether")
            os.makedirs(download_dir, exist_ok=True)
            with patch(
                "os.path.expanduser",
                side_effect=_make_fake_expanduser(tmp_home),
            ):
                with patch("server.app.platform.system", return_value="Windows"):
                    win_folders = server_app._get_quick_folders(download_dir)
                self.assertEqual(
                    win_folders[0],
                    {"label": "Carpeta Aether", "path": download_dir},
                )
                win_paths = [item["path"] for item in win_folders]
                self.assertIn("~/Downloads", win_paths)
                self.assertIn("~/Videos", win_paths)
                self.assertIn("~/Documents", win_paths)
                self.assertIn("~/Desktop", win_paths)

                with patch("server.app.platform.system", return_value="Darwin"):
                    mac_folders = server_app._get_quick_folders(download_dir)
                mac_paths = [item["path"] for item in mac_folders]
                self.assertIn("~/Downloads", mac_paths)
                self.assertIn("~/Movies", mac_paths)
                self.assertIn("~/Documents", mac_paths)
                self.assertIn("~/Desktop", mac_paths)

                with patch("server.app.platform.system", return_value="Linux"):
                    linux_en_folders = server_app._get_quick_folders(download_dir)
                linux_en_paths = [item["path"] for item in linux_en_folders]
                self.assertIn("~/Downloads", linux_en_paths)
                self.assertIn("~/Videos", linux_en_paths)

                for es_dir in ("Descargas", "Vídeos", "Documentos", "Escritorio"):
                    os.makedirs(os.path.join(tmp_home, es_dir), exist_ok=True)
                with patch("server.app.platform.system", return_value="Linux"):
                    linux_es_folders = server_app._get_quick_folders(download_dir)
                linux_es_paths = [item["path"] for item in linux_es_folders]
                self.assertIn("~/Descargas", linux_es_paths)
                self.assertIn("~/Vídeos", linux_es_paths)
                self.assertIn("~/Documentos", linux_es_paths)
                self.assertIn("~/Escritorio", linux_es_paths)

                # Deduplicate when download_dir matches one of the quick folders
                same_as_downloads = os.path.join(tmp_home, "Downloads")
                with patch("server.app.platform.system", return_value="Windows"):
                    dedup_folders = server_app._get_quick_folders(same_as_downloads)
                self.assertEqual(dedup_folders[0]["label"], "Carpeta Aether")
                self.assertEqual(
                    [item for item in dedup_folders if item["path"] == "~/Downloads"],
                    [],
                )

                for folder_list in (win_folders, mac_folders, linux_en_folders, linux_es_folders, dedup_folders):
                    for item in folder_list:
                        validate_output_dir(item["path"], download_dir)

    def test_open_folder_native_uses_platform_opener_without_shell(self):
        target = "/tmp/aether-test-dir"
        real_thread_cls = threading.Thread

        for os_name, expected_cmd in [
            ("Linux", ["xdg-open", target]),
            ("Darwin", ["open", target]),
        ]:
            with self.subTest(os_name=os_name):
                spawned_threads = []

                def _track_thread(*args, _threads=spawned_threads, **kwargs):
                    thread = real_thread_cls(*args, **kwargs)
                    _threads.append(thread)
                    return thread

                with patch("server.app.platform.system", return_value=os_name), patch(
                    "server.app.subprocess.Popen"
                ) as mock_popen, patch(
                    "server.app.threading.Thread",
                    side_effect=_track_thread,
                ):
                    server_app._open_folder_native(target)

                mock_popen.assert_called_once_with(
                    expected_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self.assertEqual(len(spawned_threads), 1)
                reaper = spawned_threads[0]
                self.assertTrue(reaper.daemon)
                reaper.join(timeout=2.0)
                self.assertFalse(reaper.is_alive())
                mock_popen.return_value.wait.assert_called_once_with()

        with patch("server.app.platform.system", return_value="Windows"), patch(
            "server.app.os.startfile",
            create=True,
        ) as mock_startfile:
            server_app._open_folder_native(target)
        mock_startfile.assert_called_once_with(target)


if __name__ == "__main__":
    unittest.main()
