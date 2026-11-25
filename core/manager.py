"""
Aether Download Accelerator - Queue Manager
Coordina las descargas activas, pausadas y en cola con emisión en tiempo real.
"""

import os
import uuid
import asyncio
from typing import Dict, List, Optional, Callable

from core.engine import DownloadTask
from core.security import (
    validate_custom_filename,
    validate_output_dir,
    ensure_within_directory,
)

class QueueManager:
    def __init__(self, download_dir: str):
        self.download_dir = download_dir
        self.tasks: Dict[str, DownloadTask] = {}
        self.listeners: List[Callable] = []
        self._active_runners: Dict[str, asyncio.Task] = {}
        os.makedirs(self.download_dir, exist_ok=True)

    def subscribe(self, callback: Callable):
        self.listeners.append(callback)

    def unsubscribe(self, callback: Callable):
        if callback in self.listeners:
            self.listeners.remove(callback)

    async def broadcast(self, data: dict):
        for listener in list(self.listeners):
            try:
                await listener(data)
            except Exception:
                pass

    async def add_download(
        self,
        url: str,
        threads: int = 8,
        filename: Optional[str] = None,
        output_dir: Optional[str] = None,
        speed_limit_bps: int = 0
    ) -> DownloadTask:
        target_dir = validate_output_dir(output_dir, self.download_dir)
        safe_filename = (
            validate_custom_filename(filename)
            if filename is not None and filename != ""
            else None
        )
        os.makedirs(target_dir, exist_ok=True)

        task_id = str(uuid.uuid4())[:8]
        task = DownloadTask(
            task_id=task_id,
            url=url,
            output_dir=target_dir,
            custom_filename=safe_filename,
            connections=threads,
            speed_limit_bps=speed_limit_bps,
            on_update=self._on_task_update
        )
        self.tasks[task_id] = task

        # Iniciar asíncronamente
        runner = asyncio.create_task(self._run_task(task))
        self._active_runners[task_id] = runner
        await self.broadcast({"type": "task_added", "task": task.to_dict()})
        return task

    async def _run_task(self, task: DownloadTask):
        current = asyncio.current_task()
        try:
            await task.start()
        except asyncio.CancelledError:
            if task.status not in ("completed", "error"):
                task.status = "paused"
                task.speed_bps = 0.0
        except Exception as e:
            task.status = "error"
            task.error_message = str(e)
        finally:
            if self._active_runners.get(task.task_id) is current:
                self._active_runners.pop(task.task_id, None)
            if task.task_id in self.tasks:
                await self.broadcast({"type": "task_updated", "task": task.to_dict()})

    async def _stop_runner(self, task_id: str, cancel_immediately: bool = False) -> None:
        runner = self._active_runners.pop(task_id, None)
        if runner is None or runner.done():
            return
        if cancel_immediately:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            return
        try:
            await asyncio.wait_for(asyncio.shield(runner), timeout=0.2)
        except asyncio.TimeoutError:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)

    async def _on_task_update(self, task_dict: dict):
        await self.broadcast({"type": "task_progress", "task": task_dict})

    async def set_speed_limit(self, task_id: str, limit_bps: int) -> bool:
        if task_id in self.tasks:
            self.tasks[task_id].set_speed_limit(limit_bps)
            await self.broadcast({"type": "task_updated", "task": self.tasks[task_id].to_dict()})
            return True
        return False

    async def pause_download(self, task_id: str) -> bool:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            await task.pause()
            await self.broadcast({"type": "task_updated", "task": task.to_dict()})
            return True
        return False

    async def resume_download(self, task_id: str) -> bool:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            if task.status in ("paused", "error"):
                existing = self._active_runners.get(task_id)
                if existing is not None and not existing.done():
                    return False
                runner = asyncio.create_task(self._run_task(task))
                self._active_runners[task_id] = runner
                await self.broadcast({"type": "task_updated", "task": task.to_dict()})
                return True
        return False

    async def _stop_task(self, task_id: str, task: DownloadTask) -> None:
        was_initializing = task.status == "initializing"
        await task.pause()
        await self._stop_runner(task_id, cancel_immediately=was_initializing)

    async def remove_download(self, task_id: str, delete_file: bool = False) -> bool:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            await self._stop_task(task_id, task)
            if delete_file:
                safe_output = ensure_within_directory(task.output_dir, task.output_path)
                safe_meta = ensure_within_directory(task.output_dir, task.meta_path)
                for path in (safe_output, safe_meta):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
            del self.tasks[task_id]
            await self.broadcast({"type": "task_removed", "task_id": task_id})
            return True
        return False

    async def shutdown(self) -> None:
        """Detiene todas las descargas activas de forma limpia."""
        for task_id, task in list(self.tasks.items()):
            await self._stop_task(task_id, task)

    def get_all(self) -> List[dict]:
        return [task.to_dict() for task in self.tasks.values()]

    def get_task(self, task_id: str) -> Optional[dict]:
        task = self.tasks.get(task_id)
        return task.to_dict() if task else None
