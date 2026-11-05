"""
Aether Download Accelerator - Web & API Server
aiohttp.web con streaming WebSocket de baja latencia (100ms) y endpoints REST.
"""

import os
import json
import asyncio
from aiohttp import web, WSMsgType
from core.manager import QueueManager

def create_app(download_dir: str, ui_dir: str) -> web.Application:
    app = web.Application()
    manager = QueueManager(download_dir=download_dir)
    app["manager"] = manager
    app["websockets"] = set()

    # Callback para emitir eventos a todos los clientes WebSocket
    async def ws_broadcast(data: dict):
        dead_ws = set()
        msg = json.dumps(data)
        for ws in list(app["websockets"]):
            try:
                await ws.send_str(msg)
            except Exception:
                dead_ws.add(ws)
        app["websockets"].difference_update(dead_ws)

    manager.subscribe(ws_broadcast)

    # --- REST Handlers ---
    async def get_downloads(request):
        return web.json_response({"downloads": manager.get_all()})

    async def add_download(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Payload JSON inválido"}, status=400)

        url = body.get("url", "").strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return web.json_response({"error": "URL inválida o no soportada"}, status=400)

        threads = int(body.get("threads", 8))
        filename = body.get("filename", "").strip() or None
        custom_dir = body.get("dir", "").strip() or None
        speed_limit = int(body.get("speed_limit_bps", 0))

        task = await manager.add_download(
            url=url,
            threads=threads,
            filename=filename,
            output_dir=custom_dir,
            speed_limit_bps=speed_limit
        )
        return web.json_response({"success": True, "task": task.to_dict()})

    async def pause_download(request):
        task_id = request.match_info["id"]
        success = await manager.pause_download(task_id)
        return web.json_response({"success": success})

    async def resume_download(request):
        task_id = request.match_info["id"]
        success = await manager.resume_download(task_id)
        return web.json_response({"success": success})

    async def set_limit(request):
        task_id = request.match_info["id"]
        try:
            body = await request.json()
            limit_bps = int(body.get("limit_bps", 0))
            success = await manager.set_speed_limit(task_id, limit_bps)
            return web.json_response({"success": success})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def delete_download(request):
        task_id = request.match_info["id"]
        delete_file = request.query.get("delete_file", "false").lower() == "true"
        success = await manager.remove_download(task_id, delete_file=delete_file)
        return web.json_response({"success": success})

    async def get_system_info(request):
        downloads = manager.get_all()
        total_speed = sum(d["speed_bps"] for d in downloads if d["status"] == "downloading")
        active_count = sum(1 for d in downloads if d["status"] == "downloading")
        return web.json_response({
            "total_speed_bps": total_speed,
            "active_tasks": active_count,
            "total_tasks": len(downloads),
            "download_dir": manager.download_dir
        })

    # --- WebSocket Handler ---
    async def websocket_handler(request):
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        app["websockets"].add(ws)

        # Enviar estado inicial inmediatamente
        await ws.send_json({
            "type": "init",
            "downloads": manager.get_all(),
            "download_dir": manager.download_dir
        })

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        action = data.get("action")
                        if action == "ping":
                            await ws.send_json({"type": "pong"})
                    except Exception:
                        pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            app["websockets"].discard(ws)

        return ws

    # --- Rutas ---
    app.router.add_get("/api/downloads", get_downloads)
    app.router.add_post("/api/downloads", add_download)
    app.router.add_post("/api/downloads/{id}/pause", pause_download)
    app.router.add_post("/api/downloads/{id}/resume", resume_download)
    app.router.add_post("/api/downloads/{id}/limit", set_limit)
    app.router.add_delete("/api/downloads/{id}", delete_download)
    app.router.add_get("/api/system", get_system_info)
    app.router.add_get("/ws", websocket_handler)

    # Servir UI estática
    async def index_handler(request):
        return web.FileResponse(os.path.join(ui_dir, "index.html"))

    app.router.add_get("/", index_handler)
    app.router.add_static("/static", ui_dir, show_index=True)

    return app
