from __future__ import annotations

from iPodRawDriveDumper.device_scanner import scan_drives
from iPodRawDriveDumper.fixtures import SyntheticFixture, fixture_reader_factory
from iPodRawDriveDumper.models import RawGeometry


def _ipod_data_partition_only_fixture() -> SyntheticFixture:
    sector = 4096
    disk_length = 0x200000
    image = bytearray(disk_length)
    # Emulate the Apple/IPOD FAT32 boot sector text seen on a real iPod Classic in disk mode.
    boot = bytearray(0x200)
    boot[0:3] = b"\xEB\x3C\x90"
    boot[0x2B:0x36] = b"IPOD       "[:11]
    boot[0x52:0x5A] = b"FAT32   "
    msg = b"{{~~| S T O P | This is Apple iPod not a bootable disk.Please try again ... "
    boot[0x77:0x77 + len(msg)] = msg
    boot[0x1FE:0x200] = b"\x55\xAA"
    image[0x3F000:0x3F000 + len(boot)] = boot
    geometry = RawGeometry(disk_length=disk_length, logical_sector_size=sector,
                           physical_sector_size=sector, alignment=sector, removable=True)
    return SyntheticFixture(bytes(image), geometry, 0, b"", 0, 0)


def test_ipod_data_partition_only_is_classified_not_dumpable():
    fixture = _ipod_data_partition_only_fixture()
    path = r"\\.\PhysicalDrive0"
    records = scan_drives(reader_factory=fixture_reader_factory({path: fixture}), max_drives=1)
    record = records[0]
    assert not any(c.accepted for c in record.candidates)
    assert record.classification == "iPod data partition only"
    assert "disk mode" in (record.note or "")
    assert "DFU" in (record.note or "")
