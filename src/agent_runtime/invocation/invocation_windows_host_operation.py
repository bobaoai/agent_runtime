"""Windows implementation of Runtime host operations.

This module uses the standard library and its native directory helpers; it is
selected by ``invocation_host_platform_selection`` and must not import it back.
"""

from __future__ import annotations

from collections.abc import Mapping
import errno
import hashlib
import msvcrt
import os
from pathlib import Path
import stat
import subprocess
from typing import Any

from .invocation_windows_directory_preparation import (
    create_private_directory, directory_identity, held_private_directory, remove_held_directory,
)


# Windows locks are mandatory byte-range locks on explicit offsets, not on the
# whole file. The lease locks one byte far past the PID record that the lease
# writes at offset 0, so the record stays readable and rewritable by other
# handles while the lock is held, and the file is not extended. Every process
# contends on this same byte. Public so tests can hold the same region.
WINDOWS_LEASE_LOCK_OFFSET = 1 << 30

_STOP_SECONDS = 3.0
_SCRIPT_SUFFIXES = (".cmd", ".bat", ".ps1")
# Launcher and script extensions are not started directly; any other suffix is part of a name.
_REFUSED_SUFFIXES = frozenset((*_SCRIPT_SUFFIXES, ".com", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
                               ".msc", ".py", ".pyw"))
_REPARSE = stat.FILE_ATTRIBUTE_REPARSE_POINT
_READ_ONLY = stat.FILE_ATTRIBUTE_READONLY


def _environment_value(environment: Mapping, name: str):
    matches = [value for key, value in environment.items() if key.upper() == name.upper()]
    return matches[0] if matches else None


def _native_candidate(name: str, environment: Mapping) -> tuple[str | None, bool]:
    executable = name if Path(name).suffix.lower() == ".exe" else name + ".exe"
    scripts = False
    for directory in filter(None, (_environment_value(environment, "PATH") or "").split(os.pathsep)):
        candidate = Path(directory) / executable
        if candidate.is_file():
            return os.path.abspath(candidate), scripts
        scripts = scripts or any((Path(directory) / (name + kind)).is_file() for kind in _SCRIPT_SUFFIXES)
    return None, scripts


def native_program(argv, *, cwd, environment) -> str:
    """Return the absolute native .exe that argv[0] names, without shell interpretation.

    A bare name is searched only in the supplied environment's PATH, as itself if it
    already ends in .exe or with .exe appended. A name with a directory part is taken
    relative to cwd. Launchers (.cmd, .bat, .ps1 and other script types) are not
    started: they hand their arguments to another interpreter that re-parses them,
    so the exact argv could not be delivered. Name the native program they start
    instead (for example in provider_cli_paths or cli_path).
    """
    name = argv[0]
    if not name:
        raise FileNotFoundError(errno.ENOENT, "CLI executable name is empty", name)
    refused = ("Windows CLI launch needs the native program: a script launcher re-parses its arguments "
               "through another interpreter, so the exact argv cannot be passed; configure the native "
               ".exe it starts (provider_cli_paths or cli_path)")
    suffix = Path(name).suffix.lower()
    if os.path.dirname(name) or os.path.isabs(name):
        candidate = Path(name) if os.path.isabs(name) else Path(cwd) / name
        if not candidate.is_file():
            raise FileNotFoundError(errno.ENOENT, "CLI executable does not exist", name)
        if suffix != ".exe":
            raise OSError(errno.ENOEXEC, refused, name)
        return os.path.abspath(candidate)
    if suffix in _REFUSED_SUFFIXES:
        raise OSError(errno.ENOEXEC, refused, name)
    found, scripts = _native_candidate(name, environment)
    if found is not None:
        return found
    detail = "; only a script launcher exists, which is not started (configure its native .exe)" if scripts else ""
    raise FileNotFoundError(errno.ENOENT, "No native .exe found on the supplied PATH" + detail, name)


def _ordinary(path: Path, *, directory: bool):
    info = os.lstat(path)
    if info.st_file_attributes & _REPARSE or stat.S_ISDIR(info.st_mode) != directory or (
            not directory and not stat.S_ISREG(info.st_mode)):
        raise ValueError("material tree contains a link, reparse point or special file")
    return info


