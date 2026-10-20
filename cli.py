"""CLI entry point shared by the standalone executable and tests."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .device_scanner import scan_drives, validated_candidates
from .dump_engine import DumpEngine
from .fixtures import build_synthetic_fixture, fixture_reader_factory
from .models import DumpStatus
from .version import APP_NAME, VERSION

EXIT_CODES = {DumpStatus.COMPLETE.value: 0, DumpStatus.PARTIAL.value: 1, DumpStatus.CANCELLED.value: 1,
              DumpStatus.FAILED.value: 2, DumpStatus.INVALID_STRUCTURE.value: 3}


def _fixture_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fixture", choices=("512", "2048"), help="use an offline synthetic fixture")


def _scan(args: argparse.Namespace):
    if args.fixture:
        fixture = build_synthetic_fixture(sector_size=int(args.fixture))
        path = r"\\.\PhysicalDrive0"
        records = scan_drives(reader_factory=fixture_reader_factory({path: fixture}), max_drives=1)
    else:
        records = scan_drives()
    payload = [record.to_dict() for record in records]
    text = json.dumps(payload, indent=2, sort_keys=True) if args.json else _human_scan(payload)
    if getattr(args, "out", None):
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"scan written to {args.out}")
    else:
        print(text)
    return 0 if any(c["accepted"] for r in payload for c in r["candidates"]) else 3


def _human_scan(records: list[dict]) -> str:
    lines = []
    for record in records:
        geometry = record.get("geometry") or {}
        candidates = [c for c in record["candidates"] if c["accepted"]]
        lines.append(f"{record['path']}: opened={record['opened']} size={geometry.get('disk_length')} sector={geometry.get('logical_sector_size')} validated={len(candidates)}")
        if record.get("classification"):
            lines.append(f"  classification: {record['classification']}")
        if record.get("note"):
            lines.append(f"  note: {record['note']}")
        for candidate in record["candidates"]:
            lines.append(f"  0x{candidate['offset']:X}: {'ACCEPTED' if candidate['accepted'] else 'rejected'} ({'; '.join(candidate['reasons'])})")
    return "\n".join(lines)


def _select(args):
    if args.fixture:
        fixture = build_synthetic_fixture(sector_size=int(args.fixture))
        path = r"\\.\PhysicalDrive0"
        factory = fixture_reader_factory({path: fixture})
        records = scan_drives(reader_factory=factory, max_drives=1)
        matches = validated_candidates(records)
        if len(matches) != 1:
            raise RuntimeError(f"expected exactly one validated fixture candidate, found {len(matches)}")
        return factory(path), matches[0][1].layout

    records = scan_drives()
    matches = validated_candidates(records)
    if args.drive:
        matches = [(record, candidate) for record, candidate in matches if record.path == args.drive]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one validated candidate, found {len(matches)}")
    record, candidate = matches[0]
    from .win32_raw_reader import Win32Reader
    return Win32Reader.open(record.path), candidate.layout


def _inspect(args):
    reader, layout = _select(args)
    try:
        print(json.dumps(layout.to_dict(), indent=2, sort_keys=True))
    finally:
        reader.close()
    return 0


def _dump(args):
    reader, layout = _select(args)
    output = Path(args.output) if args.output else Path.cwd() / f"iPodVideo_{uuid.uuid4().hex[:12]}"
    engine = DumpEngine(chunk_size=args.chunk_size, retries=args.retries, io_mode="no_buffering" if args.no_buffering else "buffered")
    diagnostic = args.start != 0 or args.end is not None
    if not args.fixture and not args.yes:
        prompt = "This operation will open the selected physical disk read-only and copy bytes from it. No write access will be requested and no disk sectors will be modified."
        if diagnostic:
            prompt += " This is an explicitly labeled diagnostic range and cannot be a COMPLETE dump."
        if input(prompt + " Confirm? [y/N] ").strip().lower() != "y":
            return 1
    manifest = engine.run(reader, layout, output, start=args.start, end=args.end,
                          extract_sections=args.extract_sections, command="dump", diagnostic=diagnostic)
    print(json.dumps(manifest, indent=2, sort_keys=True) if args.json else manifest["session"]["status"])
    return EXIT_CODES[manifest["session"]["status"]]


def _probe(args):
    """Read-only search of a bounded early region for known firmware magics.

    Grounds new device recognition (e.g. iPod Classic S5L8702) in real on-disk
    bytes without writing anything. Scans only the first --scan-bytes of the disk.
    """
    from .win32_raw_reader import Win32Reader
    magics = {b"8702": "S5L8702 IMG1 (iPod Classic)", b"{{~~": "PortalPlayer marker",
              b"~~{{": "PortalPlayer marker (reversed)", b"]ih[": "PortalPlayer ]ih[ header"}
    reader = Win32Reader.open(args.drive)
    try:
        sector = reader.geometry.logical_sector_size
        limit = min(args.scan_bytes, reader.geometry.disk_length)
        window = 1024 * 1024
        found = []
        offset = 0
        while offset < limit:
            length = min(window, limit - offset)
            aligned = (length // sector) * sector or sector
            data = reader.read_at(offset, min(aligned, reader.geometry.disk_length - offset))
            for magic, label in magics.items():
                start = 0
                while True:
                    pos = data.find(magic, start)
                    if pos < 0:
                        break
                    absolute = offset + pos
                    preview = data[pos:pos + 16].hex()
                    found.append({"magic": magic.decode("latin1"), "label": label,
                                  "offset": absolute, "offset_hex": f"0x{absolute:X}", "preview_hex": preview})
                    start = pos + 1
            offset += len(data)
            if not data:
                break
        result = {"drive": args.drive, "sector_size": sector,
                  "disk_length": reader.geometry.disk_length, "scanned_bytes": limit, "matches": found}
        text = json.dumps(result, indent=2, sort_keys=True)
        if getattr(args, "out", None):
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"probe written to {args.out}")
        else:
            print(text)
        return 0 if found else 3
    finally:
        reader.close()


def _hexdump(args):
    """Read-only hex preview of a bounded region for grounding device recognition."""
    from .win32_raw_reader import Win32Reader
    reader = Win32Reader.open(args.drive)
    try:
        length = max(16, min(args.length, 4096))
        data = reader.read_at(args.offset, length)
        lines = []
        for row in range(0, len(data), 16):
            chunk = data[row:row + 16]
            hexpart = " ".join(f"{b:02x}" for b in chunk)
            asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{args.offset + row:08X}  {hexpart:<47}  {asciipart}")
        result = {"drive": args.drive, "offset": args.offset, "offset_hex": f"0x{args.offset:X}",
                  "length": len(data), "hex": data.hex(), "dump": lines}
        text = json.dumps(result, indent=2)
        if getattr(args, "out", None):
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"hexdump written to {args.out}")
        else:
            print(text)
        return 0
    finally:
        reader.close()


def _resume(args):
    manifest_path = Path(args.manifest)
    previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    device_path = previous["device"].get("path")
    if not device_path or not device_path.startswith(r"\\.\PhysicalDrive"):
        raise RuntimeError("resume requires a live PhysicalDrive path; fixture sessions are for offline tests only")
    records = scan_drives()
    matches = [(record, candidate) for record, candidate in validated_candidates(records)
               if record.path == device_path and candidate.layout.fingerprint() == previous["layout"]["fingerprint"]]
    if len(matches) != 1:
        raise RuntimeError("resume refused: device identity or validated layout does not match checkpoint")
    from .win32_raw_reader import Win32Reader
    reader = Win32Reader.open(device_path)
    dump = previous["dump"]
    engine = DumpEngine(chunk_size=int(dump["chunk_size"]), retries=int(dump["retries"]), io_mode=dump["io_mode"])
    output = manifest_path.parent
    result = engine.run(reader, matches[0][1].layout, output,
                        start=int(dump["requested_start"]), end=int(dump["requested_end"]),
                        extract_sections=True, command="resume")
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result["session"]["status"])
    return EXIT_CODES[result["session"]["status"]]


def _diagnostics(args):
    import zipfile
    session = Path(args.session)
    target = session.with_name(session.name + "_diagnostics.zip")
    allowed = ("read.log", "read_events.jsonl", "metadata.json", "summary.txt", "checkpoint.json")
    import platform
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in allowed:
            path = session / name
            if path.exists():
                archive.write(path, name)
        archive.writestr("diagnostics.json", json.dumps({
            "application": APP_NAME,
            "python": platform.python_version(),
            "os": platform.platform(),
            "architecture": platform.machine(),
            "raw_dump_included": False,
        }, indent=2, sort_keys=True))
    print(str(target))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="iPod Video raw firmware dumper (read-only)")
    sub = parser.add_subparsers(dest="command")
    scan = sub.add_parser("scan", help="enumerate and validate physical disks")
    scan.add_argument("--json", action="store_true")
    scan.add_argument("--out", help="write scan output to this file (reliable for windowed EXE)")
    _fixture_args(scan)
    inspect = sub.add_parser("inspect", help="inspect one validated device")
    inspect.add_argument("--drive")
    inspect.add_argument("--json", action="store_true")
    _fixture_args(inspect)
    dump = sub.add_parser("dump", help="read a validated raw range")
    dump.add_argument("--drive")
    dump.add_argument("--output", required=False)
    dump.add_argument("--start", type=int, default=0)
    dump.add_argument("--end", type=int)
    dump.add_argument("--chunk-size", type=int, default=1024 * 1024)
    dump.add_argument("--retries", type=int, default=4)
    dump.add_argument("--extract-sections", action="store_true", default=False)
    dump.add_argument("--json", action="store_true")
    mode = dump.add_mutually_exclusive_group()
    mode.add_argument("--buffered", action="store_true", default=True)
    mode.add_argument("--no-buffering", action="store_true", help="reserved; rejected until aligned I/O is implemented")
    dump.add_argument("--reference-osos")
    dump.add_argument("--yes", action="store_true", help="record explicit final confirmation for live reads")
    _fixture_args(dump)
    probe = sub.add_parser("probe", help="read-only search for firmware magics on a drive")
    probe.add_argument("--drive", required=True)
    probe.add_argument("--scan-bytes", type=int, default=64 * 1024 * 1024,
                       help="how many leading bytes to search (default 64 MiB)")
    probe.add_argument("--out", help="write probe output to this file")
    hexdump = sub.add_parser("hexdump", help="read-only hex preview of a bounded region")
    hexdump.add_argument("--drive", required=True)
    hexdump.add_argument("--offset", type=lambda v: int(v, 0), required=True)
    hexdump.add_argument("--length", type=lambda v: int(v, 0), default=512)
    hexdump.add_argument("--out", help="write hexdump output to this file")
    resume = sub.add_parser("resume", help="resume a validated partial session")
    resume.add_argument("--manifest", required=True)
    resume.add_argument("--json", action="store_true")
    diagnostics = sub.add_parser("diagnostics", help="create a diagnostics ZIP without raw media")
    diagnostics.add_argument("--session", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            return _scan(args)
        if args.command == "inspect":
            return _inspect(args)
        if args.command == "dump":
            return _dump(args)
        if args.command == "probe":
            return _probe(args)
        if args.command == "hexdump":
            return _hexdump(args)
        if args.command == "resume":
            return _resume(args)
        if args.command == "diagnostics":
            return _diagnostics(args)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 6
    print("No command selected. Launch the GUI with the standalone EXE.", file=sys.stderr)
    return 6
