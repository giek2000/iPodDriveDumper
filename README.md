# IpodRawDriveDumper

A read-only tool that makes a safe backup copy of an iPod's firmware. It **never writes to, restores, or flashes your iPod** — it only reads. There is no write path in the program at all; disk access is opened read-only and the only low-level queries are for disk size and geometry.

> ⚠️ **Work in progress.** This tool is under active development. It already works well for the models listed below, but features and model support are still being added, and version numbers change often. Please read the support list before relying on it, and always keep your original iPod and a known-good IPSW as backups.

## What works right now

| iPod model | Firmware dump over USB disk mode? | Notes |
|---|---|---|
| **iPod Video 5G / 5.5G** | ✅ Yes — tested on real hardware | Full firmware dump + named sub-image extraction |
| **iPod Mini 2G** | ✅ Yes — tested on real hardware | Full firmware dump + named sub-image extraction |
| **iPod Classic 6G/6.5G/7G** | ❌ No | Detected and identified, but the Classic only exposes its data partition over disk mode — firmware is not readable this way (needs DFU/wInd3x) |
| iPod Nano / Shuffle | ❌ Not supported | — |

What a successful dump gives you:

- A complete raw image of the firmware partition.
- Each named firmware sub-image extracted separately (the OS / `soso`, the updater / `dpua`, and any others present).
- SHA-256 hashes, the firmware directory, disk geometry, and a human-readable summary — so you can verify and inspect exactly what was captured.

What it does **not** do: it is not a restore tool and cannot rebuild a flashable IPSW. The on-device OS it captures is the installed copy, which differs from the clean packed images inside an official IPSW. Keep using official IPSW files for restoring.

## Download

The ready-to-run `IpodRawDriveDumper_vx.y.z.exe` is published **only on the [Releases page](https://github.com/giek2000/iPodDriveDumper/releases)** — it is intentionally not stored in the source repository. Download the latest `.exe` from there, or [build it yourself](#build).

## Quick start (GUI)

1. **Put your iPod into disk mode** and connect it with USB. (On iPod Video/Mini: toggle Hold on then off, hold Select + Menu to reboot, then immediately hold Select + Play to force disk mode.)
2. **Run the EXE as Administrator.** Reading raw disk sectors needs admin rights; the app will ask to elevate automatically.
3. Click **Refresh**, then pick your iPod from the **iPod** list. Only validated iPod firmware disks appear here, so you won't accidentally target the wrong drive.
4. Set **Files output** to a folder on your PC — for example `C:\iPodBackup`. **Never** choose the iPod's own drive letter.
5. (Optional) Tick **Extract sections** to save each firmware sub-image by name, and **Full partition** to capture the entire firmware partition including reserved space.
6. Click **Start Read-Only Dump** and wait for it to finish. When it's done, use **Open Output** to see the files and **Open Read Log** to review exactly what was read.

If anything looks wrong, nothing has been changed on the iPod — the tool only ever reads. Just disconnect and try again.

## Compatibility (technical detail)

Supported for raw disk-mode firmware dumping:

- **iPod Video 5G/5.5G** (PortalPlayer PP5022/PP5022C) — `{{~~` / `]ih[` / `!ATA` firmware layout. This model exposes its firmware partition as readable sectors over USB disk mode.
- **iPod Mini** (PortalPlayer PP5020/PP5022) — same `{{~~` / `]ih[` / `!ATA` PortalPlayer layout, confirmed on a live iPod Mini 2G. The Mini's `]ih[` header version field is zero and its OSOS begins with a different ARM branch (`0xEA000079`) than the Video; the validator accepts any ARM-branch OSOS first word and treats a zero header version as informational, so the Mini validates and dumps.

Recognized but not raw-dumpable over disk mode:

- **iPod Classic 6G/6.5G/7G** (Samsung S5L8702) — the Classic exposes only its FAT32 data partition over USB disk mode; its firmware partition is not presented as readable sectors. The tool detects an "iPod data partition only" device and explains this, rather than claiming a dump is possible. Raw firmware extraction on Classic requires DFU/wInd3x, not disk-mode reads. The bundled S5L8702 / `8702` IMG1 recognizer applies when an actual IMG1 firmware image is present in readable sectors (for example, a restored/extracted image), not to a stock Classic in disk mode.

Other iPod families (Nano, Shuffle) are not supported.

Read-only diagnostic commands (`probe`, `hexdump`) are provided to inspect a device's real on-disk bytes before any dump.

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
