"""Windows implementation of Runtime host operations.

This module imports only the standard library; it is selected by
``invocation_host_platform_selection`` and must not import it back.
"""

from __future__ import annotations

import msvcrt
import os
from typing import Any


# Windows locks are mandatory byte-range locks on explicit offsets, not on the
# whole file. The lease locks one byte far past the PID record that the lease
# writes at offset 0, so the record stays readable and rewritable by other
# handles while the lock is held, and the file is not extended. Every process
# contends on this same byte. Public so tests can hold the same region.
WINDOWS_LEASE_LOCK_OFFSET = 1 << 30


class WindowsHostPlatform:
    """Host operations backed by the Windows C runtime."""

    def acquire_lease_lock(self, handle: Any) -> None:
        """Take a non-blocking ``LockFileEx`` lock on the lease byte.

        ``msvcrt.locking`` acts at the current file position, so the position
        is set to the lease byte first. The lock conflicts with every other
        handle, including a second open of the same file in this process.
        Windows releases it after the owning process terminates, but
        asynchronously: for a short time after a crash a new lease can still
        see contention. Contention raises ``OSError`` with ``EACCES``.
        """

        os.lseek(handle.fileno(), WINDOWS_LEASE_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def release_lease_lock(self, handle: Any) -> None:
        """Unlock the lease byte; writing the PID moved the file position."""

        os.lseek(handle.fileno(), WINDOWS_LEASE_LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


HOST_PLATFORM_IMPLEMENTATION = WindowsHostPlatform


__all__: list[str] = []
