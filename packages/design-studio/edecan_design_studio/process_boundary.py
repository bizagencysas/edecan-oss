"""Bounded subprocess I/O and whole-process-tree cancellation for Studio."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any


class ProcessOutputLimitError(RuntimeError):
    """A subprocess exceeded its per-stream output budget."""


class ProcessExecutionTimeoutError(TimeoutError):
    """A subprocess exceeded its execution deadline."""


def isolated_process_kwargs() -> dict[str, Any]:
    """Create a process-tree boundary that can be cancelled as one unit."""

    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Best-effort hard stop of the process and every child it spawned."""

    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        # The OS may still be reaping a descendant. There is no safe broader
        # target to kill here, so return without touching unrelated processes.
        return
async def _read_bounded(
    stream: asyncio.StreamReader | None,
    *,
    max_bytes: int,
    limit_reached: asyncio.Event,
) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    overflow = False
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        if overflow:
            continue
        total += len(chunk)
        if total > max_bytes:
            overflow = True
            chunks.clear()
            limit_reached.set()
        else:
            chunks.append(chunk)


async def _write_input(
    stream: asyncio.StreamWriter | None,
    payload: bytes | None,
) -> None:
    if stream is None:
        return
    try:
        if payload:
            stream.write(payload)
            await stream.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stream.close()
        try:
            await stream.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass


async def communicate_bounded(
    process: asyncio.subprocess.Process,
    payload: bytes | None,
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> tuple[bytes, bytes]:
    """Communicate without buffering unbounded output in the parent process."""

    limit_reached = asyncio.Event()
    stdout_task = asyncio.create_task(
        _read_bounded(
            process.stdout,
            max_bytes=max_output_bytes,
            limit_reached=limit_reached,
        )
    )
    stderr_task = asyncio.create_task(
        _read_bounded(
            process.stderr,
            max_bytes=max_output_bytes,
            limit_reached=limit_reached,
        )
    )
    input_task = asyncio.create_task(_write_input(process.stdin, payload))
    wait_task = asyncio.create_task(process.wait())
    tasks = (stdout_task, stderr_task, input_task, wait_task)
    all_tasks = asyncio.gather(*tasks)
    limit_task = asyncio.create_task(limit_reached.wait())
    try:
        done, _ = await asyncio.wait(
            (all_tasks, limit_task),
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if limit_task in done and limit_task.result():
            await terminate_process_tree(process)
            await all_tasks
            raise ProcessOutputLimitError
        if all_tasks not in done:
            await terminate_process_tree(process)
            await all_tasks
            raise ProcessExecutionTimeoutError
        stdout, stderr, _, _ = all_tasks.result()
        return stdout, stderr
    except (ProcessExecutionTimeoutError, ProcessOutputLimitError):
        raise
    except BaseException:
        await terminate_process_tree(process)
        await asyncio.gather(all_tasks, return_exceptions=True)
        raise
    finally:
        if not limit_task.done():
            limit_task.cancel()
        await asyncio.gather(limit_task, return_exceptions=True)


__all__ = [
    "ProcessExecutionTimeoutError",
    "ProcessOutputLimitError",
    "communicate_bounded",
    "isolated_process_kwargs",
    "terminate_process_tree",
]
