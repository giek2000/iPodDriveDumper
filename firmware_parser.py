"""Pure structure parser for iPod Video firmware candidates."""
from __future__ import annotations

import struct
from typing import Any

from .models import FirmwareLayout, PartitionInterpretation, ProbeCandidate, RawGeometry, SectionRecord
from .win32_raw_reader import RawReadError

MARKERS = (b"{{~~", b"~~{{")
HEADER_SIGNATURE = b"]ih["
# PortalPlayer (iPod Video) firmware markers plus Samsung S5L8702 (iPod Classic) IMG1 markers.
PROBE_OFFSETS = (0x0, 0x7E00, 0x1F800)
MAX_DIRECTORY_BYTES = 0x4000

# iPod Classic 6G/6.5G/7G use the Samsung S5L8702 SoC with an Apple IMG1/"8702" container.
S5L8702_MAGIC = b"8702"
IMG1_HEADER_SIZE = 0x800  # code_start offset documented across this repo's Classic analysis


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def parse_mbr(raw: bytes, geometry: RawGeometry) -> tuple[list[PartitionInterpretation], list[str]]:
    parts: list[PartitionInterpretation] = []
    warnings: list[str] = []
    if len(raw) < 512 or raw[510:512] != b"\x55\xAA":
        return parts, ["MBR signature unavailable"]
    for index in range(4):
        start = 446 + index * 16
        entry = raw[start:start + 16]
        status, ptype = entry[0], entry[4]
        lba_start = int.from_bytes(entry[8:12], "little")
        lba_length = int.from_bytes(entry[12:16], "little")
        if not lba_start and not lba_length:
            continue
        raw_start = lba_start * 512
        raw_end = raw_start + lba_length * 512
        logical_start = lba_start * geometry.logical_sector_size
        logical_end = logical_start + lba_length * geometry.logical_sector_size
        contradiction = None
        note = None
        # The authoritative interpretation is 512-byte LBA units. A genuine contradiction is only
        # when that authoritative interpretation is itself out of bounds.
        if raw_end > geometry.disk_length:
            contradiction = "MBR 512-byte interpretation exceeds queried disk length"
            warnings.append(f"partition {index + 1}: {contradiction}")
        elif logical_end > geometry.disk_length and geometry.logical_sector_size != 512:
            # Expected on 2048-byte devices: the alternate logical-sector interpretation is larger.
            # This is informational only and does not block a complete dump.
            note = "MBR LBA retained as 512-byte units (alternate logical-sector interpretation is larger)"
        parts.append(PartitionInterpretation(
            index=index + 1, partition_type=ptype, status=status,
            lba_start=lba_start, lba_length=lba_length,
            raw_start=raw_start, raw_end=raw_end,
            logical_start=logical_start, logical_end=logical_end,
            contradiction=contradiction, note=note,
        ))
    return parts, warnings


