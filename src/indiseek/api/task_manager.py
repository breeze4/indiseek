"""Background task manager for indexing and query operations."""

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
    """Manages background tasks with progress streaming.

    Exclusive tasks (indexing ops) are restricted to one at a time.
    Concurrent tasks (queries) can run in parallel without limit.
    """

    def __init__(self) -> None:
        # Unbounded pool: queries are all I/O-bound (Gemini API calls)
        self._executor = ThreadPoolExecutor(max_workers=None)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def submit(
        self, name: str, fn: Callable, task_id: str | None = None,
        kind: str = "exclusive", **kwargs,
    ) -> str:
        """Submit a task for background execution.

        Args:
            name: Human-readable task name (e.g. "treesitter", "query").
            fn: Callable to execute.
            task_id: Optional pre-generated task ID. If None, one is generated.
            kind: "exclusive" tasks (indexing) block if another exclusive task
                  is running. "concurrent" tasks (queries) always run.
            **kwargs: Arguments passed to fn.

        Returns:
            Task ID (UUID string).

        Raises:
            RuntimeError: If kind="exclusive" and an exclusive task is running.
        """
        with self._lock:
            if kind == "exclusive":
                for t in self._tasks.values():
                    if t["status"] == "running" and t.get("kind") == "exclusive":
                        raise RuntimeError("An exclusive task is already running")

            if task_id is None:
                task_id = str(uuid.uuid4())
            self._tasks[task_id] = {
                "id": task_id,
                "name": name,
                "kind": kind,
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
                    task = self._tasks.get(task_id)
                    if task is None:
                        return
                    task["status"] = "completed"
                    task["result"] = result
                self._broadcast(task_id, {"type": "done", "result": result})
            except Exception as e:
                logger.exception("Task %s (%s) failed", task_id, name)
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is None:
                        return
                    task["status"] = "failed"
                    task["error"] = traceback.format_exc()
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

    def has_running_exclusive_task(self) -> bool:
        """Check if an exclusive (indexing) task is currently running."""
        with self._lock:
            return any(
                t["status"] == "running" and t.get("kind") == "exclusive"
                for t in self._tasks.values()
            )

    # Keep old name as an alias so existing callers still work
    def has_running_task(self) -> bool:
        return self.has_running_exclusive_task()

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
