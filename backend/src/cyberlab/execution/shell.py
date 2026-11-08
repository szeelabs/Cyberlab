"""Interactive shell sessions for the browser terminal.

A ``ShellSession`` is a byte stream to an interactive shell inside an environment. The
Docker implementation runs ``docker exec -it`` attached to a pseudo-terminal (pty), the
same mechanism SSH uses, so programs see a real terminal (colours, line editing,
full-screen tools) and window-size changes are propagated.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
from typing import Protocol

READ_CHUNK = 65536


class ShellSession(Protocol):
    async def read(self) -> bytes:
        """Next chunk of output; ``b""`` means the shell has exited."""
        ...

    async def write(self, data: bytes) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    async def close(self) -> None: ...


class PtyShellSession:
    """Runs ``argv`` on a new pseudo-terminal and exposes it asynchronously."""

    def __init__(self, argv: list[str], env: dict[str, str] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        master, slave = pty.openpty()
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._fd = master
        os.set_blocking(master, False)
        self._closed = False
        self._loop.add_reader(master, self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:  # EIO once the child side of the pty is gone
            data = b""
        if not data:
            self._loop.remove_reader(self._fd)
        self._queue.put_nowait(data)

    async def read(self) -> bytes:
        return await self._queue.get()

    async def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view and not self._closed:
            try:
                n = os.write(self._fd, view)
                view = view[n:]
            except BlockingIOError:
                await asyncio.sleep(0.01)

    def resize(self, rows: int, cols: int) -> None:
        if self._closed:
            return
        fcntl.ioctl(self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        if self._proc.poll() is None:
            self._proc.send_signal(signal.SIGWINCH)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.remove_reader(self._fd)
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(self._proc.wait), timeout=5)
            except TimeoutError:
                self._proc.kill()
                await asyncio.to_thread(self._proc.wait)
        os.close(self._fd)
