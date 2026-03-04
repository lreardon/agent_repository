"""Task registry for tracking in-flight async tasks and enabling graceful shutdown."""

import asyncio
import logging

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Tracks asyncio tasks so they can be awaited on shutdown."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def register(self, task: asyncio.Task, label: str = "") -> None:
        """Register a task for tracking. Adds a done callback to auto-remove."""
        self._tasks.add(task)
        task.add_done_callback(lambda t: self._tasks.discard(t))
        logger.debug("Registered task %s (%s), active=%d", task.get_name(), label, len(self._tasks))

    def unregister(self, task: asyncio.Task) -> None:
        """Manually unregister a task."""
        self._tasks.discard(task)

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    async def shutdown(self, timeout: float = 30.0) -> None:
        """Wait for all registered tasks to complete, then cancel any remaining."""
        if not self._tasks:
            logger.info("Task registry: no in-flight tasks")
            return

        tasks = list(self._tasks)
        logger.info("Task registry: waiting for %d in-flight tasks (timeout=%.1fs)", len(tasks), timeout)

        done, pending = await asyncio.wait(tasks, timeout=timeout)

        if pending:
            logger.warning("Task registry: cancelling %d tasks after timeout", len(pending))
            for task in pending:
                task.cancel()
            await asyncio.wait(pending, timeout=5.0)

        logger.info("Task registry: shutdown complete (completed=%d, cancelled=%d)", len(done), len(pending))


registry = TaskRegistry()
