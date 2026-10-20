from __future__ import annotations

from pathlib import Path

from iPodRawDriveDumper.device_scanner import scan_drives, validated_candidates
from iPodRawDriveDumper.dump_engine import DumpEngine
from iPodRawDriveDumper.fixtures import SyntheticReader, build_s5l8702_fixture, fixture_reader_factory
from iPodRawDriveDumper.firmware_parser import find_candidate


def test_classic_img1_container_is_validated():
    fixture = build_s5l8702_fixture()
    candidate = find_candidate(SyntheticReader(fixture), fixture.geometry, fixture.marker_offset)
    assert candidate.accepted
    assert candidate.marker == "8702"
    assert candidate.layout.marker == b"8702"
    assert candidate.layout.osos is not None
    assert candidate.layout.osos.bounds_status == "IN_BOUNDS"
    assert candidate.layout.osos.declared_length == fixture.osos_length


def test_classic_scan_reports_single_validated_candidate():
    fixture = build_s5l8702_fixture()
    path = r"\\.\PhysicalDrive0"
    records = scan_drives(reader_factory=fixture_reader_factory({path: fixture}), max_drives=1)
    matches = validated_candidates(records)
    assert len(matches) == 1
    assert matches[0][1].marker == "8702"


def test_classic_complete_dump_is_byte_identical(tmp_path: Path):
    fixture = build_s5l8702_fixture(body_length=0x8000)
    reader = SyntheticReader(fixture)
    layout = find_candidate(reader, fixture.geometry, fixture.marker_offset).layout
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "classic", extract_sections=True)
    assert result["session"]["status"] == "COMPLETE"
    sections_dir = tmp_path / "classic" / "sections"
    osos_file = next(sections_dir.glob("osos_*.bin"))
    expected = fixture.image[layout.osos.absolute_start:layout.osos.absolute_end]
    assert osos_file.read_bytes() == expected
    assert result["dump"]["written_bytes"] == result["dump"]["requested_bytes"]


def test_video_path_still_rejects_non_ipod_marker():
    fixture = build_s5l8702_fixture()
    raw = bytearray(fixture.image)
    raw[fixture.marker_offset:fixture.marker_offset + 4] = b"NOPE"
    from iPodRawDriveDumper.fixtures import SyntheticFixture
    broken = SyntheticFixture(bytes(raw), fixture.geometry, fixture.marker_offset, b"NOPE",
                              fixture.osos_offset, fixture.osos_length)
    candidate = find_candidate(SyntheticReader(broken), broken.geometry, broken.marker_offset)
    assert not candidate.accepted
