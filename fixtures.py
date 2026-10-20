"""Deterministic synthetic 512/2048-byte iPod Video-like disk fixtures."""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .models import RawGeometry


@dataclass
class SyntheticFixture:
    image: bytes
    geometry: RawGeometry
    marker_offset: int
    marker: bytes
    osos_offset: int
    osos_length: int


class SyntheticReader:
    """Fake reader used by tests and offline CLI demonstrations."""
    def __init__(self, fixture: SyntheticFixture, path: str = "fixture://ipod"):
        self.image = fixture.image
        self.geometry = fixture.geometry
        self.path = path
        self.calls: list[tuple[int, int]] = []
        self.faults: dict[int, list[Exception | bytes]] = {}

    def read_at(self, offset: int, count: int) -> bytes:
        self.calls.append((offset, count))
        if offset in self.faults and self.faults[offset]:
            action = self.faults[offset].pop(0)
            if isinstance(action, Exception):
                raise action
            return action
        if offset < 0 or offset + count > len(self.image):
            raise OSError(f"fixture bounds: 0x{offset:X}+{count}")
        return self.image[offset:offset + count]

    def close(self) -> None:
        return None


def _pattern(length: int, seed: bytes) -> bytes:
    result = bytearray()
    counter = 0
    while len(result) < length:
        result.extend(hashlib.sha256(seed + counter.to_bytes(4, "little")).digest())
        counter += 1
    return bytes(result[:length])


def _record(name: bytes, device_offset: int, length: int, load: int, entry: int, flags: int = 1) -> bytes:
    data = bytearray(40)
    data[:4] = b"!ATA"
    data[4:8] = name
    struct.pack_into("<I", data, 8, flags)
    struct.pack_into("<I", data, 12, device_offset)
    struct.pack_into("<I", data, 16, length)
    struct.pack_into("<I", data, 20, load)
    struct.pack_into("<I", data, 24, entry)
    struct.pack_into("<I", data, 28, 0x12340000 | len(name))
    return bytes(data)


