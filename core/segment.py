"""
Aether Download Accelerator - Core Segment Model
Gestiona los límites, el progreso y la velocidad de cada segmento TCP paralelo.
"""

from dataclasses import dataclass, asdict
import time
from typing import Optional

@dataclass
class Segment:
    id: int
    start_byte: int
    current_byte: int
    end_byte: int
    status: str = "pending"  # pending, downloading, completed, paused, error
    speed_bps: float = 0.0
    bytes_downloaded: int = 0
    last_update_time: float = 0.0

    @property
    def total_bytes(self) -> int:
        return max(0, self.end_byte - self.start_byte + 1)

    @property
    def remaining_bytes(self) -> int:
        return max(0, self.end_byte - self.current_byte + 1)

    @property
    def progress_percentage(self) -> float:
        if self.total_bytes <= 0:
            return 100.0
        done = self.current_byte - self.start_byte
        return min(100.0, max(0.0, (done / self.total_bytes) * 100.0))

    def update_progress(self, new_bytes: int):
        now = time.time()
        self.bytes_downloaded += new_bytes
        self.current_byte += new_bytes
        
        delta = now - self.last_update_time
        if delta >= 0.2 and self.last_update_time > 0:
            instant_speed = new_bytes / delta
            # Suavizado exponencial para evitar fluctuaciones erráticas
            self.speed_bps = (self.speed_bps * 0.7) + (instant_speed * 0.3)
            self.last_update_time = now
        elif self.last_update_time == 0:
            self.last_update_time = now

        if self.current_byte > self.end_byte:
            self.status = "completed"
            self.speed_bps = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["progress"] = round(self.progress_percentage, 2)
        data["total"] = self.total_bytes
        data["remaining"] = self.remaining_bytes
        return data
