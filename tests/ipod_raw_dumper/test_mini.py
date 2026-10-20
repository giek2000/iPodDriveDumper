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
    # A live Mini 2G and a live 4G/Photo (Color) are indistinguishable over disk mode
    # (same "portalplayer" string, same 0xEA000079 OSOS branch, same PP5020 build
    # string), so the honest label is the shared PP5020 family, not a single model.
    assert layout.family == "iPod Mini / 4G / Photo (PortalPlayer PP5020)"
    assert layout.complete_safe


def test_pp5020_family_label_is_shared_and_honest():
    """The PP5020 fingerprint observed on a live 4G Color is identical to the Mini's.

    Grounded in a live iPod 4G Color (PhysicalDrive2): directory soso+dpua,
    OSOS first word 0xEA000079, "portalplayer" present, build "PP5020AF-05.00-...".
    The scanner validated it as dumpable; the only honest family label is the
    shared PP5020 family rather than a false "iPod Mini" claim.
    """
    fixture = build_mini_fixture()
    candidate = find_candidate(SyntheticReader(fixture), fixture.geometry, fixture.marker_offset)
    assert candidate.accepted, candidate.reasons
    assert candidate.layout.osos_first_word == 0xEA000079
    assert "PP5020" in candidate.layout.family
    assert "4G" in candidate.layout.family


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
