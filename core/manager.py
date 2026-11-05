"""
Aether Download Accelerator - Queue Manager
Coordina las descargas activas, pausadas y en cola con emisión en tiempo real.
"""

import os
import uuid
import json
import asyncio
from typing import Dict, List, Optional, Callable

from core.engine import DownloadTask

class QueueManager:
    def __init__(self, download_dir: str):
        self.download_dir = download_dir
        self.tasks: Dict[str, DownloadTask] = {}
        self.listeners: List[Callable] = []
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
        output_dir: Optional[str] = None
    ) -> DownloadTask:
        task_id = str(uuid.uuid4())[:8]
        target_dir = os.path.abspath(os.path.expanduser(output_dir)) if output_dir else self.download_dir
        os.makedirs(target_dir, exist_ok=True)
        
        task = DownloadTask(
            task_id=task_id,
            url=url,
            output_dir=target_dir,
            custom_filename=filename,
            connections=threads,
            on_update=self._on_task_update
        )
        self.tasks[task_id] = task

        # Iniciar asíncronamente
        asyncio.create_task(self._run_task(task))
        await self.broadcast({"type": "task_added", "task": task.to_dict()})
        return task

    async def _run_task(self, task: DownloadTask):
        try:
            await task.start()
        except Exception as e:
            task.status = "error"
            task.error_message = str(e)
        finally:
            await self.broadcast({"type": "task_updated", "task": task.to_dict()})

    async def _on_task_update(self, task_dict: dict):
        await self.broadcast({"type": "task_progress", "task": task_dict})

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
                asyncio.create_task(self._run_task(task))
                await self.broadcast({"type": "task_updated", "task": task.to_dict()})
                return True
        return False

    async def remove_download(self, task_id: str, delete_file: bool = False) -> bool:
        if task_id in self.tasks:
            task = self.tasks[task_id]
            await task.pause()
            if delete_file:
                if os.path.exists(task.output_path):
                    try:
                        os.remove(task.output_path)
                    except Exception:
                        pass
                if os.path.exists(task.meta_path):
                    try:
                        os.remove(task.meta_path)
                    except Exception:
                        pass
            del self.tasks[task_id]
            await self.broadcast({"type": "task_removed", "task_id": task_id})
            return True
        return False

    def get_all(self) -> List[dict]:
        return [task.to_dict() for task in self.tasks.values()]

    def get_task(self, task_id: str) -> Optional[dict]:
        task = self.tasks.get(task_id)
        return task.to_dict() if task else None
