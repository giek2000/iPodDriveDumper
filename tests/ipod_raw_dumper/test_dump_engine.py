from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from iPodRawDriveDumper.dump_engine import DumpEngine
from iPodRawDriveDumper.fixtures import SyntheticReader, build_synthetic_fixture
from iPodRawDriveDumper.firmware_parser import find_candidate
from iPodRawDriveDumper.win32_raw_reader import RawReadError


def prepared():
    fixture = build_synthetic_fixture(sector_size=512, osos_length=0x3000,
                                      include_secondary_sections=False)
    reader = SyntheticReader(fixture)
    layout = find_candidate(reader, fixture.geometry, fixture.marker_offset).layout
    return fixture, layout


def test_complete_dump_extracts_byte_identical_osos_and_manifest(tmp_path: Path):
    fixture, layout = prepared()
    output = tmp_path / "complete"
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, output, extract_sections=True)
    assert result["session"]["status"] == "COMPLETE"
    raw = output / "firmware_raw.bin"
    assert raw.exists() and not (output / "firmware_raw.bin.part").exists()
    assert raw.stat().st_size == result["dump"]["requested_bytes"]
    osos_file = next((output / "sections").glob("soso_*.bin"))
    expected = fixture.image[layout.osos.absolute_start:layout.osos.absolute_end]
    assert osos_file.read_bytes() == expected
    assert result["safety"] == {"write_access_requested": False, "write_file_called": False,
                                  "format_or_partition_operation_called": False}
    assert any(item["name"] == "firmware_header.bin" for item in result["artifacts"])
    events = (output / "read_events.jsonl").read_text(encoding="utf-8")
    assert "read_attempt" in events and "final_status" in events


def test_transient_error_is_retried_and_logged(tmp_path: Path):
    fixture, layout = prepared()
    reader = SyntheticReader(fixture)
    reader.faults[0] = [RawReadError("not ready", offset=0, requested=4096, win32_code=21, transient=True)]
    result = DumpEngine(chunk_size=4096, retries=2, sleep=lambda _: None).run(reader, layout, tmp_path / "retry", extract_sections=False)
    assert result["session"]["status"] == "COMPLETE"
    events = (tmp_path / "retry" / "read_events.jsonl").read_text(encoding="utf-8")
    assert "\"phase\": \"retry\"" in events


def test_short_read_is_not_padded(tmp_path: Path):
    fixture, layout = prepared()
    reader = SyntheticReader(fixture)
    reader.faults[0] = [b"short"]
    result = DumpEngine(chunk_size=4096, retries=3, sleep=lambda _: None).run(reader, layout, tmp_path / "short", extract_sections=False)
    assert result["session"]["status"] == "FAILED"
    assert result["dump"]["written_bytes"] == 0
    assert (tmp_path / "short" / "firmware_raw.bin.part").exists()


def test_zero_read_retries_then_fails(tmp_path: Path):
    fixture, layout = prepared()
    reader = SyntheticReader(fixture)
    reader.faults[0] = [b"", b""]
    result = DumpEngine(chunk_size=4096, retries=1, sleep=lambda _: None).run(reader, layout, tmp_path / "zero", extract_sections=False)
    assert result["session"]["status"] == "FAILED"
    assert len(reader.calls) == 2


def test_cancel_retains_checkpoint_and_partial_output(tmp_path: Path):
    fixture, layout = prepared()
    cancel = threading.Event()
    cancel.set()
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "cancelled", cancel=cancel)
    assert result["session"]["status"] == "CANCELLED"
    assert (tmp_path / "cancelled" / "checkpoint.json").exists()
    assert (tmp_path / "cancelled" / "firmware_raw.bin.part").exists()


def test_matching_checkpoint_resumes_without_rejecting_layout(tmp_path: Path):
    fixture, layout = prepared()
    output = tmp_path / "resume"
    reader = SyntheticReader(fixture)
    failure_offset = 4096
    reader.faults[failure_offset] = [RawReadError("disconnect", offset=failure_offset, requested=4096, win32_code=1167, transient=False)]
    first = DumpEngine(chunk_size=4096, retries=1, sleep=lambda _: None).run(reader, layout, output, extract_sections=False)
    assert first["session"]["status"] == "PARTIAL"
    checkpoint = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["last_contiguous"] == failure_offset
    resumed = DumpEngine(chunk_size=4096, retries=1, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, output, extract_sections=False, command="resume")
    assert resumed["session"]["status"] == "COMPLETE"
    assert (output / "firmware_raw.bin").read_bytes() == fixture.image[:layout.requested_end]


