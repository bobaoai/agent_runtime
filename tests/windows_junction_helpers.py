"""Synthetic native Windows junction and mount-point helpers for directory-disposal tests.

They create only test-owned directories and links; nothing outside tmp_path is touched.
"""
import os
from pathlib import Path
import struct


def make_external(tmp_path):
    folder = tmp_path / "external_directory"
    folder.mkdir()
    (folder / "payload.bin").write_bytes(b"\x00EXTERNAL synthetic payload; not parsed\xff")
    (folder / "sentinel.bin").write_bytes(b"\x00EXTERNAL synthetic sentinel\xff")
    return folder


def folder_bytes(folder):
    return {entry.name: entry.read_bytes() for entry in sorted(Path(folder).iterdir())}


def mount_in_place(directory, target):
    """Make an existing empty directory a mount point without changing its file identity.

    The handle requests only attribute access, which share modes do not restrict.
    """
    import ctypes as c
    from ctypes import wintypes as w
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD,
                                       c.POINTER(w.DWORD), w.LPVOID]
    kernel.DeviceIoControl.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateFileW(str(directory), 0x100 | 0x80, 7, None, 3, 0x02200000, None)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        substitute = ("\\??\\" + str(target)).encode("utf-16-le")
        printed = str(target).encode("utf-16-le")
        body = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printed))
        body += substitute + b"\0\0" + printed + b"\0\0"
        payload = struct.pack("<LHH", 0xA0000003, len(body), 0) + body
        data, returned = c.create_string_buffer(payload, len(payload)), w.DWORD()
        if not kernel.DeviceIoControl(handle, 0x000900A4, data, len(payload), None, 0, c.byref(returned), None):
            raise c.WinError(c.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def create_junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def remove_test_junction(path):
    path = Path(path)
    assert path.is_junction()
    os.rmdir(path)  # Removes the mount point itself, never its target.
