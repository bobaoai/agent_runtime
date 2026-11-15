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
from contextlib import contextmanager
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Protocol, Sequence


class HostPlatform(Protocol):
    """Operating-system operations that Invocation needs from its host."""

    def acquire_lease_lock(self, handle: Any) -> None:
        """Take a non-blocking exclusive lock on the open lease file.

        Contention raises ``OSError`` with ``EACCES`` or ``EAGAIN``. The
        operating system releases the lock when the holding process exits.
        """

    def release_lease_lock(self, handle: Any) -> None:
        """Release the lock taken by ``acquire_lease_lock`` on the same handle."""

    def create_private_directory(self, *, prefix: str, short_path: bool = False,
                                 parent: Path | None = None) -> Path:
        """Allocate verified private state before its caller writes material."""

    def provider_environment(self, source: Mapping[str, str], allowed_keys) -> dict[str, str]:
        """Filter existing allowed keys and host startup variables."""

    def create_task_directory(self, directory: Path) -> None:
        """Create one Runtime-owned Attempt task folder (work, materials, scratch), if absent.

        These are ordinary host task folders, not private control state. POSIX
        keeps owner-only mode 0o700. Windows uses normal creation, so the folder
        inherits its parent's access: the host user can read back what the CLI
        wrote and a CLI's own sandbox identity keeps the access its host setup
        granted on the parent tree. Access control is never edited here.
        """

    def private_directory_identity(self, directory: Path) -> Any:
        """Identity of a directory, compared again before disposal."""

    def remove_private_directory(self, directory: Path, identity: Any, *,
                                 clear_read_only: bool = False) -> None:
        """Dispose the directory at ``directory`` that still has ``identity``.

        The guarantee is host-specific, not a cross-platform promise. Windows
        binds disposal to the allocated directory object through held handles:
        a normal return means this call removed it, and a missing, replaced or
        unsafe allocation, or any disposal it cannot confirm, raises ``OSError``
        and leaves what remains. POSIX keeps its existing standard-library
        cleanup, in which an absent path is left alone and a replaced or
        symlinked path raises ``OSError`` and is retained.

        ``clear_read_only`` (Attempt-folder maintenance only) additionally lifts
        read-only state from entries already proven to belong to the owned tree
        by the same no-follow traversal: on Windows the read-only attribute of
        single-link entries through their held handle, on POSIX owner write on
        directories opened without following links. Links, reparse points and
        multiply-linked files are still refused, so nothing outside the tree
        changes.
        """

    def resolve_cli_program(self, name: str, environment: Mapping[str, str]) -> str | None:
        """Locate a CLI program by bare name the way this host starts it, or None.

        POSIX uses the supplied PATH as ``shutil.which`` does. Windows looks only
        for the native ``<name>.exe`` on the supplied PATH, because launchers are
        not started (see ``create_cli_process``). Launcher contents are never read.
        """

    def create_cli_process(self, argv: list[str], *, cwd: Path, environment: Mapping[str, str]) -> Any:
        """Start the one Runtime-invoked CLI process with binary stdin, stdout and stderr pipes.

        The result exposes pid, returncode, stdin, stdout, stderr, poll(), wait(timeout)
        and kill(). POSIX starts a new session. Windows requires a native program
        (bare names resolve only through the supplied PATH): a ``.cmd``, ``.bat`` or
        ``.ps1`` launcher hands its arguments to another interpreter that re-parses
        them, so the exact argv could not be guaranteed. A raised ``OSError`` means
        no process exists.
        """

    def stop_cli_process(self, process: Any) -> None:
        """Stop the started process with host-native control and confirm its exit, or raise.

        POSIX signals the session group Runtime created; Windows terminates the
        started process. The bound is a few seconds. CLI-internal processes are
        never enumerated or verified: they belong to the CLI.
        """

    def read_material_file(self, root: Path, parts: tuple[str, ...]) -> tuple[bytes, bool | None]:
        """Read ``root/parts`` without following links; return bytes and its execute bit.

        The execute bit is None where the host has none (Windows). A link, special
        file or a change during the read raises ``LocalResourceError`` or ``OSError``.
        """

    def write_material_tree(self, source: Path, files: Sequence[tuple[tuple[str, ...], bytes, bool]]) -> None:
        """Create ``source`` with exactly these files, then make the copy read-only.

        ``source`` must not exist. Directories and files are created exclusively
        without following links; executable files keep an execute bit where the
        host has one.
        """

    def material_tree_state(self, source: Path) -> tuple[dict[str, tuple[str, bool | None]], set[str]]:
        """Return {relative path: (sha256, execute bit or None)} and the directory set.

        Links, reparse points, special files and entries that lost their read-only
        state raise ``LocalResourceError``.
        """


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

    def create_private_directory(self, **kwargs):
        raise UnsupportedHostError("Private directories are unsupported on this host")

    def provider_environment(self, *args, **kwargs):
        raise UnsupportedHostError("Provider environment is unsupported on this host")

    def create_task_directory(self, *args, **kwargs):
        raise UnsupportedHostError("Attempt task folders are unsupported on this host")

    def private_directory_identity(self, *args, **kwargs):
        raise UnsupportedHostError("Private directory identity is unsupported on this host")

    def remove_private_directory(self, *args, **kwargs):
        raise UnsupportedHostError("Private directory disposal is unsupported on this host")

    def resolve_cli_program(self, *args, **kwargs):
        raise UnsupportedHostError("CLI program resolution is unsupported on this host")

    def create_cli_process(self, *args, **kwargs):
        raise UnsupportedHostError("CLI processes are unsupported on this host")

    def stop_cli_process(self, *args, **kwargs):
        raise UnsupportedHostError("CLI processes are unsupported on this host")

    def read_material_file(self, *args, **kwargs):
        raise UnsupportedHostError("Local materials are unsupported on this host")

    def write_material_tree(self, *args, **kwargs):
        raise UnsupportedHostError("Local materials are unsupported on this host")

    def material_tree_state(self, *args, **kwargs):
        raise UnsupportedHostError("Local materials are unsupported on this host")


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


@contextmanager
def private_temporary_directory(*, prefix: str, short_path: bool = False, parent: Path | None = None,
                                retain: Callable[[], bool] | None = None):
    """Allocate through the host, yield it, then have the host dispose that same allocation.

    The host captures the allocation identity before the caller receives the
    directory. If it cannot, nothing is yielded, no recursive cleanup is tried
    and the allocation is retained behind an ``OSError`` that names it.
    Disposal is ``HOST_PLATFORM.remove_private_directory`` with that identity;
    this shared code neither interprets identities nor deletes by path, and the
    cleanup guarantee is the selected host's. A disposal ``OSError`` keeps an
    in-flight caller exception in its displayed chain.

    ``retain`` is asked once at exit. When it returns True (the CLI process the
    caller started was not confirmed stopped), nothing is disposed and an
    ``OSError`` reports the retained location instead.
    """
    directory = HOST_PLATFORM.create_private_directory(prefix=prefix, short_path=short_path, parent=parent)
    try:
        identity = HOST_PLATFORM.private_directory_identity(directory)
    except OSError as failure:
        raise OSError(f"Runtime private directory identity could not be captured; "
                      f"the allocation at {directory} was retained") from failure
    try:
        yield directory
    finally:
        if retain is not None and retain():
            raise OSError(f"Runtime private directory retained at {directory}: "
                          "the CLI process started by Runtime was not confirmed stopped")
        HOST_PLATFORM.remove_private_directory(directory, identity)


__all__: list[str] = []
