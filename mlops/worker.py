"""
Background Task Worker
========================
Runs heavy ML pipeline tasks in separate processes so the
FastAPI server stays responsive for user-facing requests.

Usage:
    from mlops.worker import task_manager

    task_id = task_manager.submit("classify_gonogo", target_fn, args=(game,))
    status  = task_manager.get_status(task_id)
"""

import uuid
import time
import logging
import traceback
import threading
import sys
from enum import Enum
from concurrent.futures import ProcessPoolExecutor, Future
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("mlops.worker")


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class TaskInfo:
    task_id: str
    name: str
    state: TaskState = TaskState.PENDING
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    error_traceback: str | None = None


def _run_task(fn: Callable, args: tuple, kwargs: dict) -> Any:
    """Wrapper executed inside the worker process."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return fn(*args, **kwargs)


class TaskManager:
    """
    Manages background tasks using a ProcessPoolExecutor.

    - submit()      → enqueue a task, returns task_id immediately
    - get_status()  → poll task progress
    - list_tasks()  → list recent tasks
    """

    def __init__(self, max_workers: int = 2):
        self._executor = ProcessPoolExecutor(max_workers=max_workers)
        self._tasks: dict[str, TaskInfo] = {}
        self._futures: dict[str, Future] = {}
        self._lock = threading.RLock()

    def submit(self, name: str, fn: Callable, args: tuple = (), kwargs: dict = None) -> str:
        """Submit a task to run in a background process. Returns task_id."""
        with self._lock:
            return self._submit_unlocked(name, fn, args, kwargs)

    def get_or_submit(
        self,
        name: str,
        fn: Callable,
        args: tuple = (),
        kwargs: dict = None,
    ) -> tuple[str, bool]:
        """
        Return the running task for name, or atomically submit a new one.

        Returns:
            (task_id, created)
        """
        with self._lock:
            existing = self._find_running_unlocked(name)
            if existing:
                return existing, False
            return self._submit_unlocked(name, fn, args, kwargs), True

    def _submit_unlocked(self, name: str, fn: Callable, args: tuple = (), kwargs: dict = None) -> str:
        task_id = uuid.uuid4().hex[:12]
        info = TaskInfo(task_id=task_id, name=name, state=TaskState.RUNNING,
                        started_at=time.time())
        self._tasks[task_id] = info

        future = self._executor.submit(_run_task, fn, args, kwargs or {})
        self._futures[task_id] = future
        future.add_done_callback(lambda f: self._on_done(task_id, f))

        log.info("Task submitted: %s (%s)", name, task_id)
        return task_id

    def _on_done(self, task_id: str, future: Future):
        """Callback when a task finishes (success or failure)."""
        with self._lock:
            info = self._tasks.get(task_id)
            if not info:
                return

            info.finished_at = time.time()
            elapsed = round(info.finished_at - (info.started_at or info.submitted_at), 1)

            try:
                result = future.result()
                info.state = TaskState.SUCCESS
                info.result = result
                log.info("Task completed: %s (%s) in %ss", info.name, task_id, elapsed)
                return
            except Exception as e:
                info.state = TaskState.FAILED
                info.error = f"{type(e).__name__}: {str(e)}"
                info.error_traceback = traceback.format_exc()
            log.error(
                "Task failed: %s (%s) after %ss — %s\n%s",
                info.name,
                task_id,
                elapsed,
                info.error,
                info.error_traceback,
            )

    def get_status(self, task_id: str) -> dict | None:
        """Get task status as a dict. Returns None if task_id not found."""
        with self._lock:
            info = self._tasks.get(task_id)
        if not info:
            return None

        elapsed = None
        if info.started_at:
            end = info.finished_at or time.time()
            elapsed = round(end - info.started_at, 1)

        return {
            "task_id": info.task_id,
            "name": info.name,
            "state": info.state.value,
            "elapsed_seconds": elapsed,
            "result": info.result if info.state == TaskState.SUCCESS else None,
            "error": info.error if info.state == TaskState.FAILED else None,
            "error_traceback": (
                info.error_traceback if info.state == TaskState.FAILED else None
            ),
        }

    def list_tasks(self, limit: int = 20) -> list[dict]:
        """List recent tasks (newest first)."""
        with self._lock:
            sorted_tasks = sorted(
                self._tasks.values(),
                key=lambda t: t.submitted_at,
                reverse=True,
            )
        return [self.get_status(t.task_id) for t in sorted_tasks[:limit]]

    def is_running(self, name: str) -> str | None:
        """Check if a task with the given name is already running. Returns task_id or None."""
        with self._lock:
            return self._find_running_unlocked(name)

    def _find_running_unlocked(self, name: str) -> str | None:
        for info in self._tasks.values():
            if info.name == name and info.state == TaskState.RUNNING:
                return info.task_id
        return None

    def cleanup(self, max_age_seconds: int = 3600):
        """Remove completed tasks older than max_age_seconds."""
        now = time.time()
        with self._lock:
            expired = [
                tid for tid, info in self._tasks.items()
                if info.state in (TaskState.SUCCESS, TaskState.FAILED)
                and info.finished_at
                and (now - info.finished_at) > max_age_seconds
            ]
            for tid in expired:
                del self._tasks[tid]
                self._futures.pop(tid, None)


# Global singleton — shared across all routes
task_manager = TaskManager(max_workers=2)
