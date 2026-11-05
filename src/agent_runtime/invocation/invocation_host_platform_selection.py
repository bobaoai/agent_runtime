"""Select the operating-system implementation of Runtime host operations.

Every operating-system difference used by Invocation sits behind
``HostPlatform``. One POSIX implementation and one Windows implementation live
in their own modules; this module chooses one of them once, from
``sys.platform``, at import. Callers use ``HOST_PLATFORM`` and contain no
platform branches. A host whose implementation cannot be imported gets
``UnsupportedHostPlatform``, which refuses every operation instead of
degrading silently.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any, Protocol


class HostPlatform(Protocol):
    """Operating-system operations that Invocation needs from its host."""

    def acquire_lease_lock(self, handle: Any) -> None:
        """Take a non-blocking exclusive lock on the open lease file.

        Contention raises ``OSError`` with ``EACCES`` or ``EAGAIN``. The
        operating system releases the lock when the holding process exits.
        """

    def release_lease_lock(self, handle: Any) -> None:
        """Release the lock taken by ``acquire_lease_lock`` on the same handle."""


class UnsupportedHostError(RuntimeError):
    """Raised when this host has no correct implementation of an operation."""


_UNSUPPORTED_LEASE_MESSAGE = (
    "Attempt workspace leasing requires a POSIX host (fcntl) or a "
    "Windows host (msvcrt); duplicate-dispatch fencing is unsupported "
    "on this platform"
)


class UnsupportedHostPlatform:
    """Host with no supported implementation: every operation fails closed."""

    def acquire_lease_lock(self, handle: Any) -> None:
        raise UnsupportedHostError(_UNSUPPORTED_LEASE_MESSAGE)

    def release_lease_lock(self, handle: Any) -> None:
        raise UnsupportedHostError(_UNSUPPORTED_LEASE_MESSAGE)


_WINDOWS_PLATFORM = "win32"
_WINDOWS_MODULE = "agent_runtime.invocation.invocation_windows_host_operation"
_POSIX_MODULE = "agent_runtime.invocation.invocation_posix_host_operation"
_IMPLEMENTATION_CLASS = "HOST_PLATFORM_IMPLEMENTATION"


def select_host_platform(platform: str) -> HostPlatform:
    """Return the implementation for ``platform`` (a ``sys.platform`` value).

    ``"win32"`` selects the Windows module and every other value the POSIX
    module. If the selected module cannot be imported on this host, the
    result is ``UnsupportedHostPlatform``.
    """

    module_name = _WINDOWS_MODULE if platform == _WINDOWS_PLATFORM else _POSIX_MODULE
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return UnsupportedHostPlatform()
    return getattr(module, _IMPLEMENTATION_CLASS)()


HOST_PLATFORM: HostPlatform = select_host_platform(sys.platform)


__all__: list[str] = []
