"""
Aether Download Accelerator - Web & API Server
aiohttp.web con streaming WebSocket de baja latencia (100ms) y endpoints REST.
"""

import os
import re
import json
from typing import Optional, Set, Tuple
from urllib.parse import urlparse
from aiohttp import web, WSMsgType, WSCloseCode
from core.manager import QueueManager

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_EXTENSION_SCHEMES = frozenset({"chrome-extension", "moz-extension"})
_LIMIT_PATH_RE = re.compile(r"^/api/downloads/[^/]+/limit$")
_INT_STR_RE = re.compile(r"^[+-]?\d+$")

MANAGER_KEY = web.AppKey("manager", QueueManager)
WEBSOCKETS_KEY = web.AppKey("websockets", set)


def _normalize_allowed_hosts(allowed_hosts: Optional[Set[str]]) -> Set[str]:
    """Normaliza los hostnames permitidos manteniendo siempre loopback."""
    hosts = set(LOOPBACK_HOSTS)
    if allowed_hosts:
        for host in allowed_hosts:
            cleaned = str(host).strip().lower()
            if cleaned.startswith("[") and cleaned.endswith("]"):
                cleaned = cleaned[1:-1]
            if cleaned:
                hosts.add(cleaned)
    return hosts


def _extract_host_header_hostname(raw_host: str) -> Optional[str]:
    """Extrae y valida el hostname de la cabecera Host (soporta IPv6 con corchetes)."""
    if not raw_host:
        return None
    host = raw_host.strip()
    if not host or any(ch in host for ch in ("@", "/", "\\", "?", "#", " ", "\t", "\r", "\n", ",")):
        return None

    if host.startswith("["):
        end_idx = host.find("]")
        if end_idx == -1:
            return None
        hostname = host[1:end_idx].lower()
        rest = host[end_idx + 1:]
        if rest:
            if not rest.startswith(":"):
                return None
            port_str = rest[1:]
            if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
                return None
        return hostname or None

    colon_count = host.count(":")
    if colon_count == 0:
        return host.lower()
    if colon_count == 1:
        name, port_str = host.split(":", 1)
        if not name or not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
            return None
        return name.lower()
    if host == "::1":
        return "::1"
    return None


def _parse_and_validate_origin(
    origin: Optional[str],
    valid_hosts: Set[str],
) -> Tuple[bool, str]:
    """Valida la cabecera Origin para localhost/loopback y extensiones de navegador."""
    if origin is None:
        return True, ""
    stripped = origin.strip()
    if not stripped:
        return False, ""
    try:
        parsed = urlparse(stripped)
        scheme = (parsed.scheme or "").lower()
        hostname = (parsed.hostname or "").lower()
        _ = parsed.port
    except ValueError:
        return False, ""

    if parsed.username is not None or parsed.password is not None:
        return False, ""
    if parsed.path or parsed.query or parsed.fragment:
        return False, ""

    if scheme in ("http", "https"):
        return (hostname in valid_hosts), scheme
    if scheme in ALLOWED_EXTENSION_SCHEMES:
        return bool(parsed.netloc), scheme
    return False, ""


async def _read_json_dict(request: web.Request) -> Optional[dict]:
    """Lee el cuerpo JSON y verifica que sea un objeto JSON (dict)."""
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _parse_int_param(
    body: dict,
    key: str,
    default: int,
    *,
    min_value: int,
    max_value: Optional[int] = None,
) -> int:
    """Convierte y acota un parámetro entero rechazando booleanos, floats y texto inválido."""
    if key not in body:
        return default
    raw_value = body[key]
    if isinstance(raw_value, (bool, float)) or raw_value is None:
        raise ValueError(f"El parámetro '{key}' debe ser un entero válido")
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not _INT_STR_RE.fullmatch(stripped):
            raise ValueError(f"El parámetro '{key}' debe ser un entero válido")
        value = int(stripped)
    elif isinstance(raw_value, int):
        value = raw_value
    else:
        raise ValueError(f"El parámetro '{key}' debe ser un entero válido")

    if value < min_value or (max_value is not None and value > max_value):
        raise ValueError(f"El parámetro '{key}' está fuera de rango")
    return value


