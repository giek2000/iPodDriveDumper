from __future__ import annotations

from pathlib import Path

from iPodRawDriveDumper.device_scanner import scan_drives, validated_candidates
from iPodRawDriveDumper.dump_engine import DumpEngine
from iPodRawDriveDumper.fixtures import SyntheticReader, build_mini_fixture, fixture_reader_factory
from iPodRawDriveDumper.firmware_parser import find_candidate


def test_mini_portalplayer_layout_is_validated():
    fixture = build_mini_fixture()
    candidate = find_candidate(SyntheticReader(fixture), fixture.geometry, fixture.marker_offset)
    assert candidate.accepted, candidate.reasons
    layout = candidate.layout
    assert layout.marker == b"{{~~"
    assert layout.osos is not None and layout.osos.name == "soso"
    assert layout.osos.bounds_status == "IN_BOUNDS"
    # ARM branch accepted even though it is not the Video's 0xEA00007A.
    assert layout.osos_first_word == 0xEA000079
    assert (layout.osos_first_word >> 24) == 0xEA
    assert b"portalplayer" in layout.osos_signature
    assert layout.family == "iPod Mini (PortalPlayer)"
    assert layout.complete_safe


def test_mini_scan_reports_single_validated_candidate():
    fixture = build_mini_fixture()
    path = r"\\.\PhysicalDrive0"
    records = scan_drives(reader_factory=fixture_reader_factory({path: fixture}), max_drives=1)
    matches = validated_candidates(records)
    assert len(matches) == 1


def test_mini_complete_dump_is_byte_identical(tmp_path: Path):
    fixture = build_mini_fixture()
    layout = find_candidate(SyntheticReader(fixture), fixture.geometry, fixture.marker_offset).layout
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "mini", extract_sections=True)
    assert result["session"]["status"] == "COMPLETE"
    osos_file = next((tmp_path / "mini" / "sections").glob("soso_*.bin"))
    expected = fixture.image[layout.osos.absolute_start:layout.osos.absolute_end]
    assert osos_file.read_bytes() == expected


def test_video_fixture_still_identified_as_video():
    from iPodRawDriveDumper.fixtures import build_synthetic_fixture
    fixture = build_synthetic_fixture(sector_size=2048)
    layout = find_candidate(SyntheticReader(fixture), fixture.geometry, fixture.marker_offset).layout
    assert layout.family.startswith("iPod Video")
    assert layout.complete_safe
