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
from urllib.parse import urlparse

from core.segment import Segment
from core.security import (
    validate_custom_filename,
    sanitize_remote_filename,
    ensure_within_directory,
)

MIN_SPLIT_CHUNK = 2 * 1024 * 1024  # 2MB mínimo para dividir un segmento
BUFFER_SIZE = 64 * 1024            # 64KB por paquete de lectura

class RateLimiter:
    """Implementación Token Bucket para limitar el ancho de banda sin jitter."""
    def __init__(self, rate_bps: int = 0):
        self.rate_bps = max(0, rate_bps)
        self.allowance = float(self.rate_bps)
        self.last_check = time.monotonic()
        self._lock = asyncio.Lock()
        self._rate_epoch = 0

    def set_rate(self, rate_bps: int) -> None:
        self.rate_bps = max(0, rate_bps)
        self.allowance = float(self.rate_bps)
        self.last_check = time.monotonic()
        self._rate_epoch += 1

    async def consume(self, amount: int) -> None:
        async with self._lock:
            if self.rate_bps <= 0:
                return
            now = time.monotonic()
            elapsed = max(0.0, now - self.last_check)
            self.last_check = now
            self.allowance = min(
                float(self.rate_bps),
                self.allowance + elapsed * self.rate_bps,
            )
            self.allowance -= amount
            wait_time = (-self.allowance) / self.rate_bps if self.allowance < 0 else 0.0
            epoch = self._rate_epoch

        while wait_time > 0:
            if self.rate_bps <= 0 or self._rate_epoch != epoch:
                break
            slice_s = min(wait_time, 0.2)
            await asyncio.sleep(slice_s)
            wait_time -= slice_s

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
        if custom_filename is not None:
            self.custom_filename = validate_custom_filename(custom_filename)
            self.filename = self.custom_filename
            self._filename_resolved = True
        else:
            self.custom_filename = None
            self.filename = "download.bin"
            self._filename_resolved = False

        self.connections = max(1, min(connections, 32))
        self.speed_limit_bps = speed_limit_bps
        self.rate_limiter = RateLimiter(speed_limit_bps)
        self.on_update = on_update

        self.output_path = ensure_within_directory(
            self.output_dir,
            os.path.join(self.output_dir, self.filename),
        )
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
        self._io_lock = asyncio.Lock()
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
        # Resolver nombre de archivo una sola vez (preservando fallbacks en reanudación)
        if not self.custom_filename and not self._filename_resolved:
            cd = resp.headers.get("Content-Disposition", "")
            match = re.search(
                r'filename\*?=(?:UTF-8\'[^\']*\')?["\']?([^"\';\r\n]+)["\']?',
                cd,
                re.IGNORECASE,
            )
            if match:
                raw_name = match.group(1).strip()
            else:
                path = urlparse(str(resp.url)).path
                raw_name = os.path.basename(path)

            fallback = f"download_{int(time.time())}.bin"
            self.filename = sanitize_remote_filename(raw_name, fallback=fallback)
            self.output_path = ensure_within_directory(
                self.output_dir,
                os.path.join(self.output_dir, self.filename),
            )
            self.meta_path = self.output_path + ".aether.json"
            self._filename_resolved = True

        # Actualizar tamaño y capacidades Range de forma incondicional desde las cabeceras actuales
        self.supports_range = False
        cr = resp.headers.get("Content-Range", "")
        cr_match = re.search(r'/(\d+)', cr) if cr else None
        cl = resp.headers.get("Content-Length")
        if cr_match:
            self.total_size = int(cr_match.group(1))
            self.supports_range = True
        elif not cr and cl and cl.isdigit():
            self.total_size = int(cl)
        else:
            self.total_size = 0

        if resp.headers.get("Accept-Ranges", "").strip().lower() == "bytes":
            self.supports_range = True

        self.etag = resp.headers.get("ETag")

    def _init_segments(self):
        """Inicializa los segmentos iniciales."""
        self.segments = []
        if self.supports_range and self.total_size > 0:
            num_conns = min(self.connections, self.total_size)
            chunk_size = self.total_size // num_conns
            for i in range(num_conns):
                start = i * chunk_size
                end = self.total_size - 1 if i == num_conns - 1 else ((i + 1) * chunk_size - 1)
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

    async def _write_chunk(self, offset: int, data: bytes) -> None:
        """Escribe un bloque en el desplazamiento exacto usando pwrite en POSIX o lock dedicado en Windows."""
        if not data:
            return
        if hasattr(os, "pwrite"):
            fd = self._file_handle.fileno()
            written = 0
            total = len(data)
            while written < total:
                buf = data if written == 0 else memoryview(data)[written:]
                n = os.pwrite(fd, buf, offset + written)
                if n <= 0:
                    raise OSError("os.pwrite wrote 0 bytes")
                written += n
        else:
            async with self._io_lock:
                self._file_handle.seek(offset)
                self._file_handle.write(data)

    def _cleanup_meta_files(self) -> None:
        """Elimina archivos de metadatos (.aether.json y .tmp) si existen."""
        for path in (self.meta_path, f"{self.meta_path}.tmp"):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _save_state(self):
        """Persiste el estado de los segmentos en disco de forma atómica."""
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
        tmp_path = f"{self.meta_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
            os.replace(tmp_path, self.meta_path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _load_state(self) -> bool:
        """Carga el estado previo si existe para reanudar, validando ETag y restaurando bytes."""
        if not os.path.exists(self.meta_path) or not os.path.exists(self.output_path):
            return False
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("total_size") != self.total_size or state.get("url") != self.url:
                return False
            saved_etag = state.get("etag")
            if saved_etag and self.etag and saved_etag != self.etag:
                return False
            raw_segments = state.get("segments", [])
            if not raw_segments:
                return False
            self.segments = [
                Segment(
                    id=s["id"],
                    start_byte=s["start_byte"],
                    current_byte=s["current_byte"],
                    end_byte=s["end_byte"],
                    status="paused" if s["current_byte"] <= s["end_byte"] else "completed",
                    bytes_downloaded=max(0, min(s["current_byte"], s["end_byte"] + 1) - s["start_byte"]),
                )
                for s in raw_segments
            ]
            self.bytes_downloaded = sum(
                max(0, min(s.current_byte, s.end_byte + 1) - s.start_byte)
                for s in self.segments
            )
            self.last_sample_bytes = self.bytes_downloaded
            return True
        except Exception:
            pass
        return False

    async def start(self):
        """Inicia o reanuda la descarga acelerada."""
        self._stop_event.clear()
        self.status = "downloading"
        self.error_message = None
        self.speed_bps = 0.0
        self.start_time = time.time()
        self.last_sample_time = self.start_time
        self.last_sample_bytes = self.bytes_downloaded
        self._workers = []

        os.makedirs(self.output_dir, exist_ok=True)

        connector = aiohttp.TCPConnector(limit=64)
        async with aiohttp.ClientSession(connector=connector) as session:
            # 1. Sondear siempre para obtener ETag, tamaño y soporte Range actuales
            success = await self.probe(session)
            if not success:
                self.status = "paused" if self._stop_event.is_set() else "error"
                return

            if self._stop_event.is_set():
                self.status = "paused"
                return

            # 2. Rama con Range y tamaño conocido
            if self.supports_range and self.total_size > 0:
                if not self._load_state():
                    self.bytes_downloaded = 0
                    self.last_sample_bytes = 0
                    self._init_segments()
                    # Pre-asignar tamaño del archivo
                    with open(self.output_path, "wb") as f:
                        f.truncate(self.total_size)

                # Abrir archivo para escrituras concurrentes
                self._file_handle = open(self.output_path, "r+b")

                try:
                    self._workers = [
                        asyncio.create_task(self._worker(session, seg))
                        for seg in self.segments
                    ]
                    balancer = asyncio.create_task(self._dynamic_balancer(session))
                    speed_sampler = asyncio.create_task(self._speed_sampler())

                    # Drenar todos los workers, incluidos los añadidos dinámicamente
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

                    all_done = all(s.current_byte > s.end_byte for s in self.segments)
                    if all_done and not self._stop_event.is_set():
                        self.status = "completed"
                        self.speed_bps = 0.0
                finally:
                    if self._file_handle:
                        try:
                            self._file_handle.flush()
                        except Exception:
                            pass
                        self._file_handle.close()
                        self._file_handle = None

                    if self.status == "completed":
                        self._cleanup_meta_files()
                    else:
                        self._save_state()
            else:
                # Rama de flujo único sin Range o sin Content-Length
                self.bytes_downloaded = 0
                self.last_sample_bytes = 0
                self._init_segments()
                speed_sampler = asyncio.create_task(self._speed_sampler())
                try:
                    await self._single_stream_download(session)
                    if (
                        self.segments
                        and self.segments[0].status == "completed"
                        and not self._stop_event.is_set()
                    ):
                        self.status = "completed"
                        self.speed_bps = 0.0
                        self._cleanup_meta_files()
                finally:
                    speed_sampler.cancel()
                    await asyncio.gather(speed_sampler, return_exceptions=True)

    async def _worker(self, session: aiohttp.ClientSession, segment: Segment):
        """Worker individual que descarga un rango específico."""
        while not self._stop_event.is_set():
            async with self._lock:
                if segment.current_byte > segment.end_byte:
                    segment.status = "completed"
                    break
                req_start = segment.current_byte
                req_end = segment.end_byte
                segment.status = "downloading"

            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Aether/1.0",
                "Range": f"bytes={req_start}-{req_end}"
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

                        async with self._lock:
                            remaining = segment.end_byte - segment.current_byte + 1
                            if remaining <= 0:
                                segment.status = "completed"
                                break
                            if len(chunk) > remaining:
                                chunk = chunk[:remaining]
                            offset = segment.current_byte
                            chunk_len = len(chunk)
                            segment.update_progress(chunk_len)
                            self.bytes_downloaded += chunk_len
                            seg_done = segment.current_byte > segment.end_byte

                        await self._write_chunk(offset, chunk)
                        await self.rate_limiter.consume(chunk_len)

                        if seg_done:
                            break

                    async with self._lock:
                        if segment.current_byte > segment.end_byte:
                            segment.status = "completed"
                            break
                        if self._stop_event.is_set() and segment.status != "error":
                            segment.status = "paused"
                            break

            except asyncio.CancelledError:
                if segment.current_byte > segment.end_byte:
                    segment.status = "completed"
                else:
                    segment.status = "paused"
                break
            except Exception:
                if self._stop_event.is_set():
                    segment.status = "paused"
                    break
                segment.status = "reconnecting"
                await asyncio.sleep(1)

        if self._stop_event.is_set() and segment.status not in ("completed", "error"):
            segment.status = "paused"

    async def _dynamic_balancer(self, session: aiohttp.ClientSession):
        """
        Algoritmo de balanceo dinámico de IDM / XDM:
        Si un worker termina temprano y otro segmento tiene muchos bytes pendientes,
        divide el segmento activo por la mitad y reasigna el hilo libre.
        """
        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)
            async with self._lock:
                if self._stop_event.is_set():
                    break
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

        seg.status = "paused" if self._stop_event.is_set() else "completed"

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
        if self.status == "completed":
            return
        self._stop_event.set()
        while True:
            pending = [w for w in self._workers if not w.done()]
            if not pending:
                break
            for w in pending:
                w.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        if self._file_handle and not self._file_handle.closed:
            try:
                self._file_handle.flush()
            except Exception:
                pass

        if self.status != "completed":
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
