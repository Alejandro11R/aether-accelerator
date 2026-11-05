"""
Aether Download Accelerator - Core Download Engine
Arquitectura Donatello: Asincronía pura con aiohttp, segmentación dinámica y escritura no bloqueante.
Control Splinter: Verificación estricta de límites, fallback de servidor y persistencia.
"""

import os
import re
import json
import time
import asyncio
import aiohttp
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse, unquote

from core.segment import Segment

MIN_SPLIT_CHUNK = 2 * 1024 * 1024  # 2MB mínimo para dividir un segmento
BUFFER_SIZE = 64 * 1024            # 64KB por paquete de lectura

class RateLimiter:
    """Implementación Token Bucket para limitar el ancho de banda sin jitter."""
    def __init__(self, rate_bps: int = 0):
        self.rate_bps = max(0, rate_bps)
        self.allowance = float(self.rate_bps)
        self.last_check = time.time()
        self._lock = asyncio.Lock()

    def set_rate(self, rate_bps: int):
        self.rate_bps = max(0, rate_bps)
        self.allowance = float(self.rate_bps)

    async def consume(self, amount: int):
        if self.rate_bps <= 0:
            return
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_check
            self.last_check = now
            self.allowance += elapsed * self.rate_bps
            if self.allowance > self.rate_bps:
                self.allowance = float(self.rate_bps)
            if self.allowance < amount:
                deficit = amount - self.allowance
                delay = deficit / self.rate_bps
                self.allowance = 0.0
                await asyncio.sleep(min(delay, 2.0))
            else:
                self.allowance -= amount

