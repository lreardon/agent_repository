"""Tests for TaskRegistry graceful shutdown."""

import asyncio

import pytest

from app.services.task_registry import TaskRegistry


@pytest.mark.asyncio
async def test_register_and_active_count() -> None:
    reg = TaskRegistry()
    assert reg.active_count == 0

    task = asyncio.create_task(asyncio.sleep(10))
    reg.register(task, "test-task")
    assert reg.active_count == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # done callback fires after await
    await asyncio.sleep(0)
    assert reg.active_count == 0


@pytest.mark.asyncio
async def test_unregister() -> None:
    reg = TaskRegistry()
    task = asyncio.create_task(asyncio.sleep(10))
    reg.register(task, "test")
    assert reg.active_count == 1

    reg.unregister(task)
    assert reg.active_count == 0

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_auto_removal_on_completion() -> None:
    reg = TaskRegistry()
    task = asyncio.create_task(asyncio.sleep(0))
    reg.register(task, "fast")

    await task
    # Allow done callback to fire
    await asyncio.sleep(0)
    assert reg.active_count == 0


@pytest.mark.asyncio
async def test_shutdown_waits_for_tasks() -> None:
    reg = TaskRegistry()
    completed = False

    async def work() -> None:
        nonlocal completed
        await asyncio.sleep(0.1)
        completed = True

    task = asyncio.create_task(work())
    reg.register(task, "work")

    await reg.shutdown(timeout=5.0)
    assert completed
    assert reg.active_count == 0


@pytest.mark.asyncio
async def test_shutdown_timeout_cancels_stuck_tasks() -> None:
    reg = TaskRegistry()

    task = asyncio.create_task(asyncio.sleep(999))
    reg.register(task, "stuck")

    await reg.shutdown(timeout=0.1)
    assert task.cancelled()
    await asyncio.sleep(0)
    assert reg.active_count == 0


@pytest.mark.asyncio
async def test_shutdown_no_tasks() -> None:
    reg = TaskRegistry()
    # Should return immediately without error
    await reg.shutdown(timeout=1.0)
    assert reg.active_count == 0
