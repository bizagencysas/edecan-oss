from __future__ import annotations

import asyncio
import os
import sys

import pytest
from edecan_design_studio.process_boundary import (
    ProcessExecutionTimeoutError,
    ProcessOutputLimitError,
    communicate_bounded,
    isolated_process_kwargs,
)


async def _spawn(code: str):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **isolated_process_kwargs(),
    )


async def test_large_stdin_round_trip_does_not_use_argv() -> None:
    process = await _spawn("import sys; data=sys.stdin.buffer.read(); print(len(data))")
    payload = b"x" * (256 * 1024)

    stdout, stderr = await communicate_bounded(
        process,
        payload,
        timeout_seconds=5,
        max_output_bytes=1024,
    )

    assert stdout.strip() == str(len(payload)).encode()
    assert stderr == b""


async def test_output_limit_kills_process() -> None:
    process = await _spawn("import sys,time; sys.stdout.write('x'*200000); time.sleep(30)")

    with pytest.raises(ProcessOutputLimitError):
        await communicate_bounded(
            process,
            None,
            timeout_seconds=5,
            max_output_bytes=1024,
        )

    assert process.returncode is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group assertion")
async def test_timeout_kills_descendant_process(tmp_path) -> None:
    child_pid_file = tmp_path / "child.pid"
    code = (
        "import pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid)); "
        "time.sleep(30)"
    )
    process = await _spawn(code)
    for _ in range(100):
        if child_pid_file.exists():
            break
        await asyncio.sleep(0.01)
    child_pid = int(child_pid_file.read_text())

    with pytest.raises(ProcessExecutionTimeoutError):
        await communicate_bounded(
            process,
            None,
            timeout_seconds=0.05,
            max_output_bytes=1024,
        )

    assert process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
