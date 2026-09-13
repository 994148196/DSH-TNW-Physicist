"""追加式 JSONL 事件日志：谁在何时对什么对象做了什么（审计与恢复依据，计划 v2 §5.3）。"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .models import utcnow
from .status import Actor


class Event(BaseModel):
    timestamp: datetime = Field(default_factory=utcnow)
    actor: Actor
    action: str  # 例如 save / status_change / approve / reject
    project_id: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class EventLog:
    """Append-only JSONL。进程重启后重开即恢复完整历史。"""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: Event) -> Event:
        line = event.model_dump_json()
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return event

    def events(self, project_id: str | None = None) -> list[Event]:
        if not self._path.exists():
            return []
        out: list[Event] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = Event.model_validate_json(line)
            if project_id is None or event.project_id == project_id:
                out.append(event)
        return out
