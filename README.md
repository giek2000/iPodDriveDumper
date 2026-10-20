# IpodRawDriveDumper

This package is a read-only forensic reader for iPod Video 5G/5.5G disks. It is isolated from the repository's writer-oriented tools and has no physical-disk write path. Physical access uses buffered `CreateFileW` with `READ_ACCESS`, shared read/write, and `OPEN_EXISTING`; the only disk IOCTLs are geometry and length queries.

## Compatibility

Supported for raw disk-mode firmware dumping:

- **iPod Video 5G/5.5G** (PortalPlayer PP5022/PP5022C) — `{{~~` / `]ih[` / `!ATA` firmware layout. This model exposes its firmware partition as readable sectors over USB disk mode.
- **iPod Mini** (PortalPlayer PP5020/PP5022) — same `{{~~` / `]ih[` / `!ATA` PortalPlayer layout, confirmed on a live iPod Mini 2G. The Mini's `]ih[` header version field is zero and its OSOS begins with a different ARM branch (`0xEA000079`) than the Video; the validator accepts any ARM-branch OSOS first word and treats a zero header version as informational, so the Mini validates and dumps.

Recognized but not raw-dumpable over disk mode:

- **iPod Classic 6G/6.5G/7G** (Samsung S5L8702) — the Classic exposes only its FAT32 data partition over USB disk mode; its firmware partition is not presented as readable sectors. The tool detects an "iPod data partition only" device and explains this, rather than claiming a dump is possible. Raw firmware extraction on Classic requires DFU/wInd3x, not disk-mode reads. The bundled S5L8702 / `8702` IMG1 recognizer applies when an actual IMG1 firmware image is present in readable sectors (for example, a restored/extracted image), not to a stock Classic in disk mode.

Other iPod families (Nano, Mini, Shuffle) are not supported.

Read-only diagnostic commands (`probe`, `hexdump`) are provided to inspect a device's real on-disk bytes before any dump.

## Download

The compiled `IpodRawDriveDumper_vx.y.z.exe` is published **only as a GitHub Release asset** — it is intentionally not committed to the repository. Grab the latest build from the [Releases page](https://github.com/giek2000/iPodDriveDumper/releases). To build the EXE yourself from source, see [Build](#build).

### Full-partition dump and sub-image extraction

For a complete firmware-partition image (useful for preserving beta firmware and for later IPSW recreation), use full-partition mode:

```powershell
IpodRawDriveDumper.exe dump --drive \\.\PhysicalDrive2 --output C:\Backups\miniFW --full-partition --extract-sections
```

- `--full-partition` dumps the entire MBR-declared firmware partition, not just up to the last `!ATA` section, so trailing/reserved space is preserved.
- `--extract-sections` writes every validated `!ATA` sub-image by name (`soso`/OSOS, `aupd`/`dpua` updater, `rsrc`, `hibe`, and any others present), each with its directory record, offsets, load/entry addresses, and stored checksum recorded in `metadata.json`.
- The raw image, the preserved MBR, the `]ih[` directory, per-section SHA-256 hashes, and geometry together form the inputs a separate packaging tool would need to rebuild an IPSW. This tool stays read-only and does not itself package or flash.

In the GUI, tick both "Full partition" and "Extract sections" before starting the dump. A device with two firmware images (primary + mirror) lists them separately by offset so each can be dumped.


Put the iPod in disk mode, keep it connected during scanning/dumping, and verify the selected physical disk. Administrator privileges are normally required. The final confirmation states that no write access is requested and no disk sectors are modified. Do not use this tool as a restore image generator.

This is also a standalone PySide6 GUI: use the **iPod** selector to choose a validated physical disk candidate and **Files output** to choose a host-side destination. The GUI includes Refresh, layout inspection, section extraction, explicit read-only confirmation, progress/cancel, and output/log actions.


From `C:\KIRO`:

```powershell
python -m iPodRawDriveDumper inspect --fixture 2048 --json
python -m iPodRawDriveDumper dump --fixture 512 --output .\iPodRawDriveDumper\fixture-output --extract-sections --chunk-size 1048576
python -m iPodRawDriveDumper diagnostics --session .\iPodRawDriveDumper\fixture-output
```

The fixtures exercise both 512-byte and observed 2048-byte layouts without requiring Windows or a physical disk.

## Live use

```powershell
IpodRawDriveDumper_vx.y.z.exe scan --json
IpodRawDriveDumper_vx.y.z.exe inspect --drive \\.\PhysicalDrive1 --json
IpodRawDriveDumper_vx.y.z.exe dump --drive \\.\PhysicalDrive1 --output C:\Backups\iPodSession --extract-sections
```

No restore, patch, format, force-write, or equivalent command exists in this executable.

## Build

On Windows with the pinned build dependencies installed:

```powershell
python -m pip install -r requirements.txt
python build.py
```

The build script increments the patch version on each successful rebuild and produces `dist\IpodRawDriveDumper_vx.y.z.exe`.

The partition-boundary rule intentionally retains both MBR 512-byte interpretations and queried logical-sector geometry. A contradictory or unresolvable boundary is not accepted as a complete dump range.
