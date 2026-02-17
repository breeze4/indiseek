"""Background task manager for indexing operations."""

from __future__ import annotations

import logging
import queue
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TaskManager:
    """Manages background indexing tasks with progress streaming.

    Only one task runs at a time. Progress events are routed to
    subscriber queues for SSE streaming.
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, fn: Callable, **kwargs) -> str:
        """Submit a task for background execution.

        Args:
            name: Human-readable task name (e.g. "treesitter").
            fn: Callable to execute.
            **kwargs: Arguments passed to fn.

        Returns:
            Task ID (UUID string).

        Raises:
            RuntimeError: If a task is already running.
        """
        with self._lock:
            for t in self._tasks.values():
                if t["status"] == "running":
                    raise RuntimeError("A task is already running")

            task_id = str(uuid.uuid4())
            self._tasks[task_id] = {
                "id": task_id,
                "name": name,
                "status": "running",
                "progress_events": [],
                "result": None,
                "error": None,
            }
            self._subscribers[task_id] = []

        def _run():
            try:
                result = fn(**kwargs)
                with self._lock:
                    self._tasks[task_id]["status"] = "completed"
                    self._tasks[task_id]["result"] = result
                self._broadcast(task_id, {"type": "done", "result": result})
            except Exception as e:
                logger.exception("Task %s (%s) failed", task_id, name)
                with self._lock:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["error"] = traceback.format_exc()
                self._broadcast(task_id, {"type": "error", "error": str(e)})

        self._executor.submit(_run)
        return task_id

    def get_status(self, task_id: str) -> dict | None:
        """Get task status and metadata."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return dict(task)

    def list_tasks(self) -> list[dict]:
        """List all tasks."""
        with self._lock:
            return [dict(t) for t in self._tasks.values()]

    def has_running_task(self) -> bool:
        """Check if any task is currently running."""
        with self._lock:
            return any(t["status"] == "running" for t in self._tasks.values())

    def subscribe(self, task_id: str) -> queue.Queue | None:
        """Subscribe to progress events for a task.

        Returns a Queue that receives progress event dicts, or None
        if the task doesn't exist.
        """
        with self._lock:
            if task_id not in self._tasks:
                return None
            q: queue.Queue = queue.Queue()
            self._subscribers[task_id].append(q)
            return q

    def push_progress(self, task_id: str, event: dict) -> None:
        """Push a progress event for a task.

        Called by the running task via the on_progress callback.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task["progress_events"].append(event)
        self._broadcast(task_id, {"type": "progress", **event})

    def _broadcast(self, task_id: str, event: dict) -> None:
        """Send an event to all subscribers of a task."""
        with self._lock:
            subs = list(self._subscribers.get(task_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