def parse_candidate(reader: Any, geometry: RawGeometry, marker_offset: int, marker: bytes) -> ProbeCandidate:
    reasons: list[str] = []
    checks: dict[str, bool] = {}
    try:
        if marker_offset < 0 or marker_offset + 0x200 > geometry.disk_length:
            return ProbeCandidate(marker_offset, marker.decode("ascii", "replace"), False, reasons=["header probe is out of bounds"])
        header = reader.read_at(marker_offset, 0x200)
        checks["header_signature"] = header[0x100:0x104] == HEADER_SIGNATURE
        if not checks["header_signature"]:
            reasons.append("missing ]ih[ header signature")
        version = _u16(header, 0x10A)
        # iPod Video 5G/5.5G carry a version here; iPod Mini's ]ih[ header is zero past +0x0C.
        # A zero/implausible version is informational and must not reject a device whose
        # !ATA directory and OSOS validate. It is not added to rejection reasons.
        checks["plausible_version"] = 0 < version < 0x10000
        directory_relative = _u32(header, 0x104)
        directory_offset = marker_offset + directory_relative + 0x200
        checks["directory_in_bounds"] = 0 <= directory_offset < geometry.disk_length and directory_offset + 40 <= geometry.disk_length
        if not checks["directory_in_bounds"]:
            reasons.append("directory-relative field resolves out of bounds")
        if reasons:
            return ProbeCandidate(marker_offset, marker.decode("ascii", "replace"), False, len(checks) - len(reasons), reasons=reasons)
        directory_count = min(MAX_DIRECTORY_BYTES, geometry.disk_length - directory_offset)
        directory = reader.read_at(directory_offset, directory_count)
        sections: list[SectionRecord] = []
        for position in range(0, len(directory) - 39, 40):
            record = directory[position:position + 40]
            if record[:4] != b"!ATA":
                break
            name = record[4:8].decode("ascii", "replace")
            flags = _u32(record, 8)
            device_offset = _u32(record, 12)
            declared_length = _u32(record, 16)
            load_address = _u32(record, 20)
            entry_address = _u32(record, 24)
            stored_checksum = _u32(record, 28)
            absolute_start = marker_offset + geometry.logical_sector_size + device_offset
            absolute_end = absolute_start + declared_length
            in_bounds = absolute_start >= 0 and absolute_end >= absolute_start and absolute_end <= geometry.disk_length
            sections.append(SectionRecord(
                name=name, raw_record=record, flags=flags,
                device_offset=device_offset, declared_length=declared_length,
                load_address=load_address, entry_address=entry_address,
                stored_checksum=stored_checksum, absolute_start=absolute_start,
                absolute_end=absolute_end, bounds_status="IN_BOUNDS" if in_bounds else "OUT_OF_BOUNDS",
            ))
            if not in_bounds:
                reasons.append(f"section {name} extends beyond disk bounds")
        checks["ata_records"] = bool(sections)
        if not sections:
            reasons.append("directory contains no validated 40-byte !ATA records")
        osos = next((s for s in sections if s.name == "soso"), None)
        checks["soso"] = bool(osos and osos.declared_length and osos.bounds_status == "IN_BOUNDS")
        if not checks["soso"]:
            reasons.append("missing, zero-length, or out-of-bounds soso record")
        osos_first_word = None
        retail_os_found = None
        osos_signature = b""
        if osos and osos.bounds_status == "IN_BOUNDS" and osos.declared_length >= 4:
            sig_len = min(0x40, osos.declared_length)
            osos_signature = reader.read_at(osos.absolute_start, sig_len)
            first = osos_signature[:4]
            osos_first_word = int.from_bytes(first, "little")
            # PortalPlayer OSOS begins with an ARM `B` (branch) instruction (top byte 0xEA).
            # iPod Video uses 0xEA00007A; iPod Mini uses a different branch offset (e.g. 0xEA000079).
            # Accept any ARM branch and record the exact word, instead of a single hard constant.
            checks["osos_first_word"] = (osos_first_word >> 24) == 0xEA
            if not checks["osos_first_word"]:
                reasons.append(f"OSOS first word 0x{osos_first_word:08X} is not an ARM branch (0xEA......)")
            if osos.declared_length > 0x17AC:
                anchor = reader.read_at(osos.absolute_start + 0x17A4, 8)
                retail_os_found = b"retailOS" in anchor
                checks["retailOS_anchor"] = retail_os_found
                # The anchor is version-dependent; absence is diagnostic evidence, not a structural rejection.
        mbr_size = min(512, geometry.disk_length)
        mbr = reader.read_at(0, mbr_size)
        partitions, partition_warnings = parse_mbr(mbr, geometry)
        reasons.extend(partition_warnings)
        for p in partitions:
            if p.partition_type == 0x00 and p.lba_length:
                p.selected = True
        selected = [p.raw_end for p in partitions if p.selected and p.raw_end <= geometry.disk_length]
        section_end = max((s.absolute_end for s in sections), default=0)
        requested_end = max(selected, default=section_end)
        boundary_ambiguous = not bool(selected)
        if requested_end < section_end:
            reasons.append("validated partition boundary ends before a declared section")
        if boundary_ambiguous:
            reasons.append("firmware-partition end could not be established from partition metadata")
        checks["section_bounds"] = not any(s.bounds_status != "IN_BOUNDS" for s in sections)
        checks["partition_boundary"] = not boundary_ambiguous and requested_end >= section_end
        checks["geometry_consistent"] = not any(p.contradiction for p in partitions)
        score = sum(1 for value in checks.values() if value)
        layout = FirmwareLayout(
            marker_offset=marker_offset, marker=marker, header_signature=HEADER_SIGNATURE,
            header_raw=header, version=version, directory_relative=directory_relative,
            directory_offset=directory_offset, directory_raw=directory,
            firmware_body_offset=marker_offset + geometry.logical_sector_size,
            sections=sections, osos=osos, osos_first_word=osos_first_word,
            retail_os_found=retail_os_found, mbr_raw=mbr, partitions=partitions,
            requested_end=requested_end, boundary_ambiguous=boundary_ambiguous,
            validation_score=score, validation_checks=checks, rejection_reasons=reasons,
            osos_signature=osos_signature,
        )
        return ProbeCandidate(marker_offset, marker.decode("ascii", "replace"), layout.valid, score, reasons, layout)
    except (struct.error, ValueError, RawReadError, OSError) as exc:
        reasons.append(f"parse/read failure: {exc}")
        return ProbeCandidate(marker_offset, marker.decode("ascii", "replace"), False, 0, reasons=reasons)


