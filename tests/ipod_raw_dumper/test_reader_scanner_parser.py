from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from iPodRawDriveDumper.device_scanner import scan_drives, validated_candidates
from iPodRawDriveDumper.fixtures import SyntheticFixture, SyntheticReader, build_synthetic_fixture, fixture_reader_factory
from iPodRawDriveDumper.firmware_parser import find_candidate
from iPodRawDriveDumper.models import RawGeometry
from iPodRawDriveDumper.win32_raw_reader import RawReadError


def fixture(sector_size: int, marker: bytes = b"{{~~") -> SyntheticFixture:
    return build_synthetic_fixture(sector_size=sector_size, osos_length=0x3000,
                                    include_secondary_sections=False, marker=marker)


@pytest.mark.parametrize("sector_size,marker_offset", [(512, 0x7E00), (2048, 0x1F800)])
def test_scanner_accepts_both_sector_layouts_without_fixed_2048_constants(sector_size, marker_offset):
    image = fixture(sector_size)
    path = r"\\.\PhysicalDrive0"
    records = scan_drives(reader_factory=fixture_reader_factory({path: image}), max_drives=1)
    matches = validated_candidates(records)
    assert len(matches) == 1
    _, candidate = matches[0]
    assert candidate.layout.marker_offset == marker_offset
    assert candidate.layout.firmware_body_offset == marker_offset + sector_size
    assert candidate.layout.osos.declared_length == 0x3000
    assert len(candidate.layout.probe_candidates) == 3

def test_reverse_marker_order_is_supported():
    image = fixture(512, marker=b"~~{{")
    candidate = find_candidate(SyntheticReader(image), image.geometry, image.marker_offset)
    assert candidate.accepted
    assert candidate.layout.marker == b"~~{{"


def test_marker_only_candidate_is_rejected():
    image = fixture(512)
    raw = bytearray(image.image)
    raw[image.marker_offset + 0x100:image.marker_offset + 0x104] = b"NOPE"
    broken = SyntheticFixture(bytes(raw), image.geometry, image.marker_offset, image.marker,
                              image.osos_offset, image.osos_length)
    candidate = find_candidate(SyntheticReader(broken), broken.geometry, broken.marker_offset)
    assert not candidate.accepted
    assert any("header" in reason for reason in candidate.reasons)


def test_out_of_bounds_section_is_rejected():
    image = fixture(2048)
    raw = bytearray(image.image)
    directory = image.marker_offset + 0x3C00
    raw[directory + 16:directory + 20] = (image.geometry.disk_length).to_bytes(4, "little")
    broken = SyntheticFixture(bytes(raw), image.geometry, image.marker_offset, image.marker,
                              image.osos_offset, image.osos_length)
    candidate = find_candidate(SyntheticReader(broken), broken.geometry, broken.marker_offset)
    assert not candidate.accepted
    assert any("bounds" in reason for reason in candidate.reasons)


def test_geometry_preserves_mbr_alternative_for_2048_fixture():
    image = fixture(2048)
    candidate = find_candidate(SyntheticReader(image), image.geometry, image.marker_offset)
    partition = candidate.layout.partitions[0]
    assert candidate.accepted
    assert partition.raw_end == image.geometry.disk_length
    assert partition.logical_end == image.geometry.disk_length * 4
    # The alternate 2048-byte interpretation being larger is informational, not a blocking contradiction.
    assert partition.contradiction is None
    assert "512-byte units" in partition.note
    assert candidate.layout.complete_safe


def test_reader_does_not_silently_pad_out_of_bounds_reads():
    image = fixture(512)
    reader = SyntheticReader(image)
    with pytest.raises(OSError):
        reader.read_at(image.geometry.disk_length - 1, 2)


def test_raw_read_error_has_transient_classification():
    error = RawReadError("device not ready", offset=0, requested=512, win32_code=21, transient=True)
    assert error.transient
    assert error.win32_code == 21
