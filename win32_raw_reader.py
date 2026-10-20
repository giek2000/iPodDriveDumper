"""The only physical-drive access layer; it requests read access only."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

from .models import RawGeometry

READ_ACCESS = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
IOCTL_DISK_GET_DRIVE_GEOMETRY = 0x00070000
IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C


class RawReadError(OSError):
    def __init__(self, message: str, *, offset: int, requested: int, actual: int = 0,
                 win32_code: Optional[int] = None, transient: bool = False):
        super().__init__(message)
        self.offset = offset
        self.requested = requested
        self.actual = actual
        self.win32_code = win32_code
        self.transient = transient


class RawReader:
    """Small interface implemented by Win32Reader and synthetic readers."""
    geometry: RawGeometry
    path: str

    def read_at(self, offset: int, count: int) -> bytes:  # pragma: no cover - protocol method
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - protocol method
        raise NotImplementedError


class _DiskGeometry(ctypes.Structure):
    _fields_ = [
        ("cylinders", ctypes.c_longlong),
        ("media_type", ctypes.c_int),
        ("tracks_per_cylinder", ctypes.c_ulong),
        ("sectors_per_track", ctypes.c_ulong),
        ("bytes_per_sector", ctypes.c_ulong),
    ]


class _LengthInfo(ctypes.Structure):
    _fields_ = [("length", ctypes.c_longlong)]


_TRANSIENT_ERRORS = {21, 23, 24, 1117, 1167, 1168}


def _error_message(code: int) -> str:
    if os.name != "nt":
        return f"Win32 error {code}"
    try:
        return ctypes.FormatError(code).strip()
    except Exception:
        return f"Win32 error {code}"


class Win32Reader(RawReader):
    """Buffered, sector-aware reader backed by a read-only physical disk handle."""

    def __init__(self, path: str, handle: int, geometry: RawGeometry, kernel32: object):
        self.path = path
        self.handle = handle
        self.geometry = geometry
        self._kernel32 = kernel32

    @classmethod
    def open(cls, path: str) -> "Win32Reader":
        if os.name != "nt":
            raise OSError("PhysicalDrive access is available only on Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        # The access assertion is intentionally fail-closed: this module has no write-capable open path.
        if READ_ACCESS != 0x80000000:
            raise RuntimeError("read access safety assertion failed")
        handle = kernel32.CreateFileW(
            path, READ_ACCESS, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            code = ctypes.get_last_error()
            raise OSError(code, _error_message(code), path)
        try:
            geometry = cls._query_geometry(kernel32, handle)
        except Exception:
            kernel32.CloseHandle(handle)
            raise
        return cls(path, handle, geometry, kernel32)

    @staticmethod
    def _query_geometry(kernel32: object, handle: int) -> RawGeometry:
        geometry = _DiskGeometry()
        returned = wintypes.DWORD()
        ok = kernel32.DeviceIoControl(
            handle, IOCTL_DISK_GET_DRIVE_GEOMETRY, None, 0,
            ctypes.byref(geometry), ctypes.sizeof(geometry), ctypes.byref(returned), None,
        )
        if not ok or not geometry.bytes_per_sector:
            code = ctypes.get_last_error()
            raise OSError(code, _error_message(code), "geometry")
        length_info = _LengthInfo()
        ok = kernel32.DeviceIoControl(
            handle, IOCTL_DISK_GET_LENGTH_INFO, None, 0,
            ctypes.byref(length_info), ctypes.sizeof(length_info), ctypes.byref(returned), None,
        )
        if not ok or length_info.length <= 0:
            code = ctypes.get_last_error()
            raise OSError(code, _error_message(code), "disk length")
        return RawGeometry(
            disk_length=int(length_info.length),
            logical_sector_size=int(geometry.bytes_per_sector),
            cylinders=int(geometry.cylinders),
            tracks_per_cylinder=int(geometry.tracks_per_cylinder),
            sectors_per_track=int(geometry.sectors_per_track),
            alignment=int(geometry.bytes_per_sector),
            geometry_ioctl="IOCTL_DISK_GET_DRIVE_GEOMETRY (0x00070000), DISK_GEOMETRY",
            length_ioctl="IOCTL_DISK_GET_LENGTH_INFO (0x0007405C), GET_LENGTH_INFORMATION",
        )

    def read_at(self, offset: int, count: int) -> bytes:
        if offset < 0 or count < 0 or offset + count > self.geometry.disk_length:
            raise RawReadError("read range is outside disk bounds", offset=offset, requested=count)
        if count == 0:
            return b""
        # Direct physical disks can require both offsets and lengths to be logical-sector aligned,
        # including when the handle was opened in buffered mode. Read an aligned envelope and slice.
        sector = max(1, self.geometry.logical_sector_size)
        aligned_offset = (offset // sector) * sector
        aligned_end = ((offset + count + sector - 1) // sector) * sector
        if aligned_end > self.geometry.disk_length:
            aligned_end = self.geometry.disk_length
        aligned_count = aligned_end - aligned_offset
        if aligned_count <= 0 or aligned_count % sector:
            raise RawReadError("aligned physical read exceeds a non-aligned disk end",
                               offset=offset, requested=count)
        distance = ctypes.c_longlong(aligned_offset)
        if not self._kernel32.SetFilePointerEx(self.handle, distance, None, 0):
            code = ctypes.get_last_error()
            raise RawReadError(_error_message(code), offset=offset, requested=count,
                               win32_code=code, transient=code in _TRANSIENT_ERRORS)
        buffer = ctypes.create_string_buffer(aligned_count)
        returned = wintypes.DWORD(0)
        ok = self._kernel32.ReadFile(self.handle, buffer, aligned_count, ctypes.byref(returned), None)
        actual = int(returned.value)
        if not ok:
            code = ctypes.get_last_error()
            raise RawReadError(_error_message(code), offset=offset, requested=count, actual=actual,
                               win32_code=code, transient=code in _TRANSIENT_ERRORS)
        if actual != aligned_count:
            raise RawReadError("short physical read", offset=offset, requested=count, actual=actual)
        inner_start = offset - aligned_offset
        return buffer.raw[inner_start:inner_start + count]

    def close(self) -> None:
        if getattr(self, "handle", None) not in (None, ctypes.c_void_p(-1).value):
            self._kernel32.CloseHandle(self.handle)
            self.handle = None