def parse_s5l8702_candidate(reader: Any, geometry: RawGeometry, marker_offset: int, header: bytes) -> ProbeCandidate:
    """Recognize an Apple IMG1 ("8702") container used by iPod Classic (S5L8702).

    The Classic firmware partition is not the PortalPlayer !ATA layout. This path
    reports a single validated IMG1 firmware image and derives its dump range from
    the container's declared body length, without inventing PortalPlayer sections.
    """
    reasons: list[str] = []
    checks: dict[str, bool] = {"s5l8702_magic": True}
    try:
        # IMG1 fields (little-endian): body length at 0x0C, footer/signature offset at 0x10.
        body_length = _u32(header, 0x0C)
        footer_offset = _u32(header, 0x10)
        version = _u16(header, 0x04)
        checks["plausible_version"] = 0 < version < 0x10000
        body_offset = marker_offset + IMG1_HEADER_SIZE
        absolute_end = body_offset + body_length
        in_bounds = 0 < body_length and absolute_end <= geometry.disk_length
        checks["body_in_bounds"] = in_bounds
        if not in_bounds:
            reasons.append("IMG1 body length resolves out of bounds")
        checks["footer_in_bounds"] = footer_offset == 0 or (marker_offset + footer_offset) <= geometry.disk_length
        if not checks["footer_in_bounds"]:
            reasons.append("IMG1 footer offset resolves out of bounds")
        section = None
        if in_bounds:
            section = SectionRecord(
                name="osos", raw_record=header[:0x20], flags=0,
                device_offset=IMG1_HEADER_SIZE, declared_length=body_length,
                load_address=0, entry_address=0, stored_checksum=_u32(header, 0x14),
                absolute_start=body_offset, absolute_end=absolute_end, bounds_status="IN_BOUNDS",
            )
        mbr = reader.read_at(0, min(512, geometry.disk_length))
        partitions, partition_warnings = parse_mbr(mbr, geometry)
        reasons.extend(partition_warnings)
        for p in partitions:
            if p.partition_type == 0x00 and p.lba_length:
                p.selected = True
        selected = [p.raw_end for p in partitions if p.selected and p.raw_end <= geometry.disk_length]
        section_end = absolute_end if section else 0
        requested_end = max(selected, default=section_end)
        boundary_ambiguous = not bool(selected)
        if boundary_ambiguous:
            reasons.append("firmware-partition end could not be established from partition metadata")
        if requested_end < section_end:
            reasons.append("validated partition boundary ends before the IMG1 body")
        checks["partition_boundary"] = not boundary_ambiguous and requested_end >= section_end
        checks["geometry_consistent"] = not any(p.contradiction for p in partitions)
        score = sum(1 for value in checks.values() if value)
        layout = FirmwareLayout(
            marker_offset=marker_offset, marker=S5L8702_MAGIC, header_signature=S5L8702_MAGIC,
            header_raw=header, version=version, directory_relative=0,
            directory_offset=marker_offset, directory_raw=header,
            firmware_body_offset=body_offset,
            sections=[section] if section else [], osos=section,
            osos_first_word=None, retail_os_found=None, mbr_raw=mbr, partitions=partitions,
            requested_end=requested_end, boundary_ambiguous=boundary_ambiguous,
            validation_score=score, validation_checks=checks, rejection_reasons=reasons,
        )
        return ProbeCandidate(marker_offset, "8702", layout.valid, score, reasons, layout)
    except (struct.error, ValueError, RawReadError, OSError) as exc:
        reasons.append(f"parse/read failure: {exc}")
        return ProbeCandidate(marker_offset, "8702", False, 0, reasons=reasons)


def find_candidate(reader: Any, geometry: RawGeometry, offset: int) -> ProbeCandidate:
    try:
        probe = reader.read_at(offset, 0x800)
    except Exception as exc:
        return ProbeCandidate(offset, None, False, reasons=[f"probe failed: {exc}"])
    marker = probe[:4]
    if marker in MARKERS:
        return parse_candidate(reader, geometry, offset, marker)
    if probe[:4] == S5L8702_MAGIC:
        return parse_s5l8702_candidate(reader, geometry, offset, probe)
    return ProbeCandidate(offset, marker.decode("ascii", "replace") if marker else None, False, reasons=["marker not recognized"])