def test_resume_rejects_changed_layout_fingerprint(tmp_path: Path):
    fixture, layout = prepared()
    output = tmp_path / "changed"
    reader = SyntheticReader(fixture)
    reader.faults[4096] = [RawReadError("disconnect", offset=4096, requested=4096, transient=False)]
    DumpEngine(chunk_size=4096, sleep=lambda _: None).run(reader, layout, output, extract_sections=False)
    changed = build_synthetic_fixture(sector_size=512, marker_offset=0x7E00, osos_length=0x3000,
                                      include_secondary_sections=False, marker=b"~~{{")
    changed_layout = find_candidate(SyntheticReader(changed), changed.geometry, changed.marker_offset).layout
    checkpoint = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
    try:
        DumpEngine._verify_checkpoint(checkpoint, output / "firmware_raw.bin.part", changed_layout,
                                      changed.geometry, 0, layout.requested_end)
    except ValueError as exc:
        assert "fingerprint" in str(exc)
    else:
        raise AssertionError("changed layout was accepted for resume")


def test_out_of_bounds_partition_cannot_be_complete(tmp_path: Path):
    import struct
    from iPodRawDriveDumper.fixtures import SyntheticFixture
    fixture = build_synthetic_fixture(sector_size=2048, osos_length=0x3000,
                                      include_secondary_sections=False)
    raw = bytearray(fixture.image)
    # Force the authoritative 512-byte MBR interpretation beyond the disk end (genuine contradiction).
    struct.pack_into("<I", raw, 446 + 12, (fixture.geometry.disk_length // 512) + 100000)
    broken = SyntheticFixture(bytes(raw), fixture.geometry, fixture.marker_offset,
                              fixture.marker, fixture.osos_offset, fixture.osos_length)
    reader = SyntheticReader(broken)
    layout = find_candidate(reader, broken.geometry, broken.marker_offset).layout
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(broken), layout, tmp_path / "contradiction", extract_sections=False)
    assert result["session"]["status"] == "INVALID_STRUCTURE"
    assert not (tmp_path / "contradiction" / "firmware_raw.bin").exists()


def test_diagnostic_range_is_never_complete(tmp_path: Path):
    fixture, layout = prepared()
    end = min(layout.requested_end, 8192)
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "diagnostic", start=0, end=end,
        extract_sections=False, diagnostic=True)
    assert result["session"]["status"] == "PARTIAL"
    assert result["verification"]["structurally_complete"] is False
    assert (tmp_path / "diagnostic" / "firmware_raw.bin.part").exists()


def test_progress_callback_reports_completed_chunks(tmp_path: Path):
    fixture, layout = prepared()
    progress = []
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "progress",
        extract_sections=False, progress=lambda completed, total: progress.append((completed, total)))
    assert result["session"]["status"] == "COMPLETE"
    assert progress and progress[-1] == (layout.requested_end, layout.requested_end)


def test_full_partition_dump_covers_partition_end(tmp_path: Path):
    fixture, layout = prepared()
    # The fixture's MBR firmware partition spans the whole disk, which is larger than the
    # last !ATA section, so full-partition mode must extend the dump to the partition end.
    assert layout.firmware_partition_end is not None
    assert layout.firmware_partition_end > layout.section_end
    result = DumpEngine(chunk_size=65536, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "full", extract_sections=True, full_partition=True)
    assert result["session"]["status"] == "COMPLETE"
    assert result["dump"]["full_partition"] is True
    assert result["dump"]["requested_end"] == layout.firmware_partition_end
    assert result["dump"]["written_bytes"] == layout.firmware_partition_end
    # All validated sub-images are still extracted by name.
    assert any(a["name"].startswith("soso_") for a in result["artifacts"])


def test_default_dump_uses_requested_end_not_full_partition(tmp_path: Path):
    fixture, layout = prepared()
    result = DumpEngine(chunk_size=65536, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "default", extract_sections=False)
    assert result["session"]["status"] == "COMPLETE"
    assert result["dump"]["full_partition"] is False
    assert result["dump"]["requested_end"] == layout.requested_end