def _read_ordinary(path: Path) -> bytes:
    _ordinary(path, directory=False)
    with open(path, "rb") as stream:
        before = os.fstat(stream.fileno())
        body = stream.read()
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("material changed while being captured")
    return body


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

    def create_private_directory(self, *, prefix, short_path=False, parent=None):
        return create_private_directory(prefix=prefix, parent=parent)

    def provider_environment(self, source, allowed_keys):
        allowed = {key.upper() for key in allowed_keys} | {
            "SYSTEMROOT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
            "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
        }
        return {key: value for key, value in source.items() if key.upper() in allowed}

    def create_task_directory(self, directory):
        """Normal creation: the folder inherits its parent's access (no mode 0o700).

        On Python 3.13+ a 0o700 mode gives Windows folders an owner-only access
        list, which would cut the inherited access the host user and a CLI's own
        sandbox identity need. Task folders are not private control state.
        """
        Path(directory).mkdir(exist_ok=True)

    def private_directory_identity(self, directory):
        return directory_identity(directory)

    def remove_private_directory(self, directory, identity, *, clear_read_only=False):
        """Dispose the allocation through one held, identity-verified root handle.

        The root is opened at its allocated path without following a final reparse
        point and must still be the ordinary directory with its allocation-time
        volume/file identity. Descendants are enumerated, opened, re-verified and
        deleted only relative to held directory handles, so a later path replacement
        or reparse point cannot redirect disposal. A normal return means this call
        removed the allocation. A missing or replaced root, a reparse or multiply
        linked entry, a read-only entry (unless ``clear_read_only``), a concurrent
        change, or any failed open, query, delete or close raises OSError naming the
        allocated location and leaves what remains; entries already removed are not
        restored. Moved directories are not searched for and access control is never
        changed to force removal.
        """
        try:
            with held_private_directory(directory, identity) as root:
                remove_held_directory(root, identity, clear_read_only=clear_read_only)
        except OSError as failure:
            # The cause keeps any in-flight caller exception in its own displayed context.
            raise OSError(f"Runtime private directory at {directory} could not be confirmed disposed; "
                          f"its remaining state was retained: {failure}") from failure

    def resolve_cli_program(self, name, environment):
        found, _ = _native_candidate(name, environment)
        return found

    def create_cli_process(self, argv, *, cwd, environment):
        """Start the native program argv[0] names, with its exact argv, in a new process group.

        Bare names resolve only through the supplied PATH and launchers are not
        started (see ``native_program``). No console window is created.
        """
        program = native_program(argv, cwd=cwd, environment=environment)
        return subprocess.Popen(
            list(argv), executable=program, cwd=cwd, env=dict(environment),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        )

    def stop_cli_process(self, process):
        """Terminate the started process if it still runs, then require its exit within the bound.

        Processes the CLI started itself are not enumerated or terminated here.
        """
        if process.poll() is None:
            try:
                process.kill()
            except OSError as exc:
                if process.poll() is None:
                    raise RuntimeError("CLI process could not be terminated: " + str(exc)) from exc
        try:
            process.wait(timeout=_STOP_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("CLI process did not exit after TerminateProcess: " + str(exc)) from exc

    def read_material_file(self, root, parts):
        """Read a regular file below root, refusing any link or reparse point on the way.

        These are path checks, not handle-relative opens: a concurrent swap can at
        most redirect a read, which the caller's hash comparison then refuses.
        Windows has no execute bit, so it is reported as None.
        """
        current = Path(root)
        _ordinary(current, directory=True)
        for name in parts[:-1]:
            current = current / name
            _ordinary(current, directory=True)
        return _read_ordinary(current / parts[-1]), None

    def write_material_tree(self, source, files):
        source = Path(source)
        _ordinary(source.parent, directory=True)
        os.mkdir(source)
        directories = sorted({source.joinpath(*parts[:index]) for parts, _, _ in files
                              for index in range(1, len(parts))}, key=lambda path: len(path.parts))
        for directory in directories:
            os.mkdir(directory)
        for parts, body, _ in files:
            target = source.joinpath(*parts)
            with open(target, "xb") as stream:
                stream.write(body)
            os.chmod(target, stat.S_IREAD)

    def material_tree_state(self, source):
        """Hash every file below source; links, reparse points and writable files are drift."""
        source = Path(source)
        _ordinary(source, directory=True)
        files, directories = {}, set()

        def walk(folder: Path, prefix: str) -> None:
            with os.scandir(folder) as entries:
                for entry in entries:
                    relative = prefix + entry.name
                    info = entry.stat(follow_symlinks=False)
                    if info.st_file_attributes & _REPARSE:
                        raise ValueError("material tree contains a link or reparse point")
                    if stat.S_ISDIR(info.st_mode):
                        directories.add(relative)
                        walk(Path(entry.path), relative + "/")
                    elif stat.S_ISREG(info.st_mode):
                        if not info.st_file_attributes & _READ_ONLY:
                            raise ValueError("material file lost its read-only state")
                        files[relative] = (hashlib.sha256(_read_ordinary(Path(entry.path))).hexdigest(), None)
                    else:
                        raise ValueError("material tree contains a special file")

        walk(source, "")
        return files, directories


HOST_PLATFORM_IMPLEMENTATION = WindowsHostPlatform


__all__: list[str] = []
