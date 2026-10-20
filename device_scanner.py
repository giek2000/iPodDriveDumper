"""Bounded PhysicalDrive enumeration and structure-based candidate selection."""
from __future__ import annotations

from typing import Any, Callable, Optional

from .firmware_parser import PROBE_OFFSETS, find_candidate, parse_mbr
from .logging_utils import EventLogger
from .models import DriveScanRecord
from .win32_raw_reader import RawReadError, Win32Reader

ReaderFactory = Callable[[str], Any]


def scan_drives(*, reader_factory: Optional[ReaderFactory] = None, max_drives: int = 16,
                logger: Optional[EventLogger] = None) -> list[DriveScanRecord]:
    factory = reader_factory or Win32Reader.open
    records: list[DriveScanRecord] = []
    if logger:
        logger.info("scan", "scan_start", details={"max_drives": max_drives, "read_access_only": True})
    for index in range(max_drives):
        path = f"\\\\.\\PhysicalDrive{index}"
        record = DriveScanRecord(path=path, index=index)
        reader = None
        try:
            if logger:
                logger.info("open", "drive_open_attempt", details={
                    "path": path, "desired_access": "0x80000000", "share_mode": "0x3",
                    "creation": "OPEN_EXISTING", "flags": "0x0", "write_access_requested": False,
                })
            reader = factory(path)
            record.opened = True
            record.geometry = reader.geometry
            if logger:
                logger.info("geometry", "geometry_result", details=record.geometry.to_dict())
            offsets = list(PROBE_OFFSETS)
            # Partition-derived probe locations supplement, but never replace, compatibility probes.
            try:
                mbr = reader.read_at(0, min(512, record.geometry.disk_length))
                partitions, _ = parse_mbr(mbr, record.geometry)
                offsets.extend(p.raw_start for p in partitions if 0 <= p.raw_start < record.geometry.disk_length)
            except Exception as exc:
                if logger:
                    logger.warning("probe", "partition_probe_unavailable", details={"error": str(exc)})
            for offset in dict.fromkeys(offsets):
                candidate = find_candidate(reader, record.geometry, offset)
                record.candidates.append(candidate)
                if logger:
                    logger.info("probe", "candidate_result", offset=offset,
                                sector_size=record.geometry.logical_sector_size,
                                details=candidate.to_dict(include_layout=False))
            evidence = [candidate.to_dict(include_layout=False) for candidate in record.candidates]
            for candidate in record.candidates:
                if candidate.layout:
                    candidate.layout.probe_candidates = evidence
            _classify_record(record, reader)
            if logger and record.classification:
                logger.info("probe", "device_classification",
                            details={"classification": record.classification, "note": record.note})
            reader.close()
        except Exception as exc:
            error = {"phase": "open/geometry/probe", "error": str(exc), "type": type(exc).__name__}
            if isinstance(exc, RawReadError):
                error.update({"win32_error": exc.win32_code, "offset": exc.offset, "requested": exc.requested, "actual": exc.actual})
            record.errors.append(error)
            if logger:
                logger.error("scan", "drive_failure", offset=getattr(exc, "offset", None),
                             requested=getattr(exc, "requested", None), actual=getattr(exc, "actual", None),
                             win32_error=getattr(exc, "win32_code", None),
                             details={"path": path, "error": str(exc), "type": type(exc).__name__})
            try:
                if reader:
                    reader.close()
            except Exception:
                pass
        records.append(record)
    if logger:
        logger.info("scan", "scan_end", details={"drives": len(records), "validated": sum(1 for r in records for c in r.candidates if c.accepted)})
    return records


def _classify_record(record: DriveScanRecord, reader: Any) -> None:
    """Explain non-validated iPod-looking disks accurately instead of a bare rejection.

    An iPod Classic exposes only its FAT32 data partition over USB disk mode; its
    firmware partition is not presented as readable sectors. We detect that case by
    the Apple/IPOD FAT boot-sector text so the tool reports why no firmware is found.
    """
    if any(c.accepted for c in record.candidates):
        record.classification = "iPod firmware partition (dumpable)"
        return
    try:
        head = reader.read_at(0, min(1024 * 1024, reader.geometry.disk_length))
    except Exception:
        return
    apple_fat = b"This is Apple iPod not a bootable disk" in head or (b"IPOD" in head and b"FAT32" in head)
    if apple_fat:
        record.classification = "iPod data partition only"
        record.note = (
            "This looks like an iPod, but only its FAT32 data partition is exposed over USB disk mode. "
            "No PortalPlayer (]ih[/!ATA) or S5L8702 (8702) firmware structure is present in the readable sectors. "
            "iPod Classic hides its firmware partition from disk mode; raw firmware extraction on Classic "
            "requires DFU/wInd3x, not disk-mode sector reads. This tool can raw-dump firmware only on iPod "
            "Video 5G/5.5G, which exposes its firmware partition."
        )


def validated_candidates(records: list[DriveScanRecord]) -> list[tuple[DriveScanRecord, Any]]:
    result = []
    for record in records:
        for candidate in record.candidates:
            if candidate.accepted and candidate.layout is not None:
                result.append((record, candidate))
    return result
