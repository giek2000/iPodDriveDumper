"""Structured event and human-readable logging for dump sessions."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def bounded_preview(data: bytes, limit: int = 64) -> dict[str, Any]:
    sample = data[:limit]
    return {"preview_hex": sample.hex(), "preview_bytes": len(sample), "sha256": hashlib.sha256(data).hexdigest()}


class EventLogger:
    def __init__(self, output_dir: Path, session_id: str, device_path: Optional[str] = None, verbose: bool = False):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self.device_path = device_path
        self.verbose = verbose
        self.started = time.monotonic()
        self.sequence = 0
        self.events_path = self.output_dir / "read_events.jsonl"
        self.human_path = self.output_dir / "read.log"

    def event(self, level: str, phase: str, name: str, *, offset: Optional[int] = None,
              requested: Optional[int] = None, actual: Optional[int] = None,
              sector_size: Optional[int] = None, attempt: Optional[int] = None,
              win32_error: Optional[int] = None, win32_message: Optional[str] = None,
              details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.sequence += 1
        item = {
            "seq": self.sequence,
            "timestamp_utc": utc_now(),
            "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            "level": level,
            "phase": phase,
            "event": name,
            "session_id": self.session_id,
            "device_path": self.device_path,
            "offset": offset,
            "requested": requested,
            "actual": actual,
            "sector_size": sector_size,
            "attempt": attempt,
            "win32_error": win32_error,
            "win32_message": win32_message,
            "details": details or {},
        }
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, sort_keys=True) + "\n")
        line = f"{item['timestamp_utc']} [{level}] {phase}/{name}"
        if offset is not None:
            line += f" offset=0x{offset:X}"
        if requested is not None or actual is not None:
            line += f" bytes={actual if actual is not None else '?'} / {requested if requested is not None else '?'}"
        if win32_error is not None:
            line += f" win32={win32_error}"
        if details:
            compact = json.dumps(details, sort_keys=True, default=str)
            line += f" {compact[:500]}"
        with self.human_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        if self.verbose:
            print(line)
        return item

    def info(self, phase: str, name: str, **kwargs: Any) -> dict[str, Any]:
        return self.event("INFO", phase, name, **kwargs)

    def warning(self, phase: str, name: str, **kwargs: Any) -> dict[str, Any]:
        return self.event("WARNING", phase, name, **kwargs)

    def error(self, phase: str, name: str, **kwargs: Any) -> dict[str, Any]:
        return self.event("ERROR", phase, name, **kwargs)