def create_app(
    download_dir: str,
    ui_dir: str,
    allowed_hosts: Optional[Set[str]] = None,
) -> web.Application:
    valid_hosts = _normalize_allowed_hosts(allowed_hosts)

    @web.middleware
    async def security_middleware(request: web.Request, handler):
        host_headers = request.headers.getall("Host", [])
        if len(host_headers) != 1:
            return web.json_response({"error": "Host no permitido"}, status=403)
        host_name = _extract_host_header_hostname(host_headers[0])
        if host_name not in valid_hosts:
            return web.json_response({"error": "Host no permitido"}, status=403)

        if request.path.startswith("/api/") or request.path == "/ws":
            origin_headers = request.headers.getall("Origin", [])
            if len(origin_headers) > 1:
                return web.json_response({"error": "Origen no permitido"}, status=403)
            origin_ok, origin_scheme = _parse_and_validate_origin(
                origin_headers[0] if origin_headers else None,
                valid_hosts,
            )
            if not origin_ok:
                return web.json_response({"error": "Origen no permitido"}, status=403)

            sec_fetch_values = [
                part.strip().lower()
                for header_val in request.headers.getall("Sec-Fetch-Site", [])
                for part in header_val.split(",")
            ]
            if "cross-site" in sec_fetch_values and origin_scheme not in ALLOWED_EXTENSION_SCHEMES:
                return web.json_response({"error": "Petición cross-site bloqueada"}, status=403)

        if request.method == "POST" and (
            request.path == "/api/downloads" or _LIMIT_PATH_RE.match(request.path)
        ):
            if request.content_type != "application/json":
                return web.json_response(
                    {"error": "Content-Type debe ser application/json"},
                    status=415,
                )

        return await handler(request)

    app = web.Application(middlewares=[security_middleware])
    manager = QueueManager(download_dir=download_dir)
    app[MANAGER_KEY] = manager
    app[WEBSOCKETS_KEY] = set()

    # Callback para emitir eventos a todos los clientes WebSocket
    async def ws_broadcast(data: dict):
        dead_ws = set()
        msg = json.dumps(data)
        for ws in list(app[WEBSOCKETS_KEY]):
            try:
                await ws.send_str(msg)
            except Exception:
                dead_ws.add(ws)
        app[WEBSOCKETS_KEY].difference_update(dead_ws)

    manager.subscribe(ws_broadcast)

    # --- REST Handlers ---
    async def get_downloads(request):
        return web.json_response({"downloads": manager.get_all()})

    async def add_download(request):
        body = await _read_json_dict(request)
        if body is None:
            return web.json_response({"error": "Payload JSON inválido"}, status=400)

        raw_url = body.get("url")
        if not isinstance(raw_url, str):
            return web.json_response({"error": "URL inválida o no soportada"}, status=400)
        url = raw_url.strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            return web.json_response({"error": "URL inválida o no soportada"}, status=400)
        try:
            parsed_url = urlparse(url)
        except ValueError:
            return web.json_response({"error": "URL inválida o no soportada"}, status=400)
        if not parsed_url.netloc:
            return web.json_response({"error": "URL inválida o no soportada"}, status=400)

        raw_filename = body.get("filename")
        if raw_filename is not None and not isinstance(raw_filename, str):
            return web.json_response({"error": "Nombre de archivo inválido"}, status=400)
        filename = raw_filename.strip() or None if raw_filename is not None else None

        raw_dir = body.get("dir")
        if raw_dir is not None and not isinstance(raw_dir, str):
            return web.json_response({"error": "Directorio de destino inválido"}, status=400)
        custom_dir = raw_dir.strip() or None if raw_dir is not None else None

        try:
            threads = _parse_int_param(body, "threads", 8, min_value=1, max_value=32)
            speed_limit = _parse_int_param(body, "speed_limit_bps", 0, min_value=0)
            task = await manager.add_download(
                url=url,
                threads=threads,
                filename=filename,
                output_dir=custom_dir,
                speed_limit_bps=speed_limit,
            )
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)

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
        body = await _read_json_dict(request)
        if body is None:
            return web.json_response({"error": "Payload JSON inválido"}, status=400)

        try:
            limit_bps = _parse_int_param(body, "limit_bps", 0, min_value=0)
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)

        success = await manager.set_speed_limit(task_id, limit_bps)
        return web.json_response({"success": success})

    async def delete_download(request):
        task_id = request.match_info["id"]
        delete_file = request.query.get("delete_file", "false").lower() == "true"
        try:
            success = await manager.remove_download(task_id, delete_file=delete_file)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
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
        app[WEBSOCKETS_KEY].add(ws)

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
            app[WEBSOCKETS_KEY].discard(ws)

        return ws

    async def on_shutdown(app_instance: web.Application):
        for ws in list(app_instance[WEBSOCKETS_KEY]):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"Server shutdown")

    async def on_cleanup(app_instance: web.Application):
        await manager.shutdown()

    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)

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
    app.router.add_static("/static", ui_dir, show_index=False)

    return app
