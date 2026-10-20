from __future__ import annotations

from pathlib import Path

from iPodRawDriveDumper.device_scanner import scan_drives, validated_candidates
from iPodRawDriveDumper.dump_engine import DumpEngine
from iPodRawDriveDumper.fixtures import SyntheticReader, build_3g_fixture, fixture_reader_factory
from iPodRawDriveDumper.firmware_parser import find_candidate, _is_osos_entry


def test_3g_mov_pc_entry_is_accepted():
    # iPod 3G OSOS enters with MOV PC, R0 (0xE1A0F000), not an ARM B branch.
    assert _is_osos_entry(0xE1A0F000)
    assert _is_osos_entry(0xE1A0F001)  # MOV PC, R1 variant
    assert _is_osos_entry(0xEA00007A)  # Video branch still accepted
    assert _is_osos_entry(0xEA000079)  # PP5020 branch still accepted
    assert not _is_osos_entry(0x12345678)  # arbitrary data rejected


def test_3g_portalplayer_layout_is_validated():
    fixture = build_3g_fixture()
    candidate = find_candidate(SyntheticReader(fixture), fixture.geometry, fixture.marker_offset)
    assert candidate.accepted, candidate.reasons
    layout = candidate.layout
    assert layout.marker == b"{{~~"
    assert layout.osos is not None and layout.osos.name == "soso"
    assert layout.osos.bounds_status == "IN_BOUNDS"
    assert layout.osos_first_word == 0xE1A0F000
    assert layout.family == "iPod 3G (PortalPlayer PP5002)"
    assert layout.complete_safe


def test_3g_scan_reports_single_validated_candidate():
    fixture = build_3g_fixture()
    path = r"\\.\PhysicalDrive0"
    records = scan_drives(reader_factory=fixture_reader_factory({path: fixture}), max_drives=1)
    assert len(validated_candidates(records)) == 1


def test_3g_complete_dump_is_byte_identical(tmp_path: Path):
    fixture = build_3g_fixture()
    layout = find_candidate(SyntheticReader(fixture), fixture.geometry, fixture.marker_offset).layout
    result = DumpEngine(chunk_size=4096, sleep=lambda _: None).run(
        SyntheticReader(fixture), layout, tmp_path / "ipod3g", extract_sections=True)
    assert result["session"]["status"] == "COMPLETE"
    osos_file = next((tmp_path / "ipod3g" / "sections").glob("soso_*.bin"))
    expected = fixture.image[layout.osos.absolute_start:layout.osos.absolute_end]
    assert osos_file.read_bytes() == expected
