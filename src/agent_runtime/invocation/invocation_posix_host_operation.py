"""POSIX implementation of Runtime host operations (macOS and Linux).

This module imports only the standard library; it is selected by
``invocation_host_platform_selection`` and must not import it back.
"""

from __future__ import annotations

import fcntl
from typing import Any


class PosixHostPlatform:
    """Host operations backed by POSIX system calls."""

    def acquire_lease_lock(self, handle: Any) -> None:
        """Take a non-blocking ``flock`` on the whole lease file.

        The kernel releases the lock when the holding process exits, including
        after a crash. Contention raises ``OSError`` with ``EAGAIN``.
        """

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release_lease_lock(self, handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


HOST_PLATFORM_IMPLEMENTATION = PosixHostPlatform


__all__: list[str] = []
