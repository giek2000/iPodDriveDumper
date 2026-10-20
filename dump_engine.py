"""Read-only dump, checkpoint, resume, hashing, and artifact finalization."""
from __future__ import annotations

import hashlib
import json
import os
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .logging_utils import EventLogger, utc_now
from .models import DumpStatus, FirmwareLayout, SafetyState
from .version import APP_NAME, VERSION
from .win32_raw_reader import RawReadError


class DumpEngine:
    def __init__(self, *, chunk_size: int = 1024 * 1024, retries: int = 4,
                 io_mode: str = "buffered", sleep: Callable[[float], None] = time.sleep):
        if chunk_size < 512 or chunk_size > 16 * 1024 * 1024:
            raise ValueError("chunk size must be between 512 and 16 MiB")
        if io_mode not in {"buffered", "no_buffering"}:
            raise ValueError("io mode must be buffered or no_buffering")
        if io_mode == "no_buffering":
            raise ValueError("no-buffering mode is reserved until an aligned allocator is explicitly enabled")
        self.chunk_size = chunk_size
        self.retries = min(max(retries, 0), 5)
        self.io_mode = io_mode
        self.sleep = sleep
        self.safety = SafetyState()

    def run(self, reader: Any, layout: FirmwareLayout, output_dir: Path, *, start: int = 0,
            end: Optional[int] = None, extract_sections: bool = True,
            cancel: Optional[Any] = None, command: str = "dump",
            session_id: Optional[str] = None, diagnostic: bool = False,
            full_partition: bool = False, resume: bool = False,
            progress: Optional[Callable[[int, int], None]] = None) -> dict[str, Any]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        session_id = session_id or uuid.uuid4().hex
        logger = EventLogger(output_dir, session_id, getattr(reader, "path", None))
        logger.info("startup", "dump_start", details={"command": command, "version": VERSION, "read_only": True})
        manifest = self._base_manifest(session_id, command, reader, layout)
        manifest["session"]["started_utc"] = utc_now()
        manifest["dump"]["diagnostic_override"] = diagnostic
        if not layout.valid:
            reader.close()
            return self._finish(output_dir, logger, manifest, DumpStatus.INVALID_STRUCTURE,
                                "; ".join(layout.rejection_reasons) or "invalid firmware structure")
        if not layout.complete_safe and not diagnostic:
            reader.close()
            return self._finish(output_dir, logger, manifest, DumpStatus.INVALID_STRUCTURE,
                                "unresolved geometry or partition-boundary contradiction; use an explicitly labeled diagnostic range")
        if end is None:
            if full_partition:
                partition_end = layout.firmware_partition_end
                if not partition_end:
                    reader.close()
                    return self._finish(output_dir, logger, manifest, DumpStatus.INVALID_STRUCTURE,
                                        "full-partition mode requires an MBR-declared firmware partition end")
                end = max(partition_end, layout.section_end)
            else:
                end = layout.requested_end
        if start < 0 or end <= start or end > reader.geometry.disk_length:
            return self._finish(output_dir, logger, manifest, DumpStatus.INVALID_STRUCTURE, "requested range is invalid")
        manifest["dump"].update({"requested_start": start, "requested_end": end, "requested_bytes": end - start,
                                   "diagnostic_override": diagnostic, "full_partition": full_partition,
                                   "firmware_partition_end": layout.firmware_partition_end,
                                   "section_end": layout.section_end})
        logger.info("dump", "range_resolved", offset=start, requested=end - start,
                    details={"full_partition": full_partition, "end": end,
                             "firmware_partition_end": layout.firmware_partition_end,
                             "section_end": layout.section_end})
        part_path = output_dir / "firmware_raw.bin.part"
        manifest["dump"]["part_path"] = str(part_path)
        checkpoint = self._load_checkpoint(output_dir)
        resume_at = start
        if checkpoint:
            try:
                resume_at = self._verify_checkpoint(checkpoint, part_path, layout, reader.geometry, start, end)
                if resume_at > start:
                    logger.info("resume", "checkpoint_verified", offset=resume_at, details={"chunks": len(checkpoint.get("chunks", []))})
            except Exception as exc:
                # An explicit resume must never overwrite existing evidence; refuse.
                # A fresh dump into a folder with a mismatched/stale checkpoint should start
                # clean, unless a finalized firmware_raw.bin is present (never clobber a
                # completed artifact).
                if resume:
                    logger.error("resume", "checkpoint_rejected", details={"error": str(exc)})
                    reader.close()
                    return self._finish(output_dir, logger, manifest, DumpStatus.INVALID_STRUCTURE,
                                        f"resume refused without overwriting existing evidence: {exc}")
                if (output_dir / "firmware_raw.bin").exists():
                    logger.error("resume", "checkpoint_rejected_final_present", details={"error": str(exc)})
                    reader.close()
                    return self._finish(output_dir, logger, manifest, DumpStatus.INVALID_STRUCTURE,
                                        "output folder already contains a finalized firmware_raw.bin; "
                                        "choose a new output folder to avoid overwriting it")
                logger.warning("resume", "stale_checkpoint_ignored_fresh_dump",
                               details={"error": str(exc), "action": "starting a fresh dump"})
                resume_at = start
        hasher = hashlib.sha256()
        written = 0
        try:
            mode = "r+b" if part_path.exists() and resume_at > start else "wb"
            with part_path.open(mode) as output:
                if resume_at > start:
                    output.seek(0)
                    remaining = resume_at - start
                    while remaining:
                        block = output.read(min(self.chunk_size, remaining))
                        if not block:
                            raise OSError("checkpoint output is shorter than declared")
                        hasher.update(block)
                        remaining -= len(block)
                    output.seek(resume_at - start)
                    written = resume_at - start
                offset = resume_at
                while offset < end:
                    if cancel is not None and cancel.is_set():
                        logger.warning("cancel", "cancellation_requested", offset=offset)
                        output.flush()
                        os.fsync(output.fileno())
                        self._write_checkpoint(output_dir, manifest, layout, start, end, offset, 0,
                                               hashlib.sha256(b"").hexdigest(), offset)
                        return self._finish(output_dir, logger, manifest, DumpStatus.CANCELLED,
                                            "cancelled by user", written=written, last_offset=offset, part_exists=True)
                    request = min(self.chunk_size, end - offset)
                    data = self._read_with_retries(reader, logger, offset, request)
                    output.write(data)
                    output.flush()
                    hasher.update(data)
                    written += len(data)
                    offset += len(data)
                    manifest["dump"]["written_bytes"] = written
                    manifest["dump"]["last_successful_offset"] = offset
                    if progress:
                        progress(written, end - start)
                    self._write_checkpoint(output_dir, manifest, layout, start, end, offset - len(data), len(data), hashlib.sha256(data).hexdigest(), offset)
                output.flush()
                os.fsync(output.fileno())
            if written != end - start:
                return self._finish(output_dir, logger, manifest, DumpStatus.PARTIAL,
                                    "output byte count did not reach requested range", written=written, last_offset=start + written)
            raw_hash = hasher.hexdigest()
            raw_path = part_path
            if extract_sections:
                manifest["artifacts"].extend(self._extract_sections(raw_path, output_dir, layout, start, logger))
            if diagnostic:
                partial_name = "firmware_raw.bin.part"
                manifest["artifacts"].append({"name": partial_name, "path": str(raw_path), "bytes": written,
                                               "sha256": raw_hash, "scope": f"diagnostic raw[{start}:{end}]"})
                manifest["verification"] = {"raw_sha256": raw_hash, "raw_bytes": written,
                                             "osos_first_word": layout.osos_first_word,
                                             "retailOS_anchor": layout.retail_os_found,
                                             "structurally_complete": False}
                return self._finish(output_dir, logger, manifest, DumpStatus.PARTIAL,
                                    "diagnostic range completed; not eligible for COMPLETE", written=written, last_offset=end, part_exists=True)
            final_path = output_dir / "firmware_raw.bin"
            sidecar_part = output_dir / "firmware_raw.bin.sha256.part"
            sidecar_part.write_text(f"{raw_hash}  firmware_raw.bin\n", encoding="utf-8")
            manifest["artifacts"].append({"name": "firmware_raw.bin", "path": str(final_path), "bytes": written,
                                           "sha256": raw_hash, "scope": f"raw[{start}:{end}]"})
            manifest["verification"] = {"raw_sha256": raw_hash, "raw_bytes": written,
                                         "osos_first_word": layout.osos_first_word,
                                         "retailOS_anchor": layout.retail_os_found,
                                         "structurally_complete": layout.complete_safe}
            staged_metadata = output_dir / "metadata.json.part"
            manifest["session"]["status"] = DumpStatus.COMPLETE.value
            manifest["session"]["finished_utc"] = utc_now()
            staged_metadata.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
            os.replace(part_path, final_path)
            os.replace(sidecar_part, output_dir / "firmware_raw.bin.sha256")
            os.replace(staged_metadata, output_dir / "metadata.json")
            logger.info("finalize", "raw_artifact_renamed", details={"path": str(final_path), "bytes": written})
            return self._finish(output_dir, logger, manifest, DumpStatus.COMPLETE, "complete read", written=written, last_offset=end)
        except Exception as exc:
            logger.error("exception", "unhandled_exception", offset=start + written,
                         requested=end - start - written, actual=0,
                         details={"type": type(exc).__name__, "error": str(exc),
                                  "traceback": traceback.format_exc()})
            status = DumpStatus.PARTIAL if written else DumpStatus.FAILED
            return self._finish(output_dir, logger, manifest, status, str(exc), written=written,
                                last_offset=start + written, part_exists=part_path.exists())
        finally:
            try:
                reader.close()
            except Exception:
                pass

    def _read_with_retries(self, reader: Any, logger: EventLogger, offset: int, request: int) -> bytes:
        delays = (0.0, 0.25, 1.0, 2.0, 5.0)
        for attempt in range(self.retries + 1):
            logger.info("dump", "read_attempt", offset=offset, requested=request, attempt=attempt + 1)
            try:
                data = reader.read_at(offset, request)
                actual = len(data)
                logger.info("dump", "read_result", offset=offset, requested=request, actual=actual, attempt=attempt + 1)
                if actual == 0:
                    raise RawReadError("zero-byte read", offset=offset, requested=request, actual=0, transient=True)
                if actual != request:
                    raise RawReadError("short read", offset=offset, requested=request, actual=actual)
                return data
            except RawReadError as exc:
                logger.warning("retry" if exc.transient else "dump", "read_error", offset=offset,
                               requested=request, actual=exc.actual, attempt=attempt + 1,
                               win32_error=exc.win32_code, details={"transient": exc.transient, "error": str(exc)})
                if not exc.transient or attempt >= self.retries:
                    raise
                self.sleep(delays[min(attempt + 1, len(delays) - 1)])
        raise AssertionError("unreachable")

    def _base_manifest(self, session_id: str, command: str, reader: Any, layout: FirmwareLayout) -> dict[str, Any]:
        geometry = reader.geometry.to_dict()
        return {
            "schema_version": 1,
            "application": {"name": APP_NAME, "version": VERSION, "build": "source"},
            "session": {"id": session_id, "started_utc": None, "finished_utc": None,
                        "status": None, "command": command, "elevated": self._is_elevated()},
            "device": {"path": getattr(reader, "path", None), "index": self._drive_index(getattr(reader, "path", "")), **geometry,
                       "model": None, "serial_redacted": None, "volumes": []},
            "layout": layout.to_dict(),
            "dump": {"requested_start": 0, "requested_end": 0, "requested_bytes": 0, "written_bytes": 0,
                     "chunk_size": self.chunk_size, "io_mode": self.io_mode, "retries": self.retries,
                     "last_successful_offset": 0, "partial_reason": None},
            "artifacts": [], "verification": {}, "errors": [], "safety": self.safety.to_dict(),
        }

    @staticmethod
    def _is_elevated() -> Optional[bool]:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return None

    @staticmethod
    def _drive_index(path: str) -> Optional[int]:
        try:
            return int(path.rsplit("PhysicalDrive", 1)[1])
        except Exception:
            return None

    def _write_checkpoint(self, output_dir: Path, manifest: dict[str, Any], layout: FirmwareLayout,
                          start: int, end: int, chunk_offset: int, chunk_length: int,
                          chunk_digest: str, offset: int) -> None:
        path = output_dir / "checkpoint.json"
        previous = self._load_checkpoint(output_dir) or {}
        chunks = list(previous.get("chunks", []))
        item = {"offset": chunk_offset, "length": chunk_length, "sha256": chunk_digest}
        if not chunks or chunks[-1].get("offset") != item["offset"]:
            chunks.append(item)
        checkpoint = {"schema_version": 1, "session_id": manifest["session"]["id"],
                      "device": manifest["device"], "requested_start": start, "requested_end": end,
                      "chunk_size": self.chunk_size, "io_mode": self.io_mode,
                      "last_contiguous": offset, "output_part": str(output_dir / "firmware_raw.bin.part"),
                      "layout_fingerprint": layout.fingerprint(), "chunks": chunks,
                      "updated_utc": utc_now()}
        path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _load_checkpoint(output_dir: Path) -> Optional[dict[str, Any]]:
        path = Path(output_dir) / "checkpoint.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _verify_checkpoint(checkpoint: dict[str, Any], part_path: Path, layout: FirmwareLayout,
                           geometry: Any, start: int, end: int) -> int:
        if checkpoint.get("requested_start") != start or checkpoint.get("requested_end") != end:
            raise ValueError("checkpoint range differs")
        if checkpoint.get("layout_fingerprint") != layout.fingerprint():
            raise ValueError("device layout fingerprint differs")
        saved_geometry = checkpoint.get("device", {})
        current_geometry = geometry.to_dict()
        for key in ("disk_length", "logical_sector_size", "physical_sector_size", "alignment"):
            if saved_geometry.get(key) != current_geometry.get(key):
                raise ValueError(f"device geometry differs for {key}")
        last = int(checkpoint.get("last_contiguous", start))
        expected_size = last - start
        if last < start or last > end or not part_path.exists() or part_path.stat().st_size != expected_size:
            raise ValueError("partial output length does not exactly match checkpoint")
        cursor = start
        with part_path.open("rb") as stream:
            for chunk in checkpoint.get("chunks", []):
                chunk_offset = int(chunk["offset"])
                length = int(chunk["length"])
                if chunk_offset != cursor or length < 0 or chunk_offset + length > last:
                    raise ValueError("checkpoint chunks are not contiguous")
                stream.seek(chunk_offset - start)
                data = stream.read(length)
                if len(data) != length or hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                    raise ValueError("partial output chunk hash mismatch")
                cursor += length
        if cursor != last:
            raise ValueError("checkpoint chunk coverage is incomplete")
        return last

    def _extract_sections(self, raw_path: Path, output_dir: Path, layout: FirmwareLayout,
                          requested_start: int, logger: EventLogger) -> list[dict[str, Any]]:
        section_dir = output_dir / "sections"
        section_dir.mkdir(exist_ok=True)
        artifacts: list[dict[str, Any]] = []
        with raw_path.open("rb") as source:
            raw_size = raw_path.stat().st_size
            fixed = (("firmware_header.bin", layout.marker_offset, layout.header_raw),
                     ("raw_directory.bin", layout.directory_offset, layout.directory_raw))
            for name, absolute, data in fixed:
                relative = absolute - requested_start
                if relative < 0 or relative + len(data) > raw_size:
                    logger.warning("verify", "artifact_outside_requested_range", offset=absolute,
                                   details={"name": name, "length": len(data)})
                    continue
                source.seek(relative)
                copied = source.read(len(data))
                if len(copied) != len(data):
                    raise OSError(f"artifact extraction short read: {name}")
                path = output_dir / name
                path.write_bytes(copied)
                digest = hashlib.sha256(copied).hexdigest()
                artifacts.append({"name": name, "path": str(path), "bytes": len(copied),
                                  "sha256": digest, "scope": f"raw[{absolute}:{absolute + len(copied)}]"})
            for section in layout.sections:
                if section.bounds_status != "IN_BOUNDS":
                    continue
                relative = section.absolute_start - requested_start
                if relative < 0 or relative + section.declared_length > raw_size:
                    logger.warning("verify", "section_outside_requested_range", offset=section.absolute_start,
                                   details={"name": section.name, "length": section.declared_length})
                    continue
                source.seek(relative)
                data = source.read(section.declared_length)
                if len(data) != section.declared_length:
                    raise OSError(f"section extraction short read: {section.name}")
                path = section_dir / f"{section.name}_{section.absolute_start:08X}.bin"
                path.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                artifacts.append({"name": path.name, "path": str(path), "bytes": len(data),
                                  "sha256": digest, "scope": f"raw[{section.absolute_start}:{section.absolute_end}]"})
                logger.info("verify", "section_extracted", offset=section.absolute_start,
                            actual=len(data), details={"name": section.name, "path": str(path), "sha256": digest})
        return artifacts

    def _finish(self, output_dir: Path, logger: EventLogger, manifest: dict[str, Any], status: DumpStatus,
                reason: str, *, written: int = 0, last_offset: int = 0, part_exists: bool = False) -> dict[str, Any]:
        manifest["session"]["status"] = status.value
        manifest["session"]["finished_utc"] = utc_now()
        manifest["dump"]["written_bytes"] = written
        manifest["dump"]["last_successful_offset"] = last_offset
        manifest["dump"]["partial_reason"] = None if status == DumpStatus.COMPLETE else reason
        if status != DumpStatus.COMPLETE and reason:
            manifest["errors"].append({"status": status.value, "message": reason, "offset": last_offset})
        manifest["safety"] = self.safety.to_dict()
        logger.info("finalize", "final_status", details={"status": status.value, "reason": reason, "written": written})
        (output_dir / "metadata.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
        summary = [f"Status: {status.value}", f"Reason: {reason}", f"Written bytes: {written}", f"Last successful offset: 0x{last_offset:X}",
                   f"Output directory: {output_dir}"]
        if status == DumpStatus.COMPLETE:
            summary.append("Next action: preserve the raw artifact and review metadata.json before analysis.")
        elif status in (DumpStatus.PARTIAL, DumpStatus.CANCELLED):
            summary.append("Next action: reconnect the same device, verify checkpoint.json, and resume only after identity/layout validation.")
        else:
            summary.append("Next action: review read.log and read_events.jsonl; do not retry against an unverified device.")
        (output_dir / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
        return manifest