class DownloadTask:
    def __init__(
        self,
        task_id: str,
        url: str,
        output_dir: str,
        custom_filename: Optional[str] = None,
        connections: int = 8,
        speed_limit_bps: int = 0,
        on_update: Optional[Callable] = None
    ):
        self.task_id = task_id
        self.url = url
        self.output_dir = output_dir
        self.custom_filename = custom_filename
        self.connections = max(1, min(connections, 32))
        self.speed_limit_bps = speed_limit_bps
        self.rate_limiter = RateLimiter(speed_limit_bps)
        self.on_update = on_update

        self.filename = custom_filename or "download.bin"
        self.output_path = os.path.join(output_dir, self.filename)
        self.meta_path = self.output_path + ".aether.json"
        
        self.total_size: int = 0
        self.supports_range: bool = False
        self.etag: Optional[str] = None
        self.segments: List[Segment] = []
        
        self.status = "initializing"  # initializing, downloading, paused, completed, error
        self.error_message: Optional[str] = None
        self.speed_bps: float = 0.0
        self.bytes_downloaded: int = 0
        self.start_time: float = 0.0
        self.last_sample_time: float = 0.0
        self.last_sample_bytes: int = 0
        
        self._workers: List[asyncio.Task] = []
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._file_handle = None

    @property
    def progress_percentage(self) -> float:
        if self.total_size <= 0:
            return 0.0
        downloaded = sum(s.current_byte - s.start_byte for s in self.segments)
        return min(100.0, max(0.0, (downloaded / self.total_size) * 100.0))

    @property
    def eta_seconds(self) -> int:
        if self.speed_bps <= 0 or self.total_size <= 0:
            return -1
        remaining = self.total_size - self.bytes_downloaded
        return max(0, int(remaining / self.speed_bps))

    async def probe(self, session: aiohttp.ClientSession) -> bool:
        """Sondea la URL para obtener metadatos y verificar si acepta descargas segmentadas."""
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Aether/1.0",
            "Accept": "*/*"
        }
        
        try:
            # 1. Intentar HEAD request
            async with session.head(self.url, headers=headers, allow_redirects=True, timeout=10) as resp:
                if resp.status == 200:
                    self._parse_headers(resp)
                    return True
        except Exception:
            pass

        # 2. Fallback a GET con Range de 1 byte si HEAD falla o es bloqueado por CDN
        try:
            get_headers = headers.copy()
            get_headers["Range"] = "bytes=0-0"
            async with session.get(self.url, headers=get_headers, allow_redirects=True, timeout=10) as resp:
                if resp.status in (200, 206):
                    self._parse_headers(resp)
                    if resp.status == 206:
                        self.supports_range = True
                    return True
                else:
                    self.error_message = f"Servidor respondió con código HTTP {resp.status}"
                    return False
        except Exception as e:
            self.error_message = f"Error al conectar con la URL: {str(e)}"
            return False

    def _parse_headers(self, resp: aiohttp.ClientResponse):
        # Resolver nombre de archivo
        if not self.custom_filename:
            # Revisar Content-Disposition
            cd = resp.headers.get("Content-Disposition", "")
            match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\n]+)["\']?', cd, re.IGNORECASE)
            if match:
                self.filename = unquote(match.group(1).strip())
            else:
                path = urlparse(str(resp.url)).path
                name = os.path.basename(path)
                self.filename = unquote(name) if name else f"download_{int(time.time())}.bin"
            
            # Sanitizar nombre (Splinter Checkpoint)
            self.filename = re.sub(r'[\\/*?:"<>|]', "_", self.filename)
            self.output_path = os.path.join(self.output_dir, self.filename)
            self.meta_path = self.output_path + ".aether.json"

        # Content-Range o Content-Length
        cr = resp.headers.get("Content-Range", "")
        if cr:
            match = re.search(r'/(\d+)', cr)
            if match:
                self.total_size = int(match.group(1))
                self.supports_range = True

        if not self.total_size:
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                self.total_size = int(cl)

        if resp.headers.get("Accept-Ranges", "").lower() == "bytes":
            self.supports_range = True

        self.etag = resp.headers.get("ETag")

    def _init_segments(self):
        """Inicializa los segmentos iniciales."""
        self.segments = []
        if self.supports_range and self.total_size > 0:
            chunk_size = self.total_size // self.connections
            for i in range(self.connections):
                start = i * chunk_size
                end = self.total_size - 1 if i == self.connections - 1 else ((i + 1) * chunk_size - 1)
                self.segments.append(Segment(
                    id=i,
                    start_byte=start,
                    current_byte=start,
                    end_byte=end,
                    status="pending"
                ))
        else:
            # Fallback a segmento único si no soporta Range o tamaño desconocido
            self.segments.append(Segment(
                id=0,
                start_byte=0,
                current_byte=0,
                end_byte=self.total_size - 1 if self.total_size > 0 else 0,
                status="pending"
            ))

    def _save_state(self):
        """Persiste el estado de los segmentos en disco."""
        if not self.supports_range or self.total_size <= 0:
            return
        state = {
            "task_id": self.task_id,
            "url": self.url,
            "filename": self.filename,
            "total_size": self.total_size,
            "etag": self.etag,
            "segments": [s.to_dict() for s in self.segments]
        }
        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    def _load_state(self) -> bool:
        """Carga el estado previo si existe para reanudar."""
        if not os.path.exists(self.meta_path) or not os.path.exists(self.output_path):
            return False
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("total_size") == self.total_size and state.get("url") == self.url:
                self.segments = [
                    Segment(
                        id=s["id"],
                        start_byte=s["start_byte"],
                        current_byte=s["current_byte"],
                        end_byte=s["end_byte"],
                        status="paused" if s["current_byte"] <= s["end_byte"] else "completed"
                    )
                    for s in state.get("segments", [])
                ]
                return True
        except Exception:
            pass
        return False

    async def start(self):
        """Inicia o reanuda la descarga acelerada."""
        self._stop_event.clear()
        self.status = "downloading"
        self.start_time = time.time()
        self.last_sample_time = self.start_time
        self.last_sample_bytes = self.bytes_downloaded

        os.makedirs(self.output_dir, exist_ok=True)

        connector = aiohttp.TCPConnector(limit=64)
        async with aiohttp.ClientSession(connector=connector) as session:
            # 1. Sondear si es primera vez
            if self.total_size == 0:
                success = await self.probe(session)
                if not success:
                    self.status = "error"
                    return

            # 2. Asignación de archivo en Linux (truncate para evitar bloqueos)
            if not self._load_state():
                self._init_segments()
                # Pre-asignar tamaño del archivo
                with open(self.output_path, "wb") as f:
                    if self.total_size > 0:
                        f.truncate(self.total_size)

            # Abrir archivo para escrituras concurrentes
            self._file_handle = open(self.output_path, "r+b")

            try:
                # 3. Lanzar workers
                if self.supports_range and len(self.segments) > 1:
                    self._workers = [
                        asyncio.create_task(self._worker(session, seg))
                        for seg in self.segments
                    ]
                    # Tarea de balanceo dinámico de segmentos (estilo IDM)
                    balancer = asyncio.create_task(self._dynamic_balancer(session))
                    speed_sampler = asyncio.create_task(self._speed_sampler())

                    await asyncio.gather(*self._workers, return_exceptions=True)
                    balancer.cancel()
                    speed_sampler.cancel()
                else:
                    # Descarga lineal simple
                    await self._single_stream_download(session)

                # Verificar finalización
                all_done = all(s.current_byte > s.end_byte for s in self.segments)
                if all_done and not self._stop_event.is_set():
                    self.status = "completed"
                    self.speed_bps = 0.0
                    if os.path.exists(self.meta_path):
                        os.remove(self.meta_path)
            finally:
                if self._file_handle:
                    self._file_handle.close()
                    self._file_handle = None
                self._save_state()

    async def _worker(self, session: aiohttp.ClientSession, segment: Segment):
        """Worker individual que descarga un rango específico."""
        while not self._stop_event.is_set():
            if segment.current_byte > segment.end_byte:
                segment.status = "completed"
                break

            segment.status = "downloading"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Aether/1.0",
                "Range": f"bytes={segment.current_byte}-{segment.end_byte}"
            }

            try:
                async with session.get(self.url, headers=headers, timeout=aiohttp.ClientTimeout(total=None, sock_read=30)) as resp:
                    if resp.status not in (200, 206):
                        segment.status = "error"
                        await asyncio.sleep(2)
                        continue

                    while not self._stop_event.is_set():
                        chunk = await resp.content.read(BUFFER_SIZE)
                        if not chunk:
                            break

                        # Escribir en posición exacta del archivo
                        async with self._lock:
                            self._file_handle.seek(segment.current_byte)
                            self._file_handle.write(chunk)
                            self._file_handle.flush()

                        chunk_len = len(chunk)
                        segment.update_progress(chunk_len)
                        self.bytes_downloaded += chunk_len
                        await self.rate_limiter.consume(chunk_len)

                    if segment.current_byte > segment.end_byte:
                        segment.status = "completed"
                        break

            except asyncio.CancelledError:
                segment.status = "paused"
                break
            except Exception:
                segment.status = "reconnecting"
                await asyncio.sleep(1)

    async def _dynamic_balancer(self, session: aiohttp.ClientSession):
        """
        Algoritmo de balanceo dinámico de IDM / XDM:
        Si un worker termina temprano y otro segmento tiene muchos bytes pendientes,
        divide el segmento activo por la mitad y reasigna el hilo libre.
        """
        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)
            async with self._lock:
                completed = [s for s in self.segments if s.status == "completed"]
                active = [s for s in self.segments if s.status == "downloading"]

                if completed and active and len(self.segments) < 32:
                    # Encontrar el segmento con más bytes pendientes
                    largest = max(active, key=lambda s: s.remaining_bytes, default=None)
                    if largest and largest.remaining_bytes >= MIN_SPLIT_CHUNK:
                        # Dividir en dos
                        mid = largest.current_byte + (largest.remaining_bytes // 2)
                        new_start = mid + 1
                        new_end = largest.end_byte

                        largest.end_byte = mid  # El actual se queda con la primera mitad

                        new_id = len(self.segments)
                        new_seg = Segment(
                            id=new_id,
                            start_byte=new_start,
                            current_byte=new_start,
                            end_byte=new_end,
                            status="pending"
                        )
                        self.segments.append(new_seg)

                        # Lanzar nuevo worker para la segunda mitad
                        task = asyncio.create_task(self._worker(session, new_seg))
                        self._workers.append(task)

    async def _single_stream_download(self, session: aiohttp.ClientSession):
        """Descarga lineal en streaming para servidores que no aceptan Range."""
        seg = self.segments[0]
        seg.status = "downloading"
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Aether/1.0"}

        async with session.get(self.url, headers=headers, timeout=aiohttp.ClientTimeout(total=None, sock_read=30)) as resp:
            with open(self.output_path, "wb") as f:
                while not self._stop_event.is_set():
                    chunk = await resp.content.read(BUFFER_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    chunk_len = len(chunk)
                    seg.update_progress(chunk_len)
                    self.bytes_downloaded += chunk_len
                    await self.rate_limiter.consume(chunk_len)
                    
        seg.status = "completed"

    async def _speed_sampler(self):
        """Muestra y calcula la velocidad global promedio cada 500ms."""
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)
            now = time.time()
            delta = now - self.last_sample_time
            if delta > 0:
                bytes_delta = self.bytes_downloaded - self.last_sample_bytes
                instant_speed = bytes_delta / delta
                self.speed_bps = (self.speed_bps * 0.6) + (instant_speed * 0.4)
                self.last_sample_time = now
                self.last_sample_bytes = self.bytes_downloaded

            if self.on_update:
                try:
                    await self.on_update(self.to_dict())
                except Exception:
                    pass

    def set_speed_limit(self, limit_bps: int):
        self.speed_limit_bps = max(0, limit_bps)
        self.rate_limiter.set_rate(self.speed_limit_bps)

    async def pause(self):
        """Pausa la descarga de forma limpia."""
        self._stop_event.set()
        for w in self._workers:
            w.cancel()
        self.status = "paused"
        self.speed_bps = 0.0
        self._save_state()

    def to_dict(self) -> dict:
        return {
            "id": self.task_id,
            "url": self.url,
            "filename": self.filename,
            "output_path": self.output_path,
            "total_size": self.total_size,
            "bytes_downloaded": self.bytes_downloaded,
            "progress": round(self.progress_percentage, 1),
            "speed_bps": round(self.speed_bps),
            "speed_limit_bps": self.speed_limit_bps,
            "eta_seconds": self.eta_seconds,
            "status": self.status,
            "supports_range": self.supports_range,
            "connections": len(self.segments),
            "error_message": self.error_message,
            "segments": [s.to_dict() for s in self.segments]
        }
