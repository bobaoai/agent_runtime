"""Native Windows directory access control and handle-bound disposal for Runtime-owned private state."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import struct
import tempfile


class _AttributeTag(c.Structure):
    _fields_ = [("attributes", w.DWORD), ("tag", w.DWORD)]


class _FileId(c.Structure):
    _fields_ = [("volume", c.c_ulonglong), ("identifier", c.c_ubyte * 16)]


class _Standard(c.Structure):
    _fields_ = [("allocation", c.c_longlong), ("size", c.c_longlong), ("links", w.DWORD),
                ("delete_pending", w.BOOLEAN), ("directory", w.BOOLEAN)]


class _UnicodeString(c.Structure):
    _fields_ = [("length", w.USHORT), ("maximum", w.USHORT), ("buffer", w.LPVOID)]


class _ObjectAttributes(c.Structure):
    _fields_ = [("length", w.ULONG), ("root", w.HANDLE), ("name", c.POINTER(_UnicodeString)),
                ("attributes", w.ULONG), ("security", w.LPVOID), ("quality", w.LPVOID)]


class _IoStatus(c.Structure):
    _fields_ = [("status", w.LPVOID), ("information", c.c_size_t)]


class _AclHeader(c.Structure):
    _fields_ = [("revision", w.BYTE), ("padding", w.BYTE), ("size", w.WORD),
                ("count", w.WORD), ("padding2", w.WORD)]


class _AceHeader(c.Structure):
    _fields_ = [("kind", w.BYTE), ("flags", w.BYTE), ("size", w.WORD), ("mask", w.DWORD)]


_kernel = c.WinDLL("kernel32", use_last_error=True)
_security = c.WinDLL("advapi32", use_last_error=True)
_ntdll = c.WinDLL("ntdll")
_FULL_CONTROL = 0x1F01FF
_DELETE, _LIST, _READ_ATTRIBUTES, _SYNCHRONIZE = 0x10000, 0x1, 0x80, 0x100000
_DIRECTORY, _REPARSE = 0x10, 0x400
_MAX_DEPTH = 32


def _function(library, name, args, result):
    function = getattr(library, name)
    function.argtypes, function.restype = args, result
    return function


_create = _function(_kernel, "CreateFileW",
    [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE)
_close = _function(_kernel, "CloseHandle", [w.HANDLE], w.BOOL)
_free = _function(_kernel, "LocalFree", [w.LPVOID], w.LPVOID)
_current = _function(_kernel, "GetCurrentProcess", [], w.HANDLE)
_info = _function(_kernel, "GetFileInformationByHandleEx",
    [w.HANDLE, c.c_int, w.LPVOID, w.DWORD], w.BOOL)
_token = _function(_security, "OpenProcessToken",
    [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL)
_token_info = _function(_security, "GetTokenInformation",
    [w.HANDLE, c.c_int, w.LPVOID, w.DWORD, c.POINTER(w.DWORD)], w.BOOL)
_sid_text = _function(_security, "ConvertSidToStringSidW",
    [w.LPVOID, c.POINTER(w.LPWSTR)], w.BOOL)
_convert = _function(_security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
    [w.LPCWSTR, w.DWORD, c.POINTER(w.LPVOID), c.POINTER(w.DWORD)], w.BOOL)
_dacl = _function(_security, "GetSecurityDescriptorDacl",
    [w.LPVOID, c.POINTER(w.BOOL), c.POINTER(w.LPVOID), c.POINTER(w.BOOL)], w.BOOL)
_set = _function(_security, "SetSecurityInfo",
    [w.HANDLE, c.c_int, w.DWORD, w.LPVOID, w.LPVOID, w.LPVOID, w.LPVOID], w.DWORD)
_get = _function(_security, "GetSecurityInfo",
    [w.HANDLE, c.c_int, w.DWORD, w.LPVOID, w.LPVOID, c.POINTER(w.LPVOID),
     w.LPVOID, c.POINTER(w.LPVOID)], w.DWORD)
_control = _function(_security, "GetSecurityDescriptorControl",
    [w.LPVOID, c.POINTER(w.WORD), c.POINTER(w.DWORD)], w.BOOL)
_ace = _function(_security, "GetAce", [w.LPVOID, w.DWORD, c.POINTER(w.LPVOID)], w.BOOL)
_set_file = _function(_kernel, "SetFileInformationByHandle", [w.HANDLE, c.c_int, w.LPVOID, w.DWORD], w.BOOL)
_open_relative = _function(_ntdll, "NtCreateFile",
    [c.POINTER(w.HANDLE), w.DWORD, c.POINTER(_ObjectAttributes), c.POINTER(_IoStatus), w.LPVOID,
     w.ULONG, w.ULONG, w.ULONG, w.ULONG, w.LPVOID, w.ULONG], c.c_long)
_status_error = _function(_ntdll, "RtlNtStatusToDosError", [c.c_long], w.ULONG)


def _check(value):
    if not value:
        raise c.WinError(c.get_last_error())


def _check_status(value):
    if value:
        raise c.WinError(value)


def _sid_string(pointer):
    value = w.LPWSTR()
    _check(_sid_text(pointer, c.byref(value)))
    try:
        return value.value
    finally:
        _free(c.cast(value, w.LPVOID))


def current_user_sid() -> str:
    handle = w.HANDLE()
    _check(_token(_current(), 0x0008, c.byref(handle)))
    try:
        length = w.DWORD()
        _token_info(handle, 1, None, 0, c.byref(length))
        if not 0 < length.value <= 65536:
            raise OSError("Windows user identity is unavailable")
        buffer = c.create_string_buffer(length.value)
        _check(_token_info(handle, 1, buffer, length.value, c.byref(length)))
        return _sid_string(c.cast(buffer, c.POINTER(w.LPVOID)).contents)
    finally:
        _check(_close(handle))


@contextmanager
def _directory_handle(path: Path, *, writable=False):
    access = 0x20000 | 0x80 | (0x40000 if writable else 0)
    handle = _create(str(Path(path).absolute()), access, 3, None, 3, 0x02200000, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        attributes = _AttributeTag()
        _check(_info(handle, 9, c.byref(attributes), c.sizeof(attributes)))
        if not attributes.attributes & 0x10 or attributes.attributes & 0x400:
            raise OSError("Runtime private directory must be an ordinary directory")
        yield handle
    finally:
        _check(_close(handle))


def _security_view(handle):
    acl, descriptor = w.LPVOID(), w.LPVOID()
    _check_status(_get(handle, 1, 4, None, None, c.byref(acl), None, c.byref(descriptor)))
    try:
        control, revision = w.WORD(), w.DWORD()
        _check(_control(descriptor, c.byref(control), c.byref(revision)))
        if not acl:
            raise OSError("Runtime private directory has no access restriction")
        header = c.cast(acl, c.POINTER(_AclHeader)).contents
        if header.count > 64:
            raise OSError("Unexpected Runtime directory access control")
        entries = []
        for index in range(header.count):
            pointer = w.LPVOID()
            _check(_ace(acl, index, c.byref(pointer)))
            ace = c.cast(pointer, c.POINTER(_AceHeader)).contents
            if ace.kind != 0 or ace.size < 12:
                raise OSError("Unexpected Runtime directory access entry")
            entries.append((_sid_string(w.LPVOID(pointer.value + 8)), ace.mask, ace.flags))
        return bool(control.value & 0x1000), tuple(entries)
    finally:
        _free(descriptor)


def inspect_directory(path: Path):
    """Return actual DACL protection and entries; no file content is read."""
    with _directory_handle(path) as handle:
        return _security_view(handle)


def protect_directory(path: Path) -> None:
    """Restrict a newly allocated empty directory before private material is written."""
    trustees = tuple(dict.fromkeys((current_user_sid(), "S-1-5-18", "S-1-5-32-544")))
    descriptor = w.LPVOID()
    sddl = "D:P" + "".join(f"(A;OICI;FA;;;{sid})" for sid in trustees)
    _check(_convert(sddl, 1, c.byref(descriptor), None))
    try:
        present, defaulted, acl = w.BOOL(), w.BOOL(), w.LPVOID()
        _check(_dacl(descriptor, c.byref(present), c.byref(acl), c.byref(defaulted)))
        if not present.value or not acl:
            raise OSError("Runtime directory DACL construction failed")
        with _directory_handle(path, writable=True) as handle:
            _check_status(_set(handle, 1, 0x80000004, None, None, acl, None))
            protected, entries = _security_view(handle)
            expected = {(sid, _FULL_CONTROL, 3) for sid in trustees}
            if not protected or len(entries) != len(expected) or set(entries) != expected:
                raise OSError("Runtime directory access control could not be verified")
    finally:
        _free(descriptor)


def create_private_directory(*, prefix: str, parent: Path | None = None) -> Path:
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=parent)).absolute()
    try:
        protect_directory(directory)
        return directory.resolve(strict=True)
    except BaseException as failure:
        try:
            directory.rmdir()
        except OSError as cleanup:
            raise OSError("Runtime private directory setup failed and its empty directory was retained") from cleanup
        raise


def _attributes(handle):
    value = _AttributeTag()
    _check(_info(handle, 9, c.byref(value), c.sizeof(value)))
    return value.attributes


def _identity(handle):
    value = _FileId()
    _check(_info(handle, 18, c.byref(value), c.sizeof(value)))
    return value.volume, bytes(value.identifier)


def directory_identity(path: Path):
    """Return the volume and file identifier of one ordinary, non-reparse directory."""
    with _directory_handle(path) as handle:
        return _identity(handle)


def verify_entry(handle, *, directory: bool, identity=None) -> None:
    """Confirm a held object is still an ordinary single-link entry of the expected kind."""
    attributes = _attributes(handle)
    if attributes & _REPARSE or bool(attributes & _DIRECTORY) != directory:
        raise OSError("Runtime private state entry is not an ordinary owned entry; cleanup retained it")
    if identity is not None and _identity(handle) != identity:
        raise OSError("Runtime private directory was replaced; cleanup retained it")
    if not directory:
        standard = _Standard()
        _check(_info(handle, 1, c.byref(standard), c.sizeof(standard)))
        if standard.links != 1:
            raise OSError("Runtime private state file has another link; cleanup retained it")


@contextmanager
def held_private_directory(path: Path, identity):
    """Hold the allocated directory object itself at its allocated path.

    The final component is opened without following a reparse point, others cannot
    rename, delete or write-open it while held, and its identity must equal the one
    taken at allocation. Callers then use only handle-relative operations, so a later
    path replacement or in-place reparse point never redirects disposal. An absent
    path proves neither deletion nor process stop: the directory may have moved with
    its contents, so it raises OSError like any other unconfirmed ownership.
    """
    handle = _create(str(Path(path).absolute()), _DELETE | _LIST | _READ_ATTRIBUTES | _SYNCHRONIZE,
                     1, None, 3, 0x02200000, None)
    if handle == c.c_void_p(-1).value:
        error = c.get_last_error()
        if error in (2, 3):
            # No "from": an in-flight caller failure must stay in the displayed chain.
            raise OSError(f"Runtime private directory is no longer at its allocated path (WinError {error}); "
                          "its disposal cannot be confirmed")
        raise c.WinError(error)
    try:
        verify_entry(handle, directory=True, identity=identity)
        yield handle
    finally:
        _check(_close(handle))


def open_child(parent, name: str, *, directory: bool):
    """Open one direct child relative to a held directory handle without following reparse points.

    No path is resolved again: the lookup starts at the held directory object, and a
    held directory that has since become a reparse point refuses the lookup.
    """
    encoded = name.encode("utf-16-le", "surrogatepass")
    if (not name or name in {".", ".."} or any(mark in name for mark in "\\/:\0")
            or len(encoded) > 65532):
        raise OSError("Runtime private state entry is not a direct child name")
    buffer = c.create_string_buffer(encoded + b"\0\0")
    text = _UnicodeString(len(encoded), len(encoded) + 2, c.cast(buffer, w.LPVOID))
    attributes = _ObjectAttributes(c.sizeof(_ObjectAttributes), parent, c.pointer(text), 0x40, None, None)
    handle, status = w.HANDLE(), _IoStatus()
    access = _DELETE | _READ_ATTRIBUTES | _SYNCHRONIZE | (_LIST if directory else 0)
    options = 0x200020 | (0x1 if directory else 0x40)
    result = _open_relative(c.byref(handle), access, c.byref(attributes), c.byref(status),
                            None, 0, 1, 1, options, None, 0)
    if result:
        if handle.value:
            _close(handle)
        raise c.WinError(_status_error(result))
    return handle.value


def dispose_entry(handle, *, ignore_read_only: bool = False) -> None:
    """Mark the held object for deletion.

    Read-only entries are refused unless ``ignore_read_only`` is set, which only
    Attempt-folder maintenance does for entries it already verified through held
    handles (FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE). The legacy
    disposition fallback cannot honour that flag, so it still refuses them.
    """
    flags = w.DWORD(0x3 | (0x10 if ignore_read_only else 0))
    if _set_file(handle, 21, c.byref(flags), c.sizeof(flags)):
        return
    error = c.get_last_error()
    if error not in (1, 50, 87):
        raise c.WinError(error)
    delete = w.BOOLEAN(True)
    _check(_set_file(handle, 4, c.byref(delete), c.sizeof(delete)))


def _entries(handle):
    buffer = c.create_string_buffer(65536)
    entries, kind = [], 15
    while _info(handle, kind, buffer, len(buffer)):
        kind, offset = 14, 0
        while True:
            following, = struct.unpack_from("<L", buffer, offset)
            attributes, length = struct.unpack_from("<LL", buffer, offset + 56)
            name = buffer[offset + 68:offset + 68 + length].decode("utf-16-le", "surrogatepass")
            if name not in {".", ".."}:
                entries.append((name, attributes))
            if not following:
                break
            offset += following
    error = c.get_last_error()
    if error != 18:
        raise c.WinError(error)
    return entries


def _remove_contents(handle, depth, ignore_read_only=False):
    if depth > _MAX_DEPTH:
        raise OSError("Runtime private state is nested too deeply; cleanup retained it")
    for name, attributes in _entries(handle):
        if attributes & _REPARSE:
            raise OSError("Runtime private state contains a reparse entry; cleanup retained it")
        directory = bool(attributes & _DIRECTORY)
        child = open_child(handle, name, directory=directory)
        try:
            verify_entry(child, directory=directory)
            if directory:
                _remove_contents(child, depth + 1, ignore_read_only)
                verify_entry(child, directory=True)
            dispose_entry(child, ignore_read_only=ignore_read_only)
        finally:
            _check(_close(child))


def remove_held_directory(handle, identity, *, clear_read_only: bool = False) -> None:
    """Remove a held directory and its ordinary contents through handles only.

    Reparse, multiply linked or concurrently added entries raise OSError and
    leave the remaining state in place. Read-only entries are refused as well,
    unless ``clear_read_only`` (Attempt-folder maintenance) lets disposal ignore
    the read-only attribute of entries already verified as single-link and
    ordinary; no attribute of anything outside the held tree changes.
    """
    _remove_contents(handle, 0, clear_read_only)
    verify_entry(handle, directory=True, identity=identity)
    dispose_entry(handle, ignore_read_only=clear_read_only)


__all__: list[str] = []