def build_synthetic_fixture(*, sector_size: int, marker_offset: int | None = None,
                             osos_length: int = 0x736000, include_secondary_sections: bool = True,
                             marker: bytes = b"{{~~") -> SyntheticFixture:
    if sector_size not in (512, 2048):
        raise ValueError("fixtures support 512 or 2048-byte sectors")
    if marker_offset is None:
        marker_offset = 0x7E00 if sector_size == 512 else 0x1F800
    body_offset = marker_offset + sector_size
    directory_offset = marker_offset + 0x3C00
    directory_relative = 0x3A00
    osos_offset = body_offset + 0x4000
    sections = [(b"soso", 0x4000, osos_length)]
    if include_secondary_sections:
        if sector_size == 2048:
            sections.extend([(b"crsr", 0x73A800, 0x500800), (b"dpua", 0xC3F800, 0x106800)])
        else:
            sections.extend([(b"crsr", 0x20000, 0x4000), (b"dpua", 0x26000, 0x3000)])
    last_end = max(body_offset + off + length for _, off, length in sections)
    disk_length = ((last_end + 0x20000 + 0x1FF) // 0x200) * 0x200
    image = bytearray(disk_length)
    image[marker_offset:marker_offset + 4] = marker
    header = bytearray(0x200)
    header[0:4] = marker
    header[0x100:0x104] = b"]ih["
    struct.pack_into("<I", header, 0x104, directory_relative)
    struct.pack_into("<H", header, 0x10A, 0x0505 if sector_size == 2048 else 0x0400)
    header[0x110:0x120] = b"SYNTHETIC-IPOD"[:16]
    image[marker_offset:marker_offset + len(header)] = header
    directory = bytearray(0x4000)
    for index, (name, device_offset, length) in enumerate(sections):
        record = _record(name, device_offset, length, 0x10000000 + index * 0x100000, 0x10000000)
        directory[index * 40:(index + 1) * 40] = record
    image[directory_offset:directory_offset + len(directory)] = directory
    for index, (name, device_offset, length) in enumerate(sections):
        absolute = body_offset + device_offset
        content = bytearray(_pattern(length, name + sector_size.to_bytes(2, "little")))
        if name == b"soso":
            content[:4] = b"\x7A\x00\x00\xEA"
            if length > 0x17AC:
                content[0x17A4:0x17AC] = b"retailOS"
        image[absolute:absolute + length] = content
    # A conventional MBR stores its partition LBAs in 512-byte units, even when the queried logical sector is 2048.
    image[510:512] = b"\x55\xAA"
    partition_start = 0
    partition_length = disk_length // 512
    entry = bytearray(16)
    entry[4] = 0x00
    struct.pack_into("<I", entry, 8, partition_start)
    struct.pack_into("<I", entry, 12, partition_length)
    image[446:462] = entry
    geometry = RawGeometry(
        disk_length=disk_length, logical_sector_size=sector_size,
        physical_sector_size=sector_size, alignment=sector_size, removable=True,
    )
    return SyntheticFixture(bytes(image), geometry, marker_offset, marker, osos_offset, osos_length)


def build_mini_fixture(*, osos_length: int = 0x8000) -> SyntheticFixture:
    """Synthetic iPod Mini (PortalPlayer) 512-byte layout grounded in a real Mini 2G.

    Differences from the Video fixture, confirmed from a live Mini 2G:
    - ]ih[ header is zero past +0x0C (version field reads 0).
    - directory-relative 0x4000 at header +0x104 -> directory at marker+0x4000+0x200.
    - OSOS first word is an ARM branch 0xEA000079 (not the Video's 0xEA00007A).
    - OSOS carries the ASCII string "portalplayer".
    """
    sector_size = 512
    marker_offset = 0x7E00
    body_offset = marker_offset + sector_size           # 0x8000
    directory_relative = 0x4000
    directory_offset = marker_offset + directory_relative + 0x200  # 0xC000
    osos_device_offset = 0x4400
    sections = [(b"soso", osos_device_offset, osos_length),
                (b"dpua", osos_device_offset + osos_length, 0x2000)]
    last_end = max(body_offset + off + length for _, off, length in sections)
    disk_length = ((last_end + 0x20000 + 0x1FF) // 0x200) * 0x200
    image = bytearray(disk_length)
    # STOP-sign marker block at 0x7E00, then the ]ih[ header at 0x7F00.
    header = bytearray(0x200)
    header[0:4] = b"{{~~"
    header[0x100:0x104] = b"]ih["
    struct.pack_into("<I", header, 0x104, directory_relative)
    # Everything past +0x10C stays zero, matching the Mini (version reads 0).
    image[marker_offset:marker_offset + len(header)] = header
    directory = bytearray(0x4000)
    for index, (name, device_offset, length) in enumerate(sections):
        directory[index * 40:(index + 1) * 40] = _record(name, device_offset, length, 0x10000000, 0)
    image[directory_offset:directory_offset + len(directory)] = directory
    for name, device_offset, length in sections:
        absolute = body_offset + device_offset
        content = bytearray(_pattern(length, name + b"mini"))
        if name == b"soso":
            content[:4] = b"\x79\x00\x00\xEA"      # ARM branch, little-endian 0xEA000079
            content[0x20:0x2C] = b"portalplayer"
        image[absolute:absolute + length] = content
    image[510:512] = b"\x55\xAA"
    entry = bytearray(16)
    entry[4] = 0x00
    struct.pack_into("<I", entry, 8, 0)
    struct.pack_into("<I", entry, 12, (body_offset + sections[-1][1] + sections[-1][2]) // 512)
    image[446:462] = entry
    geometry = RawGeometry(
        disk_length=disk_length, logical_sector_size=sector_size,
        physical_sector_size=sector_size, alignment=sector_size, removable=True,
    )
    return SyntheticFixture(bytes(image), geometry, marker_offset, b"{{~~",
                            body_offset + osos_device_offset, osos_length)


def build_s5l8702_fixture(*, marker_offset: int = 0x1F800, body_length: int = 0x500000) -> SyntheticFixture:
    """Synthetic iPod Classic (S5L8702) IMG1/"8702" container on a 2048-byte disk."""
    sector_size = 2048
    header_size = 0x800
    body_offset = marker_offset + header_size
    body_end = body_offset + body_length
    disk_length = ((body_end + 0x40000 + 0x1FF) // 0x200) * 0x200
    image = bytearray(disk_length)
    header = bytearray(header_size)
    header[0:4] = b"8702"
    struct.pack_into("<H", header, 0x04, 0x0003)          # version
    struct.pack_into("<I", header, 0x0C, body_length)      # body length
    struct.pack_into("<I", header, 0x10, header_size + body_length)  # footer/signature offset
    struct.pack_into("<I", header, 0x14, 0x11223344)       # stored checksum
    image[marker_offset:marker_offset + header_size] = header
    image[body_offset:body_end] = _pattern(body_length, b"s5l8702-osos")
    # Conventional MBR firmware partition entry (type 0x00), 512-byte LBA units,
    # covering the firmware region and remaining in-bounds against the queried disk length.
    image[510:512] = b"\x55\xAA"
    entry = bytearray(16)
    entry[4] = 0x00
    partition_length_lba = body_end // 512
    struct.pack_into("<I", entry, 8, 0)
    struct.pack_into("<I", entry, 12, partition_length_lba)
    image[446:462] = entry
    geometry = RawGeometry(
        disk_length=disk_length, logical_sector_size=sector_size,
        physical_sector_size=sector_size, alignment=sector_size, removable=True,
    )
    return SyntheticFixture(bytes(image), geometry, marker_offset, b"8702", body_offset, body_length)


def fixture_reader_factory(fixtures: dict[str, SyntheticFixture]):
    def factory(path: str) -> SyntheticReader:
        if path not in fixtures:
            raise OSError("synthetic drive unavailable")
        return SyntheticReader(fixtures[path], path)
    return factory
