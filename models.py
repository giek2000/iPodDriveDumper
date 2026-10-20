"""Data models shared by the scanner, parser, and dump engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DumpStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"


@dataclass(frozen=True)
class RawGeometry:
    disk_length: int
    logical_sector_size: int
    physical_sector_size: Optional[int] = None
    cylinders: Optional[int] = None
    tracks_per_cylinder: Optional[int] = None
    sectors_per_track: Optional[int] = None
    alignment: int = 1
    removable: Optional[bool] = None
    geometry_ioctl: str = "IOCTL_DISK_GET_DRIVE_GEOMETRY (0x00070000)"
    length_ioctl: str = "IOCTL_DISK_GET_LENGTH_INFO (0x0007405C)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "disk_length": self.disk_length,
            "logical_sector_size": self.logical_sector_size,
            "physical_sector_size": self.physical_sector_size,
            "cylinders": self.cylinders,
            "tracks_per_cylinder": self.tracks_per_cylinder,
            "sectors_per_track": self.sectors_per_track,
            "alignment": self.alignment,
            "removable": self.removable,
            "geometry_ioctl": self.geometry_ioctl,
            "length_ioctl": self.length_ioctl,
        }


@dataclass
class PartitionInterpretation:
    index: int
    partition_type: int
    status: int
    lba_start: int
    lba_length: int
    raw_start: int
    raw_end: int
    logical_start: int
    logical_end: int
    selected: bool = False
    contradiction: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class SectionRecord:
    name: str
    raw_record: bytes
    flags: int
    device_offset: int
    declared_length: int
    load_address: int
    entry_address: int
    stored_checksum: int
    absolute_start: int
    absolute_end: int
    bounds_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "raw_record_hex": self.raw_record.hex(),
            "flags": self.flags,
            "device_offset": self.device_offset,
            "declared_length": self.declared_length,
            "load_address": self.load_address,
            "entry_address": self.entry_address,
            "stored_checksum": self.stored_checksum,
            "absolute_start": self.absolute_start,
            "absolute_end": self.absolute_end,
            "bounds_status": self.bounds_status,
        }


@dataclass
class FirmwareLayout:
    marker_offset: int
    marker: bytes
    header_signature: bytes
    header_raw: bytes
    version: int
    directory_relative: int
    directory_offset: int
    directory_raw: bytes
    firmware_body_offset: int
    sections: list[SectionRecord]
    osos: Optional[SectionRecord]
    osos_first_word: Optional[int]
    retail_os_found: Optional[bool]
    mbr_raw: bytes = b""
    partitions: list[PartitionInterpretation] = field(default_factory=list)
    requested_end: int = 0
    boundary_ambiguous: bool = False
    validation_score: int = 0
    validation_checks: dict[str, bool] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    probe_candidates: list[dict[str, Any]] = field(default_factory=list)
    osos_signature: bytes = b""

    @property
    def valid(self) -> bool:
        return not self.rejection_reasons and self.osos is not None

    @property
    def complete_safe(self) -> bool:
        """Whether the layout may produce a COMPLETE default dump."""
        return self.valid and not self.boundary_ambiguous and not any(p.contradiction for p in self.partitions)

    @property
    def firmware_partition_end(self) -> Optional[int]:
        """MBR-declared firmware-partition end (bytes), if a type-0x00 partition is present."""
        ends = [p.raw_end for p in self.partitions if p.selected and p.raw_end]
        return max(ends) if ends else None

    @property
    def section_end(self) -> int:
        """End offset of the last validated !ATA section."""
        return max((s.absolute_end for s in self.sections), default=0)

    @property
    def family(self) -> str:
        """Best-effort device family from observed, verifiable structure traits.

        Grounded in live devices rather than a single header field:
        - S5L8702 IMG1 -> iPod Classic.
        - PortalPlayer OSOS carrying the literal "portalplayer" and an OSOS first
          word of 0xEA000079 matches the observed iPod Mini.
        - PortalPlayer OSOS first word 0xEA00007A matches the observed iPod Video 5G/5.5G.
        Otherwise report the honest shared family without guessing a model.
        """
        if self.marker == b"8702":
            return "iPod Classic (S5L8702)"
        if b"portalplayer" in self.osos_signature and self.osos_first_word == 0xEA000079:
            return "iPod Mini (PortalPlayer)"
        if self.osos_first_word == 0xEA00007A:
            return "iPod Video 5G/5.5G (PortalPlayer PP5022)"
        return "iPod (PortalPlayer)"

    def fingerprint(self) -> dict[str, Any]:
        import hashlib

        return {
            "marker_offset": self.marker_offset,
            "marker": self.marker.hex(),
            "header_sha256": hashlib.sha256(self.header_raw).hexdigest(),
            "directory_sha256": hashlib.sha256(self.directory_raw).hexdigest(),
            "version": self.version,
            "directory_offset": self.directory_offset,
            "sections": [
                {"name": s.name, "start": s.absolute_start, "length": s.declared_length}
                for s in self.sections
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_candidates": self.probe_candidates,
            "accepted_firmware_offset": self.marker_offset,
            "marker": self.marker.hex(),
            "header_signature": self.header_signature.hex(),
            "header_raw_hex": self.header_raw.hex(),
            "version": self.version,
            "directory_relative": self.directory_relative,
            "directory_offset": self.directory_offset,
            "directory_raw_hex": self.directory_raw.hex(),
            "firmware_body_offset": self.firmware_body_offset,
            "mbr_raw_hex": self.mbr_raw.hex(),
            "partition_interpretations": [p.to_dict() for p in self.partitions],
            "sections": [s.to_dict() for s in self.sections],
            "osos": self.osos.to_dict() if self.osos else None,
            "osos_first_word": self.osos_first_word,
            "retail_os_found": self.retail_os_found,
            "requested_end": self.requested_end,
            "boundary_ambiguous": self.boundary_ambiguous,
            "validation_score": self.validation_score,
            "validation_checks": self.validation_checks,
            "rejection_reasons": self.rejection_reasons,
            "device_family": self.family,
            "complete_safe": self.complete_safe,
            "fingerprint": self.fingerprint(),
        }


@dataclass
class ProbeCandidate:
    offset: int
    marker: Optional[str]
    accepted: bool
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    layout: Optional[FirmwareLayout] = None

    def to_dict(self, *, include_layout: bool = True) -> dict[str, Any]:
        data = {
            "offset": self.offset,
            "marker": self.marker,
            "accepted": self.accepted,
            "score": self.score,
            "reasons": self.reasons,
        }
        if include_layout:
            data["layout"] = self.layout.to_dict() if self.layout else None
        return data


@dataclass
class DriveScanRecord:
    path: str
    index: int
    opened: bool = False
    geometry: Optional[RawGeometry] = None
    candidates: list[ProbeCandidate] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    model: Optional[str] = None
    serial_redacted: Optional[str] = None
    volumes: list[str] = field(default_factory=list)
    classification: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "index": self.index,
            "opened": self.opened,
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "candidates": [c.to_dict(include_layout=False) for c in self.candidates],
            "errors": self.errors,
            "model": self.model,
            "serial_redacted": self.serial_redacted,
            "volumes": self.volumes,
            "classification": self.classification,
            "note": self.note,
        }


@dataclass
class SafetyState:
    write_access_requested: bool = False
    write_file_called: bool = False
    format_or_partition_operation_called: bool = False

    def to_dict(self) -> dict[str, bool]:
        return self.__dict__.copy()
