"""POSIX implementation of Runtime host operations (macOS and Linux).

This module imports only the standard library; it is selected by
``invocation_host_platform_selection`` and must not import it back.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import stat
import subprocess
import tempfile
from typing import Any

_STOP_SECONDS = 3


def _stop_process_group(process: subprocess.Popen) -> None:
    """Signal the session group Runtime created, then confirm the started process exited."""
    try:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except PermissionError:
            # Darwin may report EPERM for an exited, unreaped group leader.
            # Reap only if already finished and retry the same known group;
            # a live leader or a second denial remains a stop failure.
            if process.poll() is None:
                raise
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as exc:
        raise RuntimeError("CLI process group could not be signalled: " + str(exc)) from exc
    try:
        process.wait(timeout=_STOP_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("CLI process did not exit after stop: " + str(exc)) from exc


@contextmanager
def _directory(path: Path):
    """Open each path component without following a symlink."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("directory locator cannot contain parent traversal")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _read_file(directory, parts):
    descriptor = os.dup(directory)
    try:
        for name in parts[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("materials must be regular files")
            body = stream.read()
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_mode) != (after.st_size, after.st_mtime_ns, after.st_mode):
                raise ValueError("material changed while being captured")
            return body, stat.S_IMODE(after.st_mode)
    finally:
        os.close(descriptor)


def _tree(directory, prefix=""):
    files, directories = {}, set()
    for name in os.listdir(directory):
        relative = prefix + name
        mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o555:
                    raise ValueError("material directory permissions changed")
                children, subdirs = _tree(descriptor, relative + "/")
                files.update(children)
                directories.update(subdirs)
                directories.add(relative)
            finally:
                os.close(descriptor)
        elif stat.S_ISREG(mode):
            content, file_mode = _read_file(directory, (name,))
            if file_mode not in {0o444, 0o555}:
                raise ValueError("material file permissions changed")
            files[relative] = (hashlib.sha256(content).hexdigest(), bool(file_mode & 0o111))
        else:
            raise ValueError("material tree contains a symlink or special file")
    return files, directories


def _owner_writable_directories(directory_fd: int) -> None:
    """Give owner rwx to every directory below an open directory, never following links."""
    os.fchmod(directory_fd, stat.S_IMODE(os.fstat(directory_fd).st_mode) | stat.S_IRWXU)
    for name in os.listdir(directory_fd):
        if stat.S_ISDIR(os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            try:
                _owner_writable_directories(child)
            finally:
                os.close(child)


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

    def create_private_directory(self, *, prefix, short_path=False, parent=None):
        directory = parent if parent is not None else ("/tmp" if short_path else None)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=directory)).resolve()

    def provider_environment(self, source, allowed_keys):
        return {key: value for key, value in source.items() if key in allowed_keys}

    def create_task_directory(self, directory):
        """Owner-only task folder, unchanged POSIX behavior."""
        Path(directory).mkdir(mode=0o700, exist_ok=True)

    def private_directory_identity(self, directory):
        current = directory.stat()
        return current.st_dev, current.st_ino

    def remove_private_directory(self, directory, identity, *, clear_read_only=False):
        """Keep the accepted POSIX cleanup of a common private directory.

        An absent path is left alone. A symlink, or a directory whose device and
        inode differ from ``identity``, raises OSError and is retained. Otherwise
        ``shutil.rmtree`` removes the tree; no new race-resistance claim is made.
        With ``clear_read_only`` every directory, opened without following links,
        first gets owner rwx so read-only material copies can be unlinked.
        """
        if not os.path.lexists(directory):
            return
        current = directory.lstat()
        if directory.is_symlink() or (current.st_dev, current.st_ino) != identity:
            raise OSError("Runtime private directory was replaced; cleanup retained it")
        if clear_read_only:
            with _directory(directory) as descriptor:
                if (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) != identity:
                    raise OSError("Runtime private directory was replaced; cleanup retained it")
                _owner_writable_directories(descriptor)
        shutil.rmtree(directory)

    def resolve_cli_program(self, name, environment):
        return shutil.which(name, path=environment.get("PATH"))

    def create_cli_process(self, argv, *, cwd, environment):
        """Start argv in a new session whose group Runtime later stops.

        Popen reaps a child whose exec failed before raising, so a raised OSError
        leaves no created process behind.
        """
        return subprocess.Popen(
            argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )

    def stop_cli_process(self, process):
        """Signal the session group, then require the started process to exit within the bound."""
        _stop_process_group(process)

    def read_material_file(self, root, parts):
        with _directory(Path(root)) as root_fd:
            content, mode = _read_file(root_fd, tuple(parts))
        return content, bool(mode & 0o111)

    def write_material_tree(self, source, files):
        source = Path(source)
        with _directory(source.parent) as parent:
            os.mkdir(source.name, mode=0o700, dir_fd=parent)
        # All child directories are constructed before read-only modes are set.
        paths = sorted({str(parent) for parts, _, _ in files
                        for parent in PurePosixPath(*parts).parents if str(parent) != "."},
                       key=lambda p: (p.count("/"), p))
        for relative in paths:
            with _directory(source / PurePosixPath(relative).parent) as folder:
                os.mkdir(PurePosixPath(relative).name, mode=0o700, dir_fd=folder)
        for parts, body, executable in files:
            path = PurePosixPath(*parts)
            with _directory(source / path.parent) as folder:
                fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(body)
                    os.fchmod(stream.fileno(), 0o555 if executable else 0o444)
        for relative in reversed(paths):
            with _directory(source / relative) as folder:
                os.fchmod(folder, 0o555)
        with _directory(source) as directory:
            os.fchmod(directory, 0o555)

    def material_tree_state(self, source):
        with _directory(Path(source)) as directory:
            if stat.S_IMODE(os.fstat(directory).st_mode) != 0o555:
                raise ValueError("material source directory permissions changed")
            return _tree(directory)


HOST_PLATFORM_IMPLEMENTATION = PosixHostPlatform


__all__: list[str] = []
